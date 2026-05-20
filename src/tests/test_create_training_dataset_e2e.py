"""
Tier-3 end-to-end tests for the test-set picker integration with DLC.

These tests duplicate the real DREADD-Ali DLC project on disk (via the
existing `dlc_sandbox_project` fixture in conftest.py), run
`deeplabcut.create_training_dataset` for real with hand-picked marks,
then read back Documentation_data-*.pickle and the .mat file to assert
that every marked frame landed in the test set (and no marked frame
landed in train).

Skips automatically when:
  - The source project isn't mounted (so non-NAS machines run Tier 1+2 only).
  - `deeplabcut` can't be imported (so the test only fires inside the
    worker container).

Pickle format (DLC 3.0+):
  payload[0]  = list of dicts (TRAIN frames only, joints.size > 0 only)
  payload[1]  = train_idx  — absolute positional indices into the merged H5
  payload[2]  = test_idx   — absolute positional indices into the merged H5
  payload[3]  = trainFraction (float)

  The merged H5 (CollectedData_<scorer>.h5) lives in the same
  UnaugmentedDataSet_*/ folder as the pickle and is the authoritative
  source for resolving indices → (stem, image_name) pairs.
"""
from __future__ import annotations

import pickle
import re
from pathlib import Path

import pytest

try:
    import deeplabcut  # noqa: F401
    import scipy.io as sio
    HAVE_DLC = True
except Exception:
    HAVE_DLC = False

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not HAVE_DLC, reason="deeplabcut not importable; run in worker container."
    ),
]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _list_labeled_frames(project: Path) -> list[tuple[str, str]]:
    """Every (stem, image) under labeled-data/ that has label data attached."""
    out: list[tuple[str, str]] = []
    root = project / "labeled-data"
    for stem_dir in sorted(root.iterdir()):
        if not stem_dir.is_dir():
            continue
        # Only frames belonging to a folder with a CollectedData_*.csv are
        # represented in the merged H5. Frames without that file are silently
        # excluded by DLC.
        csv_files = list(stem_dir.glob("CollectedData_*.csv"))
        if not csv_files:
            continue
        for p in sorted(stem_dir.iterdir()):
            if p.suffix.lower() == ".png":
                out.append((stem_dir.name, p.name))
    return out


def _bump_iteration_for_isolation(config_path: Path) -> int:
    """Increase iteration so each e2e run lands in a fresh iteration folder."""
    text = config_path.read_text()
    m = re.search(r'^(iteration\s*:\s*)(\d+)', text, re.MULTILINE)
    cur = int(m.group(2)) if m else 0
    # Random-ish bump so parallel test runs don't collide
    import uuid as _uuid
    new = cur + 100 + (int(_uuid.uuid4().int) % 1000)
    if m:
        text = re.sub(
            r'^(iteration\s*:\s*)\d+',
            lambda mm: mm.group(1) + str(new),
            text, count=1, flags=re.MULTILINE,
        )
    else:
        text += f"\niteration: {new}\n"
    config_path.write_text(text)
    return new


def _find_doc_pickle(project: Path, iteration: int) -> Path:
    matches = list(
        (project / "training-datasets" / f"iteration-{iteration}").glob(
            "UnaugmentedDataSet_*/Documentation_data-*.pickle"
        )
    )
    assert matches, f"No Documentation_data-*.pickle found in iteration-{iteration}"
    return matches[0]


def _load_merged_h5(pickle_path: Path):
    """Load the CollectedData_*.h5 that lives next to the pickle.

    Returns a pandas DataFrame with a row MultiIndex of
    ('labeled-data', stem, image_name).
    """
    import pandas as pd
    h5_files = list(pickle_path.parent.glob("CollectedData_*.h5"))
    assert h5_files, f"No CollectedData_*.h5 found next to {pickle_path}"
    data = pd.read_hdf(str(h5_files[0]))
    # Strip scorer column level so index is the canonical 3-tuple.
    if hasattr(data.columns, "levels") and len(data.columns.levels) > 0:
        scorer = data.columns.levels[0][0]
        if scorer in data.columns.levels[0]:
            data = data[scorer]
    return data


def _resolve_split(pickle_path: Path):
    """Return (train_frames, test_frames, train_fraction) from a doc-pickle.

    DLC stores absolute positional indices into the merged H5 DataFrame.
    We resolve them to (stem, image_name) tuples using that H5 file.
    """
    with open(pickle_path, "rb") as f:
        payload = pickle.load(f)
    # payload = [train_data_list, train_idx, test_idx, trainFraction]
    train_idx, test_idx, frac = payload[1], payload[2], payload[3]

    h5_data = _load_merged_h5(pickle_path)
    n = len(h5_data.index)

    def _idx_to_frames(indices):
        out = set()
        for i in indices:
            i = int(i)
            if 0 <= i < n:
                row = h5_data.index[i]
                # row is ('labeled-data', stem, image_name)
                if len(row) >= 3:
                    out.add((row[1], row[2]))
        return out

    return _idx_to_frames(train_idx), _idx_to_frames(test_idx), float(frac)


def _read_config(config_path: Path) -> dict:
    from deeplabcut.utils import auxiliaryfunctions
    return auxiliaryfunctions.read_config(str(config_path))


def _run_ctd_task(config_path: Path, *, split_mode: str, marks: list[tuple[str, str]]):
    """Drive the celery task body synchronously (no Celery worker required)."""
    from unittest.mock import MagicMock
    from dlc import tasks as tasks_mod
    tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
    return tasks_mod.dlc_create_training_dataset.run(
        str(config_path),
        num_shuffles=1,
        freeze_split=True,
        split_mode=split_mode,
        marks=[[s, i] for (s, i) in marks],
    )


# ── Tests ───────────────────────────────────────────────────────────────────────

def test_e2e_manual_mode_exact_match(dlc_sandbox_project):
    project = dlc_sandbox_project
    config_path = project / "config.yaml"
    iteration = _bump_iteration_for_isolation(config_path)

    all_frames = _list_labeled_frames(project)
    assert len(all_frames) >= 6, "Sandbox project doesn't have enough labeled frames for the test"

    # Pick up to 5 marks across at least 2 folders
    folders = sorted({stem for stem, _ in all_frames})
    marks: list[tuple[str, str]] = []
    for f in folders[:2]:
        frames_in_f = [img for stem, img in all_frames if stem == f]
        for img in frames_in_f[:3]:
            marks.append((f, img))
            if len(marks) >= 5:
                break
        if len(marks) >= 5:
            break
    marks = marks[:5]

    _run_ctd_task(config_path, split_mode="manual", marks=marks)

    pickle_path = _find_doc_pickle(project, iteration)
    train_frames, test_frames, derived_frac = _resolve_split(pickle_path)

    # Manual mode invariants
    assert set(marks) == test_frames, (
        f"manual: test set must equal marks exactly.\n"
        f"  expected: {sorted(marks)}\n"
        f"  got:      {sorted(test_frames)}"
    )
    assert test_frames.isdisjoint(train_frames), "train and test must be disjoint"
    universe = train_frames | test_frames
    # Every mark must be in the universe (it should be, since we picked from labeled-data)
    assert set(marks).issubset(universe)
    # Derived fraction reflects the actual split
    derived_pct = int(round(derived_frac * 100))
    # Folder under dlc-models-pytorch encodes the derived %
    pattern = list(
        (project / "dlc-models-pytorch" / f"iteration-{iteration}").glob(
            f"*trainset{derived_pct}shuffle*"
        )
    )
    assert pattern, (
        f"No model folder with trainset{derived_pct}* in iteration-{iteration}. "
        f"derived_frac={derived_frac}"
    )


def test_e2e_hybrid_mode_below_quota(dlc_sandbox_project):
    project = dlc_sandbox_project
    config_path = project / "config.yaml"
    iteration = _bump_iteration_for_isolation(config_path)

    all_frames = _list_labeled_frames(project)
    folders = sorted({stem for stem, _ in all_frames})
    # Pick 2 marks (well below the typical 20% test quota)
    marks = []
    for f in folders[:2]:
        frames_in_f = [img for stem, img in all_frames if stem == f]
        if frames_in_f:
            marks.append((f, frames_in_f[0]))

    _run_ctd_task(config_path, split_mode="hybrid", marks=marks)

    pickle_path = _find_doc_pickle(project, iteration)
    train_frames, test_frames, derived_frac = _resolve_split(pickle_path)
    cfg = _read_config(config_path)
    train_fraction = float(cfg.get("TrainingFraction", [0.8])[0])

    # Hybrid invariants
    assert set(marks).issubset(test_frames), "hybrid: marks must be a subset of the test set"
    assert test_frames.isdisjoint(train_frames)
    universe = train_frames | test_frames
    expected_test = round((1 - train_fraction) * len(universe))
    # Allow ±1 for rounding of the target_test_count
    assert abs(len(test_frames) - expected_test) <= 1, (
        f"hybrid test count off: {len(test_frames)} vs expected {expected_test}"
    )


def test_e2e_random_mode_unchanged_behavior(dlc_sandbox_project):
    """Regression: split_mode='random' produces a valid split with no marks."""
    project = dlc_sandbox_project
    config_path = project / "config.yaml"
    iteration = _bump_iteration_for_isolation(config_path)

    _run_ctd_task(config_path, split_mode="random", marks=[])

    pickle_path = _find_doc_pickle(project, iteration)
    train_frames, test_frames, derived_frac = _resolve_split(pickle_path)
    universe = train_frames | test_frames
    assert test_frames.isdisjoint(train_frames)
    assert len(universe) > 0


def test_e2e_manual_empty_marks_fails_clean(dlc_sandbox_project):
    """Manual mode with no marks must fail fast — DLC never called."""
    project = dlc_sandbox_project
    config_path = project / "config.yaml"
    _bump_iteration_for_isolation(config_path)

    with pytest.raises(RuntimeError, match=r"manual|Manual"):
        _run_ctd_task(config_path, split_mode="manual", marks=[])


def test_e2e_mat_file_train_count_matches_pickle(dlc_sandbox_project):
    """The .mat file's dataset row count equals the pickle's train entry count.

    DLC's format_training_data() iterates over train indices and appends an
    entry only when the frame has at least one valid joint inside the image.
    The pickle's payload[0] (train_data list) and the .mat file therefore both
    represent this filtered-train count, which may be <= len(train_idx).
    """
    project = dlc_sandbox_project
    config_path = project / "config.yaml"
    iteration = _bump_iteration_for_isolation(config_path)

    all_frames = _list_labeled_frames(project)
    marks = all_frames[:2] if len(all_frames) >= 2 else []
    _run_ctd_task(config_path, split_mode="hybrid", marks=marks)

    pickle_path = _find_doc_pickle(project, iteration)

    # payload[0] = train_data list (filtered train entries with valid joints)
    with open(pickle_path, "rb") as f:
        payload = pickle.load(f)
    train_entries = payload[0]
    n_train_entries = len(train_entries)

    # .mat file lives next to the pickle
    mat_files = list(pickle_path.parent.glob("*shuffle*.mat"))
    assert mat_files, f"No .mat file next to {pickle_path}"
    mat = sio.loadmat(str(mat_files[0]))
    ds = mat["dataset"]
    n_rows = ds.shape[1] if ds.ndim == 2 else len(ds)
    assert n_rows == n_train_entries, (
        f".mat dataset row count ({n_rows}) does not match "
        f"pickle train entry count ({n_train_entries})"
    )
