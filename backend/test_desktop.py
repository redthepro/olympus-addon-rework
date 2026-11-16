import contextlib
import io
import os
import unittest
from unittest.mock import patch

import desktop
import service


class DesktopTests(unittest.TestCase):
    def test_browser_gets_no_credentials_and_failure_does_not_stop_service(self):
        for result in (True, False, OSError('synthetic-secret')):
            with self.subTest(result=type(result).__name__):
                def browser(url, new):
                    self.assertEqual(url, 'http://127.0.0.1:8787')
                    self.assertEqual(new, 2)
                    self.assertNotIn('BNET_CLIENT_SECRET', os.environ)
                    self.assertNotIn('BNET_CLIENT_ID', os.environ)
                    if isinstance(result, Exception):
                        raise result
                    return result
                out = io.StringIO()
                with patch.dict(os.environ, BNET_CLIENT_ID='synthetic-client', BNET_CLIENT_SECRET='synthetic-secret'), \
                        patch('desktop.webbrowser.open', side_effect=browser), \
                        patch('desktop.webbrowser.get') as controller, contextlib.redirect_stdout(out):
                    controller.return_value.open.side_effect = browser
                    desktop.open_dashboard(8787)
                    self.assertEqual(os.environ['BNET_CLIENT_SECRET'], 'synthetic-secret')
                self.assertNotIn('synthetic-secret', out.getvalue())
                self.assertEqual('http://127.0.0.1:8787' in out.getvalue(), result is not True)

    def test_failed_bind_never_opens_browser_or_starts_collector(self):
        with patch('service.load_config'), patch('service.Store'), patch('service.Poller') as poller, \
                patch('webapp.make_server', side_effect=OSError('occupied')):
            seen = []
            with self.assertRaises(OSError):
                service.main(on_ready=seen.append)
            self.assertEqual(seen, [])
            poller.return_value.start.assert_not_called()

    def test_service_cleanup_if_ready_callback_fails(self):
        with patch('service.load_config'), patch('service.Store'), patch('service.Poller') as poller, \
                patch('webapp.make_server') as server:
            def failed(port):
                raise RuntimeError('ready callback')
            with self.assertRaises(RuntimeError):
                service.main(on_ready=failed)
            poller.return_value.stop.assert_called_once()
            server.return_value.server_close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
