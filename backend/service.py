"""Local Olympus roster collector and desktop companion service."""
import base64
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from database import Database

# Only documented roster namespaces may reach the network. Forever remains
# disabled until Blizzard publishes its contract; there is no environment bypass.
DOCUMENTED_PROFILES = frozenset('profile-' + region for region in ('us', 'eu', 'kr', 'tw'))
RETENTION_SECONDS = 29 * 86400  # margin below Blizzard's 30-day maximum


class UnsupportedNamespace(ValueError):
    pass


def validate_target(target):
    if not isinstance(target, (list, tuple)) or len(target) != 4:
        raise ValueError('Invalid target')
    region, namespace, realm, guild = target
    if region not in ('us', 'eu', 'kr', 'tw'):
        raise ValueError('Invalid region')
    if (not isinstance(namespace, str) or len(namespace) > 64
            or not re.fullmatch(r'profile(?:-[a-z0-9]+)*-' + region, namespace)):
        raise ValueError('Invalid profile namespace')
    for slug, maximum in ((realm, 80), (guild, 96)):
        if (not isinstance(slug, str) or not 1 <= len(slug) <= maximum
                or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug)):
            raise ValueError('Invalid slug')
    if namespace == 'profile-forever-' + region:
        if realm not in ('global', 'normal', 'pvp', 'roleplay', 'roleplay-pvp'):
            raise ValueError('Forever requires a supported ruleset or global scope')
    elif realm == 'global':
        raise ValueError('Other namespaces require a realm')
    return region, namespace, realm, guild


def forever_target(region, namespace):
    # Experimental adapter: a client author's live observations, not a Blizzard
    # support guarantee. Do not infer fictional realm IDs or rulesets.
    # https://github.com/Pinta365/blizzard_api/blob/6d3c0a12e076ff1f2c10d93ba4f530995411171a/src/wow_forever/guild.ts
    return namespace == 'profile-forever-' + region


def roster_path(region, namespace, scope, guild):
    parts = (guild,) if forever_target(region, namespace) else (scope, guild)
    return '/data/wow/guild/' + '/'.join(urllib.parse.quote(part, safe='') for part in parts) + '/roster'


def http_timestamp(value):
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            raise ValueError('Missing time zone')
        stamp = parsed.timestamp()
        if not math.isfinite(stamp) or stamp <= 0:
            raise ValueError('Invalid timestamp')
        return stamp
    except (TypeError, OverflowError) as error:
        raise ValueError('Invalid HTTP date') from error


def positive_id(value):
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise ValueError('Invalid identity')
    return value


def safe_name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid name')
    return value


class RulesetMismatch(ValueError):
    """The guild API did not confirm the requested Normal ruleset."""


def normal_evidence(summary, expected_slug, scope="normal"):
    expected = {"normal": "Normal", "pvp": "PvP", "roleplay": "Roleplay", "roleplay-pvp": "Roleplay PvP"}[scope]
    try:
        if summary['slug'] != expected_slug:
            raise ValueError('Guild slug mismatch')
        safe_name(summary['name'])
        ruleset = summary['ruleset']
        if not isinstance(ruleset['name'], str) or ruleset['name'].casefold() != expected.casefold():
            raise ValueError('Normal ruleset not confirmed')
        return {'guild_id': positive_id(summary['id']), 'guild_slug': expected_slug,
                'id': positive_id(ruleset['id']), 'name': expected}
    except (KeyError, TypeError, ValueError) as error:
        raise RulesetMismatch('Guild API did not confirm Normal ruleset') from error


def validate_normal_evidence(evidence, guild_id, guild_slug):
    try:
        if (evidence['guild_id'] != guild_id or type(evidence['guild_id']) is not int
                or evidence['guild_slug'] != guild_slug
                or not isinstance(evidence['name'], str) or evidence['name'].casefold() not in ('normal', 'pvp', 'roleplay', 'roleplay pvp')):
            raise ValueError('Ruleset evidence does not match roster')
        return positive_id(evidence['id']), evidence['name']
    except (KeyError, TypeError, ValueError) as error:
        raise RulesetMismatch('Guild API did not confirm Normal ruleset') from error


class Store:
    def __init__(self, path, targets=None):
        self.path = path
        self.database = Database(path)
        self.ready = False
        self.active_targets = None if targets is None else tuple('/'.join(validate_target(target)) for target in targets)
        if self.active_targets is not None and len(self.active_targets) > 256:
            raise ValueError('Too many active census targets')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (target TEXT PRIMARY KEY, fetched DOUBLE PRECISION, modified TEXT, members TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (target TEXT PRIMARY KEY, attempted DOUBLE PRECISION, error TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS collector_limits (name TEXT PRIMARY KEY, until_at DOUBLE PRECISION NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS snapshot_meta (target TEXT PRIMARY KEY, modified_epoch DOUBLE PRECISION, guild_id BIGINT, realm_id BIGINT, guild_name TEXT, faction TEXT)')
            columns = {row[1] for row in db.execute('PRAGMA table_info(attempts)').fetchall()}
            if 'next_retry_at' not in columns:
                db.execute('ALTER TABLE attempts ADD COLUMN next_retry_at DOUBLE PRECISION NOT NULL DEFAULT 0')
            if 'failures' not in columns:
                db.execute('ALTER TABLE attempts ADD COLUMN failures INTEGER NOT NULL DEFAULT 0')
            metadata_columns = {row[1] for row in db.execute('PRAGMA table_info(snapshot_meta)').fetchall()}
            if 'ruleset_id' not in metadata_columns:
                db.execute('ALTER TABLE snapshot_meta ADD COLUMN ruleset_id BIGINT')
            if 'ruleset_name' not in metadata_columns:
                db.execute('ALTER TABLE snapshot_meta ADD COLUMN ruleset_name TEXT')
        self.ready = True
        self.purge()

    @contextmanager
    def connect(self):
        with self.database.connect() as db:
            if self.ready:
                self._purge(db)
            yield db

    def _purge(self, db):
        now = time.time()
        db.execute('DELETE FROM snapshots WHERE fetched IS NULL OR fetched <= ? OR fetched > ?',
                   (now - RETENTION_SECONDS, now + 60))
        db.execute('DELETE FROM attempts WHERE attempted IS NULL OR attempted <= ?', (now - RETENTION_SECONDS,))
        if self.active_targets is not None:
            placeholders = ','.join('?' for _ in self.active_targets) or 'NULL'
            for table in ('snapshots', 'attempts'):
                db.execute('DELETE FROM ' + table + ' WHERE target NOT IN (' + placeholders + ')' if self.active_targets
                           else 'DELETE FROM ' + table, self.active_targets)
        db.execute('DELETE FROM snapshot_meta WHERE target NOT IN (SELECT target FROM snapshots)')

    def purge(self):
        with self.connect():
            pass

    def forget(self, key):
        with self.connect() as db:
            for table in ('snapshots', 'snapshot_meta', 'attempts'):
                db.execute('DELETE FROM ' + table + ' WHERE target=?', (key,))

    def save(self, key, payload, modified=None):
        region, namespace, realm_slug, guild_slug = validate_target(key.split('/'))
        if not isinstance(payload, dict):
            raise ValueError('Invalid roster')
        members = payload.get('members')
        # Defensive payload bound, not a claim about any particular game client's cap.
        if not isinstance(members, list) or len(members) > 10000:
            raise ValueError('Roster lacks members array')
        guild = payload['guild']
        guild_id = positive_id(guild['id'])
        forever = forever_target(region, namespace)
        ruleset_id, ruleset_name = None, None
        if forever and realm_slug != 'global':
            if guild.get('slug') != guild_slug:
                raise RulesetMismatch('Roster guild does not match Normal summary')
            ruleset_id, ruleset_name = validate_normal_evidence(payload.get('_olympus_ruleset'), guild_id, guild_slug)
            if ruleset_name.casefold() != {'normal': 'normal', 'pvp': 'pvp', 'roleplay': 'roleplay', 'roleplay-pvp': 'roleplay pvp'}[realm_slug]:
                raise RulesetMismatch('Roster ruleset does not match target')
        if forever:
            if guild.get('realm') is not None:
                raise ValueError('Unexpected realm in global guild reference')
            realm_id = 0  # Internal sentinel scoped by this region/namespace/target, never a real realm.
        else:
            realm_id = positive_id(guild['realm']['id'])
            if guild['realm']['slug'] != realm_slug:
                raise ValueError('Roster realm mismatch')
        if 'slug' in guild and guild['slug'] != guild_slug:
            raise ValueError('Roster guild mismatch')
        guild_name = safe_name(guild['name'])
        faction = guild.get('faction', {}).get('type')
        if faction not in (None, 'ALLIANCE', 'HORDE'):
            raise ValueError('Invalid faction')
        # Validate the response's declared resource when present; never follow it.
        href = payload.get('_links', {}).get('self', {}).get('href')
        if href is not None:
            link = urllib.parse.urlsplit(href)
            expected = roster_path(region, namespace, realm_slug, guild_slug)
            if (link.scheme != 'https' or link.netloc != region + '.api.blizzard.com'
                    or urllib.parse.unquote(link.path) != expected
                    or urllib.parse.parse_qs(link.query).get('namespace') != [namespace]):
                raise ValueError('Roster resource mismatch')
        modified_epoch = http_timestamp(modified)
        now = time.time()
        if modified_epoch is not None and modified_epoch > now + 60:
            raise ValueError('Future source timestamp')
        unique = {}
        for member in members:
            char = member['character']
            if forever:
                if char.get('realm') is not None:
                    raise ValueError('Unexpected realm in global character reference')
                character_realm = 0
            else:
                character_realm = positive_id(char['realm']['id'])
            identity = (character_realm, positive_id(char['id']))
            level = char.get('level')
            if level is not None and (type(level) is not int or not 1 <= level <= 1000):
                raise ValueError('Invalid level')
            class_id = char.get('playable_class', {}).get('id')
            if class_id is not None:
                positive_id(class_id)
            row = {'id': identity[1], 'realm_id': identity[0],
                   'name': safe_name(char['name']), 'level': level, 'class_id': class_id}
            if identity in unique and unique[identity] != row:
                raise ValueError('Conflicting duplicate character')
            unique[identity] = row
        serialized = json.dumps([unique[k] for k in sorted(unique)], sort_keys=True)
        with self.connect() as db:
            old = db.execute('SELECT s.members,m.modified_epoch,m.guild_id,m.realm_id,s.modified,m.guild_name,m.faction FROM snapshots s LEFT JOIN snapshot_meta m ON m.target=s.target WHERE s.target=?', (key,)).fetchone()
            if old:
                if old[2] is not None and (old[2], old[3]) != (guild_id, realm_id):
                    raise ValueError('Guild identity changed')
                if old[1] is not None:
                    same_roster = json.loads(old[0]) == json.loads(serialized)
                    if modified_epoch is None:
                        if not same_roster or (old[5], old[6]) != (guild_name, faction):
                            raise ValueError('Changed roster has no source timestamp')
                        # An unchanged response may omit this optional header.
                        # Refresh only the fetch date; retain the known source
                        # date rather than declaring old data newly observed.
                        modified_epoch, modified = old[1], old[4]
                    if modified_epoch < old[1]:
                        raise ValueError('Source timestamp regressed')
                    if modified_epoch == old[1] and not same_roster:
                        raise ValueError('Source timestamp conflicts')
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?) ON CONFLICT(target) DO UPDATE SET fetched=excluded.fetched,modified=excluded.modified,members=excluded.members',
                       (key, now, modified, serialized))
            db.execute('INSERT INTO snapshot_meta (target,modified_epoch,guild_id,realm_id,guild_name,faction,ruleset_id,ruleset_name) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(target) DO UPDATE SET modified_epoch=excluded.modified_epoch,guild_id=excluded.guild_id,realm_id=excluded.realm_id,guild_name=excluded.guild_name,faction=excluded.faction,ruleset_id=excluded.ruleset_id,ruleset_name=excluded.ruleset_name',
                       (key, modified_epoch, guild_id, realm_id, guild_name, faction, ruleset_id, ruleset_name))
            db.execute('INSERT INTO attempts (target,attempted,error,next_retry_at,failures) VALUES (?,?,NULL,0,0) ON CONFLICT(target) DO UPDATE SET attempted=excluded.attempted,error=NULL,next_retry_at=0,failures=0', (key, now))

    def failure(self, key, error, retry_after=0, global_limit=False):
        with self.connect() as db:
            old = db.execute('SELECT failures FROM attempts WHERE target=?', (key,)).fetchone()
            failures = min(20, (old[0] if old else 0) + 1)
            delay = max(retry_after, min(3600, 300 * 2 ** (failures - 1)))
            now = time.time()
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?) ON CONFLICT(target) DO UPDATE SET attempted=excluded.attempted,error=excluded.error,next_retry_at=excluded.next_retry_at,failures=excluded.failures',
                       (key, now, error, now + delay, failures))
            if global_limit:
                # Persist the app-wide 429 gate atomically with the failed
                # attempt, so restart or target edits cannot bypass it.
                db.execute("INSERT INTO collector_limits VALUES ('blizzard',?) ON CONFLICT(name) DO UPDATE SET until_at=MAX(collector_limits.until_at,excluded.until_at)", (now + delay,))
        return delay

    def global_retry_delay(self):
        with self.connect() as db:
            row = db.execute("SELECT until_at FROM collector_limits WHERE name='blizzard'").fetchone()
        return max(0, row[0] - time.time()) if row else 0

    def retry_delay(self, key):
        with self.connect() as db:
            row = db.execute('SELECT next_retry_at FROM attempts WHERE target=?', (key,)).fetchone()
        return max(0, row[0] - time.time()) if row else 0

    def census(self):
        now = time.time()
        params = self.active_targets or ()
        snapshot_filter, attempt_filter = '', ''
        if self.active_targets is not None:
            if self.active_targets:
                placeholders = ','.join('?' for _ in self.active_targets)
                snapshot_filter = ' WHERE s.target IN (' + placeholders + ')'
                attempt_filter = ' WHERE target IN (' + placeholders + ')'
            else:
                snapshot_filter = attempt_filter = ' WHERE 0=1'
        with self.connect() as db:
            rows = db.execute('SELECT s.target,s.fetched,s.modified,s.members,m.modified_epoch,m.guild_name,m.faction,m.ruleset_id,m.ruleset_name FROM snapshots s LEFT JOIN snapshot_meta m ON m.target=s.target' + snapshot_filter + ' ORDER BY s.target', params).fetchall()
            attempts = db.execute('SELECT target,attempted,error,next_retry_at,failures FROM attempts' + attempt_filter + ' ORDER BY target', params).fetchall()
        guilds = []
        for key, fetched, modified, members, source_at, guild_name, faction, ruleset_id, ruleset_name in rows:
            region, namespace, scope, _ = validate_target(key.split('/'))
            forever = forever_target(region, namespace)
            fetch_stale = now - fetched > 1800
            source_stale = now - source_at > 1800 if source_at is not None else None
            guilds.append({'target': key, 'fetched_at': fetched, 'source_last_modified': modified,
                           'source_modified_at': source_at, 'source_stale': source_stale,
                           'fetch_stale': fetch_stale, 'stale': fetch_stale or source_stale is not False,
                           'characters': len(json.loads(members)), 'guild_name': guild_name,
                           'faction': faction, 'online': None,
                           'scope_kind': ('ruleset' if scope != 'global' else 'global') if forever else 'realm',
                           'ruleset': {'id': ruleset_id, 'name': ruleset_name} if ruleset_id and ruleset_name in ('Normal', 'PvP', 'Roleplay', 'Roleplay PvP') else None,
                           'experimental': forever})
        return {'source': 'blizzard-api', 'guilds': guilds, 'attempts':
                [{'target': k, 'attempted_at': t, 'error': e, 'next_retry_at': r, 'failures': f} for k, t, e, r, f in attempts],
                'note': 'Fetch time is not observation time. Online population is unavailable.'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward bearer credentials to a redirected resource.


def read_json(response, maximum=4 * 1024 * 1024):
    raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Response too large')
    return json.loads(raw)


class Blizzard:
    def __init__(self, opener=None):
        self.token = None
        self.expires = 0
        self.rate_limited_until = 0
        self._opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.request_lock = threading.Lock()
        self.next_request = 0

    def opener(self, request, timeout):
        # Bound bursts as well as honoring HTTP 429. OAuth and roster requests
        # share the same ten-per-second ceiling within this collector.
        with self.request_lock:
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + 0.1
            return self._opener(request, timeout=timeout)

    def roster(self, target):
        region, namespace, realm, guild = validate_target(target)
        if namespace not in DOCUMENTED_PROFILES:
            raise UnsupportedNamespace('API namespace is not enabled: awaiting official Blizzard documentation')
        if time.time() >= self.expires:
            pair = os.environ['BNET_CLIENT_ID'] + ':' + os.environ['BNET_CLIENT_SECRET']
            request = urllib.request.Request('https://oauth.battle.net/token',
                data=b'grant_type=client_credentials', headers={
                    'Authorization': 'Basic ' + base64.b64encode(pair.encode()).decode(),
                    'Content-Type': 'application/x-www-form-urlencoded'})
            with self.opener(request, timeout=30) as response:
                token = read_json(response, 65536)
            if (not isinstance(token.get('access_token'), str) or not token['access_token']
                    or any(ord(c) < 33 or ord(c) > 126 for c in token['access_token'])
                    or type(token.get('expires_in')) is not int or token['expires_in'] <= 0):
                raise ValueError('Invalid access token response')
            self.token = token['access_token']
            self.expires = time.time() + max(0, token['expires_in'] - 60)
        base = 'https://' + region + '.api.blizzard.com'
        path = roster_path(region, namespace, realm, guild)
        query = {'namespace': namespace}
        evidence = None
        if forever_target(region, namespace) and realm != 'global':
            # Ruleset is present on the guild summary, not on the roster's guild
            # reference. Fetch it first and match IDs/slugs before storing rows.
            query['locale'] = 'en_US'
            summary_url = base + path.removesuffix('/roster') + '?' + urllib.parse.urlencode(query)
            summary_request = urllib.request.Request(summary_url, headers={'Authorization': 'Bearer ' + self.token})
            with self.opener(summary_request, timeout=30) as response:
                evidence = normal_evidence(read_json(response), guild, realm)
        url = base + path + '?' + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token})
        with self.opener(request, timeout=30) as response:
            payload = read_json(response)
            modified = response.headers.get('Last-Modified')
        if evidence is not None:
            try:
                roster_guild = payload['guild']
                if roster_guild['slug'] != guild:
                    raise RulesetMismatch('Roster guild does not match Normal summary')
                validate_normal_evidence(evidence, positive_id(roster_guild['id']), roster_guild['slug'])
                # Overwrite any similarly named data from the remote JSON. Only
                # this independently fetched summary supplies ruleset evidence.
                payload['_olympus_ruleset'] = evidence
            except (KeyError, TypeError, ValueError) as error:
                raise RulesetMismatch('Roster does not match Normal guild summary') from error
        return payload, modified


def retry_after_seconds(error):
    value = getattr(error, 'headers', {}).get('Retry-After') if getattr(error, 'headers', None) else None
    if value is None:
        return 0
    try:
        if value.isdigit():
            return int(value)
        return max(0, http_timestamp(value) - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0


def sync_once(store, client, targets, stop_event=None):
    """Collect eligible guilds; return only app-wide delay, never a guild's backoff."""
    if stop_event is not None and stop_event.is_set():
        return 0
    next_delay = max(store.global_retry_delay(), getattr(client, 'rate_limited_until', 0) - time.time(), 0)
    if next_delay:
        return next_delay
    for target in targets:
        if stop_event is not None and stop_event.is_set():
            break
        validate_target(target)
        key = '/'.join(target)
        delay = store.retry_delay(key)
        if delay:
            continue
        try:
            payload, modified = client.roster(target)
            store.save(key, payload, modified)
        except Exception as error:
            # Do not persist exception text: it can contain URLs or credentials.
            try:
                if isinstance(error, UnsupportedNamespace) or getattr(error, 'code', None) in (403, 404, 410):
                    store.forget(key)
                delay = store.failure(key, type(error).__name__ + (':' + str(error.code) if hasattr(error, 'code') else ''), retry_after_seconds(error),
                                      global_limit=getattr(error, 'code', None) == 429)
                if getattr(error, 'code', None) == 401:
                    client.expires = 0
            finally:
                if hasattr(error, 'close'):
                    error.close()
            if getattr(error, 'code', None) == 429:
                client.rate_limited_until = time.time() + delay
                return delay
    return 0


@dataclass(frozen=True)
class Config:
    database_path: str
    targets: tuple
    interval: int
    port: int
    credentials_configured: bool

    @property
    def sync_configured(self):
        return bool(self.targets and self.credentials_configured)


def load_config(environ=None):
    env = os.environ if environ is None else environ
    try:
        targets = json.loads(env.get('CENSUS_TARGETS', '[]'))
    except (TypeError, ValueError) as error:
        raise ValueError('CENSUS_TARGETS must be a JSON array') from error
    if not isinstance(targets, list) or len(targets) > 256:
        raise ValueError('CENSUS_TARGETS must contain at most 256 targets')
    validated = tuple(validate_target(target) for target in targets)
    if len(set(validated)) != len(validated):
        raise ValueError('CENSUS_TARGETS contains duplicate targets')
    try:
        interval = int(env.get('CENSUS_INTERVAL', '900'))
        port = int(env.get('CENSUS_PORT', '8787'))
    except (ValueError, TypeError) as error:
        raise ValueError('Census interval and port must be integers') from error
    if not 300 <= interval <= 86400:
        raise ValueError('CENSUS_INTERVAL must be between 300 and 86400 seconds')
    if not 1 <= port <= 65535:
        raise ValueError('CENSUS_PORT must be between 1 and 65535')
    database_path = env.get('CENSUS_DB', 'census.sqlite3')
    Database(database_path)  # Validate local storage before starting any work.
    credentials = bool(env.get('BNET_CLIENT_ID', '').strip() and env.get('BNET_CLIENT_SECRET', '').strip())
    return Config(database_path, validated, interval, port, credentials)


class Poller:
    def __init__(self, store, config, client=None):
        self.store = store
        self.config = config
        self.client = client if client is not None else Blizzard()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.state = 'starting' if config.sync_configured else 'disabled'
        self.last_cycle_at = None
        self.next_poll_at = None
        self.last_error = None

    def status(self):
        with self.lock:
            alive = self.thread is not None and self.thread.is_alive()
            failed = self.last_error is not None or (self.config.sync_configured and not alive)
            return {'status': 'degraded' if failed else 'ok',
                    'sync_configured': self.config.sync_configured,
                    'credentials_configured': self.config.credentials_configured,
                    'target_count': len(self.config.targets), 'worker_alive': alive,
                    'worker_state': self.state, 'last_cycle_at': self.last_cycle_at,
                    'next_poll_at': self.next_poll_at, 'last_error': self.last_error}

    def cycle(self):
        if not self.config.sync_configured:
            return self.config.interval
        with self.lock:
            self.state = 'polling'
            self.next_poll_at = None
        delay = self.config.interval
        error_code = None
        try:
            retry = float(sync_once(self.store, self.client, self.config.targets, self.stop_event))
            if not math.isfinite(retry) or retry < 0:
                raise ValueError('Invalid retry delay')
            delay = max(delay, retry)
        except Exception as error:
            # DB outages must not silently kill the worker. Exception messages
            # can contain URLs or sensitive configuration; expose the type only.
            error_code = type(error).__name__
        with self.lock:
            self.last_cycle_at = time.time()
            self.next_poll_at = self.last_cycle_at + delay
            self.last_error = error_code
            self.state = 'error' if error_code else 'waiting'
        return delay

    def run(self):
        try:
            while not self.stop_event.is_set():
                delay = self.cycle()
                # Chunk very long Retry-After waits so platform timeout bounds
                # cannot terminate the worker and shutdown remains immediate.
                deadline = time.monotonic() + delay
                while not self.stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self.stop_event.wait(min(remaining, 60))
                    try:
                        self.store.purge()  # also when collection is disabled/backing off
                    except Exception as error:
                        # A storage outage fails reads closed, but must not kill
                        # the worker permanently; retry at the next wakeup.
                        with self.lock:
                            self.last_error = type(error).__name__
                            self.state = 'error'
        finally:
            with self.lock:
                self.state = 'stopped'
                self.next_poll_at = None

    def start(self):
        if self.thread is not None:
            raise RuntimeError('Collector already started')
        self.thread = threading.Thread(target=self.run, name='olympus-collector', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


def main(on_ready=None):
    from webapp import make_server
    config = load_config()
    store = Store(config.database_path, targets=config.targets)
    poller = Poller(store, config)
    # Bind before starting the collector: an occupied port must fail without
    # creating an invisible second polling worker.
    server = make_server(('127.0.0.1', config.port), store, poller.status)
    try:
        # Desktop startup briefly removes credentials from os.environ while
        # launching a browser. Do that before any worker can read credentials.
        # The bound listener already queues requests until serve_forever starts.
        if on_ready is not None:
            on_ready(config.port)
        poller.start()
        server.serve_forever()
    finally:
        poller.stop()
        server.server_close()


if __name__ == '__main__':
    main()
