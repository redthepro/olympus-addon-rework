import json
import os
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

from service import Config, Poller, load_config, main


TARGET = ('us', 'profile-us', 'realm', 'guild')


class ConfigTests(unittest.TestCase):
    def test_empty_setup_is_disabled_without_network_credentials(self):
        config = load_config({})
        self.assertFalse(config.sync_configured)
        self.assertFalse(config.credentials_configured)
        self.assertEqual(config.targets, ())

    def test_valid_configuration_does_not_retain_secrets(self):
        config = load_config({'CENSUS_TARGETS': json.dumps([TARGET]),
                              'BNET_CLIENT_ID': 'private-id', 'BNET_CLIENT_SECRET': 'private-secret'})
        self.assertTrue(config.sync_configured)
        self.assertNotIn('private', repr(config))

    def test_invalid_targets_fail_before_startup(self):
        bad = ['null', '{}', '"text"', 'bad json', json.dumps([TARGET, TARGET]),
               json.dumps([TARGET] * 257), json.dumps([['us', 'dynamic-us', 'realm', 'guild']])]
        for targets in bad:
            with self.subTest(targets=targets[:80]), self.assertRaises(ValueError):
                load_config({'CENSUS_TARGETS': targets})

    def test_interval_and_port_are_bounded(self):
        for key, values in {'CENSUS_INTERVAL': ['0', '299', '86401', 'nan'],
                            'CENSUS_PORT': ['0', '65536', 'nan']}.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    load_config({key: value})

    def test_cloud_database_configuration_is_not_used(self):
        config = load_config({'DATABASE_URL': 'postgresql://private/remote',
                              'RDS_IAM_HOST': 'private.example'})
        self.assertEqual(config.database_path, 'census.sqlite3')
        with self.assertRaises(ValueError):
            load_config({'CENSUS_DB': 'postgresql://private/remote'})


class PollerTests(unittest.TestCase):
    def configured(self):
        return Config('unused.db', (TARGET,), 900, 8787, True)

    def test_no_config_no_api_calls(self):
        poller = Poller(Mock(), load_config({}), client=Mock())
        with patch('service.sync_once') as sync:
            self.assertEqual(poller.cycle(), 900)
            sync.assert_not_called()
        self.assertEqual(poller.status()['worker_state'], 'disabled')

    def test_worker_recovers_from_database_failure(self):
        poller = Poller(Mock(), self.configured(), client=Mock())
        with patch('service.sync_once', side_effect=[sqlite3.OperationalError('SECRET path'), 0]):
            self.assertEqual(poller.cycle(), 900)
            status = poller.status()
            self.assertEqual(status['last_error'], 'OperationalError')
            self.assertEqual(status['worker_state'], 'error')
            self.assertNotIn('SECRET', json.dumps(status))
            self.assertEqual(poller.cycle(), 900)
            self.assertIsNone(poller.status()['last_error'])
            self.assertEqual(poller.status()['worker_state'], 'waiting')

    def test_retry_after_delay_is_respected(self):
        poller = Poller(Mock(), self.configured(), client=Mock())
        with patch('service.sync_once', return_value=3600):
            self.assertEqual(poller.cycle(), 3600)
        status = poller.status()
        self.assertEqual(status['next_poll_at'] - status['last_cycle_at'], 3600)

    def test_invalid_retry_cannot_kill_worker(self):
        poller = Poller(Mock(), self.configured(), client=Mock())
        with patch('service.sync_once', return_value=float('inf')):
            self.assertEqual(poller.cycle(), 900)
        self.assertEqual(poller.status()['last_error'], 'ValueError')

    def test_thread_survives_database_failure_and_stops_promptly(self):
        called = threading.Event()
        def broken(*args):
            called.set()
            raise sqlite3.OperationalError('SECRET path')
        poller = Poller(Mock(), self.configured(), client=Mock())
        with patch('service.sync_once', side_effect=broken):
            poller.start()
            try:
                self.assertTrue(called.wait(2))
                self.assertTrue(poller.thread.is_alive())
            finally:
                poller.stop()
        self.assertFalse(poller.thread.is_alive())
        self.assertEqual(poller.status()['worker_state'], 'stopped')


class MainTests(unittest.TestCase):
    def test_ready_callback_clears_credentials_before_collector_starts(self):
        config = load_config({'CENSUS_TARGETS': json.dumps([TARGET]),
                              'BNET_CLIENT_ID': 'synthetic-id', 'BNET_CLIENT_SECRET': 'synthetic-secret'})
        poller = Mock()
        observed = []
        def ready(port):
            saved = os.environ.pop('BNET_CLIENT_SECRET')
            try:
                observed.append((port, poller.start.called, 'BNET_CLIENT_SECRET' in os.environ))
            finally:
                os.environ['BNET_CLIENT_SECRET'] = saved
        with patch.dict(os.environ, BNET_CLIENT_SECRET='synthetic-secret'), \
             patch('service.load_config', return_value=config), patch('service.Store'), \
             patch('service.Poller', return_value=poller), patch('webapp.make_server'):
            main(on_ready=ready)
        self.assertEqual(observed, [(8787, False, False)])
        poller.start.assert_called_once()

    def test_main_binds_local_dashboard_then_starts_worker(self):
        config = load_config({})
        server = Mock()
        store = Mock()
        poller = Mock()
        with patch('service.load_config', return_value=config), patch('service.Store', return_value=store) as create_store, \
             patch('service.Poller', return_value=poller), patch('webapp.make_server', return_value=server) as make:
            main()
        make.assert_called_once_with(('127.0.0.1', 8787), store, poller.status)
        create_store.assert_called_once_with(config.database_path, targets=config.targets)
        poller.start.assert_called_once()
        server.serve_forever.assert_called_once()
        poller.stop.assert_called_once()
        server.server_close.assert_called_once()

    def test_port_in_use_does_not_start_background_worker(self):
        poller = Mock()
        with patch('service.load_config', return_value=load_config({})), patch('service.Store'), \
             patch('service.Poller', return_value=poller), patch('webapp.make_server', side_effect=OSError()):
            with self.assertRaises(OSError):
                main()
        poller.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
