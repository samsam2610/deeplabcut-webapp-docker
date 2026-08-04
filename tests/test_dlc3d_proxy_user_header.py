"""The dlc-3d proxy must name the user, or dlc-3D cannot tell two users apart.

dlc-3D has no session of its own: the browser's cookie is for the main webapp.
The uid stamped here is the same one keying webapp:dlc_project:{uid}, so the
header and the key cannot drift.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Imported from the REAL requests package before any test monkeypatches
# sys.modules["requests"] to a MagicMock, so we can faithfully reproduce how
# requests resolves a headers dict that (thanks to Werkzeug's header-name
# canonicalisation -- see test_client_supplied_header_cannot_spoof_the_uid)
# can end up with two keys that only differ by case, e.g. "X-DLC-User" and
# "X-Dlc-User". requests.PreparedRequest folds such a dict into a
# CaseInsensitiveDict where the LAST-inserted key wins case-insensitively --
# that, not dict insertion order in the abstract, is what actually reaches
# the wire.
from requests.structures import CaseInsensitiveDict

# The proxy sits behind the Jupyter-style token gate (app.py's
# _require_auth before_request hook). Bypass it so the test can drive the
# route directly with only a session uid set, same pattern used by the
# other test_*.py files in this suite (e.g. test_viewer_ui_sync.py).
os.environ["AUTH_DISABLED"] = "true"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _captured_headers(monkeypatch, app_module):
    """Call the proxy and return the headers it forwarded."""
    seen = {}

    class _Resp:
        status_code = 200
        headers = {}
        def iter_content(self, chunk_size=8192):
            return iter([b""])

    def _fake_request(**kw):
        seen.update(kw.get("headers") or {})
        return _Resp()

    fake_requests = MagicMock()
    fake_requests.request = _fake_request
    fake_requests.exceptions.ConnectionError = Exception
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    return seen


def test_proxy_stamps_the_user_id(monkeypatch):
    import app as app_module
    seen = _captured_headers(monkeypatch, app_module)
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = "user-abc"
    client.get("/dlc-3d/labeled-frames?session=s1")
    assert seen.get("X-DLC-User") == "user-abc", (
        "without this header dlc-3D cannot distinguish two users"
    )


def test_two_sessions_get_different_ids(monkeypatch):
    """The whole point: two browsers must not look like one user."""
    import app as app_module
    seen = _captured_headers(monkeypatch, app_module)

    ids = []
    for uid in ("user-one", "user-two"):
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["uid"] = uid
        client.get("/dlc-3d/")
        ids.append(seen.get("X-DLC-User"))
    assert ids == ["user-one", "user-two"]


def test_header_survives_the_hop_by_hop_filter(monkeypatch):
    """The proxy strips host/content-length/transfer-encoding. X-DLC-User must
    not be caught by that filter."""
    import app as app_module
    seen = _captured_headers(monkeypatch, app_module)
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = "survivor"
    client.post("/dlc-3d/project", json={"path": "/tmp/x"})
    assert seen.get("X-DLC-User") == "survivor"


def test_client_supplied_header_cannot_spoof_the_uid(monkeypatch):
    """A browser-supplied X-DLC-User must never win over the session uid.

    If fwd_headers is built as {"X-DLC-User": uid} and THEN updated with the
    client's own headers on top, an attacker who sends X-DLC-User: <victim>
    can impersonate them: dict.update() with a client-supplied dict adds
    "X-Dlc-User" (Werkzeug's canonical casing for anything it parsed from
    the WSGI environ -- it does not preserve "DLC" as sent) as a SEPARATE
    key alongside "X-DLC-User". Both keys individually look harmless, but
    once requests folds the dict into a CaseInsensitiveDict for the actual
    HTTP request, the key inserted LAST wins case-insensitively -- so
    ordering silently decides whose value reaches dlc-3D. We must therefore
    resolve fwd_headers the same way requests would, not just check the one
    literal "X-DLC-User" key.

    Also try the lowercase spelling the attacker might send directly
    (x-dlc-user) -- Werkzeug canonicalises the incoming header name before
    routes.py or this test ever sees it, so both spellings must be checked.
    """
    import app as app_module
    seen = _captured_headers(monkeypatch, app_module)
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = "victim"

    client.get("/dlc-3d/labeled-frames?session=s1", headers={"X-DLC-User": "attacker"})
    resolved = CaseInsensitiveDict(seen)
    assert resolved.get("X-DLC-User") == "victim", (
        "client-supplied X-DLC-User must not override the session uid "
        f"(what requests would actually send: {dict(seen)!r})"
    )

    seen.clear()
    client.get("/dlc-3d/labeled-frames?session=s1", headers={"x-dlc-user": "attacker"})
    resolved = CaseInsensitiveDict(seen)
    assert resolved.get("X-DLC-User") == "victim", (
        "client-supplied x-dlc-user (lowercase) must not override the session uid "
        f"(what requests would actually send: {dict(seen)!r})"
    )
