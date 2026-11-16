"""Policy regressions exercise storage and the real outbound request boundary."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from service import Blizzard, Store, sync_once
from test_service import KEY, TARGET, roster


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'census.sqlite3')
        self.store = Store(self.path)

    def test_expired_roster_and_metadata_are_deleted_not_hidden(self):
        self.store.save(KEY, roster(1))
        with patch('service.time.time', return_value=time.time() + 29 * 86400 + 1):
            self.assertEqual(self.store.census()['guilds'], [])
            with self.store.connect() as db:
                for table in ('snapshots', 'snapshot_meta', 'attempts'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_removed_target_is_deleted_on_startup(self):
        self.store.save(KEY, roster(1))
        reopened = Store(self.path, targets=[])
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshot_meta').fetchone()[0], 0)

    def test_gone_resource_deletes_last_good_roster(self):
        import urllib.error
        for code in (403, 404, 410):
            with self.subTest(code=code):
                self.store.save(KEY, roster(1))
                class Gone:
                    def roster(self, target):
                        raise urllib.error.HTTPError('https://example.invalid', code, 'gone', {}, None)
                sync_once(self.store, Gone(), [TARGET])
                self.assertEqual(self.store.census()['guilds'], [])

    def test_outbound_requests_are_spaced(self):
        client = Blizzard(lambda request, timeout: 'response')
        with patch('service.time.monotonic', return_value=100), patch('service.time.sleep') as sleep:
            self.assertEqual(client.opener('one', 30), 'response')
            self.assertEqual(client.opener('two', 30), 'response')
            self.assertAlmostEqual(sleep.call_args.args[0], 0.1)

    def test_unverified_namespace_discards_previous_roster(self):
        from test_service import forever_roster
        target = ['us', 'profile-forever-us', 'global', 'olympus']
        self.store.save('/'.join(target), forever_roster(1))
        sync_once(self.store, Blizzard(), [target])
        self.assertEqual(self.store.census()['guilds'], [])
        self.assertEqual(self.store.census()['attempts'][0]['error'], 'UnsupportedNamespace')

    def test_unverified_namespace_never_reaches_network_or_credentials(self):
        calls = []
        client = Blizzard(lambda *args, **kwargs: calls.append(args))
        with self.assertRaisesRegex(ValueError, 'not enabled'):
            client.roster(['us', 'profile-forever-us', 'normal', 'olympus'])
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
