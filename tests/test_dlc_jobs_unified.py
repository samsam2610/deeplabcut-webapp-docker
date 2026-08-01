"""Route-level tests for the unified Jobs page: GET /dlc/jobs/all and
POST /dlc/jobs/cancel (src/dlc/monitoring.py).

Follows the fixture style of tests/test_inline_analysis_peaks_route.py and
tests/test_inline_analysis_session_stop_guard.py: a minimal Flask app
registering only `dlc.monitoring.bp`, `dlc.ctx` populated directly, and the
repo's existing session-scoped `fake_redis` fixture (tests/conftest.py). No
`dlc_sandbox_project` (multi-GB project copy) and no
`flask_test_client`/`ia_client` fixtures anywhere in this file, per the
disk-fill hazard noted in docs/superpowers/feedback_test_disk_fill.md.

Covers:
  GET  /dlc/jobs/all    — merges dlc_train_jobs/dlc_analyze_jobs zsets +
                           Celery inspect(active/reserved) + inline sessions;
                           de-dupes by id; degrades to celery_reachable:false
                           without blocking/erroring when the inspector is
                           slow/down; inline rows carry the LLEN pending count.
  POST /dlc/jobs/cancel — celery type revokes with terminate=True; inline type
                           sets the control key AND deletes the queue; bad
                           input (missing/unknown id or type, empty id) -> 400
                           and never reaches `revoke`.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import ctx as _ctx  # noqa: E402
import dlc.monitoring as monitoring  # noqa: E402


# ── Fake Celery: control.revoke + control.inspect(...).active()/.reserved() ──

class _FakeInspect:
    def __init__(self, active, reserved, none_mode):
        self._active = active
        self._reserved = reserved
        self._none_mode = none_mode

    def active(self):
        return None if self._none_mode else self._active

    def reserved(self):
        return None if self._none_mode else self._reserved


class _FakeControl:
    def __init__(self):
        self.revoked = []           # list of (task_id, terminate)
        self.active = {}
        self.reserved = {}
        self.raise_on_inspect = False
        self.none_mode = False      # simulate an unreachable/timed-out inspector
        self.last_timeout = None

    def revoke(self, task_id, terminate=False):
        self.revoked.append((task_id, terminate))

    def inspect(self, timeout=None):
        self.last_timeout = timeout
        if self.raise_on_inspect:
            raise RuntimeError("inspector unreachable")
        return _FakeInspect(self.active, self.reserved, self.none_mode)


class _FakeBackend:
    """Always reports a live state so a zset job's own reconciliation
    (`_reconcile_job`, unrelated to what this test file targets) doesn't
    flip 'running' -> 'dead' under our feet."""

    def get_task_meta(self, jid):
        return {"status": "STARTED"}


class _FakeCelery:
    def __init__(self):
        self.control = _FakeControl()
        self.backend = _FakeBackend()


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def user_data_dir(tmp_path):
    d = tmp_path / "user-data"
    d.mkdir()
    return d


@pytest.fixture
def fake_celery():
    return _FakeCelery()


@pytest.fixture
def client(data_dir, user_data_dir, fake_redis, fake_celery):
    from flask import Flask

    # fake_redis is session-scoped — clear it so state doesn't leak between
    # tests in this file or from other test modules.
    fake_redis._store.clear()
    fake_redis._hstore.clear()
    fake_redis._zsets.clear()
    fake_redis._sets.clear()
    fake_redis._lists.clear()

    _ctx.setup(data_dir, user_data_dir, fake_redis, fake_celery, None, None)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(monitoring.bp)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def _seed_train_job(fake_redis, task_id="t-train-1", **overrides):
    now = time.time()
    mapping = {
        "task_id": task_id, "status": "running", "engine": "pytorch",
        "project": "TrainProj", "gpu_id": "0", "started_at": str(now),
    }
    mapping.update(overrides)
    fake_redis.zadd("dlc_train_jobs", {task_id: now})
    fake_redis.hset("dlc_train_job:" + task_id, mapping=mapping)


def _seed_inline_session(fake_redis, user_id="u1", snap_key="sk1",
                          pending=0, status="ready", project="InlineProj"):
    now = time.time()
    fake_redis.hset(f"inline:session:{user_id}:{snap_key}", mapping={
        "status": status, "project": project,
        "snapshot_path": "/data/proj/snap/snapshot-best-100.pt",
        "started_at": str(now), "last_activity": str(now),
    })
    if pending:
        fake_redis.rpush(f"inline:queue:{user_id}:{snap_key}",
                          *[f"range-{i}" for i in range(pending)])


# ── GET /dlc/jobs/all ─────────────────────────────────────────────────────

class TestJobsAll:
    def test_merges_all_three_sources(self, client, fake_redis, fake_celery):
        _seed_train_job(fake_redis, task_id="t-train-1")
        fake_celery.control.active = {
            "worker1": [{"id": "t-peaks-1", "name": "tasks.dlc_emit_peaks",
                         "time_start": time.time(), "args": [], "kwargs": {}}],
        }
        _seed_inline_session(fake_redis, user_id="u1", snap_key="sk1", pending=3)

        resp = client.get("/dlc/jobs/all")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["celery_reachable"] is True

        ids = {j["id"] for j in body["jobs"]}
        assert "t-train-1" in ids
        assert "t-peaks-1" in ids
        assert "u1:sk1" in ids

        train_row = next(j for j in body["jobs"] if j["id"] == "t-train-1")
        assert train_row["type"] == "celery"
        assert train_row["kind"] == "train"
        assert train_row["cancellable"] is True

        peaks_row = next(j for j in body["jobs"] if j["id"] == "t-peaks-1")
        assert peaks_row["type"] == "celery"
        assert peaks_row["kind"] == "tasks.dlc_emit_peaks"
        assert peaks_row["state"] == "running"
        assert peaks_row["cancellable"] is True

        inline_row = next(j for j in body["jobs"] if j["id"] == "u1:sk1")
        assert inline_row["type"] == "inline"
        assert inline_row["cancellable"] is True
        assert "3 pending" in inline_row["label"]

    def test_dedupes_by_id_preferring_the_richer_zset_record(
            self, client, fake_redis, fake_celery):
        """Same task_id surfaces from BOTH the zset (rich: project/engine/...)
        and the Celery inspector (bare: name/args/worker). Only one row must
        survive, and it must be the richer zset one."""
        _seed_train_job(fake_redis, task_id="dup-1", project="RichProject")
        fake_celery.control.active = {
            "worker1": [{"id": "dup-1", "name": "tasks.dlc_train_network",
                         "time_start": time.time(), "args": [], "kwargs": {}}],
        }

        resp = client.get("/dlc/jobs/all")
        body = resp.get_json()
        rows = [j for j in body["jobs"] if j["id"] == "dup-1"]
        assert len(rows) == 1, rows
        assert rows[0]["detail"].get("project") == "RichProject", rows[0]

    def test_celery_unreachable_on_raise_still_lists_other_sources(
            self, client, fake_redis, fake_celery):
        _seed_train_job(fake_redis, task_id="t-train-2")
        _seed_inline_session(fake_redis, user_id="u2", snap_key="sk2", pending=1)
        fake_celery.control.raise_on_inspect = True

        resp = client.get("/dlc/jobs/all")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["celery_reachable"] is False
        ids = {j["id"] for j in body["jobs"]}
        assert "t-train-2" in ids
        assert "u2:sk2" in ids

    def test_celery_unreachable_on_timeout_none_still_lists_other_sources(
            self, client, fake_redis, fake_celery):
        """A timed-out/unreachable inspector returns None from active()/
        reserved() rather than raising — must be treated the same as a raise."""
        _seed_train_job(fake_redis, task_id="t-train-3")
        fake_celery.control.none_mode = True

        resp = client.get("/dlc/jobs/all")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["celery_reachable"] is False
        ids = {j["id"] for j in body["jobs"]}
        assert "t-train-3" in ids

    def test_inline_row_pending_count_matches_llen(self, client, fake_redis):
        _seed_inline_session(fake_redis, user_id="u3", snap_key="sk3", pending=5)
        resp = client.get("/dlc/jobs/all")
        body = resp.get_json()
        row = next(j for j in body["jobs"] if j["id"] == "u3:sk3")
        assert row["detail"]["pending"] == 5
        assert "5 pending" in row["label"]

    def test_inline_row_present_even_when_session_is_no_longer_running(
            self, client, fake_redis):
        """A session whose Celery task already died (status != warming/ready)
        but still has a nonempty queue is exactly the 'stranded work' case —
        it must still show up, and still be cancellable."""
        _seed_inline_session(fake_redis, user_id="u4", snap_key="sk4",
                              pending=261, status="expired")
        resp = client.get("/dlc/jobs/all")
        body = resp.get_json()
        row = next(j for j in body["jobs"] if j["id"] == "u4:sk4")
        assert row["state"] == "expired"
        assert row["detail"]["pending"] == 261
        assert row["cancellable"] is True

    # ── Stranded queue: `inline:queue:*` with no matching `inline:session:*` ──
    # (the gap this task fixes — see module docstring on `_inline_session_rows`)

    def test_queue_with_no_session_produces_a_stranded_row(self, client, fake_redis):
        """A queue whose session hash is gone entirely (worker died / lost
        the race with a stop signal) MUST still surface a row — this is
        exactly the stranded-work state the Jobs page exists to expose."""
        fake_redis.rpush(
            "inline:queue:u5:sk5",
            *[f"range-{i}" for i in range(261)],
        )
        resp = client.get("/dlc/jobs/all")
        assert resp.status_code == 200
        body = resp.get_json()
        row = next(j for j in body["jobs"] if j["id"] == "u5:sk5")
        assert row["type"] == "inline"
        assert row["state"] == "stranded"
        assert row["detail"]["stranded"] is True
        assert row["detail"]["pending"] == 261
        assert row["cancellable"] is True
        # No session hash existed, so project/snapshot metadata must not be
        # fabricated — left empty rather than guessed at.
        assert row["detail"]["project"] == ""
        assert row["detail"]["snapshot"] == ""

    def test_session_with_no_queue_is_not_stranded(self, client, fake_redis):
        """An idle warm session with nothing pending is a normal row, not
        the stranded-queue state."""
        _seed_inline_session(fake_redis, user_id="u6", snap_key="sk6",
                              pending=0, status="ready", project="IdleProj")
        resp = client.get("/dlc/jobs/all")
        body = resp.get_json()
        row = next(j for j in body["jobs"] if j["id"] == "u6:sk6")
        assert row["state"] == "ready"
        assert row["state"] != "stranded"
        assert row["detail"]["stranded"] is False
        assert row["detail"]["pending"] == 0

    def test_session_plus_nonempty_queue_is_one_row_not_two(self, client, fake_redis):
        """The suffix (user_id, snap_key) is the unit of dedup — a session
        key AND a queue key for the same suffix must merge into one row."""
        _seed_inline_session(fake_redis, user_id="u7", snap_key="sk7",
                              pending=4, status="ready", project="ActiveProj")
        resp = client.get("/dlc/jobs/all")
        body = resp.get_json()
        rows = [j for j in body["jobs"] if j["id"] == "u7:sk7"]
        assert len(rows) == 1, rows
        assert rows[0]["state"] == "ready"
        assert rows[0]["detail"]["stranded"] is False
        assert rows[0]["detail"]["pending"] == 4

    def test_malformed_inline_queue_key_is_skipped_without_raising(
            self, client, fake_redis):
        """A key that doesn't split cleanly into `inline:queue:<user_id>:
        <snap_key>` must be skipped, not crash the whole /dlc/jobs/all
        response — and other, well-formed rows must still come back."""
        # No colon after the prefix at all.
        fake_redis.rpush("inline:queue:malformed-no-colon", "x")
        # Prefix present but user_id (or snap_key) empty.
        fake_redis.rpush("inline:queue::sk-empty-user", "x")
        _seed_inline_session(fake_redis, user_id="u8", snap_key="sk8",
                              pending=2, status="ready", project="GoodProj")

        resp = client.get("/dlc/jobs/all")
        assert resp.status_code == 200
        body = resp.get_json()
        ids = {j["id"] for j in body["jobs"]}
        assert "u8:sk8" in ids
        assert "malformed-no-colon" not in ids
        assert not any("sk-empty-user" in i for i in ids)


# ── POST /dlc/jobs/cancel ─────────────────────────────────────────────────

class TestJobsCancel:
    def test_celery_type_revokes_with_terminate_true(self, client, fake_celery):
        resp = client.post("/dlc/jobs/cancel", json={"id": "t-1", "type": "celery"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["cancelled"] is True
        assert body["type"] == "celery"
        assert fake_celery.control.revoked == [("t-1", True)]

    def test_inline_type_sets_control_key_and_deletes_queue(self, client, fake_redis):
        fake_redis.rpush("inline:queue:u1:sk1", "r1", "r2", "r3")
        resp = client.post("/dlc/jobs/cancel", json={"id": "u1:sk1", "type": "inline"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["cancelled"] is True
        assert body["type"] == "inline"
        assert body["cleared"] == 3
        assert fake_redis.get("inline:control:u1:sk1") == "stop"
        assert fake_redis.llen("inline:queue:u1:sk1") == 0

    def test_missing_id_is_400(self, client):
        resp = client.post("/dlc/jobs/cancel", json={"type": "celery"})
        assert resp.status_code == 400

    def test_missing_type_is_400(self, client):
        resp = client.post("/dlc/jobs/cancel", json={"id": "t-1"})
        assert resp.status_code == 400

    def test_unknown_type_is_400(self, client):
        resp = client.post("/dlc/jobs/cancel", json={"id": "t-1", "type": "bogus"})
        assert resp.status_code == 400

    def test_empty_id_never_reaches_revoke(self, client, fake_celery):
        resp = client.post("/dlc/jobs/cancel", json={"id": "", "type": "celery"})
        assert resp.status_code == 400
        assert fake_celery.control.revoked == []

    def test_malformed_inline_id_is_400(self, client, fake_redis):
        """id without a ':' can't be split into (user_id, snap_key)."""
        resp = client.post("/dlc/jobs/cancel", json={"id": "no-colon-here", "type": "inline"})
        assert resp.status_code == 400

    def test_cancel_stranded_row_with_no_session_hash_still_succeeds(
            self, client, fake_redis):
        """A stranded row (from GET /dlc/jobs/all) has NO backing
        `inline:session:*` hash at all. Cancelling it must still delete the
        queue and report success — `_explicit_session_stop` must not error
        on (or require) a missing session hash."""
        fake_redis.rpush(
            "inline:queue:u9:sk9",
            *[f"range-{i}" for i in range(42)],
        )
        assert fake_redis.hgetall("inline:session:u9:sk9") == {}

        resp = client.post("/dlc/jobs/cancel", json={"id": "u9:sk9", "type": "inline"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["cancelled"] is True
        assert body["type"] == "inline"
        assert body["cleared"] == 42
        assert fake_redis.get("inline:control:u9:sk9") == "stop"
        assert fake_redis.llen("inline:queue:u9:sk9") == 0

        # The row must be gone from /dlc/jobs/all now (queue emptied, no
        # session exists to keep the suffix alive).
        resp2 = client.get("/dlc/jobs/all")
        ids = {j["id"] for j in resp2.get_json()["jobs"]}
        assert "u9:sk9" not in ids
