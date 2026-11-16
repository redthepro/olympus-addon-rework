import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hosted import application
from service import Config, Store
from test_service import KEY, TARGET, roster, forever_roster


class HostedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(str(Path(temp.name) / 'db.sqlite3'))
        self.config = Config(self.store.path, (tuple(TARGET),), 900, 8788, True)
        self.app = application(self.store, self.config, lambda: {'worker_alive': True})

    def request(self, path, method='GET', host='olympus-v.org'):
        result = {}
        def start(status, headers):
            result.update(status=status, headers=dict(headers))
        result['body'] = b''.join(self.app({'PATH_INFO': path, 'REQUEST_METHOD': method,
                                          'HTTP_HOST': host}, start)).decode()
        return result

    def test_public_output_never_exposes_roster_or_attempt_diagnostics(self):
        self.store.save(KEY, roster(123456789))
        self.store.failure(KEY, 'secret-diagnostic')
        for path in ('/census', '/census/status.json', '/census/snapshot.txt'):
            result = self.request(path)
            self.assertEqual(result['status'], '200 OK')
            self.assertNotIn('123456789', result['body'])
            self.assertNotIn('secret-diagnostic', result['body'])
            self.assertEqual(result['headers']['Cache-Control'], 'no-store')

    def test_disabled_forever_never_exports_old_cached_data(self):
        target = ('us', 'profile-forever-us', 'global', 'olympus-v')
        self.store.save('us/profile-forever-us/global/olympus', forever_roster(123))
        self.app = application(self.store, Config(self.store.path, (target,), 900, 8788, True),
                               lambda: {'worker_alive': True})
        result = self.request('/census/status.json')
        self.assertEqual(json.loads(result['body'])['status'], 'awaiting_official_api')
        self.assertEqual(self.request('/census/snapshot.txt')['status'], '503 Service Unavailable')

    def test_routes_methods_hosts_and_health(self):
        self.assertEqual(self.request('/v1/census')['status'], '404 Not Found')
        self.assertEqual(self.request('/census', 'POST')['status'], '405 Method Not Allowed')
        self.assertEqual(self.request('/census', host='evil.example')['status'], '403 Forbidden')
        self.assertEqual(self.request('/census/healthz', host='172.31.0.1:8788')['status'], '200 OK')
        self.assertEqual(self.request('/census', 'HEAD')['body'], '')

    def test_storage_outage_hides_exception_details(self):
        with patch.object(self.store, 'census', side_effect=RuntimeError('private path secret')):
            result = self.request('/census/status.json')
        self.assertEqual(result['status'], '503 Service Unavailable')
        self.assertNotIn('secret', result['body'])
