"""Tests for GET /dlc/project/training-dataset/splits — auto-populated splits list."""
from __future__ import annotations
import json
import pickle
from pathlib import Path

import numpy as np
import pytest


def _write_doc_pickle(folder: Path, scorer: str, train_pct: int, shuffle: int,
                      train_idx: list[int], test_idx: list[int]) -> Path:
    """Minimal Documentation_data-*.pickle (payload[0] left empty — only the
    filename is parsed by the splits endpoint)."""
    folder.mkdir(parents=True, exist_ok=True)
    payload = [
        [],
        np.array(train_idx, dtype=np.int64),
        np.array(test_idx, dtype=np.int64),
        train_pct / 100.0,
    ]
    out = folder / f"Documentation_data-{scorer}_{train_pct}shuffle{shuffle}.pickle"
    with open(out, "wb") as f:
        pickle.dump(payload, f)
    return out


def _client(flask_test_client):
    return flask_test_client[0]


def _redis(flask_test_client):
    return flask_test_client[2]


def _activate(client, fake_redis, project_path: Path):
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
def splits_project(tmp_path):
    proj = tmp_path / "SplitsTest-2026-05-20"
    proj.mkdir()
    (proj / "config.yaml").write_text(
        "scorer: T\nproject_path: " + str(proj) + "\niteration: 0\n"
    )
    return proj


def test_splits_empty_project(flask_test_client, splits_project):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate(client, fake_redis, splits_project)
    rv = client.get("/dlc/project/training-dataset/splits")
    assert rv.status_code == 200
    assert rv.get_json() == {"splits": []}


def test_splits_single_iteration_single_shuffle(flask_test_client, splits_project):
    _write_doc_pickle(
        splits_project / "training-datasets" / "iteration-0" / "UnaugmentedDataSet_T0",
        scorer="T", train_pct=80, shuffle=1, train_idx=[0, 1], test_idx=[2],
    )
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate(client, fake_redis, splits_project)
    rv = client.get("/dlc/project/training-dataset/splits")
    body = rv.get_json()
    assert rv.status_code == 200
    assert len(body["splits"]) == 1
    s = body["splits"][0]
    assert s["iteration"] == 0
    assert s["shuffle"] == 1
    assert s["train_fraction"] == 0.8
    assert s["pickle"].startswith("Documentation_data-")
    assert s["label"] == "iteration-0 • shuffle-1 • trainset 80%"


def test_splits_sorted_iteration_desc_shuffle_asc(flask_test_client, splits_project):
    # Two iterations, multiple shuffles, written out of order to verify sorting
    _write_doc_pickle(
        splits_project / "training-datasets" / "iteration-1" / "UnaugmentedDataSet_T0",
        scorer="T", train_pct=80, shuffle=2, train_idx=[0], test_idx=[1],
    )
    _write_doc_pickle(
        splits_project / "training-datasets" / "iteration-0" / "UnaugmentedDataSet_T0",
        scorer="T", train_pct=70, shuffle=1, train_idx=[0], test_idx=[1],
    )
    _write_doc_pickle(
        splits_project / "training-datasets" / "iteration-1" / "UnaugmentedDataSet_T0",
        scorer="T", train_pct=80, shuffle=1, train_idx=[0], test_idx=[1],
    )
    _write_doc_pickle(
        splits_project / "training-datasets" / "iteration-0" / "UnaugmentedDataSet_T0",
        scorer="T", train_pct=70, shuffle=2, train_idx=[0], test_idx=[1],
    )
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate(client, fake_redis, splits_project)
    rv = client.get("/dlc/project/training-dataset/splits")
    body = rv.get_json()
    pairs = [(s["iteration"], s["shuffle"]) for s in body["splits"]]
    # Iteration DESC, shuffle ASC within iteration
    assert pairs == [(1, 1), (1, 2), (0, 1), (0, 2)]


def test_splits_skip_malformed_filenames(flask_test_client, splits_project):
    folder = splits_project / "training-datasets" / "iteration-0" / "UnaugmentedDataSet_T0"
    folder.mkdir(parents=True)
    # Valid one
    _write_doc_pickle(folder, scorer="T", train_pct=80, shuffle=1,
                      train_idx=[0], test_idx=[1])
    # Decoy filenames that should NOT show up
    (folder / "Documentation_data-wrong_format.pickle").write_bytes(b"junk")
    (folder / "not-a-doc.pickle").write_bytes(b"junk")
    (folder / "Documentation_data-T_80shuffle1.txt").write_text("nope")
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    _activate(client, fake_redis, splits_project)
    rv = client.get("/dlc/project/training-dataset/splits")
    body = rv.get_json()
    assert len(body["splits"]) == 1
    assert body["splits"][0]["pickle"].endswith("_80shuffle1.pickle")


def test_splits_no_active_project_returns_400(flask_test_client):
    client = _client(flask_test_client)
    rv = client.get("/dlc/project/training-dataset/splits")
    assert rv.status_code == 400
