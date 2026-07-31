"""The three quick-tag keys must be accepted by the /dlc/project/ui-setting allow-list."""
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
def tag_client(flask_test_client, dlc_sandbox_project):
    client, app_module, redis, data_dir, _user_data_dir = flask_test_client
    redis._store.clear(); redis._hstore.clear()
    redis._zsets.clear(); redis._sets.clear(); redis._lists.clear()
    _auth(client)
    dest = data_dir / dlc_sandbox_project.name
    if not dest.exists():
        shutil.copytree(str(dlc_sandbox_project), str(dest))
    cfg = dest / "config.yaml"
    redis.set(
        "webapp:dlc_project:u1",
        json.dumps({"config_path": str(cfg), "project_path": str(dest), "project": dest.name}),
    )
    yield client


@pytest.mark.parametrize("key", ["postfix_tags", "status_tags", "note_tags"])
def test_tag_keys_round_trip(tag_client, key):
    value = json.dumps(["reach", "good", "retry"])
    post = tag_client.post("/dlc/project/ui-setting", json={"key": key, "value": value})
    assert post.status_code == 200, post.get_json()
    got = tag_client.get(f"/dlc/project/ui-setting?key={key}")
    assert got.status_code == 200
    assert got.get_json()["value"] == value


def test_unknown_tag_key_still_rejected(tag_client):
    resp = tag_client.post("/dlc/project/ui-setting", json={"key": "bogus_tags", "value": "[]"})
    assert resp.status_code == 400


def test_pose3d_bg_color_key_allowed():
    from dlc import inline_analysis
    assert "pose3d_bg_color" in inline_analysis._UI_SETTING_KEYS


def test_pose3d_bg_color_round_trip(tag_client):
    value = "#101820"
    post = tag_client.post("/dlc/project/ui-setting", json={"key": "pose3d_bg_color", "value": value})
    assert post.status_code == 200, post.get_json()
    got = tag_client.get("/dlc/project/ui-setting?key=pose3d_bg_color")
    assert got.status_code == 200
    assert got.get_json()["value"] == value


def test_pose3d_view_prefs_key_allowed():
    from dlc import inline_analysis
    assert "pose3d_view_prefs" in inline_analysis._UI_SETTING_KEYS


def test_pose3d_view_prefs_round_trip(tag_client):
    value = '{"w":800,"h":600,"flipX":true,"flipY":false,"flipZ":false}'
    post = tag_client.post("/dlc/project/ui-setting", json={"key": "pose3d_view_prefs", "value": value})
    assert post.status_code == 200, post.get_json()
    got = tag_client.get("/dlc/project/ui-setting?key=pose3d_view_prefs")
    assert got.status_code == 200
    assert got.get_json()["value"] == value


def test_pinned_snapshot_key_allowed():
    from dlc import inline_analysis
    assert "pinned_snapshot" in inline_analysis._UI_SETTING_KEYS


def test_pinned_snapshot_round_trip(tag_client):
    value = "dlc-models-pytorch/iteration-23/foo/train/snapshot-200.pt"
    post = tag_client.post("/dlc/project/ui-setting", json={"key": "pinned_snapshot", "value": value})
    assert post.status_code == 200, post.get_json()
    got = tag_client.get("/dlc/project/ui-setting?key=pinned_snapshot")
    assert got.status_code == 200
    assert got.get_json()["value"] == value
