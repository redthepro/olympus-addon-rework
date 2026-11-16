import http.client
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from service import Store
from webapp import export_snapshot, make_server, snapshot_revision


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'test.db'))
        self.server = make_server(('127.0.0.1', 0), self.store,
                                  lambda: {'status': 'ok', 'sync_configured': False,
                                           'credentials_configured': False, 'target_count': 0})
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read().decode()
        finally:
            connection.close()

    def test_local_census_and_unconfigured_status(self):
        code, headers, body = self.request('GET', '/v1/census')
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data['guilds'], [])
        self.assertFalse(data['sync_configured'])
        self.assertFalse(data['credentials_configured'])
        self.assertEqual(data['target_count'], 0)
        self.assertEqual(dict(headers)['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', dict(headers))
        self.assertRegex(data['snapshot_revision'], r'^[a-f0-9]{64}$')

    def test_database_snapshot_round_trip(self):
        # Exercise Store -> HTTP -> addon-export without contacting Blizzard.
        with self.store.connect() as db:
            db.execute('INSERT INTO snapshots (target,fetched,modified,members) VALUES (?,?,?,?)',
                       ('us/profile-us/test-realm/olympus', time.time(), None,
                        json.dumps([{'id': 1, 'realm_id': 2, 'name': 'PrivateName'}])))
        code, _, body = self.request('GET', '/v1/census')
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data['guilds'][0]['characters'], 1)
        self.assertNotIn('PrivateName', body)
        code, headers, body = self.request('GET', '/v1/census/export')
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith('OLYC1;us;profile-us;test-realm;olympus;'))
        self.assertTrue(body.endswith(';0;1'))
        self.assertEqual(dict(headers)['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(dict(headers)['X-Olympus-Snapshot-Revision'], data['snapshot_revision'])
        self.assertEqual(int(dict(headers)['X-Olympus-Expires-At']), int(data['guilds'][0]['fetched_at']) + 86400)

    def test_old_auth_endpoints_removed_and_writes_refused(self):
        for path in ('/auth/login', '/auth/session', '/auth/callback?code=SECRET'):
            code, headers, body = self.request('GET', path)
            self.assertEqual(code, 404)
            self.assertNotIn('SECRET', body)
            self.assertNotIn('Location', dict(headers))
        code, headers, _ = self.request('POST', '/credentials')
        self.assertEqual(code, 405)
        self.assertEqual(dict(headers)['Allow'], 'GET')

    def test_experimental_forever_snapshot_round_trip(self):
        # Synthetic shape from Pinta365/blizzard_api's Forever types; not a live capture.
        href = 'https://us.api.blizzard.com/data/wow/guild/olympus/roster?namespace=profile-forever-us'
        self.store.save('us/profile-forever-us/global/olympus', {
            '_links': {'self': {'href': href}},
            'guild': {'id': 9, 'name': 'Olympus', 'slug': 'olympus', 'faction': {'type': 'ALLIANCE'}},
            'members': [{'character': {'id': 11, 'name': 'Private', 'surname': 'Example',
                                      'slug': 'private-example', 'level': 5, 'playable_class': {'id': 1}}, 'rank': 0}]})
        code, _, body = self.request('GET', '/v1/census')
        self.assertEqual(code, 200)
        row = json.loads(body)['guilds'][0]
        self.assertEqual(row['characters'], 1)
        self.assertEqual(row['scope_kind'], 'global')
        self.assertTrue(row['experimental'])
        self.assertNotIn('Private', body)
        code, _, body = self.request('GET', '/v1/census/export')
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith('OLYC1;us;profile-forever-us;global;olympus;'))
        self.assertTrue(body.endswith(';0;1'))

    def test_host_rejection_and_no_public_bind(self):
        for method in ('GET', 'POST'):
            self.assertEqual(self.request(method, '/v1/census', {'Host': 'attacker.example'})[0], 403)
        with self.assertRaises(ValueError):
            make_server(('0.0.0.0', 0), self.store, lambda: {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.putrequest('GET', '/healthz')
            connection.putheader('Host', 'attacker.example')
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

    def test_second_server_cannot_bind_active_companion_port(self):
        with self.assertRaises(OSError):
            duplicate = make_server(self.server.server_address, self.store, lambda: {})
            # Close an unexpectedly successful bind even when the assertion fails.
            self.addCleanup(duplicate.server_close)

    def test_normal_forever_snapshot_requires_api_evidence(self):
        # Synthetic response, deliberately nonstandard numeric ruleset id: match its name.
        href = 'https://us.api.blizzard.com/data/wow/guild/olympus/roster?namespace=profile-forever-us'
        self.store.save('us/profile-forever-us/normal/olympus', {
            '_links': {'self': {'href': href}},
            'guild': {'id': 9, 'name': 'Olympus', 'slug': 'olympus', 'faction': {'type': 'ALLIANCE'}},
            'members': [{'character': {'id': 11, 'name': 'Private', 'surname': 'Example',
                                      'slug': 'private-example', 'level': 5, 'playable_class': {'id': 1}}, 'rank': 0}],
            '_olympus_ruleset': {'guild_id': 9, 'guild_slug': 'olympus', 'id': 123, 'name': 'Normal'}})
        code, _, body = self.request('GET', '/v1/census')
        self.assertEqual(code, 200)
        row = json.loads(body)['guilds'][0]
        self.assertEqual(row['scope_kind'], 'ruleset')
        self.assertEqual(row['ruleset'], {'id': 123, 'name': 'Normal'})
        code, _, body = self.request('GET', '/v1/census/export')
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith('OLYC2;us;profile-forever-us;normal;olympus;'))
        self.assertTrue(body.endswith(';0;1;A'))

    def test_export_failure_and_internal_error_do_not_leak_details(self):
        row = {'target': 'us/profile-us/bad;realm/olympus', 'fetched_at': time.time(),
               'source_modified_at': 0, 'characters': 12}
        with patch.object(self.store, 'census', return_value={'guilds': [row]}):
            self.assertEqual(self.request('GET', '/v1/census/export')[0], 400)
        with patch.object(self.store, 'census', side_effect=RuntimeError('SECRET-CREDENTIAL')):
            code, _, body = self.request('GET', '/v1/census')
            self.assertEqual(code, 503)
            self.assertNotIn('SECRET-CREDENTIAL', body)

    def test_local_dashboard_and_static_headers(self):
        code, headers, body = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn('Olympus API census', body)
        self.assertNotIn('Sign in', body)
        self.assertNotIn('/auth/', body)
        self.assertIn("frame-ancestors 'none'", dict(headers)['Content-Security-Policy'])
        self.assertEqual(dict(headers)['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.request('GET', '/../../service.py')[0], 404)
        for path in ('/app.js', '/style.css'):
            self.assertEqual(self.request('GET', path)[0], 200)


class ExportTests(unittest.TestCase):
    def row(self, **changes):
        row = {'target': 'us/profile-us/test-realm/olympus', 'fetched_at': 1000000,
               'source_modified_at': 0, 'characters': 10}
        row.update(changes)
        return row

    def test_stale_fetch_is_excluded(self):
        self.assertEqual(export_snapshot({'guilds': [self.row()]}, now=1086401), '')

    def test_revision_tracks_baseline_changes_not_age_flags(self):
        row = self.row()
        revision = snapshot_revision({'guilds': [row]})
        row.update(fetch_stale=True, source_stale=True)
        self.assertEqual(snapshot_revision({'guilds': [row]}), revision)
        row['characters'] += 1
        self.assertNotEqual(snapshot_revision({'guilds': [row]}), revision)


    def test_duplicate_and_invalid_timestamps(self):
        with self.assertRaises(ValueError):
            export_snapshot({'guilds': [self.row(), self.row()]}, now=1000000)
        for changes in [{'source_modified_at': -1}, {'source_modified_at': 1000061},
                        {'fetched_at': float('nan')}, {'fetched_at': True}, {'characters': True},
                        {'target': 'xx/profile-xx/realm/guild'}, {'target': 'us/profile-eu/realm/guild'},
                        {'target': 'us/profile-us/realm--bad/guild'}, {'target': 'us/profile-us/realm/guild-'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                export_snapshot({'guilds': [self.row(**changes)]}, now=1000000)

    def test_row_and_byte_limits(self):
        rows = [self.row(target='us/profile-us/test-realm/guild-' + str(n)) for n in range(257)]
        with self.assertRaises(ValueError):
            export_snapshot({'guilds': rows}, now=1000000)
        rows = [self.row(target='us/profile-' + 'x' * 53 + '-us/' + 'r' * 80 + '/' + 'g' * 90 + str(n))
                for n in range(256)]
        with self.assertRaises(ValueError):
            export_snapshot({'guilds': rows}, now=1000000)

    def test_forever_scope_and_normal_ruleset_evidence(self):
        with self.assertRaises(ValueError):
            export_snapshot({'guilds': [self.row(target='us/profile-forever-us/realm/olympus')]}, now=1000000)
        result = export_snapshot({'guilds': [self.row(target='us/profile-forever-us/global/olympus')]}, now=1000000)
        self.assertEqual(result, 'OLYC1;us;profile-forever-us;global;olympus;1000000;0;10')
        normal = self.row(target='us/profile-forever-us/normal/olympus')
        with self.assertRaises(ValueError):
            export_snapshot({'guilds': [normal]}, now=1000000)
        normal.update(scope_kind='ruleset', ruleset={'id': 123, 'name': 'Normal'})
        self.assertIn(';normal;olympus;', export_snapshot({'guilds': [normal]}, now=1000000))
        normal['ruleset']['name'] = 'PvP'
        with self.assertRaises(ValueError):
            export_snapshot({'guilds': [normal]}, now=1000000)

    def test_faction_scoped_forever_export(self):
        row = self.row(target='us/profile-forever-us/normal/olympus')
        row.update(scope_kind='ruleset', ruleset={'id': 2, 'name': 'Normal'}, faction='ALLIANCE')
        text = export_snapshot({'guilds':[row]}, now=1000000)
        self.assertTrue(text.startswith('OLYC2;')); self.assertTrue(text.endswith(';A'))
        row.update(target='us/profile-forever-us/pvp/olympus', ruleset={'id':1,'name':'PvP'}, faction='HORDE')
        text = export_snapshot({'guilds':[row]}, now=1000000)
        self.assertIn(';pvp;',text); self.assertTrue(text.endswith(';H'))



class DashboardTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed only for dashboard async regression tests')
    def test_shipped_dashboard_async_behaviour(self):
        result = subprocess.run([shutil.which('node'), '--test', str(Path(__file__).with_name('test_dashboard.js'))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
