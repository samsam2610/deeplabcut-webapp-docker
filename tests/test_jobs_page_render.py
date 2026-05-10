"""Tests for the /jobs page render + nav button + auth gate."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _auth(client):
    """Match the helper used in test_jobs_page_endpoints.py / test_dlc_viewer_routes.py."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True


def test_jobs_page_renders_when_authenticated(flask_test_client):
    client, _, _, _, _ = flask_test_client
    _auth(client)
    res = client.get("/jobs")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'id="jobs-rail"' in body
    assert 'id="jobs-detail"' in body
    assert "/static/js/jobs.js" in body


def test_jobs_page_redirects_to_login_when_unauth(flask_test_client):
    client, _, _, _, _ = flask_test_client
    res = client.get("/jobs")
    assert res.status_code in (302, 401)
