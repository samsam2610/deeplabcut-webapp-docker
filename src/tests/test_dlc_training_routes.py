"""
Tests for DLC training-related Flask routes:
  POST /dlc/project/create-training-dataset
  POST /dlc/project/add-datasets-to-video-list
  GET /dlc/project/pytorch-configs
  GET/PATCH /dlc/project/pytorch-config
  POST /dlc/project/train-network
  POST /dlc/project/train-network/stop
  GET /dlc/project/snapshots
  GET /dlc/training/jobs
  POST /dlc/training/jobs/clear
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_client(data_dir, user_data_dir, fake_redis):
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


def _set_project(fake_redis, uid, project_path, engine="pytorch"):
    fake_redis.set(
        f"webapp:dlc_project:{uid}",
        json.dumps({
            "project_path": str(project_path),
            "project_name": project_path.name,
            "has_config": True,
            "config_path": str(project_path / "config.yaml"),
            "engine": engine,
        }),
    )


class TestCreateTrainingDatasetRoute:
    """POST /dlc/project/create-training-dataset dispatches Celery task."""

    def test_dispatches_task(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        with patch("celery.Celery.send_task") as mock_send:
            mock_result = MagicMock()
            mock_result.id = "fake-task-id-train-dataset"
            mock_send.return_value = mock_result

            client, app_mod = _make_client(data_dir, tmp_path / "user", fake_redis)
            uid = "ctd-uid"
            _set_project(fake_redis, uid, dest)
            with client.session_transaction() as sess:
                sess["uid"] = uid

            # Also patch the Celery task send on the app's celery instance
            with patch.object(app_mod.celery, "send_task", return_value=mock_result):
                resp = client.post(
                    "/dlc/project/create-training-dataset",
                    json={"num_shuffles": 1, "freeze_split": True},
                )

        assert resp.status_code == 202
        body = resp.get_json()
        assert "task_id" in body
        assert body["operation"] == "create_training_dataset"

    def test_no_project_returns_400(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_redis._store.clear()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post(
            "/dlc/project/create-training-dataset",
            json={"num_shuffles": 1},
        )
        assert resp.status_code == 400


class TestListPytorchConfigs:
    """GET /dlc/project/pytorch-configs"""

    def test_lists_pytorch_config_files(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        # Create a fake pytorch_config.yaml
        models_dir = dest / "dlc-models-pytorch" / "iteration-0" / "TestJan7-trainset80shuffle1"
        models_dir.mkdir(parents=True, exist_ok=True)
        (models_dir / "pytorch_config.yaml").write_text(
            "method: bu\nbatch_size: 8\n"
        )

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "list-pt-uid"
        _set_project(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/dlc/project/pytorch-configs")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "configs" in body
        assert len(body["configs"]) >= 1
        assert any("pytorch_config.yaml" in c["config_path"] for c in body["configs"])


class TestGetDlcProjectSnapshots:
    """GET /dlc/project/snapshots"""

    def test_lists_snapshots(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "snapshots-uid"
        _set_project(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/dlc/project/snapshots")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "snapshots" in body
        assert "engine" in body
        assert isinstance(body["snapshots"], list)

    def test_no_project_returns_400(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_redis._store.clear()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.get("/dlc/project/snapshots")
        assert resp.status_code == 400


class TestTrainNetworkStop:
    """POST /dlc/project/train-network/stop"""

    def test_sets_stop_flag_in_redis(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, app_mod = _make_client(data_dir, tmp_path / "user", fake_redis)

        task_id = "fake-train-task-001"
        # Pre-seed a job entry
        fake_redis._hstore[f"dlc_train_job:{task_id}"] = {
            "task_id": task_id, "status": "running"
        }

        # celery.control.revoke requires a live broker — patch it out
        with patch.object(app_mod.celery.control, "revoke", return_value=None):
            resp = client.post(
                "/dlc/project/train-network/stop",
                json={"task_id": task_id},
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "stop_requested"
        assert fake_redis.get(f"dlc_train_stop:{task_id}") == "1"


class TestDlcTrainingJobs:
    """GET /dlc/training/jobs and POST /dlc/training/jobs/clear"""

    def test_returns_job_list(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.get("/dlc/training/jobs")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "jobs" in body
        assert isinstance(body["jobs"], list)

    def test_clear_jobs_returns_removed_count(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post("/dlc/training/jobs/clear")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "removed" in body
        assert isinstance(body["removed"], int)


class TestDlcGpuStatus:
    """GET /dlc/gpu/status"""

    def test_returns_gpu_availability(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Pre-seed GPU stats cache
        fake_redis.set(
            "dlc_gpu_stats",
            "index, name, utilization.gpu [%], memory.used [MiB], memory.total [MiB], temperature.gpu\n"
            "0, NVIDIA GeForce RTX 5090, 0 %, 512 MiB, 32768 MiB, 45\n",
        )
        fake_redis.set("dlc_gpu_stats_ts", "1700000000.0")

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.get("/dlc/gpu/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "gpus" in body
        assert "available" in body
