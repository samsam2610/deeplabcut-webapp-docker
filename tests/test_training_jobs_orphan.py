"""Regression guard: /dlc/training/jobs must surface orphan zset entries.

When the dlc_train_jobs sorted set holds a task_id whose backing
dlc_train_job:<id> hash has been deleted (e.g. a partial hard-reset or an
out-of-band cleanup), the route used to silently drop the entry, hiding
running-but-untracked work from the UI.

This test drives the route's reconcile loop with a fake redis that returns
an empty dict for the orphan's hash, and asserts the response includes a
synthetic stub so the user can see + clean it up via the rail.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class _FakeRedis:
    """Tiny stand-in for redis_client used by the route."""

    def __init__(self, train_zset, train_hashes):
        self._train_zset   = list(train_zset)
        self._train_hashes = dict(train_hashes)

    def zrevrange(self, key, start, stop):
        if key == "dlc_train_jobs":
            return list(reversed(self._train_zset))[start : stop + 1]
        return []

    def hgetall(self, key):
        return dict(self._train_hashes.get(key, {}))

    def hset(self, key, field, value):
        self._train_hashes.setdefault(key, {})[field] = value


class _FakeAsyncResultModule:
    """Stand-in for celery.result.AsyncResult — always returns a 'gone' state."""

    def __init__(self, jid, app=None):
        self.state = "FAILURE"


def test_orphan_zset_entry_surfaces_as_synthetic_stub(monkeypatch):
    """zset has an id, hash is missing → response includes a stub for that id."""
    from dlc import monitoring

    orphan_id = "7f977f80-7774-417f-b28e-f47bfaa80cd3"
    live_id   = "aa83b0ab-f9ed-42af-9506-cdcfd86c8c60"
    fake = _FakeRedis(
        train_zset=[orphan_id, live_id],
        train_hashes={
            "dlc_train_job:" + live_id: {
                "task_id":    live_id,
                "status":     "complete",
                "operation":  "train",
                "started_at": "1.0",
            },
            # NB: no entry for the orphan
        },
    )

    monkeypatch.setattr(monitoring._ctx, "redis_client", lambda: fake)
    monkeypatch.setattr(monitoring._ctx, "celery",       lambda: object())
    monkeypatch.setattr(monitoring, "AsyncResult", _FakeAsyncResultModule)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(monitoring.bp)
    client = app.test_client()

    resp = client.get("/dlc/training/jobs")
    assert resp.status_code == 200
    body = resp.get_json()
    ids = [j["task_id"] for j in body["jobs"]]
    assert orphan_id in ids, (
        f"orphan zset entry {orphan_id} was dropped from the response; "
        "it should appear as a synthetic stub so the UI can clean it up. "
        f"Got jobs: {body['jobs']!r}"
    )
    orphan = next(j for j in body["jobs"] if j["task_id"] == orphan_id)
    assert orphan["status"] == "orphaned", (
        f"orphan stub should be marked status=orphaned; got {orphan['status']!r}"
    )


def test_normal_jobs_still_returned(monkeypatch):
    """Sanity: a job WITH its hash still comes back as before."""
    from dlc import monitoring

    jid = "aa83b0ab-f9ed-42af-9506-cdcfd86c8c60"
    fake = _FakeRedis(
        train_zset=[jid],
        train_hashes={
            "dlc_train_job:" + jid: {
                "task_id":    jid,
                "status":     "complete",
                "operation":  "train",
                "started_at": "1.0",
            },
        },
    )
    monkeypatch.setattr(monitoring._ctx, "redis_client", lambda: fake)
    monkeypatch.setattr(monitoring._ctx, "celery",       lambda: object())
    monkeypatch.setattr(monitoring, "AsyncResult", _FakeAsyncResultModule)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(monitoring.bp)
    client = app.test_client()

    resp = client.get("/dlc/training/jobs")
    body = resp.get_json()
    assert resp.status_code == 200
    assert any(j["task_id"] == jid and j["status"] == "complete" for j in body["jobs"])
