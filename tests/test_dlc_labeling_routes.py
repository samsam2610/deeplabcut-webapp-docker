"""
Tests for DLC labeling Flask routes — POST /dlc/project/labels/convert-to-h5.

Key regression: convert-to-h5 must always dispatch to the "celery" queue,
never to an engine-specific queue, because the task is pure pandas (no GPU
or ML framework required). If this queue ever changes back to _get_engine_queue,
TF-engine projects break whenever worker-tf is not running.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_client(data_dir, user_data_dir, fake_redis):
    env = {
        "DATA_DIR": str(data_dir),
        "USER_DATA_DIR": str(user_data_dir),
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "testkey1234567890abcdef12345678",
        "AUTH_DISABLED": "true",
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


def _make_project(root: Path, engine: str = "pytorch", scorer: str = "TestScorer") -> Path:
    """Write a minimal DLC project with one labeled stem and a CollectedData CSV."""
    import pandas as pd
    import numpy as np

    project_dir = root / f"proj_{engine}"
    labeled_dir = project_dir / "labeled-data" / "stem_001"
    labeled_dir.mkdir(parents=True)
    (project_dir / "videos").mkdir()

    bodyparts = ["Snout", "Wrist"]
    frames = ["labeled-data/stem_001/img0001.png"]
    cols = pd.MultiIndex.from_tuples(
        [(scorer, bp, c) for bp in bodyparts for c in ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    df = pd.DataFrame(np.random.rand(1, len(cols)), index=frames, columns=cols)
    df.to_csv(str(labeled_dir / f"CollectedData_{scorer}.csv"))

    videos_dir = project_dir / "videos"
    config = (
        f"Task: Test\nscorer: {scorer}\nproject_path: {project_dir}\n"
        f"date: Jan2026\nengine: {engine}\n"
        f"TrainingFraction:\n- 0.8\nbodyparts:\n- Snout\n- Wrist\n"
        f"video_sets:\n  {videos_dir}/stem_001.mp4:\n    crop: 0, 640, 0, 480\n"
    )
    (project_dir / "config.yaml").write_text(config)
    return project_dir


class TestConvertToH5Route:
    """POST /dlc/project/labels/convert-to-h5 — queue routing regression tests."""

    def test_dispatches_to_celery_queue_pytorch_project(self, tmp_path, fake_redis):
        """PyTorch project → task sent to 'celery' queue."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project_dir = _make_project(data_dir, engine="pytorch")

        mock_result = MagicMock()
        mock_result.id = "task-id-pt"

        client, app_mod = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "pt-uid"
        _set_project(fake_redis, uid, project_dir, engine="pytorch")
        with client.session_transaction() as sess:
            sess["uid"] = uid

        with patch.object(app_mod.celery, "send_task", return_value=mock_result) as mock_send:
            resp = client.post("/dlc/project/labels/convert-to-h5")

        assert resp.status_code == 202
        assert mock_send.called
        _, kwargs = mock_send.call_args
        assert kwargs.get("queue") == "celery", (
            f"Expected queue='celery', got queue={kwargs.get('queue')!r}"
        )

    def test_dispatches_to_celery_queue_tensorflow_project(self, tmp_path, fake_redis):
        """
        TF project → task MUST still go to 'celery' queue, not 'tensorflow'.

        Regression: previously used _get_engine_queue(engine) which returned
        'tensorflow' for TF projects, causing tasks to queue forever when
        worker-tf was not running.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project_dir = _make_project(data_dir, engine="tensorflow")

        mock_result = MagicMock()
        mock_result.id = "task-id-tf"

        client, app_mod = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "tf-uid"
        _set_project(fake_redis, uid, project_dir, engine="tensorflow")
        with client.session_transaction() as sess:
            sess["uid"] = uid

        with patch.object(app_mod.celery, "send_task", return_value=mock_result) as mock_send:
            resp = client.post("/dlc/project/labels/convert-to-h5")

        assert resp.status_code == 202
        assert mock_send.called
        _, kwargs = mock_send.call_args
        assert kwargs.get("queue") == "celery", (
            f"TF project must use 'celery' queue (engine-agnostic task), "
            f"got queue={kwargs.get('queue')!r}"
        )

    def test_returns_task_id_and_operation(self, tmp_path, fake_redis):
        """Response body contains task_id and operation name."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project_dir = _make_project(data_dir, engine="pytorch")

        mock_result = MagicMock()
        mock_result.id = "task-abc-123"

        client, app_mod = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "ret-uid"
        _set_project(fake_redis, uid, project_dir, engine="pytorch")
        with client.session_transaction() as sess:
            sess["uid"] = uid

        with patch.object(app_mod.celery, "send_task", return_value=mock_result):
            resp = client.post("/dlc/project/labels/convert-to-h5")

        body = resp.get_json()
        assert body["task_id"] == "task-abc-123"
        assert body["operation"] == "convert_labels_to_h5"

    def test_no_project_returns_400(self, tmp_path, fake_redis):
        """Route returns 400 when no project is active in session."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_redis._store.clear()
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        resp = client.post("/dlc/project/labels/convert-to-h5")
        assert resp.status_code == 400
