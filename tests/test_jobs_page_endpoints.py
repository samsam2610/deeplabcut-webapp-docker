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


# ── /dlc/training/jobs reconciliation ────────────────────────────────────────

def test_jobs_endpoint_force_running_when_celery_live(flask_test_client):
    """If Redis says 'dead' but Celery state is live, the response must show 'running'."""
    client, app_module, redis, _, _ = flask_test_client
    _auth(client)
    redis.zadd("dlc_train_jobs", {"tL": 12345.0})
    redis.hset("dlc_train_job:tL", mapping={
        "task_id": "tL", "status": "dead", "engine": "pytorch",
        "project": "TestProj", "gpu_id": "0", "started_at": "12345.0",
    })
    fake_async = type("FA", (), {"state": "PROGRESS"})
    with patch("dlc.monitoring.AsyncResult", return_value=fake_async):
        res = client.get("/dlc/training/jobs")
    assert res.status_code == 200
    jobs = res.get_json()["jobs"]
    target = next((j for j in jobs if j.get("task_id") == "tL"), None)
    assert target is not None, jobs
    assert target["status"] == "running"


def test_jobs_endpoint_running_when_celery_terminal_marks_dead(flask_test_client):
    """The pre-existing direction is preserved: running → dead when Celery is gone."""
    client, _, redis, _, _ = flask_test_client
    _auth(client)
    redis.zadd("dlc_train_jobs", {"tD": 99999.0})
    redis.hset("dlc_train_job:tD", mapping={
        "task_id": "tD", "status": "running", "engine": "pytorch",
        "project": "TestProj", "gpu_id": "0", "started_at": "99999.0",
    })
    fake_async = type("FA", (), {"state": "SUCCESS"})  # not in _LIVE_CELERY_STATES
    with patch("dlc.monitoring.AsyncResult", return_value=fake_async):
        res = client.get("/dlc/training/jobs")
    target = next((j for j in res.get_json()["jobs"] if j.get("task_id") == "tD"), None)
    assert target is not None
    assert target["status"] == "dead"


# ── Cross-session correctness pins ───────────────────────────────────────────

def test_jobs_endpoint_no_uid_in_payload(flask_test_client):
    """The wire format must not leak any session/uid identifiers."""
    client, _, redis, _, _ = flask_test_client
    _auth(client)
    redis.zadd("dlc_train_jobs", {"tA": 1.0})
    redis.hset("dlc_train_job:tA", mapping={
        "task_id": "tA", "status": "running", "engine": "pytorch",
        "project": "P", "gpu_id": "0", "started_at": "1.0",
    })
    fake_async = type("FA", (), {"state": "PROGRESS"})
    with patch("dlc.monitoring.AsyncResult", return_value=fake_async):
        res = client.get("/dlc/training/jobs")
    body = res.get_data(as_text=True)
    for forbidden in ("uid", '"user"', '"session"', "webapp:"):
        assert forbidden not in body, f"{forbidden!r} leaked into response: {body[:300]}"


def test_jobs_endpoint_lists_jobs_across_sessions(flask_test_client):
    """A task seeded in 'session A's redis must appear when 'session B' fetches the list.

    Sessions are simulated by clearing Flask cookies between requests; the underlying
    Redis state is shared (which is the whole point of the design)."""
    client, _, redis, _, _ = flask_test_client
    _auth(client)
    redis.zadd("dlc_train_jobs", {"tCross": 1.0})
    redis.hset("dlc_train_job:tCross", mapping={
        "task_id": "tCross", "status": "running", "engine": "pytorch",
        "project": "Pcross", "gpu_id": "0", "started_at": "1.0",
    })
    fake_async = type("FA", (), {"state": "PROGRESS"})

    # Simulate 'session B' by clearing all cookies on the test client.
    client.delete_cookie("session")
    _auth(client)  # re-auth as a fresh session
    with patch("dlc.monitoring.AsyncResult", return_value=fake_async):
        res = client.get("/dlc/training/jobs")
    assert res.status_code == 200
    ids = [j.get("task_id") for j in res.get_json()["jobs"]]
    assert "tCross" in ids


def test_terminate_endpoint_works_without_session_state(flask_test_client):
    """POST /dlc/task/<id>/terminate must not 500 on a missing PID — should return 404
    or 200 with a plain 'task not found' / 'stopped' body."""
    client, _, redis, _, _ = flask_test_client
    _auth(client)
    # Seed a finished job (no PID key) — drives the "Path B" cleanup
    redis.zadd("dlc_train_jobs", {"tNoPid": 1.0})
    redis.hset("dlc_train_job:tNoPid", mapping={
        "task_id": "tNoPid", "status": "running", "engine": "pytorch",
        "project": "P", "gpu_id": "0", "started_at": "1.0",
    })
    res = client.post("/dlc/task/tNoPid/terminate")
    assert res.status_code in (200, 404), res.get_data(as_text=True)
    assert res.get_json() is not None  # JSON body, not HTML traceback
