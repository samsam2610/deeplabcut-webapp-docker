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
