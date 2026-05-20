"""Tests for the inspect endpoint that reads frozen splits from pickle."""
from __future__ import annotations
import json
import pickle
from pathlib import Path

import numpy as np
import pytest


def _make_pickle(folder: Path, scorer_task: str, train_fraction_pct: int, shuffle: int,
                 frames: list[tuple[str, str]], train_idx: list[int], test_idx: list[int]):
    """Write a Documentation_data-*.pickle DLC-style."""
    folder.mkdir(parents=True, exist_ok=True)
    all_entries = [
        {"image": ("labeled-data", stem, image), "size": (3, 100, 100),
         "joints": np.zeros((1, 3), dtype=np.int64)}
        for (stem, image) in frames
    ]
    payload = [all_entries, np.array(train_idx, dtype=np.int64),
               np.array(test_idx, dtype=np.int64), train_fraction_pct / 100.0]
    out = folder / f"Documentation_data-{scorer_task}_{train_fraction_pct}shuffle{shuffle}.pickle"
    with open(out, "wb") as f:
        pickle.dump(payload, f)
    return out


# conftest.flask_test_client yields (client, app_module, fake_redis, data_dir, user_data_dir)
def _client(ftc):
    return ftc[0]

def _redis(ftc):
    return ftc[2]


def _activate(ftc, project_path):
    client = _client(ftc)
    fake_redis = _redis(ftc)
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
def inspect_project(tmp_path):
    proj = tmp_path / "InspectTest-2026-05-19"
    proj.mkdir()
    cfg = proj / "config.yaml"
    cfg.write_text(
        "scorer: TestScorer\nproject_path: " + str(proj) + "\n"
        "TrainingFraction:\n  - 0.8\niteration: 0\nTask: MyTask\n"
    )
    # Iteration 0, shuffle 1 — 5 frames, 4 train / 1 test
    folder = proj / "training-datasets" / "iteration-0" / "UnaugmentedDataSet_MyTaskJan1"
    _make_pickle(
        folder, "MyTask", 80, 1,
        frames=[
            ("vid_a", "img0001.png"),
            ("vid_a", "img0002.png"),
            ("vid_b", "img0010.png"),
            ("vid_b", "img0020.png"),
            ("vid_b", "img0030.png"),
        ],
        train_idx=[0, 2, 3, 4],
        test_idx=[1],
    )
    return proj


def test_inspect_default_iteration(flask_test_client, inspect_project):
    _activate(flask_test_client, inspect_project)
    rv = _client(flask_test_client).get("/dlc/project/training-dataset/inspect")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["iteration"] == 0
    assert len(body["datasets"]) == 1
    ds = body["datasets"][0]
    assert ds["shuffle"] == 1
    assert ds["train_fraction"] == 0.8
    train_pairs = {(d["video_stem"], d["image_name"]) for d in ds["train"]}
    test_pairs  = {(d["video_stem"], d["image_name"]) for d in ds["test"]}
    assert train_pairs == {
        ("vid_a", "img0001.png"),
        ("vid_b", "img0010.png"),
        ("vid_b", "img0020.png"),
        ("vid_b", "img0030.png"),
    }
    assert test_pairs == {("vid_a", "img0002.png")}


def test_inspect_specific_shuffle(flask_test_client, inspect_project):
    _activate(flask_test_client, inspect_project)
    rv = _client(flask_test_client).get("/dlc/project/training-dataset/inspect?iteration=0&shuffle=1")
    assert rv.status_code == 200


def test_inspect_strips_minus_one_padding(flask_test_client, tmp_path):
    proj = tmp_path / "Pad-2026-05-19"
    proj.mkdir()
    (proj / "config.yaml").write_text(
        "scorer: T\nproject_path: " + str(proj) + "\nTrainingFraction:\n  - 0.8\niteration: 0\nTask: MyTask\n"
    )
    folder = proj / "training-datasets" / "iteration-0" / "UnaugmentedDataSet_MyTaskJan1"
    _make_pickle(
        folder, "MyTask", 80, 1,
        frames=[("a", "1.png"), ("a", "2.png")],
        train_idx=[0, -1],
        test_idx=[1, -1],
    )
    _activate(flask_test_client, proj)
    rv = _client(flask_test_client).get("/dlc/project/training-dataset/inspect")
    ds = rv.get_json()["datasets"][0]
    assert ds["train"] == [{"video_stem": "a", "image_name": "1.png"}]
    assert ds["test"]  == [{"video_stem": "a", "image_name": "2.png"}]


def test_inspect_missing_iteration_empty(flask_test_client, tmp_path):
    proj = tmp_path / "Empty-2026-05-19"
    proj.mkdir()
    (proj / "config.yaml").write_text("scorer: T\nproject_path: " + str(proj) + "\niteration: 5\n")
    _activate(flask_test_client, proj)
    rv = _client(flask_test_client).get("/dlc/project/training-dataset/inspect")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["datasets"] == []
