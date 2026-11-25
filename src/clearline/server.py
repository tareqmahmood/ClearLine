"""Local web UI. Binds to 127.0.0.1 only; nothing leaves the machine."""

from __future__ import annotations

import datetime as dt
import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import DBLP, MissingDatabase
from .pclist import parse
from .profile import Profile, ProfileStore, parse_month
from .rules import Settings, build_authors, check, summarize


class App:
    def __init__(self, home: Path):
        self.home = home
        self.store = ProfileStore(home / "profiles")
        self._db: DBLP | None = None
        self._lock = threading.Lock()

    @property
    def db(self) -> DBLP:
        with self._lock:
            if self._db is None:
                self._db = DBLP(self.home / "dblp.sqlite")
            return self._db

    def status(self) -> dict:
        try:
            meta, error = self.db.meta(), None
        except MissingDatabase as e:
            meta, error = None, str(e)
        return {"db": meta, "db_error": error, "home": str(self.home),
                "profiles": [p.to_dict() for p in self.store.list()]}

    def search(self, q: str) -> list[dict]:
        return [p.to_dict() for p in self.db.find(q)] if q.strip() else []

    def save_profile(self, body: dict) -> dict:
        old_slug = body.get("slug") or ""
        profile = Profile.from_dict(body, slug=old_slug)
        path = self.store.save(profile)
        return {"profile": profile.to_dict(), "path": str(path)}

    def check(self, body: dict) -> dict:
        members = parse(body.get("pclist", ""))
        if not members:
            raise ValueError("Paste at least one reviewer name.")
        profiles = [self.store.load(s) for s in body.get("profiles", [])]
        ids = [a for a in body.get("authors", []) if a.strip()]
        if not profiles and not ids:
            raise ValueError("Choose at least one author profile or add a DBLP author.")
        s = body.get("settings", {})
        settings = Settings(
            coauthor_years=int(s.get("coauthor_years", 2)),
            institution_years=int(s.get("institution_years", 2)),
            max_authors=int(s.get("max_authors", 30)),
            as_of=parse_month(s["as_of"]) if s.get("as_of") else dt.date.today())
        authors, warnings = build_authors(self.db, profiles, ids)
        results = check(self.db, authors, members, settings)
        return {"settings": settings.to_dict(), "warnings": warnings,
                "authors": [a.to_dict() for a in authors], "summary": summarize(results),
                "results": [r.to_dict() for r in results]}


def _make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ClearLine"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _trusted(self) -> bool:
            # Block DNS rebinding and cross-site requests from other pages in the browser.
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.removeprefix("http://") in allowed_hosts

        def _body(self) -> dict:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Expected JSON.")
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def _route(self, method: str):
            if not self._trusted():
                return self._json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
            url = urlparse(self.path)
            try:
                if method == "GET" and url.path in ("/", "/index.html"):
                    page = resources.files("clearline").joinpath("web/index.html").read_bytes()
                    return self._send(200, page, "text/html; charset=utf-8")
                if method == "GET" and url.path == "/api/status":
                    return self._json(app.status())
                if method == "GET" and url.path == "/api/search":
                    q = parse_qs(url.query).get("q", [""])[0]
                    return self._json(app.search(q))
                if method == "POST" and url.path == "/api/profiles":
                    return self._json(app.save_profile(self._body()))
                if method == "DELETE" and url.path.startswith("/api/profiles/"):
                    app.store.delete(url.path.rsplit("/", 1)[-1])
                    return self._json({"ok": True})
                if method == "POST" and url.path == "/api/check":
                    return self._json(app.check(self._body()))
                return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            except (ValueError, FileNotFoundError, MissingDatabase, KeyError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

    return Handler


def serve(home: Path, port: int = 8765, open_browser: bool = True) -> None:
    app = App(home)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(app, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"ClearLine is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
