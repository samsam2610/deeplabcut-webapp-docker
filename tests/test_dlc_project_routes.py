"""
Tests for DLC project management Flask routes:
  GET/POST/DELETE /dlc/project
  GET /dlc/project/browse
  POST /dlc/project/upload
  DELETE/PATCH /dlc/project/file
  GET /dlc/project/download

All tests run against the UNMODIFIED monolithic app.py.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(scope="module", autouse=True)
def patch_redis_globally():
    """Patch Redis at module import time."""
    with patch("redis.Redis.from_url", return_value=MagicMock()):
        yield


def _make_client(data_dir: Path, user_data_dir: Path, fake_redis):
    """Helper to construct a fresh Flask test client with controlled env."""
    env = {
        "DATA_DIR": str(data_dir),
        "USER_DATA_DIR": str(user_data_dir),
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "testkey1234567890abcdef12345678",
    }
    with patch.dict(os.environ, env):
        import importlib
        import app as app_mod
        importlib.reload(app_mod)
        app_mod.DATA_DIR = data_dir
        app_mod.USER_DATA_DIR = user_data_dir
        app_mod._redis_client = fake_redis
        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "testkey"
        return app_mod.app.test_client(), app_mod


class TestGetDlcProject:
    """GET /dlc/project"""

    def test_returns_none_when_no_project_set(self, tmp_path, fake_redis):
        fake_redis._store.clear()
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        with client.session_transaction() as sess:
            sess["uid"] = "test-uid-001"
        resp = client.get("/dlc/project")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("status") == "none"

    def test_returns_project_data_when_set(self, tmp_path, fake_redis):
        fake_redis._store.clear()
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        uid = "test-uid-002"
        project_data = {"project_path": "/some/path", "project_name": "test"}
        fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps(project_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid
        resp = client.get("/dlc/project")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["project_name"] == "test"


class TestSetDlcProject:
    """POST /dlc/project"""

    def test_set_project_success(self, tmp_path, fake_redis):
        fake_redis._store.clear()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project_dir = data_dir / "DREADD-Test"
        project_dir.mkdir()
        (project_dir / "config.yaml").write_text(
            "project_path: /tmp/old_path\nTask: Test\nscorer: Sam\nengine: pytorch\n"
        )
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        with client.session_transaction() as sess:
            sess["uid"] = "set-uid-001"
        resp = client.post(
            "/dlc/project",
            json={"path": str(project_dir)},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["project_name"] == "DREADD-Test"
        assert body["has_config"] is True
        assert body["engine"] == "pytorch"

    def test_set_project_empty_path_returns_400(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post("/dlc/project", json={"path": ""})
        assert resp.status_code == 400

    def test_set_project_outside_data_dir_returns_403(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post("/dlc/project", json={"path": str(outside_dir)})
        assert resp.status_code == 403

    def test_set_project_nonexistent_returns_404(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post(
            "/dlc/project",
            json={"path": str(data_dir / "nonexistent_project")},
        )
        assert resp.status_code == 404

    def test_set_project_patches_stale_project_path(self, tmp_path, fake_redis):
        """
        If config.yaml has a stale project_path, set_dlc_project must update it
        to the actual directory location (Path Integrity Constraint #6).
        """
        fake_redis._store.clear()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project_dir = data_dir / "MyDLCProject"
        project_dir.mkdir()
        stale_path = "/stale/old/path/MyDLCProject"
        (project_dir / "config.yaml").write_text(
            f"project_path: {stale_path}\nTask: Test\nscorer: Sam\n"
        )
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        with client.session_transaction() as sess:
            sess["uid"] = "stale-path-uid"
        client.post("/dlc/project", json={"path": str(project_dir)})
        updated_text = (project_dir / "config.yaml").read_text()
        assert stale_path not in updated_text
        assert str(project_dir) in updated_text


class TestClearDlcProject:
    """DELETE /dlc/project"""

    def test_clear_removes_redis_key(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "clear-uid-001"
        fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps({"project_path": "/x"}))
        with client.session_transaction() as sess:
            sess["uid"] = uid
        resp = client.delete("/dlc/project")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "cleared"
        assert fake_redis.get(f"webapp:dlc_project:{uid}") is None


class TestBrowseDlcProject:
    """GET /dlc/project/browse"""

    def test_browse_returns_pipeline_folders(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Copy sandbox into data_dir so security check passes
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "browse-uid-001"
        project_data = {
            "project_path": str(dest),
            "project_name": dest.name,
            "has_config": True,
            "config_path": str(dest / "config.yaml"),
            "engine": "pytorch",
        }
        fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps(project_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/dlc/project/browse")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "folders" in body
        folder_names = [f["folder"] for f in body["folders"]]
        assert "labeled-data" in folder_names
        assert "videos" in folder_names

    def test_browse_no_project_returns_400(self, tmp_path, fake_redis):
        fake_redis._store.clear()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.get("/dlc/project/browse")
        assert resp.status_code == 400


class TestDlcProjectConfig:
    """GET/PATCH /dlc/project/config"""

    def test_get_config_returns_yaml_content(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "cfg-get-uid"
        project_data = {
            "project_path": str(dest),
            "project_name": dest.name,
            "has_config": True,
            "config_path": str(dest / "config.yaml"),
            "engine": "pytorch",
        }
        fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps(project_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid
        resp = client.get("/dlc/project/config")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "content" in body
        assert "Task:" in body["content"]

    def test_patch_config_writes_content(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "cfg-patch-uid"
        project_data = {
            "project_path": str(dest),
            "project_name": dest.name,
            "has_config": True,
            "config_path": str(dest / "config.yaml"),
            "engine": "pytorch",
        }
        fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps(project_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid
        new_content = "Task: UpdatedTask\nscorer: TestScorer\nproject_path: " + str(dest) + "\n"
        resp = client.patch("/dlc/project/config", json={"content": new_content})
        assert resp.status_code == 200
        assert (dest / "config.yaml").read_text() == new_content


class TestDlcProjectUpload:
    """POST /dlc/project/upload"""

    def test_upload_file_to_labeled_data(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "upload-uid"
        project_data = {
            "project_path": str(dest),
            "project_name": dest.name,
            "has_config": True,
            "config_path": str(dest / "config.yaml"),
            "engine": "pytorch",
        }
        fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps(project_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid

        dummy_file = (io.BytesIO(b"fake_data"), "test_upload.csv")
        resp = client.post(
            "/dlc/project/upload",
            data={"folder": "labeled-data", "files[]": dummy_file},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert "test_upload.csv" in body.get("saved", [])
