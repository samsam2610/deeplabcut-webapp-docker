"""HTTP-endpoint tests for the inline-analysis blueprint.

Celery is mocked (we capture .send_task calls). Redis is FakeRedis.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _auth(client):
    """Mark the test session authenticated and pin the user id."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["uid"] = "u1"


def _snap_key(config_path, shuffle, snapshot_path):
    raw = f"{config_path}|{int(shuffle)}|{snapshot_path}".encode()
    return hashlib.sha1(raw).hexdigest()


@pytest.fixture
def ia_client(flask_test_client, dlc_sandbox_project):
    """Test client with an active DLC project set in Redis."""
    client, app_module, redis, data_dir, user_data_dir = flask_test_client
    # Reset FakeRedis state to avoid leakage between tests.
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
    redis.set(
        "webapp:dlc_project:u1",
        json.dumps({
            "config_path":  str(cfg),
            "project_path": str(dest),
            "project":      dest.name,
        }),
    )
    yield client, app_module, redis, dest


class TestSessionStart:
    def test_dispatches_celery_task_with_snap_key(self, ia_client):
        client, _app, redis, project = ia_client
        sent = []
        with patch("dlc.inline_analysis._celery_send_task",
                   side_effect=lambda *a, **kw: sent.append((a, kw)) or MagicMock(id="celery-id")):
            resp = client.post("/dlc/project/inline-analysis/session/start", json={
                "snapshot_path": "snap-200000.pt",
                "shuffle":       1,
                "ttl_seconds":   300,
            })
        assert resp.status_code == 202, resp.get_json()
        data = resp.get_json()
        assert data["snap_key"] == _snap_key(
            str(project / "config.yaml"), 1, "snap-200000.pt",
        )
        assert data["status"] in {"warming", "ready"}
        assert sent and sent[0][1]["kwargs"]["snap_key"] == data["snap_key"]

    def test_400_when_no_active_project(self, flask_test_client):
        client, _app, redis, _d, _u = flask_test_client
        redis._store.clear()
        _auth(client)
        resp = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "x", "shuffle": 1, "ttl_seconds": 300,
        })
        assert resp.status_code == 400

    def test_409_when_project_is_multianimal(self, ia_client):
        client, _app, _redis, project = ia_client
        cfg = project / "config.yaml"
        import yaml
        data = yaml.safe_load(cfg.read_text()) or {}
        data["multianimalproject"] = True
        cfg.write_text(yaml.safe_dump(data))
        resp = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "s", "shuffle": 1, "ttl_seconds": 300,
        })
        assert resp.status_code == 409
        assert "single-animal" in resp.get_json()["error"]

    def test_409_when_engine_is_tensorflow(self, ia_client):
        client, _app, _redis, project = ia_client
        cfg = project / "config.yaml"
        import yaml
        data = yaml.safe_load(cfg.read_text()) or {}
        data["engine"] = "tensorflow"
        cfg.write_text(yaml.safe_dump(data))
        resp = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "s", "shuffle": 1, "ttl_seconds": 300,
        })
        assert resp.status_code == 409
        assert "PyTorch" in resp.get_json()["error"]


class TestSessionStatus:
    def test_returns_warming_when_hash_says_so(self, ia_client):
        client, _app, redis, _ = ia_client
        sk = "abc123"
        redis.hset(f"inline:session:u1:{sk}", mapping={
            "status": "warming", "last_activity": "0",
        })
        resp = client.get(f"/dlc/project/inline-analysis/session/status?snap_key={sk}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "warming"
        assert "idle_remaining_s" in body

    def test_absent_when_hash_missing(self, ia_client):
        client, _app, _redis, _ = ia_client
        resp = client.get("/dlc/project/inline-analysis/session/status?snap_key=zzz")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "absent"


class TestSessionStop:
    def test_sets_control_key_to_stop(self, ia_client):
        client, _app, redis, _ = ia_client
        resp = client.post("/dlc/project/inline-analysis/session/stop", json={"snap_key": "k1"})
        assert resp.status_code == 204
        assert redis.get("inline:control:u1:k1") == "stop"


class TestRangeSubmit:
    def test_pushes_to_queue_and_returns_req_id(self, ia_client):
        client, _app, redis, project = ia_client
        v = project / "videos" / "fake.mp4"
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_bytes(b"")
        resp = client.post("/dlc/project/inline-analysis/range", json={
            "snap_key": "k1",
            "video_path": str(v),
            "start_frame": 0, "n_frames": 10, "batch_size": 8,
            "save_as_csv": True,
        })
        assert resp.status_code == 202, resp.get_json()
        rid = resp.get_json()["req_id"]
        items = redis._lists.get("inline:queue:u1:k1", [])
        assert len(items) == 1
        payload = json.loads(items[0])
        assert payload["req_id"] == rid
        assert payload["video_path"] == str(v)
        assert payload["start_frame"] == 0
        assert payload["n_frames"] == 10

    def test_403_on_path_outside_data_root(self, ia_client):
        client, _app, _r, _project = ia_client
        resp = client.post("/dlc/project/inline-analysis/range", json={
            "snap_key": "k1",
            "video_path": "/etc/passwd",
            "start_frame": 0, "n_frames": 1, "batch_size": 8,
            "save_as_csv": False,
        })
        # Either 400 (not a file) or 403 (outside data root) is fine.
        assert resp.status_code in (400, 403)


class TestRangeStatus:
    def test_returns_done_with_counts(self, ia_client):
        client, _app, redis, _ = ia_client
        redis.hset("inline:result:r1", mapping={
            "status": "done", "n_analyzed": "42", "n_skipped": "8", "error": "",
            "scorer": "DLC_resnet50_DREADD-Alishuffle1_snapshot-200000",
        })
        resp = client.get("/dlc/project/inline-analysis/range/status?req_id=r1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "done"
        assert body["n_analyzed"] == 42
        assert body["n_skipped"] == 8
        assert body["scorer"] == "DLC_resnet50_DREADD-Alishuffle1_snapshot-200000", (
            "polish spec §1.4: /range/status done payload must include scorer "
            "so the JS can construct the canonical h5 path"
        )

    def test_returns_pending_when_no_hash_yet(self, ia_client):
        client, _app, _r, _ = ia_client
        resp = client.get("/dlc/project/inline-analysis/range/status?req_id=missing")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "pending"


class TestVideoInfo:
    def test_returns_basic_metadata(self, ia_client):
        client, _app, _r, project = ia_client
        v = project / "videos" / "vinfo.mp4"
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_bytes(b"")
        with patch("dlc.inline_analysis._probe_video",
                   return_value={"nframes": 1000, "fps": 30.0, "width": 640, "height": 480}):
            resp = client.get(f"/dlc/project/inline-analysis/video-info?path={v}")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["nframes"] == 1000
        assert body["fps"] == 30.0
        assert "has_h5_at_snapshot" in body
