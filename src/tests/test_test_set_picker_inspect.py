"""Tests for the inspect endpoint that reads frozen splits from pickle."""
from __future__ import annotations
import json
import pickle
from pathlib import Path

import numpy as np
import pytest


def _make_pickle(folder: Path, scorer: str, train_fraction_pct: int, shuffle: int,
                 frames: list[tuple[str, str]], train_idx: list[int], test_idx: list[int]):
    """Write a Documentation_data-*.pickle + sibling CollectedData_<scorer>.h5
    matching DLC's real layout.

    The H5 is written with h5py directly (no PyTables/tables dependency) in
    the fixed-format layout that DLC produces. The production reader also uses
    h5py so the encoding matches.
    """
    import h5py
    folder.mkdir(parents=True, exist_ok=True)

    # Build the MultiIndex components from frames list.
    stems = sorted(set(stem for stem, _ in frames))
    images = sorted(set(img for _, img in frames))
    stem_to_idx = {s: i for i, s in enumerate(stems)}
    img_to_idx = {im: i for i, im in enumerate(images)}

    lb1 = np.array([stem_to_idx[stem] for stem, _ in frames], dtype=np.int8)
    lb2 = np.array([img_to_idx[img] for _, img in frames], dtype=np.int16)

    h5_path = folder / f"CollectedData_{scorer}.h5"
    with h5py.File(str(h5_path), "w") as f:
        g = f.create_group("df_with_missing")
        g.attrs["CLASS"] = np.bytes_(b"GROUP")
        g.attrs["TITLE"] = np.bytes_(b"")
        g.attrs["VERSION"] = np.bytes_(b"1.0")
        g.attrs["axis1_nlevels"] = np.int64(3)
        g.attrs["axis1_variety"] = np.bytes_(b"multi")
        g.attrs["axis0_nlevels"] = np.int64(1)
        g.attrs["axis0_variety"] = np.bytes_(b"regular")
        g.attrs["block0_items_nlevels"] = np.int64(1)
        g.attrs["block0_items_variety"] = np.bytes_(b"regular")
        g.attrs["encoding"] = np.bytes_(b"UTF-8")
        g.attrs["errors"] = np.bytes_(b"strict")
        g.attrs["nblocks"] = np.int64(1)
        g.attrs["ndim"] = np.int64(2)
        g.attrs["pandas_type"] = np.bytes_(b"frame")
        g.attrs["pandas_version"] = np.bytes_(b"0.15.2")

        # Row index (axis1 = rows in pandas fixed/legacy format)
        g.create_dataset("axis1_level0", data=np.array([b"labeled-data"]))
        g.create_dataset("axis1_level1", data=np.array([s.encode() for s in stems]))
        g.create_dataset("axis1_level2", data=np.array([im.encode() for im in images]))
        g.create_dataset("axis1_label0", data=np.zeros(len(frames), dtype=np.int8))
        g.create_dataset("axis1_label1", data=lb1)
        g.create_dataset("axis1_label2", data=lb2)

        # Column index (axis0) — one dummy column
        g.create_dataset("axis0_level0", data=np.array([b"dummy"]))
        g.create_dataset("axis0_label0", data=np.zeros(1, dtype=np.int8))
        g.create_dataset("block0_items_level0", data=np.array([b"dummy"]))
        g.create_dataset("block0_items_label0", data=np.zeros(1, dtype=np.int8))
        g.create_dataset("block0_values", data=np.zeros((len(frames), 1)))
        g.create_dataset("axis0", data=np.arange(1, dtype=np.int64))

    # Pickle: payload[0] is the train-only filtered list (we put a dummy value
    # here — the new endpoint reads only payload[1]/payload[2] for indices and
    # uses the H5 for frame mapping).
    payload = [
        [],
        np.array(train_idx, dtype=np.int64),
        np.array(test_idx, dtype=np.int64),
        train_fraction_pct / 100.0,
    ]
    out = folder / f"Documentation_data-{scorer}_{train_fraction_pct}shuffle{shuffle}.pickle"
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
        folder, "TestScorer", 80, 1,
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
        folder, "T", 80, 1,
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
