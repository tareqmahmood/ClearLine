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
        profiles = [p.to_dict() for p in self.store.list()]
        return {"db": meta, "db_error": error, "home": str(self.home), "profiles": profiles,
                "profile_errors": [f"{f.name}: {e}" for f, e in self.store.errors]}

    def search(self, q: str) -> list[dict]:
        return [p.to_dict() for p in self.db.find(q)] if q.strip() else []

    def save_profile(self, body: dict) -> dict:
        old_slug = str(body.get("slug") or "")
        profile = Profile.from_dict(body, slug=old_slug)
        if not old_slug and self.store.path(profile.name).exists():
            raise ValueError(f"A profile named {profile.name!r} already exists. Open it from the "
                             "list to edit it, or use a different name.")
        path = self.store.save(profile)
        return {"profile": profile.to_dict(), "path": str(path)}

    def check(self, body: dict) -> dict:
        members = parse(body.get("pclist", ""))
        if not members:
            raise ValueError("Paste at least one reviewer name.")
        profiles = [self.store.load(str(s)) for s in _list(body, "profiles")]
        ids = [str(a) for a in _list(body, "authors") if str(a).strip()]
        if not profiles and not ids:
            raise ValueError("Choose at least one author profile or add a DBLP author.")
        s = body.get("settings") or {}
        if not isinstance(s, dict):
            raise ValueError("settings must be an object.")
        settings = Settings(
            coauthor_years=_int(s, "coauthor_years", 2, "Coauthored within"),
            institution_years=_int(s, "institution_years", 2, "Same institution within"),
            max_authors=_int(s, "max_authors", 30, "Ignore papers with more than"),
            as_of=parse_month(str(s["as_of"])) if s.get("as_of") else dt.date.today())
        authors, warnings = build_authors(self.db, profiles, ids)
        results = check(self.db, authors, members, settings)
        return {"settings": settings.to_dict(), "warnings": warnings,
                "authors": [a.to_dict() for a in authors], "summary": summarize(results),
                "results": [r.to_dict() for r in results]}


def _list(body: dict, key: str) -> list:
    value = body.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list.")
    return value


def _int(settings: dict, key: str, default: int, label: str) -> int:
    value = settings.get(key)
    if value is None or str(value).strip() == "":
        return default
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{label}: expected a whole number, got {value!r}.") from None
    if n < 0:
        raise ValueError(f"{label}: must not be negative.")
    return n


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
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ValueError("Request body is not valid JSON.") from None
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object.")
            return body

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
            except (ValueError, FileNotFoundError, MissingDatabase) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except Exception as e:  # noqa: BLE001 - never drop the connection without an answer
                return self._json({"error": f"Internal error: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

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
