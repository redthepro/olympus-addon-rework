"""Regression coverage for multi-guild polling and retained source chronology."""
from email.utils import formatdate
import io
import sqlite3
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from service import Config, Poller, Store, sync_once
from test_service import roster


FIRST = ('us', 'profile-us', 'realm', 'first')
SECOND = ('us', 'profile-us', 'realm', 'second')


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'census.sqlite3')
        self.store = Store(self.path)

    def test_one_guild_backoff_does_not_delay_healthy_guild(self):
        self.store.failure('/'.join(FIRST), 'HTTPError:503', retry_after=7200)
        calls = []
        class Client:
            def roster(self, target):
                calls.append(target)
                return roster(1), None
        poller = Poller(self.store, Config(self.path, (FIRST, SECOND), 900, 8787, True), Client())
        self.assertEqual(poller.cycle(), 900)
        self.assertEqual(calls, [SECOND])
        self.assertGreater(self.store.retry_delay('/'.join(FIRST)), 7100)

    def test_new_client_honors_global_429_cooldown_from_previous_process(self):
        class Limited:
            def roster(self, target):
                raise urllib.error.HTTPError('https://api.invalid', 429, 'limited', {'Retry-After': '7200'}, None)
        sync_once(self.store, Limited(), [FIRST])
        calls = []
        class FreshClient:
            def roster(self, target):
                calls.append(target)
                return roster(1), None
        # This new Store/client and different target model restart + config edit.
        remaining = sync_once(Store(self.path), FreshClient(), [SECOND])
        self.assertEqual(calls, [])
        self.assertGreater(remaining, 7100)

    def test_expired_global_cooldown_allows_collection_again(self):
        now = 1800000000
        with patch('service.time.time', return_value=now):
            self.store.failure('/'.join(FIRST), 'HTTPError:429', retry_after=7200, global_limit=True)
        calls = []
        class Client:
            def roster(self, target):
                calls.append(target)
                return roster(1), None
        with patch('service.time.time', return_value=now + 7201):
            self.assertEqual(sync_once(Store(self.path), Client(), [SECOND]), 0)
        self.assertEqual(calls, [SECOND])

    def test_http_error_is_closed_even_if_failure_cannot_be_persisted(self):
        body = io.BytesIO(b'error body')
        error = urllib.error.HTTPError('https://api.invalid', 503, 'busy', {}, body)
        class Client:
            def roster(self, target):
                raise error
        with patch.object(self.store, 'failure', side_effect=sqlite3.OperationalError('disk unavailable')):
            with self.assertRaises(sqlite3.OperationalError):
                sync_once(self.store, Client(), [FIRST])
        self.assertTrue(body.closed)

    def test_unchanged_roster_without_last_modified_refreshes_fetch_only(self):
        key = '/'.join(FIRST)
        original = 1800000000
        modified = formatdate(original - 3600, usegmt=True)
        with patch('service.time.time', return_value=original):
            self.store.save(key, roster(1), modified)
        with patch('service.time.time', return_value=original + 900):
            self.store.save(key, roster(1), None)
            row = self.store.census()['guilds'][0]
        self.assertEqual(row['fetched_at'], original + 900)
        self.assertEqual(row['source_modified_at'], original - 3600)
        self.assertEqual(row['source_last_modified'], modified)
        self.assertTrue(row['source_stale'])

    def test_stop_request_does_not_start_another_guild_request(self):
        calls = []
        class Client:
            def roster(self, target):
                calls.append(target)
                poller.stop_event.set()
                return roster(1), None
        poller = Poller(self.store, Config(self.path, (FIRST, SECOND), 900, 8787, True), Client())
        poller.cycle()
        self.assertEqual(calls, [FIRST])


if __name__ == '__main__':
    unittest.main()
