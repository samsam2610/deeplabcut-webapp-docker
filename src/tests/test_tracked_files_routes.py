"""Tests for the dlc_tracked_files blueprint."""
from __future__ import annotations
import json
from pathlib import Path

import pytest


def _activate_project(client, fake_redis, project_path: Path):
    """Seed the Redis project key the way dlc_project would."""
    with client.session_transaction() as sess:
        sess["uid"] = "test-uid"
    fake_redis.set(
        "webapp:dlc_project:test-uid",
        json.dumps({
            "project_path": str(project_path),
            "config_path": str(project_path / "config.yaml"),
            "engine": "pytorch",
        }),
    )


@pytest.fixture
def tracked_project(flask_test_client):
    """A project INSIDE data_dir — _sec_check rejects anything outside it."""
    client, _app, fake_redis, data_dir, _udd = flask_test_client
    proj = data_dir / "TrackedTest-2026-07-31"
    proj.mkdir()
    (proj / "config.yaml").write_text("scorer: TestScorer\n")
    _activate_project(client, fake_redis, proj)
    return client, proj


def test_list_requires_active_project(flask_test_client):
    client = flask_test_client[0]
    rv = client.get("/dlc/project/tracked-files")
    assert rv.status_code == 400
    assert "error" in rv.get_json()


def test_list_is_empty_on_fresh_project(tracked_project):
    client, _proj = tracked_project
    rv = client.get("/dlc/project/tracked-files")
    assert rv.status_code == 200
    assert rv.get_json()["files"] == []


def test_post_then_list_returns_derived_name_and_dir(tracked_project):
    client, _proj = tracked_project
    rv = client.post("/dlc/project/tracked-files",
                     json={"path": "/data/eggtart/day1/eggtart-1_cam0.avi"})
    assert rv.status_code == 200
    assert rv.get_json()["tracked"] is True

    files = client.get("/dlc/project/tracked-files").get_json()["files"]
    assert len(files) == 1
    assert files[0]["path"] == "/data/eggtart/day1/eggtart-1_cam0.avi"
    assert files[0]["name"] == "eggtart-1_cam0.avi"
    assert files[0]["dir"] == "/data/eggtart/day1"
    assert files[0]["last_opened_at"] is None


def test_list_does_not_require_the_file_to_exist(tracked_project):
    """No stat at list time — a path on an unmounted disk still lists."""
    client, _proj = tracked_project
    client.post("/dlc/project/tracked-files", json={"path": "/nowhere/gone.avi"})
    files = client.get("/dlc/project/tracked-files").get_json()["files"]
    assert [f["path"] for f in files] == ["/nowhere/gone.avi"]


def test_delete_removes_the_row(tracked_project):
    client, _proj = tracked_project
    client.post("/dlc/project/tracked-files", json={"path": "/data/a.avi"})
    rv = client.delete("/dlc/project/tracked-files", json={"path": "/data/a.avi"})
    assert rv.status_code == 200
    assert rv.get_json()["tracked"] is False
    assert client.get("/dlc/project/tracked-files").get_json()["files"] == []


def test_rejects_relative_path(tracked_project):
    client, _proj = tracked_project
    rv = client.post("/dlc/project/tracked-files", json={"path": "data/a.avi"})
    assert rv.status_code == 400
    assert client.get("/dlc/project/tracked-files").get_json()["files"] == []


def test_rejects_non_video_extension(tracked_project):
    client, _proj = tracked_project
    rv = client.post("/dlc/project/tracked-files", json={"path": "/data/a.csv"})
    assert rv.status_code == 400
    assert client.get("/dlc/project/tracked-files").get_json()["files"] == []


def test_accepts_every_whitelisted_extension(tracked_project):
    client, _proj = tracked_project
    for ext in (".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"):
        rv = client.post("/dlc/project/tracked-files", json={"path": f"/data/v{ext}"})
        assert rv.status_code == 200, ext
    assert len(client.get("/dlc/project/tracked-files").get_json()["files"]) == 6


def test_opened_stamps_only_an_existing_row(tracked_project):
    client, _proj = tracked_project
    client.post("/dlc/project/tracked-files", json={"path": "/data/a.avi"})

    rv = client.post("/dlc/project/tracked-files/opened", json={"path": "/data/a.avi"})
    assert rv.status_code == 200
    files = client.get("/dlc/project/tracked-files").get_json()["files"]
    assert files[0]["last_opened_at"] is not None

    # An untracked path must not be created by 'opened'.
    client.post("/dlc/project/tracked-files/opened", json={"path": "/data/other.avi"})
    paths = [f["path"] for f in client.get("/dlc/project/tracked-files").get_json()["files"]]
    assert paths == ["/data/a.avi"]
