"""Tests for the dlc_progress_bar blueprint."""
from __future__ import annotations
import json
from pathlib import Path

import pytest


def _activate_project(client, fake_redis, project_path: Path):
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
def bar_project(flask_test_client):
    """A project INSIDE data_dir — _sec_check rejects anything outside it."""
    client, _app, fake_redis, data_dir, _udd = flask_test_client
    proj = data_dir / "BarTest-2026-08-01"
    proj.mkdir()
    (proj / "config.yaml").write_text("scorer: TestScorer\n")
    _activate_project(client, fake_redis, proj)
    return client, proj


def _save(client, segments):
    return client.put("/dlc/project/progress-bar", json={"segments": segments})


def test_requires_active_project(flask_test_client):
    client = flask_test_client[0]
    assert client.get("/dlc/project/progress-bar").status_code == 400


def test_definition_starts_empty(bar_project):
    client, _ = bar_project
    rv = client.get("/dlc/project/progress-bar")
    assert rv.status_code == 200
    assert rv.get_json()["segments"] == []


def test_put_then_get_round_trip_with_server_ids(bar_project):
    client, _ = bar_project
    rv = _save(client, [{"name": "Label", "options": [
        {"label": "Todo", "color": "#888888"}]}])
    assert rv.status_code == 200
    seg = rv.get_json()["segments"][0]
    assert seg["segment_id"].startswith("seg_")
    assert seg["options"][0]["option_id"].startswith("opt_")

    again = client.get("/dlc/project/progress-bar").get_json()["segments"][0]
    assert again["segment_id"] == seg["segment_id"]


def test_rejects_more_than_ten_segments(bar_project):
    client, _ = bar_project
    rv = _save(client, [{"name": f"S{i}", "options": []} for i in range(11)])
    assert rv.status_code == 400
    assert client.get("/dlc/project/progress-bar").get_json()["segments"] == []


def test_rejects_malformed_colour(bar_project):
    client, _ = bar_project
    rv = _save(client, [{"name": "L", "options": [
        {"label": "X", "color": "#fff; background:url(x)"}]}])
    assert rv.status_code == 400


def test_set_and_clear_a_value(bar_project):
    client, _ = bar_project
    seg = _save(client, [{"name": "Label", "options": [
        {"label": "Done", "color": "#2ea043"}]}]).get_json()["segments"][0]
    sid, oid = seg["segment_id"], seg["options"][0]["option_id"]
    client.post("/dlc/project/tracked-files", json={"path": "/data/a.avi"})

    rv = client.put("/dlc/project/progress-bar/value",
                    json={"path": "/data/a.avi", "segment_id": sid, "option_id": oid})
    assert rv.status_code == 200
    files = client.get("/dlc/project/tracked-files").get_json()["files"]
    assert files[0]["progress"] == {sid: oid}

    client.put("/dlc/project/progress-bar/value",
               json={"path": "/data/a.avi", "segment_id": sid, "option_id": None})
    files = client.get("/dlc/project/tracked-files").get_json()["files"]
    assert files[0]["progress"] == {}


def test_tracked_files_always_carries_a_progress_object(bar_project):
    """Even with no bar defined, the key exists so the client never branches."""
    client, _ = bar_project
    client.post("/dlc/project/tracked-files", json={"path": "/data/a.avi"})
    files = client.get("/dlc/project/tracked-files").get_json()["files"]
    assert files[0]["progress"] == {}


def test_value_write_requires_a_path_and_segment(bar_project):
    client, _ = bar_project
    assert client.put("/dlc/project/progress-bar/value",
                      json={"segment_id": "seg_x", "option_id": None}).status_code == 400
    assert client.put("/dlc/project/progress-bar/value",
                      json={"path": "/data/a.avi", "option_id": None}).status_code == 400
