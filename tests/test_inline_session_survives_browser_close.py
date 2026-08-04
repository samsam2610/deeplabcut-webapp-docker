"""Regression tests: an "Analyze for tag" run must outlive the browser.

The three defects these cover were all found in production Redis + the worker
log on 2026-08-04, on a card that had already been made not to stop its own
session on tab close:

  1. tasks.dlc_inline_session inherited celery_app's global
     task_time_limit=7200, so a 200-300 range tag run was SIGKILLed at
     exactly 2 h with ranges still queued (worker log, 2026-08-01 and
     2026-08-04; 30 ranges left stranded in inline:queue:*).
  2. The session hash carried a 1 h expiry that only _publish_status refreshed,
     and _publish_status only runs at session start/exit. Past T+1h the hash of
     a live session vanished, so /session/status called it absent and
     /session/start would dispatch a SECOND worker onto the same queue. The
     stranded 2026-08-01 session hash was the fingerprint: `{last_activity}`
     alone, no status, no TTL — _bump_activity's HSET having recreated it.
  3. The candidate-peak pass was fired by the browser only after every range
     poll resolved, so closing the tab silently skipped it.

Style follows tests/test_inline_analysis_peaks_route.py: a minimal Flask app
registering only dlc.inline_analysis.bp, and the repo's session-scoped
fake_redis (cleared per test).
"""
import json
import sys
import time as _time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# dlc.tasks does `import deeplabcut` at module level, so it can only be
# imported through test_inline_analysis_worker's stubbing loader. Reuse that
# module rather than duplicating the stub set.
sys.path.insert(0, str(Path(__file__).parent))

from dlc import ctx as _ctx  # noqa: E402
import dlc.inline_analysis as inline_analysis  # noqa: E402

from test_inline_analysis_worker import (  # noqa: E402
    dlc_tasks,
    _fake_loader_factory,
)


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def ia_redis(fake_redis):
    fake_redis._store.clear()
    fake_redis._hstore.clear()
    fake_redis._zsets.clear()
    fake_redis._sets.clear()
    fake_redis._lists.clear()
    return fake_redis


@pytest.fixture
def client(tmp_path, ia_redis):
    from flask import Flask

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_data_dir = tmp_path / "user-data"
    user_data_dir.mkdir()
    _ctx.setup(data_dir, user_data_dir, ia_redis, None, None, None)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(inline_analysis.bp)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c._data_dir = data_dir
        yield c


@pytest.fixture
def active_project(client, ia_redis):
    proj = client._data_dir / "proj"
    proj.mkdir()
    (proj / "config.yaml").write_text("bodyparts: []\n")
    with client.session_transaction() as sess:
        sess["uid"] = "u1"
    ia_redis.set("webapp:dlc_project:u1", json.dumps({
        "config_path":  str(proj / "config.yaml"),
        "project_path": str(proj),
        "project":      proj.name,
    }))
    return proj


@pytest.fixture
def snapshot_rel(active_project):
    snap_dir = (active_project / "dlc-models-pytorch" / "iteration-0"
                / "trainset" / "train")
    snap_dir.mkdir(parents=True)
    (snap_dir / "snapshot-best-180.pt").write_bytes(b"fake snapshot")
    return str((snap_dir / "snapshot-best-180.pt").relative_to(active_project))


# ── 1. the 2 h hard kill ──────────────────────────────────────────────────

class TestSessionTimeLimit:
    def test_time_limit_is_far_above_the_global_two_hours(self):
        """celery_app sets task_time_limit=7200 for every task. This session is
        a multi-hour drain loop, so it must override that or long tag runs die
        mid-queue."""
        from celery_app import celery as _celery_app

        task = dlc_tasks.dlc_inline_session
        assert task.time_limit is not None, (
            "no per-task limit → inherits the global 2 h kill"
        )
        assert task.time_limit > _celery_app.conf.task_time_limit
        assert task.time_limit >= 43200
        assert task.soft_time_limit < task.time_limit


# ── 2. the session hash expiring mid-run ──────────────────────────────────

class TestTouchSession:
    def test_stamps_heartbeat_on_an_existing_hash(self, ia_redis):
        dlc_tasks._publish_status(ia_redis, "u1", "k1", "ready")
        before = _time.time()
        dlc_tasks._touch_session(ia_redis, "u1", "k1")
        beat = float(ia_redis._hstore["inline:session:u1:k1"]["heartbeat"])
        assert beat >= before

    def test_does_not_resurrect_a_missing_hash(self, ia_redis):
        """The bug this guards: recreating the hash here would leave a
        status-less key that session_start cannot classify — exactly the
        `{last_activity}`-only corpse found in production."""
        dlc_tasks._touch_session(ia_redis, "u1", "gone")
        assert "inline:session:u1:gone" not in ia_redis._hstore

    def test_refreshes_the_redis_ttl(self, ia_redis):
        dlc_tasks._publish_status(ia_redis, "u1", "k1", "ready")
        seen = []
        with patch.object(ia_redis, "expire", lambda k, s: seen.append((k, s))):
            dlc_tasks._touch_session(ia_redis, "u1", "k1")
        assert seen == [("inline:session:u1:k1", dlc_tasks._SESSION_HASH_TTL_S)]


class TestSessionHeartbeatThread:
    def test_session_run_leaves_a_heartbeat_behind(self, ia_redis, tmp_path):
        """A run that reaches the drain loop must have beaten at least once,
        so /session/start can tell it from a corpse."""
        runner_factory = MagicMock(return_value=MagicMock())
        with patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
             patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)):
            dlc_tasks._dlc_inline_session_inner(
                ia_redis, user_id="u1", config_path=str(tmp_path / "config.yaml"),
                snap_key="k1", snapshot_path="snap.pt", shuffle=1,
                trainingsetindex=0, batch_size=8, ttl=1,
            )
        h = ia_redis._hstore["inline:session:u1:k1"]
        assert "heartbeat" in h, "no heartbeat → every later start re-dispatches"
        assert h["status"] == "expired"

    def test_heartbeat_thread_stops_when_the_session_ends(self, ia_redis):
        stop, thread = dlc_tasks._start_session_heartbeat(
            ia_redis, "u1", "k1", every=0.01)
        try:
            assert thread.is_alive()
        finally:
            stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive(), "a leaked beat keeps a corpse looking alive"


class TestSessionIsAlive:
    def test_fresh_heartbeat_is_alive(self):
        assert inline_analysis._session_is_alive(
            {"status": "ready", "heartbeat": str(_time.time())}) is True

    def test_stale_heartbeat_is_dead(self):
        stale = _time.time() - inline_analysis._SESSION_STALE_AFTER_S - 1
        assert inline_analysis._session_is_alive(
            {"status": "ready", "heartbeat": str(stale)}) is False

    def test_ready_without_a_heartbeat_is_dead(self):
        """The hard-kill corpse: status frozen at "ready" forever because the
        SIGKILL skipped the exit path."""
        assert inline_analysis._session_is_alive({"status": "ready"}) is False

    def test_warming_without_a_heartbeat_is_alive(self):
        """Dispatched but not yet picked up. The worker log has a task waiting
        2 h in the broker queue behind another job — calling that dead would
        dispatch a duplicate session on every click."""
        assert inline_analysis._session_is_alive({"status": "warming"}) is True

    def test_warming_with_a_stale_heartbeat_is_dead(self):
        """It started warming, beat, then died — e.g. OOM during model load."""
        stale = _time.time() - inline_analysis._SESSION_STALE_AFTER_S - 1
        assert inline_analysis._session_is_alive(
            {"status": "warming", "heartbeat": str(stale)}) is False

    def test_terminal_status_is_dead(self):
        assert inline_analysis._session_is_alive(
            {"status": "expired", "heartbeat": str(_time.time())}) is False


class TestSessionStartRecoversFromADeadWorker:
    def _post(self, client, snapshot_rel):
        return client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": snapshot_rel, "shuffle": 1, "ttl_seconds": 300,
        })

    def test_stale_ready_session_is_replaced(
            self, client, ia_redis, active_project, snapshot_rel, monkeypatch):
        sent = []
        monkeypatch.setattr(inline_analysis, "_celery_send_task",
                            lambda name, **kw: sent.append(name))
        snap_key = inline_analysis._snap_key(
            str(active_project / "config.yaml"), 1,
            str((active_project / snapshot_rel).resolve()))
        ia_redis.hset(f"inline:session:u1:{snap_key}", mapping={
            "status": "ready",
            "heartbeat": str(_time.time() - 999),
            "last_activity": str(_time.time() - 999),
        })

        assert self._post(client, snapshot_rel).status_code == 202
        assert sent == ["tasks.dlc_inline_session"], (
            "a dead session must be re-dispatched — otherwise every later "
            "submit piles onto a queue with no consumer"
        )

    def test_live_session_is_reused(
            self, client, ia_redis, active_project, snapshot_rel, monkeypatch):
        sent = []
        monkeypatch.setattr(inline_analysis, "_celery_send_task",
                            lambda name, **kw: sent.append(name))
        snap_key = inline_analysis._snap_key(
            str(active_project / "config.yaml"), 1,
            str((active_project / snapshot_rel).resolve()))
        ia_redis.hset(f"inline:session:u1:{snap_key}", mapping={
            "status": "ready", "heartbeat": str(_time.time()),
            "last_activity": str(_time.time()),
        })

        assert self._post(client, snapshot_rel).status_code == 202
        assert sent == [], "a beating session must not get a second worker"

    def test_status_reports_a_dead_worker_as_dead(
            self, client, ia_redis, active_project):
        ia_redis.hset("inline:session:u1:k9", mapping={
            "status": "ready", "heartbeat": str(_time.time() - 999),
            "last_activity": str(_time.time() - 999),
        })
        r = client.get("/dlc/project/inline-analysis/session/status?snap_key=k9")
        assert r.get_json()["status"] == "dead"


# ── 3. the peak pass depending on an open tab ─────────────────────────────

class TestPeaksQueuedInSession:
    def _submit(self, client, active_project, snapshot_rel, **extra):
        video = client._data_dir / "vid.mp4"
        video.write_bytes(b"fake video")
        body = {
            "video_paths": [str(video)],
            "ranges": [{"start": 10, "n": 3}],
            "snapshot_path": snapshot_rel,
        }
        body.update(extra)
        return video, client.post(
            "/dlc/project/inline-analysis/peaks", json=body)

    def test_snap_key_queues_at_the_TAIL_of_the_session_queue(
            self, client, ia_redis, active_project, snapshot_rel, monkeypatch):
        """RPUSH, not LPUSH. The queue is drained from the head, so only the
        tail is guaranteed to run after the ranges the caller just submitted."""
        dispatched = []
        monkeypatch.setattr(inline_analysis, "_dispatch_emit_peaks",
                            lambda **kw: dispatched.append(kw))
        qkey = "inline:queue:u1:SK"
        ia_redis.lpush(qkey, json.dumps({"req_id": "range-1"}))

        _video, resp = self._submit(
            client, active_project, snapshot_rel, snap_key="SK")

        assert resp.status_code == 202
        assert resp.get_json()["queued_in_session"] is True
        assert dispatched == [], "must not also fire the standalone task"
        items = [json.loads(x) for x in ia_redis._lists[qkey]]
        assert [i.get("kind") or "range" for i in items] == ["range", "peaks"]
        assert items[1]["frames"] == [10, 11, 12]
        assert items[1]["snapshot_name"] == "snapshot-best-180.pt"

    def test_snap_key_path_needs_no_h5_paths(
            self, client, ia_redis, active_project, snapshot_rel):
        """The worker derives them from the scorer it already holds. Requiring
        them is what forced the card to wait for a range to finish first."""
        _video, resp = self._submit(
            client, active_project, snapshot_rel, snap_key="SK")
        assert resp.status_code == 202

    def test_without_snap_key_h5_paths_are_still_required(
            self, client, active_project, snapshot_rel):
        _video, resp = self._submit(client, active_project, snapshot_rel)
        assert resp.status_code == 400
        assert "h5_paths" in resp.get_json()["error"]


class TestWorkerRunsQueuedPeaks:
    def test_peaks_item_is_run_with_derived_h5_paths(self, ia_redis, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")
        ia_redis.lpush("inline:queue:u1:k1", json.dumps({
            "kind": "peaks", "req_id": "p1",
            "video_paths": [str(video)], "frames": [1, 2],
            "model_dir": "/m", "snapshot_name": "snap.pt",
            "k": 5, "min_distance": 3, "batch_size": 1,
        }))
        seen = {}

        def _fake_emit(redis_, req_id, video_paths, h5_paths, frames,
                       model_dir, snapshot_name, k, min_distance, batch_size=1):
            seen.update(req_id=req_id, h5_paths=h5_paths, frames=frames)
            dlc_tasks._publish_result(redis_, req_id, "done", n_analyzed=len(frames))

        runner_factory = MagicMock(return_value=MagicMock())
        with patch.object(dlc_tasks, "_emit_peaks_inner", _fake_emit), \
             patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
             patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)):
            dlc_tasks._dlc_inline_session_inner(
                ia_redis, user_id="u1", config_path="cfg", snap_key="k1",
                snapshot_path="snap.pt", shuffle=1, trainingsetindex=0,
                batch_size=8, ttl=1,
            )

        # _fake_loader_factory's scorer is "S"; _resolve_h5_path appends it.
        assert seen["h5_paths"] == [str(tmp_path / "vS.h5")]
        assert seen["frames"] == [1, 2]
        assert ia_redis._hstore["inline:result:p1"]["status"] == "done"

    def test_a_failing_peaks_item_does_not_kill_the_session(
            self, ia_redis, tmp_path):
        """Additive means additive: the ranges already on disk are untouched
        and the session keeps draining."""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")
        ia_redis.lpush("inline:queue:u1:k1", json.dumps({
            "kind": "peaks", "req_id": "p1", "video_paths": [str(video)],
            "frames": [1], "model_dir": "/m", "snapshot_name": "snap.pt",
        }))

        def _boom(*a, **kw):
            raise RuntimeError("CUDA out of memory")

        runner_factory = MagicMock(return_value=MagicMock())
        with patch.object(dlc_tasks, "_emit_peaks_inner", _boom), \
             patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
             patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)):
            dlc_tasks._dlc_inline_session_inner(
                ia_redis, user_id="u1", config_path="cfg", snap_key="k1",
                snapshot_path="snap.pt", shuffle=1, trainingsetindex=0,
                batch_size=8, ttl=1,
            )

        assert ia_redis._hstore["inline:result:p1"]["status"] == "error"
        assert "CUDA" in ia_redis._hstore["inline:result:p1"]["error"]
        assert ia_redis._hstore["inline:session:u1:k1"]["status"] == "expired"
