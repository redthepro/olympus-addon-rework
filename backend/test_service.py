from email.utils import formatdate
import io
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from service import Blizzard, RulesetMismatch, Store, sync_once, validate_target, read_json


TARGET = ['us', 'profile-us', 'realm', 'guild']
KEY = '/'.join(TARGET)


def roster(*ids):
    return {'guild': {'id': 8, 'name': 'Guild', 'realm': {'id': 1, 'slug': 'realm'},
                      'faction': {'type': 'ALLIANCE'}},
            'members': [{'character': {'id': i, 'realm': {'id': 1}, 'name': 'Test'}} for i in ids]}


def forever_roster(*ids):
    """Synthetic fixture from Pinta365's pinned types, not an actual API capture.

    https://github.com/Pinta365/blizzard_api/blob/6d3c0a12e076ff1f2c10d93ba4f530995411171a/src/wow_forever/guild.ts
    """
    return {'_links': {'self': {'href': 'https://us.api.blizzard.com/data/wow/guild/olympus/roster?namespace=profile-forever-us'}},
            'guild': {'key': {'href': 'https://us.api.blizzard.com/data/wow/guild/olympus?namespace=profile-forever-us'},
                      'id': 80, 'name': 'Olympus', 'slug': 'olympus',
                      'faction': {'type': 'ALLIANCE', 'name': 'Alliance'}},
            'members': [{'character': {'key': {'href': 'https://us.api.blizzard.com/profile/wow/character/test-example?namespace=profile-forever-us'},
                                       'id': i, 'name': 'Test', 'surname': 'Example', 'slug': 'test-example',
                                       'level': 5, 'playable_class': {'id': 1}, 'playable_race': {'id': 1},
                                       'faction': {'type': 'ALLIANCE'}}, 'rank': 0} for i in ids]}


class Response(io.BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(json.dumps(value).encode())
        self.headers = headers or {}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'test.db'))

    def test_deduplicates_and_replaces_roster(self):
        self.store.save(KEY, roster(1, 1, 2))
        self.assertEqual(self.store.census()['guilds'][0]['characters'], 2)
        self.store.save(KEY, roster(2))
        self.assertEqual(self.store.census()['guilds'][0]['characters'], 1)
        self.assertIsNone(self.store.census()['guilds'][0]['online'])

    def test_bad_payload_does_not_destroy_last_good(self):
        self.store.save(KEY, roster(1))
        for payload in ({}, {'members': None}, {'members': []}, []):
            with self.subTest(payload=payload), self.assertRaises((ValueError, KeyError)):
                self.store.save(KEY, payload)
        self.assertEqual(self.store.census()['guilds'][0]['characters'], 1)

    def test_failure_preserves_data_and_hides_exception_text(self):
        self.store.save(KEY, roster(1))
        class Failed:
            def roster(self, target):
                raise RuntimeError('SECRET')
        delay = sync_once(self.store, Failed(), [TARGET])
        result = self.store.census()
        self.assertEqual(result['guilds'][0]['characters'], 1)
        self.assertEqual(result['attempts'][0]['error'], 'RuntimeError')
        self.assertNotIn('SECRET', str(result))
        self.assertEqual(delay, 0)  # Target backoff must not pause other guilds.
        self.assertGreater(self.store.retry_delay(KEY), 299)

    def test_fetch_fresh_does_not_mean_source_fresh(self):
        self.store.save(KEY, roster(1), formatdate(time.time() - 3600, usegmt=True))
        row = self.store.census()['guilds'][0]
        self.assertFalse(row['fetch_stale'])
        self.assertTrue(row['source_stale'])
        self.assertTrue(row['stale'])

    def test_unknown_source_is_not_fresh(self):
        self.store.save(KEY, roster(1))
        row = self.store.census()['guilds'][0]
        self.assertFalse(row['fetch_stale'])
        self.assertIsNone(row['source_stale'])
        self.assertIsNone(row['source_modified_at'])
        self.assertTrue(row['stale'])

    def test_stale_fetch_is_explicit(self):
        self.store.save(KEY, roster(1), formatdate(time.time(), usegmt=True))
        with self.store.connect() as db:
            db.execute('UPDATE snapshots SET fetched=?', (time.time() - 3600,))
        row = self.store.census()['guilds'][0]
        self.assertTrue(row['fetch_stale'])
        self.assertFalse(row['source_stale'])
        self.assertTrue(row['stale'])

    def test_conflicting_duplicate_rejected(self):
        payload = roster(1, 1)
        payload['members'][1]['character']['name'] = 'Other'
        with self.assertRaises(ValueError):
            self.store.save(KEY, payload)
        self.assertEqual(self.store.census()['guilds'], [])

    def test_invalid_ids_rejected(self):
        for value in (True, -1, 0, '1', 2 ** 64):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save(KEY, roster(value))

    def test_wrong_realm_and_resource_rejected(self):
        payload = roster(1)
        payload['guild']['realm']['slug'] = 'different'
        with self.assertRaises(ValueError):
            self.store.save(KEY, payload)
        for href in ('https://evil.example/data/wow/guild/realm/guild/roster?namespace=profile-us',
                     'https://us.api.blizzard.com/data/wow/guild/realm/other/roster?namespace=profile-us',
                     'https://us.api.blizzard.com/data/wow/guild/realm/guild/roster?namespace=profile-classic-us'):
            payload = roster(1)
            payload['_links'] = {'self': {'href': href}}
            with self.subTest(href=href), self.assertRaises(ValueError):
                self.store.save(KEY, payload)

    def test_old_source_never_overwrites_newer_snapshot(self):
        now = time.time()
        stamp = formatdate(now - 20, usegmt=True)
        self.store.save(KEY, roster(1, 2), stamp)
        for modified in (formatdate(now - 100, usegmt=True), None, stamp):
            with self.subTest(modified=modified), self.assertRaises(ValueError):
                self.store.save(KEY, roster(1), modified)
        self.assertEqual(self.store.census()['guilds'][0]['characters'], 2)
        self.store.save(KEY, roster(2, 1), stamp)

    def test_guild_identity_change_is_not_silent(self):
        self.store.save(KEY, roster(1))
        payload = roster(2)
        payload['guild']['id'] = 9
        with self.assertRaises(ValueError):
            self.store.save(KEY, payload)

    def test_invalid_and_future_dates_rejected(self):
        for value in ('nonsense', 'Wed, 12 Nov 2025 12:00:00', formatdate(time.time() + 600, usegmt=True)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save(KEY, roster(1), value)

    def test_retry_after_blocks_batch_without_sleeping(self):
        class Limited:
            calls = 0
            def roster(self, target):
                self.calls += 1
                raise urllib.error.HTTPError('SECRET', 429, 'limited', {'Retry-After': '1800'}, None)
        client = Limited()
        delay = sync_once(self.store, client, [TARGET, ['us', 'profile-us', 'realm', 'other']])
        self.assertGreaterEqual(delay, 1800)
        self.assertEqual(client.calls, 1)
        self.assertGreater(sync_once(self.store, client, [TARGET]), 1700)
        self.assertEqual(client.calls, 1)

    def test_retry_after_http_date_supported(self):
        class Limited:
            def roster(self, target):
                raise urllib.error.HTTPError('x', 503, 'busy', {'Retry-After': formatdate(time.time() + 2400, usegmt=True)}, None)
        delay = sync_once(self.store, Limited(), [TARGET])
        self.assertEqual(delay, 0)
        self.assertGreater(self.store.retry_delay(KEY), 2300)

    def test_failure_backoff_survives_client_restart(self):
        class Failed:
            calls = 0
            def roster(self, target):
                self.calls += 1
                raise RuntimeError()
        sync_once(self.store, Failed(), [TARGET])
        replacement = Failed()
        sync_once(self.store, replacement, [TARGET])
        self.assertEqual(replacement.calls, 0)
        with self.store.connect() as db:
            db.execute('UPDATE attempts SET next_retry_at=0')
        self.assertEqual(sync_once(self.store, replacement, [TARGET]), 0)
        self.assertGreater(self.store.retry_delay(KEY), 599)
        self.assertEqual(self.store.census()['attempts'][0]['failures'], 2)

    def test_401_invalidates_cached_token(self):
        class Unauthorized:
            expires = time.time() + 86400
            def roster(self, target):
                raise urllib.error.HTTPError('x', 401, 'no', {}, None)
        client = Unauthorized()
        sync_once(self.store, client, [TARGET])
        self.assertEqual(client.expires, 0)

    def test_success_resets_retry_state(self):
        self.store.failure(KEY, 'x')
        self.store.save(KEY, roster(1))
        result = self.store.census()['attempts'][0]
        self.assertIsNone(result['error'])
        self.assertEqual(result['failures'], 0)
        self.assertEqual(result['next_retry_at'], 0)

    def test_target_validation(self):
        for target in (['evil', 'profile-us', 'realm', 'guild'], ['us', 'dynamic-us', 'realm', 'guild'],
                       ['us', 'profile-eu', 'realm', 'guild'], ['us', 'profile-us', '../realm', 'guild']):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_target(target)

    def test_forever_target_requires_supported_scope(self):
        valid = ['us', 'profile-forever-us', 'global', 'olympus']
        self.assertEqual(validate_target(valid), tuple(valid))
        normal = ['us', 'profile-forever-us', 'normal', 'olympus']
        self.assertEqual(validate_target(normal), tuple(normal))
        retail = ['us', 'profile-us', 'normal', 'olympus']
        self.assertEqual(validate_target(retail), tuple(retail))
        for target in (['us', 'profile-forever-us', 'realm', 'olympus'],
                       ['us', 'profile-us', 'global', 'olympus']):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_target(target)

    def test_forever_references_are_stored_without_fabricating_realms(self):
        key = 'us/profile-forever-us/global/olympus'
        self.store.save(key, forever_roster(123, 123, 456))
        row = self.store.census()['guilds'][0]
        self.assertEqual(row['characters'], 2)
        self.assertEqual(row['scope_kind'], 'global')
        self.assertTrue(row['experimental'])
        self.assertEqual(row['guild_name'], 'Olympus')
        self.assertIsNone(row['online'])
        with self.store.connect() as db:
            members = json.loads(db.execute('SELECT members FROM snapshots WHERE target=?', (key,)).fetchone()[0])
        self.assertEqual([member['realm_id'] for member in members], [0, 0])
        self.assertEqual([member['level'] for member in members], [5, 5])

    def test_forever_wrong_guild_or_realmful_response_rejected(self):
        key = 'us/profile-forever-us/global/olympus'
        for part in ('guild_slug', 'guild_realm', 'character_realm', 'self_path'):
            payload = forever_roster(1)
            if part == 'guild_slug':
                payload['guild']['slug'] = 'other'
            elif part == 'guild_realm':
                payload['guild']['realm'] = {'id': 1, 'slug': 'realm'}
            elif part == 'character_realm':
                payload['members'][0]['character']['realm'] = {'id': 1}
            else:
                payload['_links']['self']['href'] = 'https://us.api.blizzard.com/data/wow/guild/global/olympus/roster?namespace=profile-forever-us'
            with self.subTest(part=part), self.assertRaises(ValueError):
                self.store.save(key, payload)

    # Synthetic adapter test only; production blocks this undocumented namespace.
    @patch('service.DOCUMENTED_PROFILES', frozenset({'profile-forever-us'}))
    def test_forever_request_uses_realmless_endpoint(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response(forever_roster(1))
        client = Blizzard(opener)
        client.token = 'test-token'
        client.expires = time.time() + 300
        payload, modified = client.roster(['us', 'profile-forever-us', 'global', 'olympus'])
        self.assertEqual(calls, ['https://us.api.blizzard.com/data/wow/guild/olympus/roster?namespace=profile-forever-us'])
        self.store.save('us/profile-forever-us/global/olympus', payload, modified)
        self.assertEqual(self.store.census()['guilds'][0]['characters'], 1)

    # Synthetic adapter test only; production blocks this undocumented namespace.
    @patch('service.DOCUMENTED_PROFILES', frozenset({'profile-forever-us'}))
    def test_normal_summary_precedes_roster_and_persists_evidence(self):
        calls = []
        summary = {'id': 80, 'name': 'Olympus', 'slug': 'olympus',
                   'ruleset': {'id': 123, 'name': 'NORMAL'}}
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response(forever_roster(1, 2) if '/roster?' in request.full_url else summary)
        client = Blizzard(opener)
        client.token, client.expires = 'test-token', time.time() + 300
        target = ['us', 'profile-forever-us', 'normal', 'olympus']
        payload, modified = client.roster(target)
        self.assertEqual(calls, [
            'https://us.api.blizzard.com/data/wow/guild/olympus?namespace=profile-forever-us&locale=en_US',
            'https://us.api.blizzard.com/data/wow/guild/olympus/roster?namespace=profile-forever-us&locale=en_US'])
        self.store.save('/'.join(target), payload, modified)
        row = self.store.census()['guilds'][0]
        self.assertEqual(row['scope_kind'], 'ruleset')
        self.assertEqual(row['ruleset'], {'id': 123, 'name': 'Normal'})
        self.assertEqual(row['characters'], 2)
        self.assertTrue(row['experimental'])

    # Synthetic adapter test only; production blocks this undocumented namespace.
    @patch('service.DOCUMENTED_PROFILES', frozenset({'profile-forever-us'}))
    def test_each_forever_ruleset_is_verified_and_wrong_scope_rejected(self):
        for scope, label in [('pvp', 'PvP'), ('roleplay', 'Roleplay'), ('roleplay-pvp', 'Roleplay PvP')]:
            def opener(request, timeout):
                return Response(forever_roster(1, 2) if '/roster?' in request.full_url else
                                {'id': 80, 'name': 'Olympus', 'slug': 'olympus', 'ruleset': {'id': 321, 'name': label}})
            client = Blizzard(opener)
            client.token, client.expires = 'test-token', time.time() + 300
            target = ['us', 'profile-forever-us', scope, 'olympus']
            payload, modified = client.roster(target)
            self.store.save('/'.join(target), payload, modified)
            row = next(r for r in self.store.census()['guilds'] if r['target'] == '/'.join(target))
            self.assertEqual(row['ruleset']['name'], label)
            with self.assertRaises(RulesetMismatch):
                self.store.save('us/profile-forever-us/normal/olympus', payload, modified)

    # Synthetic adapter test only; production blocks this undocumented namespace.
    @patch('service.DOCUMENTED_PROFILES', frozenset({'profile-forever-us'}))
    def test_normal_missing_or_wrong_ruleset_stops_before_roster(self):
        for ruleset in (None, {}, {'id': 123, 'name': 'PvP'}, {'id': 123, 'name': 'PvE'},
                        {'id': True, 'name': 'Normal'}, {'id': 0, 'name': 'Normal'}):
            calls = []
            def opener(request, timeout):
                calls.append(request.full_url)
                return Response({'id': 80, 'name': 'Olympus', 'slug': 'olympus', 'ruleset': ruleset})
            client = Blizzard(opener)
            client.token, client.expires = 'test-token', time.time() + 300
            with self.subTest(ruleset=ruleset), self.assertRaises(RulesetMismatch):
                client.roster(['us', 'profile-forever-us', 'normal', 'olympus'])
            self.assertEqual(len(calls), 1)

    # Synthetic adapter test only; production blocks this undocumented namespace.
    @patch('service.DOCUMENTED_PROFILES', frozenset({'profile-forever-us'}))
    def test_normal_summary_and_roster_identity_must_match(self):
        for mismatch in ('id', 'slug'):
            def opener(request, timeout):
                if '/roster?' not in request.full_url:
                    return Response({'id': 80, 'name': 'Olympus', 'slug': 'olympus',
                                     'ruleset': {'id': 123, 'name': 'Normal'}})
                payload = forever_roster(1)
                payload['guild'][mismatch] = 999 if mismatch == 'id' else 'other'
                return Response(payload)
            client = Blizzard(opener)
            client.token, client.expires = 'test-token', time.time() + 300
            with self.subTest(mismatch=mismatch), self.assertRaises(RulesetMismatch):
                client.roster(['us', 'profile-forever-us', 'normal', 'olympus'])

    def test_normal_store_requires_matching_ruleset_evidence(self):
        target = ['us', 'profile-forever-us', 'normal', 'olympus']
        payload = forever_roster(1)
        with self.assertRaises(RulesetMismatch):
            self.store.save('/'.join(target), payload)
        payload['_olympus_ruleset'] = {'guild_id': 80, 'guild_slug': 'olympus', 'id': 123, 'name': 'Normal'}
        self.store.save('/'.join(target), payload)
        class WrongRuleset:
            def roster(self, unused):
                bad = forever_roster(1, 2)
                bad['_olympus_ruleset'] = {'guild_id': 80, 'guild_slug': 'olympus', 'id': 999, 'name': 'PvP'}
                return bad, None
        sync_once(self.store, WrongRuleset(), [target])
        census = self.store.census()
        self.assertEqual(census['guilds'][0]['characters'], 1)
        self.assertEqual(census['guilds'][0]['ruleset']['id'], 123)
        self.assertEqual(census['attempts'][0]['error'], 'RulesetMismatch')

    def test_active_targets_delete_previous_scope_cache(self):
        normal = ['us', 'profile-forever-us', 'normal', 'olympus']
        global_target = ['us', 'profile-forever-us', 'global', 'olympus']
        payload = forever_roster(1)
        self.store.save('/'.join(global_target), payload)
        payload['_olympus_ruleset'] = {'guild_id': 80, 'guild_slug': 'olympus', 'id': 123, 'name': 'Normal'}
        self.store.save('/'.join(normal), payload)
        scoped = Store(self.store.path, targets=[normal]).census()
        self.assertEqual([row['target'] for row in scoped['guilds']], ['/'.join(normal)])
        self.assertEqual([row['target'] for row in scoped['attempts']], ['/'.join(normal)])
        empty = Store(self.store.path, targets=[]).census()
        self.assertEqual(empty['guilds'], [])
        self.assertEqual(empty['attempts'], [])
        self.assertEqual(len(self.store.census()['guilds']), 0)

    def test_target_slugs_match_export_limits(self):
        namespace = 'profile-' + 'a' * 53 + '-us'
        self.assertEqual(len(namespace), 64)
        valid = ['us', namespace, 'r' * 80, 'g' * 96]
        self.assertEqual(validate_target(valid), tuple(valid))
        for index in (1, 2, 3):
            too_long = valid.copy()
            # Namespace must still end in the region to test its length bound.
            too_long[index] = (namespace[:-3] + 'a-us') if index == 1 else valid[index] + 'a'
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_target(too_long)

    def test_nonportable_slugs_rejected_before_collection(self):
        for slug in ('Realm', 'réalm', '-realm', 'realm-', 'realm--name', 'realm_name'):
            for index in (2, 3):
                target = TARGET.copy()
                target[index] = slug
                with self.subTest(slug=slug, index=index), self.assertRaises(ValueError):
                    validate_target(target)

    def test_source_clock_skew_matches_export_tolerance(self):
        now = 1800000000
        with patch('service.time.time', return_value=now):
            self.store.save(KEY, roster(1), formatdate(now + 60, usegmt=True))
            with self.assertRaises(ValueError):
                self.store.save(KEY, roster(1), formatdate(now + 61, usegmt=True))

    def test_http_requests_use_bearer_header_and_cache_token(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if request.full_url.endswith('/token'):
                return Response({'access_token': 'secret-token', 'expires_in': 3600})
            return Response(roster(1))
        client = Blizzard(opener)
        with patch.dict('os.environ', {'BNET_CLIENT_ID': 'id', 'BNET_CLIENT_SECRET': 'secret'}):
            client.roster(TARGET)
            client.roster(TARGET)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1].get_header('Authorization'), 'Bearer secret-token')
        self.assertNotIn('secret', calls[1].full_url)
        self.assertIn('namespace=profile-us', calls[1].full_url)

    def test_payload_size_is_bounded(self):
        with self.assertRaises(ValueError):
            read_json(io.BytesIO(b' ' * 11), maximum=10)

    def test_migrates_pilot_database(self):
        import sqlite3
        path = str(Path(self.temp.name) / 'pilot.db')
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE snapshots (target TEXT PRIMARY KEY, fetched REAL, modified TEXT, members TEXT)')
            db.execute('CREATE TABLE attempts (target TEXT PRIMARY KEY, attempted REAL, error TEXT)')
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?)', (KEY, time.time(), None, '[]'))
        db.close()
        migrated = Store(path)
        self.assertIsNone(migrated.census()['guilds'][0]['source_modified_at'])
        migrated.save(KEY, roster(1))
        self.assertEqual(migrated.census()['guilds'][0]['characters'], 1)


if __name__ == '__main__':
    unittest.main()
