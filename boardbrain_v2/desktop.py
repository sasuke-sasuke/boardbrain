from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import threading
import urllib.request
import webbrowser

from .server import App, make_server
from .store import Store


def default_data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/BoardBrain"
    return Path.home() / ".local/share/boardbrain"


def main():
    parser = argparse.ArgumentParser(description="BoardBrain local repair workspace")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--browser", action="store_true", help="Use a browser instead of the native Mac window")
    parser.add_argument("--serve", action="store_true", help="Development/test server without a window")
    parser.add_argument("--import-reference", type=Path, help="Import a local reference manifest during setup, then exit")
    args = parser.parse_args()
    os.umask(0o077)
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.data_dir.chmod(0o700)
    lock = (args.data_dir / "instance.lock").open("a+")
    info = args.data_dir / "runtime.json"
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if args.import_reference:
            raise RuntimeError("Quit BoardBrain, then run setup again to add the reference files.") from None
        try:
            existing = json.loads(info.read_text())
            # The private file is written only by this local app. Validate even so.
            port = int(existing["port"])
            if not 1 <= port <= 65535:
                raise ValueError()
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/activate", data=b"{}", headers={"Authorization": "Bearer " + existing["token"], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3):
                return
        except Exception:
            raise RuntimeError("BoardBrain is already running or finishing a request. Wait a moment, then reopen it.") from None
    store = Store(args.data_dir)
    store.recover_interrupted()
    if args.import_reference:
        from .reference import import_reference_bundle
        try:
            case = import_reference_bundle(store, args.import_reference)
            print(json.dumps({"workspace": case["title"], "kind": case["kind"], "files": [{"name": s["name"], "status": s["status"]} for s in case["sources"]]}))
        finally:
            lock.close()
        return
    app = App(store)
    server = make_server(app)
    url = f"http://127.0.0.1:{server.server_port}/#{app.token}"
    temporary = info.with_suffix(".tmp")
    temporary.write_text(json.dumps({"port": server.server_port, "token": app.token, "pid": os.getpid()}))
    temporary.chmod(0o600)
    os.replace(temporary, info)
    stop = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app.on_quit = stop.set
    app.on_activate = lambda: webbrowser.open(url)
    try:
        if args.serve or args.browser or sys.platform != "darwin":
            if not args.serve:
                webbrowser.open(url)
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            stop.wait()
        else:
            import webview
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.settings["ALLOW_FILE_URLS"] = False
            window = webview.create_window("BoardBrain", url, width=1320, height=900, min_size=(850, 620), background_color="#111516")
            app.on_activate = lambda: (window.show(), window.restore())
            app.on_quit = window.destroy
            # No Python bridge is exposed to web content. The only API is the
            # loopback server, gated by a private per-process bearer token.
            webview.start(gui="cocoa", debug=False, private_mode=True)
    finally:
        server.shutdown()
        server.server_close()
        app.jobs.shutdown()
        # ThreadPoolExecutor joins active work on exit. Keep the instance lock
        # until that work has settled its ledger; no second writer starts early.
        app.jobs.pool.shutdown(wait=True, cancel_futures=True)
        info.unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    main()
