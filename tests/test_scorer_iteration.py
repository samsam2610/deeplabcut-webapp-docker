"""Unit tests for dlc.tasks._scorer_with_iteration.

Guards against output-filename collisions across training iterations:
scorer names from loader.scorer(snapshot_path) don't include the training
iteration, so iteration-22's snapshot-200.pt and iteration-23's
snapshot-200.pt both produced the same `..._snapshot_200.h5` and silently
overwrote each other.

DLC + GPU are fully mocked — this test runs on the host without CUDA and
does not import torch or deeplabcut for real. Mirrors the stubbing pattern
in test_inline_analysis_worker.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _load_dlc_tasks():
    """Stub deeplabcut modules before importing dlc.tasks."""
    _stub = MagicMock()
    _patches = {
        "deeplabcut": _stub,
        "deeplabcut.pose_estimation_pytorch": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.apis": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.apis.videos": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.data": MagicMock(),
    }
    with patch.dict(sys.modules, _patches):
        for key in list(sys.modules):
            if key == "dlc.tasks" or key.startswith("dlc.tasks."):
                del sys.modules[key]
        from dlc import tasks as _mod
        return _mod


dlc_tasks = _load_dlc_tasks()

REALISTIC_SNAPSHOT_PATH = (
    "/user-data/Parra-Data/Disk/DREADDJan7-me-2024-01-07/"
    "dlc-models-pytorch/iteration-23/DREADDJan7Jan7-trainset95shuffle1/"
    "train/snapshot-200.pt"
)
REALISTIC_SCORER = "DLC_HrnetW48_DREADDJan7shuffle1_snapshot_200"


def test_inserts_iter_before_snapshot_token():
    out = dlc_tasks._scorer_with_iteration(REALISTIC_SCORER, REALISTIC_SNAPSHOT_PATH)
    assert out == "DLC_HrnetW48_DREADDJan7shuffle1_iter23_snapshot_200"


def test_idempotent_when_already_present():
    once = dlc_tasks._scorer_with_iteration(REALISTIC_SCORER, REALISTIC_SNAPSHOT_PATH)
    twice = dlc_tasks._scorer_with_iteration(once, REALISTIC_SNAPSHOT_PATH)
    assert twice == once


def test_unchanged_when_no_iteration_in_path():
    no_iter_path = (
        "/user-data/Parra-Data/Disk/DREADDJan7-me-2024-01-07/"
        "dlc-models-pytorch/some-other-dir/train/snapshot-200.pt"
    )
    out = dlc_tasks._scorer_with_iteration(REALISTIC_SCORER, no_iter_path)
    assert out == REALISTIC_SCORER


def test_unchanged_when_scorer_has_no_snapshot_token():
    scorer_no_snapshot = "DLC_HrnetW48_DREADDJan7shuffle1_customname"
    out = dlc_tasks._scorer_with_iteration(scorer_no_snapshot, REALISTIC_SNAPSHOT_PATH)
    assert out == scorer_no_snapshot


def test_regression_guard_snapshot_index_capture_survives():
    """The regression this whole feature could break: tasks.py:1464 recovers
    the snapshot index via re.search(r'snapshot[_-](.+)$', scorer, re.I).
    Inserting the iteration token must land BEFORE `snapshot`, never after,
    or that capture group picks up the iteration suffix too.
    """
    pattern = re.compile(r'snapshot[_-](.+)$', re.IGNORECASE)
    before = pattern.search(REALISTIC_SCORER)
    assert before is not None

    transformed = dlc_tasks._scorer_with_iteration(REALISTIC_SCORER, REALISTIC_SNAPSHOT_PATH)
    after = pattern.search(transformed)
    assert after is not None

    assert after.group(1) == before.group(1)
