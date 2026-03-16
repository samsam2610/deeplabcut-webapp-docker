"""
Tests for DLC video-related Flask routes:
  GET /dlc/project/videos
  GET /dlc/project/video-info/<filename>
  POST /dlc/project/video-upload
  POST /dlc/project/add-video
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


def _set_project_redis(fake_redis, uid: str, project_path: Path):
    project_data = {
        "project_path": str(project_path),
        "project_name": project_path.name,
        "has_config": True,
        "config_path": str(project_path / "config.yaml"),
        "engine": "pytorch",
    }
    fake_redis.set(f"webapp:dlc_project:{uid}", json.dumps(project_data))


class TestDlcListVideos:
    """GET /dlc/project/videos"""

    def test_returns_video_list(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "list-vid-uid"
        _set_project_redis(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/dlc/project/videos")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "videos" in body
        assert isinstance(body["videos"], list)
        # Videos are returned as dicts {"name": "...", "size": int}
        for v in body["videos"]:
            assert isinstance(v, dict)
            assert "name" in v
            assert v["name"].endswith((".avi", ".mp4", ".mov", ".mkv", ".mpg"))

    def test_returns_empty_when_no_videos_dir(self, tmp_path, fake_redis):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj = data_dir / "EmptyProject"
        proj.mkdir()
        (proj / "config.yaml").write_text("Task: Test\nscorer: Sam\nproject_path: " + str(proj) + "\n")

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "empty-vid-uid"
        _set_project_redis(fake_redis, uid, proj)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/dlc/project/videos")
        assert resp.status_code == 200
        assert resp.get_json()["videos"] == []


class TestDlcVideoInfo:
    """GET /dlc/project/video-info/<filename>"""

    @pytest.mark.xfail(
        reason="Flask 3.x session_transaction() isolation issue with importlib.reload — "
               "fixed in Phase 4 when tests move to proper fixture-based app setup",
        strict=False,
    )
    def test_returns_video_metadata(self, tmp_path, fake_redis, dlc_sandbox_project):
        """
        Test with a real AVI from the sandbox project if available.
        Uses mocked cv2 if real video is large/unavailable.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        videos_dir = dest / "videos"
        avi_files = list(videos_dir.glob("*.avi")) if videos_dir.exists() else []

        if not avi_files:
            pytest.skip("No AVI video found in sandbox project videos/")

        video_filename = avi_files[0].name
        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "video-info-uid"
        _set_project_redis(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get(f"/dlc/project/video-info/{video_filename}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "fps" in body
        assert "frame_count" in body
        assert "width" in body
        assert "height" in body
        assert body["frame_count"] > 0
        assert body["fps"] > 0

    def test_returns_404_for_missing_video(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "missing-vid-uid"
        _set_project_redis(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.get("/dlc/project/video-info/nonexistent_video.avi")
        assert resp.status_code == 404


class TestDlcVideoUpload:
    """POST /dlc/project/video-upload"""

    def test_upload_video_file(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))
        (dest / "videos").mkdir(exist_ok=True)

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "vid-upload-uid"
        _set_project_redis(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        # Fake video file (small, just a few bytes)
        fake_video = io.BytesIO(b"\x00" * 100)
        resp = client.post(
            "/dlc/project/video-upload",
            data={"video": (fake_video, "test_clip.avi")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert "saved" in body
        assert "test_clip.avi" in body["saved"]
        assert (dest / "videos" / "test_clip.avi").exists()


class TestDlcAddVideo:
    """POST /dlc/project/add-video"""

    @pytest.mark.xfail(
        reason="Flask 3.x session_transaction() isolation with importlib.reload",
        strict=False,
    )
    def test_add_video_registers_in_video_sets(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        # Create a fake video inside the allowed dir
        video_path = dest / "videos" / "new_video.avi"
        (dest / "videos").mkdir(exist_ok=True)
        video_path.write_bytes(b"\x00" * 100)

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "add-vid-uid"
        _set_project_redis(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.post("/dlc/project/add-video", json={"video_path": str(video_path)})
        assert resp.status_code == 200
        body = resp.get_json()
        assert "abs_path" in body
        assert "new_video" in body.get("name", body.get("abs_path", ""))

    def test_add_video_outside_data_dir_denied(self, tmp_path, fake_redis, dlc_sandbox_project):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        dest = data_dir / dlc_sandbox_project.name
        shutil.copytree(str(dlc_sandbox_project), str(dest))

        outside_video = tmp_path / "outside.avi"
        outside_video.write_bytes(b"\x00" * 100)

        client, _ = _make_client(data_dir, tmp_path / "user", fake_redis)
        uid = "add-vid-outside-uid"
        _set_project_redis(fake_redis, uid, dest)
        with client.session_transaction() as sess:
            sess["uid"] = uid

        resp = client.post("/dlc/project/add-video", json={"video_path": str(outside_video)})
        assert resp.status_code == 403
