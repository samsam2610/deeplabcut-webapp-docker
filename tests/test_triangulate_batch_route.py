"""HTTP-endpoint tests for the aggregate triangulate-batch route
(dlc/inline_analysis.py :: POST /dlc/project/triangulate/batch).

Redis is the FakeRedis from conftest; we assert the aggregate job hash + the
`dlc_analyze_jobs` zset are written directly on the fake. Mirrors the
ia_client fixture from test_triangulate_range_routes.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["uid"] = "u1"


@pytest.fixture
def ia_client(flask_test_client, dlc_sandbox_project):
    client, app_module, redis, data_dir, user_data_dir = flask_test_client
    redis._store.clear(); redis._hstore.clear(); redis._zsets.clear()
    redis._sets.clear(); redis._lists.clear()
    _auth(client)
    dest = data_dir / dlc_sandbox_project.name
    if not dest.exists():
        shutil.copytree(str(dlc_sandbox_project), str(dest))
    cfg = dest / "config.yaml"
    redis.set("webapp:dlc_project:u1", json.dumps({
        "config_path": str(cfg), "project_path": str(dest), "project": dest.name,
    }))
    yield client, app_module, redis, dest


def _make_cam0(project) -> Path:
    v = project / "videos" / "surv1_cam0_20260123_120000.avi"
    v.parent.mkdir(parents=True, exist_ok=True)
    v.write_bytes(b"")
    return v


class TestTriangulateBatchStart:
    def test_start_writes_hash_and_zadd(self, ia_client):
        client, _app, redis, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-1", "action": "start",
            "total": 5, "video": str(v),
        })
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json() == {"ok": True}
        h = redis._hstore["dlc_analyze_job:batch-1"]
        assert h["task_id"] == "batch-1"
        assert h["operation"] == "triangulate"
        assert h["status"] == "running"
        assert h["total"] == 5
        assert h["done"] == 0
        assert h["stage"] == "0/5"
        assert h["target_path"] == str(v)
        assert h["project"] == v.parent.name
        # zset membership
        assert "batch-1" in redis._zsets["dlc_analyze_jobs"]

    def test_start_without_video_ok(self, ia_client):
        client, _app, redis, _project = ia_client
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-nov", "action": "start", "total": 3,
        })
        assert resp.status_code == 200
        h = redis._hstore["dlc_analyze_job:batch-nov"]
        assert h["project"] == ""
        assert h["target_path"] == ""
        assert h["stage"] == "0/3"


class TestTriangulateBatchProgress:
    def test_progress_updates_done_and_stage(self, ia_client):
        client, _app, redis, project = ia_client
        v = _make_cam0(project)
        client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-2", "action": "start", "total": 5, "video": str(v)})
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-2", "action": "progress",
            "done": 2, "skipped": 1, "stage": "2/5 · Merging… 40%",
        })
        assert resp.status_code == 200
        h = redis._hstore["dlc_analyze_job:batch-2"]
        assert h["done"] == 2
        assert h["skipped"] == 1
        assert h["stage"] == "2/5 · Merging… 40%"
        assert h["status"] == "running"  # unchanged

    def test_progress_ignored_when_hash_missing(self, ia_client):
        client, _app, redis, _project = ia_client
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "ghost", "action": "progress", "done": 1, "stage": "1/2"})
        assert resp.status_code == 200
        assert "dlc_analyze_job:ghost" not in redis._hstore


class TestTriangulateBatchDone:
    def test_done_sets_complete_and_final_stage(self, ia_client):
        client, _app, redis, project = ia_client
        v = _make_cam0(project)
        client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-3", "action": "start", "total": 5, "video": str(v)})
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-3", "action": "done", "stage": "5/5 done · 1 skipped"})
        assert resp.status_code == 200
        h = redis._hstore["dlc_analyze_job:batch-3"]
        assert h["status"] == "complete"
        assert h["stage"] == "5/5 done · 1 skipped"

    def test_done_error_used_as_stage(self, ia_client):
        client, _app, redis, project = ia_client
        v = _make_cam0(project)
        client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-e", "action": "start", "total": 2, "video": str(v)})
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "batch-e", "action": "done", "error": "boom failed"})
        assert resp.status_code == 200
        h = redis._hstore["dlc_analyze_job:batch-e"]
        assert h["status"] == "complete"
        assert h["stage"] == "boom failed"


class TestTriangulateBatchValidation:
    def test_400_missing_batch_id(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/batch", json={
            "action": "start", "total": 1})
        assert resp.status_code == 400

    def test_400_bad_action(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "b", "action": "frobnicate"})
        assert resp.status_code == 400

    def test_403_video_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/batch", json={
            "batch_id": "b", "action": "start", "total": 1,
            "video": "/etc/passwd"})
        assert resp.status_code == 403
