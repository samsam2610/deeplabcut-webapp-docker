"""
Tests for DLC config management Flask routes:
  POST/GET/PATCH/DELETE /session/dlc-config
  POST /session/dlc-config/from-path
  GET /dlc/project/engine
  GET/PATCH /dlc/project/pytorch-config(s)
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_client(data_dir: Path, user_data_dir: Path, fake_redis):
    import importlib
    env = {
        "DATA_DIR": str(data_dir),
        "USER_DATA_DIR": str(user_data_dir),
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "testkey1234567890abcdef12345678",
    }
    with patch.dict(os.environ, env):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import app as app_mod
            importlib.reload(app_mod)
            app_mod.DATA_DIR = data_dir
            app_mod.USER_DATA_DIR = user_data_dir
            app_mod._redis_client = fake_redis
            app_mod.app.config["TESTING"] = True
            app_mod.app.config["SECRET_KEY"] = "testkey"
            return app_mod.app.test_client(), app_mod


class TestSessionDlcConfig:
    """Tests for /session/dlc-config routes."""

    def test_upload_dlc_config_yaml(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        fake_redis._store.clear()
        # Setup a fake session
        uid = "upload-config-uid"
        fake_redis.set(f"webapp:session:{uid}", json.dumps({}))
        with client.session_transaction() as sess:
            sess["uid"] = uid

        yaml_content = b"Task: Test\nscorer: Sam\nproject_path: /tmp/test\n"
        resp = client.post(
            "/session/dlc-config",
            data={"config": (io.BytesIO(yaml_content), "config.yaml")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert "dlc_config_path" in body
        assert body["dlc_config_path"].endswith("config.yaml")

    def test_get_dlc_config_returns_content(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        config_path = str(dest / "config.yaml")

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "get-config-uid"
        session_data = {
            "dlc_config_path": config_path,
            "dlc_config_name": "config.yaml",
        }
        fake_redis.set(f"webapp:session:{uid}", json.dumps(session_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/session/dlc-config")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "content" in body
        assert "Task:" in body["content"]

    def test_save_dlc_config(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        config_path = str(dest / "config.yaml")

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "save-config-uid"
        session_data = {
            "dlc_config_path": config_path,
            "dlc_config_name": "config.yaml",
        }
        fake_redis.set(f"webapp:session:{uid}", json.dumps(session_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid

        new_yaml = "Task: NewTask\nscorer: NewScorer\nproject_path: /updated\n"
        resp = client.patch("/session/dlc-config", json={"content": new_yaml})
        assert resp.status_code == 200
        assert Path(config_path).read_text() == new_yaml

    def test_clear_dlc_config(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "clear-config-uid"
        session_data = {"dlc_config_path": "/some/config.yaml"}
        fake_redis.set(f"webapp:session:{uid}", json.dumps(session_data))
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.delete("/session/dlc-config")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "cleared"

    def test_load_from_path(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        config_path = str(dest / "config.yaml")

        # Create a session dir (the route copies config into session_data["config_path"] parent)
        session_dir = data_dir / "session_001"
        session_dir.mkdir()
        session_file = session_dir / "config.toml"
        session_file.write_text("[pipeline]\ntype = dlc\n")

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "frompath-uid"
        # Session must have config_path so session_dir resolves correctly
        fake_redis.set(
            f"webapp:session:{uid}",
            json.dumps({"config_path": str(session_file)}),
        )
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.post("/session/dlc-config/from-path", json={"config_path": config_path})
        assert resp.status_code == 201
        body = resp.get_json()
        # Route copies to session_dir/config.yaml - path ends with config.yaml
        assert body["dlc_config_path"].endswith("config.yaml")

    def test_load_from_path_outside_data_dir_denied(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        outside = tmp_path / "outside" / "config.yaml"
        outside.parent.mkdir()
        outside.write_text("Task: Test\n")

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post("/session/dlc-config/from-path", json={"config_path": str(outside)})
        assert resp.status_code in (403, 400)


class TestGetDlcProjectEngine:
    """GET /dlc/project/engine"""

    def test_returns_pytorch_engine(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        # Overwrite with a clean minimal config — sandbox config.yaml has a
        # multi-line video-path entry that breaks yaml.safe_load.
        (dest / "config.yaml").write_text(
            f"Task: TestTask\nscorer: TestScorer\nproject_path: {dest}\nengine: pytorch\n"
        )

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "engine-uid"
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

        resp = client.get("/dlc/project/engine")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["engine"] in ("pytorch", "tensorflow")
