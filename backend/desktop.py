"""Desktop entry point: open the dashboard only after its listener is ready."""
import argparse
import os
import webbrowser

from service import main as serve


def open_dashboard(port):
    url = f'http://127.0.0.1:{port}'
    # This runs after binding, before starting the collector thread. Browser
    # subprocesses must not inherit credentials, including inherited ones.
    names = ('BNET_CLIENT_ID', 'BNET_CLIENT_SECRET')
    saved = {name: os.environ.pop(name, None) for name in names}
    opened = False
    try:
        # A custom BROWSER command may wait for its process to exit. This Windows
        # companion uses the OS default URL handler, which returns immediately.
        browser = webbrowser.get('windows-default') if os.name == 'nt' else webbrowser
        opened = browser.open(url, new=2)
    except Exception:
        # A missing/default browser must never stop collection or expose an
        # exception containing environment details or command arguments.
        pass
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
    if not opened:
        print(f'Open {url} in your browser to view the census.', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Olympus local census companion')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        serve(on_ready=None if args.no_browser else open_dashboard)
    except KeyboardInterrupt:
        pass
    except OSError as error:
        if getattr(error, 'winerror', None) == 10048 or error.errno in (48, 98, 10048):
            raise SystemExit('The local port is already in use. Close the other companion window or change the port in settings.json.') from None
        raise SystemExit('The companion could not start. Check that its settings folder and local port are available.') from None


if __name__ == '__main__':
    main()
