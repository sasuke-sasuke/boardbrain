"""Authenticated loopback-only API for the native window and development browser."""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlparse

from . import __version__
from .assistant import Assistant, KeyStore
from .ingest import import_source, render_page, photo_preview
from .jobs import Jobs
from .measurements import parse_reading
from .store import Store, dumps

STATIC = Path(__file__).parent / "static"
MAX_BODY = 35 * 1024 * 1024


class App:
    def __init__(self, store: Store, keys=None):
        self.store = store
        self.keys = keys or KeyStore()
        self.jobs = Jobs()
        self.assistant = Assistant(store, self.keys)
        self.token = secrets.token_urlsafe(32)
        self.on_activate = lambda: None
        self.on_quit = lambda: None

    def dispatch(self, method, path, query, data):
        if method == "GET":
            if path == "/api/state":
                return {"version": __version__, "cases": self.store.list_cases(), "settings": self.store.settings(),
                        "has_api_key": bool(self.keys.get()), "usage": self.store.usage_summary(), "jobs": self.jobs.list()}
            if path == "/api/case":
                return self.store.case(query.get("id", [""])[0])
            if path == "/api/lookup":
                return self.store.lookup(query.get("case_id", [""])[0], query.get("q", [""])[0])
            if path == "/api/confirmed":
                return self.store.confirmed_cases(query.get("case_id", [""])[0], query.get("q", [""])[0])
            if path == "/api/export":
                return self.store.case(query.get("id", [""])[0])
        if method == "POST":
            if path == "/api/cases":
                return self.store.create_case(data)
            if path == "/api/case-details":
                return self.store.update_case(data["case_id"], data)
            if path == "/api/source-scope":
                return self.store.confirm_source(data["case_id"], data["source_id"], data.get("confirmed") is True)
            if path == "/api/parse-reading":
                return parse_reading(data.get("text", ""))
            if path == "/api/observations":
                return self.store.capture(data["case_id"], data["kind"], data["data"])
            if path == "/api/retract":
                self.store.retract(data["case_id"], data["event_id"])
                return {"ok": True}
            if path == "/api/outcome":
                return self.store.close_case(data["case_id"], data)
            if path == "/api/settings":
                settings = dict(data)
                key = settings.pop("api_key", None)
                if key:
                    self.keys.set(key)
                return self.store.set_settings(settings)
            if path == "/api/upload":
                try:
                    raw = base64.b64decode(data["content"], validate=True)
                except (ValueError, TypeError):
                    raise ValueError("File upload was incomplete. Retry the file.") from None
                source = self.store.add_source(data["case_id"], data["filename"], raw, data["role"], data.get("scope_confirmed") is True)
                if source["status"] != "queued":
                    return {"source": source, "job": None, "message": "This file is already saved; unchanged content was not reprocessed."}
                job = self.jobs.submit(data["case_id"], "import", lambda cancel, progress: import_source(self.store, data["case_id"], source["id"], cancel, progress))
                return {"source": source, "job": job}
            if path == "/api/retry-import":
                source = self.store.source(data["case_id"], data["source_id"])
                if any(j["case_id"] == data["case_id"] and j["kind"] == "import" and j["status"] in ("queued", "running", "stopping") for j in self.jobs.list()):
                    raise ValueError("Wait for this case's current import to finish.")
                return self.jobs.submit(data["case_id"], "import", lambda cancel, progress: import_source(self.store, data["case_id"], source["id"], cancel, progress, data.get("ocr_pages", [])))
            if path == "/api/ask":
                case_id = data["case_id"]
                if self.store.case(case_id)["status"] != "open":
                    raise ValueError("Reopen this case to continue the diagnosis.")
                if any(j["case_id"] == case_id and j["kind"] == "import" and j["status"] in ("queued", "running", "stopping") for j in self.jobs.list()):
                    raise ValueError("Wait for the file import to finish so the assistant has its evidence.")
                return self.jobs.submit(case_id, "assistant", lambda cancel, progress: self.assistant.run(case_id, data["question"], data.get("photo_ids", []), data.get("research") is True, cancel, progress, deep_analysis=data.get("deep_analysis") is True))
            if path == "/api/cancel":
                self.jobs.cancel(data["id"])
                return {"ok": True}
            if path == "/api/backup":
                backup = self.store.backup()
                return {"filename": backup.name}
            if path == "/api/open-source":
                source = self.store.source(data["case_id"], data["source_id"])
                if sys.platform != "darwin":
                    raise ValueError("Opening a local viewer is available in the Mac app.")
                export = self.store.root / "open-files" / source["sha"]
                export.mkdir(parents=True, exist_ok=True, mode=0o700)
                target = export / source["name"]
                target.write_bytes((self.store.blobs / source["sha"]).read_bytes())
                args = ["/usr/bin/open"]
                if source["role"] == "boardview":
                    # FlexBV remains an independent viewer. No undocumented IPC.
                    apps = [Path("/Applications/FlexBV.app"), Path("/Applications/FlexBV5.app"), Path.home() / "Applications/FlexBV.app"]
                    app = next((str(p) for p in apps if p.exists()), None)
                    if app:
                        args += ["-a", app]
                result = subprocess.run(args + [str(target)], capture_output=True, timeout=15)
                if result.returncode:
                    raise ValueError("macOS could not open the file. Associate this file type with FlexBV in Finder, then retry.")
                return {"ok": True}
            if path == "/api/activate":
                self.on_activate()
                return {"ok": True}
            if path == "/api/quit":
                threading.Timer(.2, self.on_quit).start()
                return {"ok": True}
        raise LookupError("Endpoint not found")

    def binary(self, path, query):
        if path == "/api/backup-file":
            filename = query.get("name", [""])[0]
            if Path(filename).name != filename or not filename.endswith(".zip"):
                raise ValueError("Invalid backup name")
            target = self.store.root / "backups" / filename
            if not target.is_file():
                raise ValueError("Backup not found")
            return target.read_bytes(), "application/zip"
        if path not in ("/api/file", "/api/page", "/api/photo"):
            return None
        source = self.store.source(query.get("case_id", [""])[0], query.get("id", [""])[0])
        if path == "/api/photo":
            if source["role"] != "photo" or source["status"] != "ready":
                raise ValueError("Photo is not ready")
            return photo_preview(self.store, source), "image/jpeg"
        if path == "/api/page":
            if not source["name"].lower().endswith(".pdf"):
                raise ValueError("This file is not a PDF")
            return render_page(self.store, source, int(query.get("page", ["1"])[0])), "image/png"
        return (self.store.blobs / source["sha"]).read_bytes(), "application/octet-stream"


def make_server(app: App, port=0):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass  # Do not put case names, questions or credentials in access logs.

        def send(self, code, body, mime="application/json"):
            if not isinstance(body, bytes):
                body = dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-src blob:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host:
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host:
                return False
            token = self.headers.get("Authorization", "")
            return secrets.compare_digest(token, "Bearer " + app.token)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/app.js", "/style.css"):
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send(403, {"error": "Invalid local host"})
                    return
                filename = "index.html" if parsed.path == "/" else parsed.path[1:]
                self.send(200, (STATIC / filename).read_bytes(), {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[filename])
                return
            self.handle_api("GET", parsed)

        def do_POST(self):
            self.handle_api("POST", urlparse(self.path))

        def handle_api(self, method, parsed):
            if not self.authorized():
                self.send(403, {"error": "Open BoardBrain from its app icon to reconnect securely."})
                return
            try:
                query = parse_qs(parsed.query)
                data = {}
                if method == "POST":
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValueError("Expected JSON")
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > MAX_BODY:
                        raise ValueError("Request is empty or too large")
                    self.connection.settimeout(20)
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Expected a JSON object")
                binary = app.binary(parsed.path, query) if method == "GET" else None
                if binary:
                    self.send(200, *binary)
                else:
                    self.send(200, app.dispatch(method, parsed.path, query, data))
            except (ValueError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)[:600]})
            except LookupError:
                self.send(404, {"error": "Not found"})
            except Exception:
                self.send(500, {"error": "This operation failed. Your saved case is intact. Retry or export a backup."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
