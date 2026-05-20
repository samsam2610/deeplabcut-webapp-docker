"""Tier-2 regression + new-behavior tests for the modified CTD task & route."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helper: import the tasks module the way the existing test file does ──
@pytest.fixture
def tasks_mod():
    """Imports src.dlc.tasks with `deeplabcut` mocked at sys.modules level."""
    import sys, importlib, types
    fake_dlc = types.ModuleType("deeplabcut")
    fake_dlc.create_training_dataset = MagicMock()
    # Build the deeplabcut.utils.auxiliaryfunctions sub-module hierarchy so
    # `from deeplabcut.utils import auxiliaryfunctions` inside the task works.
    fake_utils = types.ModuleType("deeplabcut.utils")
    fake_aux = types.ModuleType("deeplabcut.utils.auxiliaryfunctions")
    fake_cfg = {"TrainingFraction": [0.8], "project_path": "/tmp/fake"}
    fake_aux.read_config = MagicMock(return_value=fake_cfg)
    fake_dlc.utils = fake_utils
    fake_utils.auxiliaryfunctions = fake_aux
    sys.modules["deeplabcut"] = fake_dlc
    sys.modules["deeplabcut.utils"] = fake_utils
    sys.modules["deeplabcut.utils.auxiliaryfunctions"] = fake_aux
    # Re-import the celery task module so it picks up the fake
    if "dlc.tasks" in sys.modules:
        del sys.modules["dlc.tasks"]
    mod = importlib.import_module("dlc.tasks")
    # The Celery PromiseProxy wraps the underlying Python function; its globals
    # may point to a previous module dict (not the freshly-created one). We
    # update both the module attribute AND the function's actual globals dict so
    # the task body always sees our fake regardless of how the module was cached.
    mod.dlc = fake_dlc
    _run = mod.dlc_create_training_dataset.run
    if hasattr(_run, "__func__") and "dlc" in _run.__func__.__globals__:
        _run.__func__.__globals__["dlc"] = fake_dlc
    yield mod
    # Restore would be nice but pytest module-isolation handles teardown.


# ── Helpers to unpack flask_test_client tuple ──────────────────────────────────
# conftest.flask_test_client yields (client, app_module, fake_redis, data_dir, user_data_dir)

def _client(ftc):
    return ftc[0]

def _redis(ftc):
    return ftc[2]


# ── 1. Default body (no split_mode) calls DLC with same kwargs as today ──
def test_default_mode_random_calls_dlc_unchanged(tmp_path, tasks_mod):
    import deeplabcut as dlc
    dlc.create_training_dataset.reset_mock()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nproject_path: " + str(tmp_path) + "\n")
    tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
    tasks_mod.dlc_create_training_dataset.run(str(cfg), num_shuffles=1, freeze_split=True)
    # Asserts the call was made with no trainIndices/testIndices kwargs
    args, kwargs = dlc.create_training_dataset.call_args
    assert "trainIndices" not in kwargs
    assert "testIndices" not in kwargs
    assert kwargs.get("num_shuffles") == 1
    assert kwargs.get("userfeedback") is False


# ── 2. split_mode="random" explicit call also doesn't pass indices ──
def test_explicit_random_does_not_pass_indices(tmp_path, tasks_mod):
    import deeplabcut as dlc
    dlc.create_training_dataset.reset_mock()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nproject_path: " + str(tmp_path) + "\n")
    tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
    tasks_mod.dlc_create_training_dataset.run(
        str(cfg), num_shuffles=1, freeze_split=True, split_mode="random", marks=None,
    )
    args, kwargs = dlc.create_training_dataset.call_args
    assert "trainIndices" not in kwargs
    assert "testIndices" not in kwargs


# ── 3. split_mode="manual" with marks calls DLC with indices ──
def test_manual_mode_forwards_indices(tmp_path, tasks_mod, monkeypatch):
    import deeplabcut as dlc
    dlc.create_training_dataset.reset_mock()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nproject_path: " + str(tmp_path) + "\n")

    # Patch build_indices to a known split so we don't depend on a real merged H5
    fake_train = [0, 1, 2, 3]
    fake_test = [4, 5]
    monkeypatch.setattr(
        "dlc.test_set_split.build_indices",
        lambda *a, **kw: (fake_train, fake_test, {"dropped_marks": 0, "total_frames": 6}),
    )
    tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
    tasks_mod.dlc_create_training_dataset.run(
        str(cfg),
        num_shuffles=2,
        freeze_split=True,
        split_mode="manual",
        marks=[["vid_a", "img0001.png"], ["vid_b", "img0002.png"]],
    )
    args, kwargs = dlc.create_training_dataset.call_args
    assert kwargs.get("trainIndices") == [fake_train, fake_train]
    assert kwargs.get("testIndices") == [fake_test, fake_test]


# ── 4. split_mode="hybrid" with marks also forwards ──
def test_hybrid_mode_forwards_indices(tmp_path, tasks_mod, monkeypatch):
    import deeplabcut as dlc
    dlc.create_training_dataset.reset_mock()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nproject_path: " + str(tmp_path) + "\n")
    monkeypatch.setattr(
        "dlc.test_set_split.build_indices",
        lambda *a, **kw: ([0, 1, 2], [3, 4, 5], {"dropped_marks": 0, "total_frames": 6}),
    )
    tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
    tasks_mod.dlc_create_training_dataset.run(
        str(cfg), num_shuffles=1, freeze_split=True,
        split_mode="hybrid", marks=[["vid_a", "img0001.png"]],
    )
    args, kwargs = dlc.create_training_dataset.call_args
    assert kwargs.get("trainIndices") == [[0, 1, 2]]
    assert kwargs.get("testIndices") == [[3, 4, 5]]


# ── 5. manual mode with empty marks fails fast — DLC not called ──
def test_manual_empty_marks_errors(tmp_path, tasks_mod, monkeypatch):
    import deeplabcut as dlc
    dlc.create_training_dataset.reset_mock()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nproject_path: " + str(tmp_path) + "\n")

    def raise_value(*a, **kw):
        raise ValueError("Full manual mode requires at least one marked frame")
    monkeypatch.setattr("dlc.test_set_split.build_indices", raise_value)
    tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
    with pytest.raises(RuntimeError, match="manual"):
        tasks_mod.dlc_create_training_dataset.run(
            str(cfg), num_shuffles=1, freeze_split=True, split_mode="manual", marks=[],
        )
    assert dlc.create_training_dataset.call_count == 0


# ── 6. The route includes a marks snapshot when mode != random ──
def test_route_snapshots_marks_for_manual(flask_test_client, tmp_path):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    app_mod = flask_test_client[1]

    # Activate a project; seed two marks in the SQLite via marks_store
    proj = tmp_path / "RouteSnap-2026-05-19"
    (proj / "labeled-data" / "vid_a").mkdir(parents=True)
    (proj / "labeled-data" / "vid_a" / "img0001.png").write_bytes(b"\x89PNG\r\n")
    (proj / "config.yaml").write_text("scorer: X\nproject_path: " + str(proj) + "\n")
    with client.session_transaction() as sess:
        sess["uid"] = "test-uid"
    fake_redis.set(
        "webapp:dlc_project:test-uid",
        json.dumps({"project_path": str(proj), "config_path": str(proj / "config.yaml"), "engine": "pytorch"}),
    )

    from dlc import marks_store
    marks_store.set_mark(proj, "vid_a", "img0001.png", True)

    sent_kwargs: dict = {}
    mock_result = MagicMock()
    mock_result.id = "fake-task-id"

    def fake_send_task(name, *, kwargs, queue):
        sent_kwargs.update(kwargs)
        return mock_result

    with patch.object(app_mod.celery, "send_task", side_effect=fake_send_task):
        rv = client.post(
            "/dlc/project/create-training-dataset",
            json={"num_shuffles": 1, "freeze_split": True, "split_mode": "manual"},
        )
    assert rv.status_code == 202
    assert sent_kwargs.get("split_mode") == "manual"
    assert sent_kwargs.get("marks") == [["vid_a", "img0001.png"]]


def test_route_no_split_mode_field_defaults_to_random(flask_test_client, tmp_path):
    client = _client(flask_test_client)
    fake_redis = _redis(flask_test_client)
    app_mod = flask_test_client[1]

    proj = tmp_path / "RouteDefault-2026-05-19"
    proj.mkdir()
    (proj / "config.yaml").write_text("scorer: X\nproject_path: " + str(proj) + "\n")
    with client.session_transaction() as sess:
        sess["uid"] = "test-uid"
    fake_redis.set(
        "webapp:dlc_project:test-uid",
        json.dumps({"project_path": str(proj), "config_path": str(proj / "config.yaml"), "engine": "pytorch"}),
    )

    sent_kwargs: dict = {}
    mock_result = MagicMock()
    mock_result.id = "fake-task-id"

    def fake_send_task(name, *, kwargs, queue):
        sent_kwargs.update(kwargs)
        return mock_result

    with patch.object(app_mod.celery, "send_task", side_effect=fake_send_task):
        rv = client.post(
            "/dlc/project/create-training-dataset",
            json={"num_shuffles": 1, "freeze_split": True},
        )
    assert rv.status_code == 202
    # Must include split_mode="random" so the worker knows to skip indices logic
    assert sent_kwargs.get("split_mode") == "random"
    # marks may be absent or empty — but never a non-empty list
    assert not sent_kwargs.get("marks")
