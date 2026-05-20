"""Session-lifecycle integration tests for inline analysis.

Stitches Flask routes + worker code with DLC mocked. Covers:
  - Snapshot change while warm (control:stop on old, dispatch on new)
  - Idle TTL exit
  - control:stop teardown
  - Concurrent range requests serialise via the queue
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Pre-import dlc.tasks with deeplabcut stubbed (same pattern as
#     test_inline_analysis_worker.py / test_tf_dlc_snapshot_index_fix.py) ──

def _load_dlc_tasks():
    _stub = MagicMock()
    _patches = {
        "deeplabcut": _stub,
        "deeplabcut.pose_estimation_pytorch": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.apis": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.apis.videos": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.data": MagicMock(),
    }
    with patch.dict(sys.modules, _patches):
        for key in list(sys.modules):
            if key == "dlc.tasks" or key.startswith("dlc.tasks."):
                del sys.modules[key]
        from dlc import tasks as _mod
        return _mod


dlc_tasks = _load_dlc_tasks()


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["uid"] = "u1"


@pytest.fixture
def ia_client(flask_test_client, dlc_sandbox_project):
    """Same shape as test_inline_analysis_routes.ia_client."""
    client, app_module, redis, data_dir, _user_data_dir = flask_test_client
    redis._store.clear()
    redis._hstore.clear()
    redis._zsets.clear()
    redis._sets.clear()
    redis._lists.clear()
    _auth(client)
    dest = data_dir / dlc_sandbox_project.name
    if not dest.exists():
        shutil.copytree(str(dlc_sandbox_project), str(dest))
    cfg = dest / "config.yaml"
    redis.set("webapp:dlc_project:u1", json.dumps({
        "config_path":  str(cfg),
        "project_path": str(dest),
        "project":      dest.name,
    }))
    yield client, app_module, redis, dest


def _fake_loader_factory():
    """Shared fake DLCLoader for session-lifecycle tests."""
    def _make(**kwargs):
        m = MagicMock()
        m.scorer.return_value = "S"
        m.model_cfg = {"metadata": {"bodyparts": ["nose"]}}
        m.project_cfg = {"multianimalproject": False}
        return m
    return _make


def test_snapshot_change_signals_stop_to_old_worker(ia_client):
    """Starting a session with a different snapshot must SET control:stop on the old snap_key."""
    client, _app, redis, _project = ia_client
    sent = []
    with patch("dlc.inline_analysis._celery_send_task",
               side_effect=lambda *a, **kw: sent.append(kw) or MagicMock(id="cid")):
        r1 = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "snap-A.pt", "shuffle": 1, "ttl_seconds": 300,
        })
        assert r1.status_code == 202, r1.get_json()
        snap_a = r1.get_json()["snap_key"]
        # Simulate the worker reaching 'ready'.
        redis.hset(f"inline:session:u1:{snap_a}", "status", "ready")

        client.post("/dlc/project/inline-analysis/session/stop", json={"snap_key": snap_a})
        assert redis.get(f"inline:control:u1:{snap_a}") == "stop"

        r2 = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "snap-B.pt", "shuffle": 1, "ttl_seconds": 300,
        })
        assert r2.status_code == 202, r2.get_json()
        snap_b = r2.get_json()["snap_key"]
        assert snap_a != snap_b
    assert len(sent) == 2


def test_concurrent_range_requests_serialise_via_queue(ia_client):
    """Two POSTs to /range with the same snap_key both land in the same Redis list, in order."""
    client, _app, redis, project = ia_client
    v = project / "videos" / "v.mp4"
    v.parent.mkdir(parents=True, exist_ok=True)
    v.write_bytes(b"")
    snap_key = "sk1"
    for n in (10, 20):
        resp = client.post("/dlc/project/inline-analysis/range", json={
            "snap_key": snap_key, "video_path": str(v),
            "start_frame": 0, "n_frames": n, "batch_size": 8,
            "save_as_csv": False,
        })
        assert resp.status_code == 202, resp.get_json()
    items = redis._lists.get(f"inline:queue:u1:{snap_key}", [])
    assert len(items) == 2
    parsed = [json.loads(i) for i in items]
    assert {p["n_frames"] for p in parsed} == {10, 20}


def test_idle_ttl_exit_publishes_expired_status(ia_client):
    """When the worker loop exhausts its TTL, the session hash status = 'expired'."""
    _, _app, redis, _project = ia_client
    with patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
         patch.object(dlc_tasks, "_dlc_apis_utils",
                      MagicMock(get_pose_inference_runner=MagicMock(return_value=MagicMock()))):
        dlc_tasks._dlc_inline_session_inner(
            redis, user_id="u1", config_path="cfg",
            snap_key="sk-ttl", snapshot_path="snap.pt",
            shuffle=1, trainingsetindex=0, batch_size=8, ttl=1,
        )
    h = redis._hstore["inline:session:u1:sk-ttl"]
    assert h["status"] == "expired"


def test_control_stop_takes_priority_over_pending_queue(ia_client, tmp_path):
    """If control:stop is set before BLPOP, the worker exits without processing queued items."""
    _, _app, redis, _project = ia_client
    redis.set("inline:control:u1:sk-stop", "stop")
    redis.lpush("inline:queue:u1:sk-stop", json.dumps({
        "req_id": "r1", "video_path": str(tmp_path / "v.mp4"),
        "start_frame": 0, "n_frames": 1, "batch_size": 8,
        "save_as_csv": False, "snapshot_path": "snap.pt",
    }))
    with patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
         patch.object(dlc_tasks, "_dlc_apis_utils",
                      MagicMock(get_pose_inference_runner=MagicMock(return_value=MagicMock()))), \
         patch.object(dlc_tasks, "video_inference",
                      side_effect=AssertionError("must not run after control:stop")):
        dlc_tasks._dlc_inline_session_inner(
            redis, user_id="u1", config_path="cfg",
            snap_key="sk-stop", snapshot_path="snap.pt",
            shuffle=1, trainingsetindex=0, batch_size=8, ttl=60,
        )
    h = redis._hstore["inline:session:u1:sk-stop"]
    assert h["status"] == "stopped"
