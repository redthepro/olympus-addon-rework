"""Read-only public census, separate from the private local companion dashboard."""
import atexit
import html
import json
import time

from service import DOCUMENTED_PROFILES, Poller, Store, load_config
from webapp import export_snapshot


def public_snapshot(store, config):
    census = store.census()
    # Never publish attempts, member rows, credential state or internal diagnostics.
    rows = [row for row in census['guilds']
            if row['target'].split('/')[1] in DOCUMENTED_PROFILES
            and 0 <= time.time() - row['fetched_at'] <= 86400]
    text = export_snapshot({'guilds': rows})
    blocked = any(target[1] not in DOCUMENTED_PROFILES for target in config.targets)
    status = 'available' if text else ('awaiting_official_api' if blocked else 'unavailable')
    message = ('Forever API collection is disabled pending official Blizzard documentation. Use the live addon census.'
               if blocked else 'No current API snapshot is available. Use the live addon census.')
    if text:
        message = 'API membership snapshot available. Online presence remains live in the addon.'
    return {'status': status, 'message': message, 'guild_count': len(rows),
            'source': 'Blizzard API', 'generated_at': int(time.time())}, text


def application(store, config, worker_status):
    def app(environ, start_response):
        path = environ.get('PATH_INFO', '')
        method = environ.get('REQUEST_METHOD', 'GET')
        code, mime, body = '200 OK', 'application/json; charset=utf-8', ''
        if method not in ('GET', 'HEAD'):
            code, body = '405 Method Not Allowed', '{"error":"read_only"}'
        elif path == '/census/healthz':
            alive = worker_status().get('worker_alive', False)
            code = '200 OK' if alive else '503 Service Unavailable'
            body = json.dumps({'service': 'olympus-census', 'healthy': alive})
        elif environ.get('HTTP_HOST', '').lower() not in ('olympus-v.org', 'olympus-v.org:443'):
            code, body = '403 Forbidden', '{"error":"unexpected_host"}'
        elif path not in ('/census', '/census/', '/census/status.json', '/census/snapshot.txt'):
            code, body = '404 Not Found', '{"error":"not_found"}'
        else:
            try:
                status, snapshot = public_snapshot(store, config)
                if path.endswith('/status.json'):
                    body = json.dumps(status)
                elif path.endswith('/snapshot.txt'):
                    mime = 'text/plain; charset=utf-8'
                    code = '200 OK' if snapshot else '503 Service Unavailable'
                    body = snapshot if snapshot else status['message']
                else:
                    mime = 'text/html; charset=utf-8'
                    body = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Olympus census</title>
<main><h1>Olympus guild census</h1><p>''' + html.escape(status['message']) + '''</p>
<p>No personal API credentials or companion installation are needed for this shared page.</p>
<h2>Import into Olympus</h2><p>When a snapshot is available, select and copy the text below.
In WoW, open Census → API census → Paste snapshot, then review and import.</p>
<textarea readonly rows="12" cols="80" aria-label="Census snapshot">''' + html.escape(snapshot) + '''</textarea>
<p><a href="/census/snapshot.txt">Plain-text snapshot</a> · <a href="/census/status.json">Service status</a> · <a href="/">Guild website</a></p>
<h2>Privacy and data</h2><p>Only aggregate guild counts and source dates are published.
No member roster or developer credentials are served. Import is manual; the addon
does not contact this website. API caches expire after 29 days without refresh;
do not retain copied snapshots or backups beyond 30 days. Use /oly censusapi clear to remove an import.</p>
<p>This free community project is not endorsed by Blizzard. Blizzard controls API availability.
<a href="https://www.blizzard.com/en-us/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use">API terms</a></p></main></html>'''
            except Exception:
                # Do not turn database/configuration exceptions into public diagnostics.
                code, body = '503 Service Unavailable', '{"error":"census_unavailable"}'
        data = body.encode('utf-8')
        headers = [('Content-Type', mime), ('Content-Length', str(len(data))),
                   ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                   ('Referrer-Policy', 'no-referrer'),
                   ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")]
        if code.startswith('405'):
            headers.append(('Allow', 'GET, HEAD'))
        start_response(code, headers)
        return [b'' if method == 'HEAD' else data]
    return app


def create_app():
    # Run exactly one Gunicorn worker: it owns one background collector, while
    # bounded HTTP threads only read snapshots and never trigger API requests.
    config = load_config()
    store = Store(config.database_path, targets=config.targets)
    poller = Poller(store, config)
    poller.start()
    atexit.register(poller.stop)
    return application(store, config, poller.status)
