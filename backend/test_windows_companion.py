"""Exercise Windows setup encryption and launcher using isolated fake settings."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows launcher only')
class WindowsCompanionTests(unittest.TestCase):
    def test_setup_encrypts_and_launches_with_nested_targets(self):
        root = Path(__file__).resolve().parent.parent
        # The .cmd launchers clear inherited PowerShell 7 module paths before
        # invoking Windows PowerShell 5.1, otherwise Security can fail to load.
        launch_env = dict(os.environ)
        launch_env.pop('PSModulePath', None)
        launch_env.pop('PSMODULEPATH', None)
        with tempfile.TemporaryDirectory(prefix='olympus-companion-test-') as directory:
            setup = str(root / 'companion' / 'Setup-Companion.ps1').replace("'", "''")
            state = directory.replace("'", "''")
            command = ("$secret=ConvertTo-SecureString 'synthetic-test-secret' -AsPlainText -Force; "
                       f"& '{setup}' -ClientId 'synthetic-client' -ClientSecret $secret "
                       f"-Region us -Namespace profile-us -Realm test-realm -Guild olympus -StateDirectory '{state}'")
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
                                    capture_output=True, text=True, timeout=30, env=launch_env)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = Path(directory) / 'settings.json'
            content = path.read_text(encoding='utf-8-sig')
            self.assertNotIn('synthetic-test-secret', content)
            config = json.loads(content)
            self.assertEqual(config['targets'], [['us', 'profile-us', 'test-realm', 'olympus']])
            self.assertTrue(config['protectedSecret'])
            forever_command = command.replace('-Namespace profile-us -Realm test-realm', '-Namespace profile-forever-us')
            forever = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                      '-Command', forever_command], capture_output=True, text=True, timeout=30, env=launch_env)
            self.assertEqual(forever.returncode, 0, forever.stderr)
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            self.assertEqual(config['targets'], [['us', 'profile-forever-us', 'normal', 'olympus']])
            # No targets means no external Blizzard requests during this test.
            config['targets'] = []
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                config['port'] = sock.getsockname()[1]
            path.write_text(json.dumps(config), encoding='utf-8')
            log_path = Path(directory) / 'launcher.log'
            browser_probe = Path(directory) / 'browser-env.txt'
            # Instrument Python's real browser boundary as well as the old
            # PowerShell boundary. The service itself remains unmodified.
            (Path(directory) / 'sitecustomize.py').write_text('''
import json, os, socket, types, urllib.parse, webbrowser
from pathlib import Path
def browser_probe(url, **kwargs):
    with socket.create_connection(('127.0.0.1', urllib.parse.urlparse(url).port), timeout=1):
        ready = True
    Path(os.environ['OLY_TEST_BROWSER_PROBE']).write_text(json.dumps({
        'secret': os.environ.get('BNET_CLIENT_SECRET', ''), 'ready': ready}))
    return True
webbrowser.open = browser_probe
webbrowser.get = lambda name: types.SimpleNamespace(open=browser_probe)
''', encoding='utf-8')
            launch_env['PYTHONPATH'] = directory
            launch_env['OLY_TEST_BROWSER_PROBE'] = str(browser_probe)
            launcher = str(root / 'companion' / 'Start-Companion.ps1').replace("'", "''")
            probe = str(browser_probe).replace("'", "''")
            # Substitute only browser opening: still execute the actual launcher,
            # decryption, environment handoff and Python service.
            launch_command = ("function global:Start-Process { param($FilePath) "
                              "$ready=$false; $tcp=New-Object Net.Sockets.TcpClient; "
                              f"try {{ $tcp.Connect('127.0.0.1', {config['port']}); $ready=$true }} catch {{}} finally {{ $tcp.Dispose() }}; "
                              "$data=@{secret=[string]$env:BNET_CLIENT_SECRET;ready=$ready}|ConvertTo-Json; "
                              f"[IO.File]::WriteAllText('{probe}', $data) }}; "
                              f"& '{launcher}' -StateDirectory '{state}'")
            launch_env['BNET_CLIENT_SECRET'] = 'inherited-test-secret'
            with log_path.open('w', encoding='utf-8') as log:
                process = subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-Command', launch_command], stdout=log, stderr=log, env=launch_env)
                try:
                    deadline = time.monotonic() + 25
                    while True:
                        try:
                            with urllib.request.urlopen(f"http://127.0.0.1:{config['port']}/healthz", timeout=1) as response:
                                health = json.load(response)
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail(log_path.read_text(encoding='utf-8'))
                            time.sleep(.2)
                    self.assertFalse(health['sync_configured'])
                    self.assertTrue((Path(directory) / 'census.sqlite3').exists())
                    browser = json.loads(browser_probe.read_text())
                    self.assertEqual(browser['secret'], '')
                    self.assertTrue(browser['ready'], 'Browser opened before companion was listening')
                    # A second launch must fail without opening a duplicate
                    # browser or redirecting the user to an unrelated listener.
                    second_probe = Path(directory) / 'second-browser.txt'
                    second_env = dict(launch_env, OLY_TEST_BROWSER_PROBE=str(second_probe))
                    second_command = launch_command.replace(probe, str(second_probe).replace("'", "''"))
                    second = subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-Command', second_command], stdout=log, stderr=log, env=second_env)
                    try:
                        second.wait(timeout=20)
                    finally:
                        if second.poll() is None:
                            subprocess.run(['taskkill', '/PID', str(second.pid), '/T', '/F'], capture_output=True)
                            second.wait(timeout=10)
                    self.assertNotEqual(second.returncode, 0)
                    self.assertFalse(second_probe.exists(), 'Failed launch opened a browser')
                finally:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                    process.wait(timeout=10)
            self.assertNotIn('synthetic-test-secret', log_path.read_text(encoding='utf-8'))

    def test_setup_normalizes_scope_and_preserves_targets_when_rotating_credentials(self):
        root = Path(__file__).resolve().parent.parent
        env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
        with tempfile.TemporaryDirectory(prefix='olympus-setup-test-') as directory:
            setup = str(root / 'companion' / 'Setup-Companion.ps1').replace("'", "''")
            state = directory.replace("'", "''")
            prefix = ("$secret=ConvertTo-SecureString 'synthetic-test-secret' -AsPlainText -Force; "
                      f"& '{setup}' -ClientId 'synthetic-client' -ClientSecret $secret -StateDirectory '{state}' ")
            def run(arguments):
                return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                    'Bypass', '-Command', prefix + arguments], capture_output=True, text=True, timeout=30, env=env)
            first = run("-Region ' US ' -Namespace profile-forever-us -Realm ' Normal ' -Guild ' Olympus '")
            self.assertEqual(first.returncode, 0, first.stderr)
            path = Path(directory) / 'settings.json'
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            config['targets'].append(['us', 'profile-us', 'test-realm', 'second'])
            config['port'] = 9876
            config['intervalSeconds'] = 1800
            path.write_text(json.dumps(config), encoding='utf-8')
            rotated = run('-CredentialsOnly')
            self.assertEqual(rotated.returncode, 0, rotated.stderr)
            updated = json.loads(path.read_text(encoding='utf-8-sig'))
            for key in ('targets', 'port', 'intervalSeconds'):
                self.assertEqual(updated[key], config[key])


if __name__ == '__main__':
    unittest.main()
