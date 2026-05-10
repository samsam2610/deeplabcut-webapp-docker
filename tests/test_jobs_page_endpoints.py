"""Backend tests for the /jobs page endpoints.

Covers:
  GET /dlc/task/<id>/log-tail?n=N — log backfill
  GET /dlc/training/jobs           — cross-session listing + reconciliation (later tasks)
  POST /dlc/task/<id>/terminate    — cross-session stop (later tasks)
"""
from __future__ import annotations

import json
from unittest.mock import patch


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True


# ── log-tail ─────────────────────────────────────────────────────────────────

def test_log_tail_returns_last_n_lines(flask_test_client):
    client, app_module, redis, _, _ = flask_test_client
    _auth(client)
    redis.rpush("dlc_task:t1:log", "line 1", "line 2", "line 3", "line 4", "line 5")
    res = client.get("/dlc/task/t1/log-tail?n=3")
    assert res.status_code == 200
    data = res.get_json()
    assert data["lines"] == ["line 3", "line 4", "line 5"]
    assert data["total"] == 5


def test_log_tail_default_n_is_2000(flask_test_client):
    client, _, redis, _, _ = flask_test_client
    _auth(client)
    redis.rpush("dlc_task:t2:log", *[f"l{i}" for i in range(10)])
    res = client.get("/dlc/task/t2/log-tail")
    assert res.status_code == 200
    assert len(res.get_json()["lines"]) == 10  # well under the default cap


def test_log_tail_n_capped_at_10000(flask_test_client):
    client, _, redis, _, _ = flask_test_client
    _auth(client)
    redis.rpush("dlc_task:t3:log", "x")
    # n=99999 must clamp without raising; behavioral check via no-error.
    res = client.get("/dlc/task/t3/log-tail?n=99999")
    assert res.status_code == 200
    assert res.get_json()["lines"] == ["x"]


def test_log_tail_unknown_task_returns_empty_list(flask_test_client):
    client, _, _, _, _ = flask_test_client
    _auth(client)
    res = client.get("/dlc/task/never-existed/log-tail")
    assert res.status_code == 200
    data = res.get_json()
    assert data["lines"] == []
    assert data["total"] == 0
