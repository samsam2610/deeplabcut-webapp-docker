"""Tests for the post-process predictions blueprint."""
from __future__ import annotations

import pytest


def test_blueprint_registered(flask_test_client):
    """The /dlc/postprocess/recent route must be registered on the app.

    Note: this app's auth middleware returns 302 (login redirect) for ANY
    unknown URL, so a status-code check alone cannot distinguish a real
    route from a missing one. Instead, assert the rule is present in the
    URL map, then confirm the response is not a 404.
    """
    client, app_module, _redis_client, _data_dir, _user_data_dir = flask_test_client

    rules = {str(r) for r in app_module.app.url_map.iter_rules()}
    assert "/dlc/postprocess/recent" in rules, (
        f"/dlc/postprocess/recent not registered. Rules sample: "
        f"{sorted(r for r in rules if r.startswith('/dlc/'))[:5]}"
    )

    resp = client.get("/dlc/postprocess/recent")
    # Route exists; either 200 (empty list), 302 (auth redirect), or 400
    # (no active project), but never 404.
    assert resp.status_code != 404
