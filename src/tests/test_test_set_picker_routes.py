"""Tests for the dlc_test_set_picker blueprint."""
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
def picker_project(tmp_path):
    """A minimal DLC project skeleton sufficient for picker route tests."""
    proj = tmp_path / "PickerTest-2026-05-19"
    (proj / "labeled-data" / "vid_a").mkdir(parents=True)
    (proj / "labeled-data" / "vid_a" / "img0001.png").write_bytes(b"\x89PNG\r\n")
    (proj / "labeled-data" / "vid_a" / "img0002.png").write_bytes(b"\x89PNG\r\n")
    # minimal config.yaml so _get_dlc_project_and_config doesn't 404
    (proj / "config.yaml").write_text(
        "scorer: TestScorer\nproject_path: " + str(proj) + "\nbodyparts:\n  - nose\n"
    )
    return proj


# ── Helpers to unpack flask_test_client tuple ──────────────────────────────────
# conftest.flask_test_client yields (client, app_module, fake_redis, data_dir, user_data_dir)

def _client(flask_test_client):
    return flask_test_client[0]

def _redis(flask_test_client):
    return flask_test_client[2]


def test_get_marks_empty(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    rv = client.get("/dlc/project/test-set/marks")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["mode"] == "random"
    assert body["marks"] == {}
    assert body["counts"]["marked"] == 0


def test_post_mark_then_get(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    rv = client.post(
        "/dlc/project/test-set/marks/vid_a/img0001.png",
        json={"marked": True},
    )
    assert rv.status_code == 200
    assert rv.get_json()["marked"] is True

    rv2 = client.get("/dlc/project/test-set/marks")
    body = rv2.get_json()
    assert body["marks"] == {"vid_a": ["img0001.png"]}
    assert body["counts"]["marked"] == 1


def test_post_unmark(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    client.post("/dlc/project/test-set/marks/vid_a/img0001.png", json={"marked": True})
    client.post("/dlc/project/test-set/marks/vid_a/img0001.png", json={"marked": False})
    rv = client.get("/dlc/project/test-set/marks")
    assert rv.get_json()["marks"] == {}


def test_bulk_set(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    rv = client.post("/dlc/project/test-set/marks/bulk", json={"ops": [
        {"video_stem": "vid_a", "image_name": "img0001.png", "marked": True},
        {"video_stem": "vid_a", "image_name": "img0002.png", "marked": True},
    ]})
    assert rv.status_code == 200
    assert rv.get_json()["applied"] == 2


def test_clean_stale(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    # Mark a real frame + a frame that doesn't exist on disk
    client.post("/dlc/project/test-set/marks/bulk", json={"ops": [
        {"video_stem": "vid_a", "image_name": "img0001.png", "marked": True},
        {"video_stem": "vid_a", "image_name": "img_gone.png", "marked": True},
    ]})
    rv = client.post("/dlc/project/test-set/marks/clean-stale")
    assert rv.status_code == 200
    assert rv.get_json()["removed"] == 1


def test_set_and_get_mode(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    rv = client.post("/dlc/project/test-set/mode", json={"mode": "hybrid"})
    assert rv.status_code == 200
    rv2 = client.get("/dlc/project/test-set/marks")
    assert rv2.get_json()["mode"] == "hybrid"


def test_set_mode_rejects_unknown(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    rv = client.post("/dlc/project/test-set/mode", json={"mode": "bogus"})
    assert rv.status_code == 400


def test_path_traversal_blocked(flask_test_client, picker_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate_project(client, fake_redis, picker_project)
    rv = client.post(
        "/dlc/project/test-set/marks/..%2F..%2Fetc/passwd",
        json={"marked": True},
    )
    assert rv.status_code in (400, 404)


def test_no_active_project_returns_400(flask_test_client):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    # Ensure no project is set for this user
    fake_redis.delete("webapp:dlc_project:test-uid")
    with client.session_transaction() as sess:
        sess.pop("uid", None)
    rv = client.get("/dlc/project/test-set/marks")
    assert rv.status_code == 400
