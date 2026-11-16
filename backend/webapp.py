"""Read-only local companion interface; never bind to a public address."""
import json
import hashlib
import math
import os
import re
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).with_name('static')


class CompanionServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR allows a second listener to steal an active port.
    # POSIX can still reuse a closed listener while its connections drain.
    allow_reuse_address = os.name != 'nt'
    allow_reuse_port = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def snapshot_revision(census):
    """Change marker for prepared exports, not an authenticity signature.

    Exclude derived ages and polling attempts: they change without changing the
    imported baseline. Include scope evidence so a ruleset correction invalidates it.
    """
    fields = ('target', 'fetched_at', 'source_modified_at', 'characters',
              'scope_kind', 'ruleset', 'experimental')
    rows = [{key: row.get(key) for key in fields} for row in census['guilds']]
    rows.sort(key=lambda row: row['target'])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def export_snapshot(census, now=None):
    now = time.time() if now is None else now
    lines = []
    seen = set()
    for row in census['guilds']:
        parts = row['target'].split('/')
        if (len(parts) != 4 or parts[0] not in ('us', 'eu', 'kr', 'tw')
                or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]*', p)
                       or '--' in p or p.endswith('-') or len(p) > limit
                       for p, limit in zip(parts, (2, 64, 80, 96)))
                or not parts[1].startswith('profile-') or not parts[1].endswith('-' + parts[0])):
            raise ValueError('Target cannot be represented in addon snapshot')
        if parts[1] == 'profile-forever-' + parts[0]:
            if parts[2] not in ('normal', 'pvp', 'roleplay', 'roleplay-pvp', 'global'):
                raise ValueError('Forever snapshots require explicit Normal or global scope')
            if parts[2] != 'global':
                ruleset = row.get('ruleset')
                if (row.get('scope_kind') != 'ruleset' or not isinstance(ruleset, dict)
                        or type(ruleset.get('id')) is not int or not 0 < ruleset['id'] < 2 ** 63
                        or not isinstance(ruleset.get('name'), str) or ruleset['name'].casefold() != {'normal': 'normal', 'pvp': 'pvp', 'roleplay': 'roleplay', 'roleplay-pvp': 'roleplay pvp'}[parts[2]]):
                    raise ValueError('Normal export requires matching API ruleset evidence')
        if row['target'] in seen:
            raise ValueError('Duplicate target')
        seen.add(row['target'])
        fetched = row['fetched_at']
        modified = row.get('source_modified_at')
        modified = 0 if modified is None else modified
        if (any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= 9999999999
                for t in (fetched, modified)) or fetched < 1 or modified > fetched + 60):
            raise ValueError('Invalid snapshot timestamp')
        fetched, modified = int(fetched), int(modified)
        if now - fetched > 86400 or fetched > now + 60:
            continue
        count = row['characters']
        if type(count) is not int or not 0 <= count <= 1000:
            raise ValueError('Roster exceeds addon snapshot contract')
        faction = {'ALLIANCE': 'A', 'HORDE': 'H'}.get(row.get('faction'))
        scoped = parts[1] == 'profile-forever-' + parts[0] and parts[2] != 'global' and faction
        fields = ['OLYC2' if scoped else 'OLYC1', *parts, str(fetched), str(modified), str(count)]
        if scoped:
            fields.append(faction)
        lines.append(';'.join(fields))
    result = '\n'.join(lines)
    if len(lines) > 256 or len(result.encode()) > 49152:
        raise ValueError('Snapshot exceeds addon limits')
    return result


def make_server(address, store, status):
    if address[0] not in ('127.0.0.1', 'localhost'):
        raise ValueError('The companion must listen on IPv4 loopback')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Never log request text or accidentally pasted credentials.
            pass

        def reply(self, code, body, mime='application/json', headers=()):
            if mime == 'application/json':
                body = json.dumps(body)
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            port = self.server.server_port
            hosts = {f'localhost:{port}', f'127.0.0.1:{port}'}
            return len(self.headers.get_all('Host', [])) == 1 and self.headers.get('Host') in hosts

        def do_GET(self):
            if not self.valid_host():
                self.reply(403, {'error': 'Unexpected host'})
                return
            try:
                parsed = urlsplit(self.path)
                path = parsed.path
                if path == '/healthz':
                    self.reply(200, status())
                elif path in ('/', '/app.js', '/style.css'):
                    file, mime = {'/': ('index.html', 'text/html; charset=utf-8'),
                                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                                  '/style.css': ('style.css', 'text/css; charset=utf-8')}[path]
                    self.reply(200, (STATIC / file).read_bytes(), mime)
                elif path in ('/v1/census', '/v1/census/export'):
                    census = store.census()
                    census.update(status())
                    revision = snapshot_revision(census)
                    if path.endswith('/export'):
                        exported = export_snapshot(census)
                        expires = min((int(line.split(';')[5]) + 86400 for line in exported.splitlines()), default=0)
                        self.reply(200, exported, 'text/plain; charset=utf-8',
                                   [('X-Olympus-Snapshot-Revision', revision),
                                    ('X-Olympus-Expires-At', str(expires))])
                    else:
                        census['snapshot_revision'] = revision
                        self.reply(200, census)
                else:
                    self.reply(404, {'error': 'Not found'})
            except ValueError:
                self.reply(400, {'error': 'Request rejected; check snapshot configuration'})
            except Exception:
                self.reply(503, {'error': 'Service temporarily unavailable'})

        def do_POST(self):
            if not self.valid_host():
                self.reply(403, {'error': 'Unexpected host'})
                return
            # This local UI has no mutation or credential-submission endpoints.
            self.close_connection = True
            self.reply(405, {'error': 'Read-only companion'}, headers=[('Allow', 'GET')])

    return CompanionServer(address, Handler)
