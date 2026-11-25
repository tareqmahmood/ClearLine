import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from clearline.server import App, _make_handler


@pytest.fixture
def server(home):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = _make_handler(App(home), port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def call(url, body=None, method=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_page_and_status(server):
    with urllib.request.urlopen(server + "/") as r:
        assert b"ClearLine" in r.read()
    s = call(server + "/api/status")
    assert s["db"]["publications"] == "7"
    assert s["profiles"] == []


def test_profile_then_check(server):
    saved = call(server + "/api/profiles", {
        "name": "Alice Ames", "dblp": "1/alice",
        "affiliations": [{"institution": "Stanford University", "domains": ["stanford.edu"],
                          "start": "2016-09", "end": "2023-06"}],
    })
    slug = saved["profile"]["slug"]
    out = call(server + "/api/check", {
        "profiles": [slug], "authors": [],
        "pclist": "Bob Brown (Stanford University)\nSomeone Else\n",
        "settings": {"as_of": "2026-11"},
    })
    levels = {r["member"]["name"]: r["level"] for r in out["results"]}
    assert levels == {"Bob Brown": "conflict", "Someone Else": "clear"}
    kinds = {f["kind"] for f in out["results"][0]["findings"]}
    assert kinds == {"coauthor", "institution"}


def test_rejects_cross_site_requests(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server + "/api/profiles", {"name": "Evil"}, headers={"Origin": "https://evil.example"})
    assert e.value.code == 403


@pytest.mark.parametrize("body", [
    [1, 2],
    {"profiles": "x", "pclist": "Bob Brown"},
    {"authors": ["1/alice"], "pclist": "Bob Brown", "settings": {"coauthor_years": "two"}},
    {"authors": ["1/alice"], "pclist": "Bob Brown", "settings": "nope"},
    {"profiles": ["../../etc/passwd"], "pclist": "Bob Brown"},
])
def test_bad_requests_get_json_errors(server, body):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server + "/api/check", body)
    assert e.value.code == 400
    assert json.loads(e.value.read())["error"]


def test_empty_and_null_settings_use_defaults(server):
    out = call(server + "/api/check", {
        "authors": ["1/alice"], "pclist": "Bob Brown",
        "settings": {"coauthor_years": "", "institution_years": None, "as_of": "2026-11"}})
    assert out["settings"]["coauthor_years"] == 2
    assert out["results"][0]["level"] == "conflict"


def test_new_profile_does_not_overwrite(server):
    call(server + "/api/profiles", {"name": "Alice Ames"})
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server + "/api/profiles", {"name": "Alice Ames"})
    assert "already exists" in json.loads(e.value.read())["error"]
