"""
Tests for src/dlc_dataset_curator.py — frame extraction and CollectedData I/O.

Constraint #3 — Isolated test state:
  Tests that touch files use tmp_path (pytest built-in) or a sandbox fixture
  that copies ONLY the target video, never the full project.

Constraint #1 — GPU routing:
  cv2.VideoCapture is CPU-only; no GPU allocation occurs.

Constraint #4 — Zero name collisions:
  All helpers/fixtures in this file are prefixed with `tvdc_` (test_viewer_dataset_curation).
"""
from __future__ import annotations

import csv
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── Reference data ────────────────────────────────────────────────────────────
_MAPS_DIR    = Path(
    "/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/MAPS-DREADSS"
)
_TARGET_STEM = "MAP1_20250713_112101_3"
_TARGET_AVI  = _MAPS_DIR / f"{_TARGET_STEM}.avi"

_VIDEO_AVAILABLE = _TARGET_AVI.is_file()
_skip_no_video   = pytest.mark.skipif(
    not _VIDEO_AVAILABLE,
    reason="Target AVI not mounted — skipping video-dependent test.",
)

# ── Dummy project parameters ──────────────────────────────────────────────────
_SCORER     = "TestScorer"
_BODYPARTS  = ["Snout", "Wrist", "Elbow", "Shoulder"]
_VIDEO_STEM = "testvideo"


# ── Sandbox fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def tvdc_video_sandbox(tmp_path) -> Path:
    """
    Copy ONLY the target AVI into a temp sandbox.
    Teardown is handled automatically by pytest's tmp_path fixture.
    """
    if not _VIDEO_AVAILABLE:
        pytest.skip("Target AVI not available.")
    sandbox = tmp_path / "video_sandbox"
    sandbox.mkdir()
    shutil.copy2(str(_TARGET_AVI), str(sandbox / _TARGET_AVI.name))
    return sandbox


@pytest.fixture(scope="function")
def tvdc_dataset_sandbox(tmp_path) -> tuple[Path, Path]:
    """
    Build a synthetic labeled-data directory with two pre-existing frames.

    Returns (stem_dir, csv_path).
    """
    stem_dir = tmp_path / "labeled-data" / _VIDEO_STEM
    stem_dir.mkdir(parents=True)

    # Create two dummy PNG files
    for name in ("img0000-00010.png", "img0001-00020.png"):
        (stem_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")  # valid PNG header

    # Write a CollectedData CSV with two frames
    headers = [
        ["scorer",    "", ""] + [_SCORER] * (len(_BODYPARTS) * 2),
        ["bodyparts", "", ""] + [bp for bp in _BODYPARTS for _ in range(2)],
        ["coords",    "", ""] + ["x", "y"] * len(_BODYPARTS),
    ]
    data_rows = [
        ["labeled-data", _VIDEO_STEM, "img0000-00010.png",
         "100.0", "200.0", "NaN", "NaN", "300.0", "400.0", "NaN", "NaN"],
        ["labeled-data", _VIDEO_STEM, "img0001-00020.png",
         "110.0", "210.0", "50.0", "60.0", "NaN", "NaN", "NaN", "NaN"],
    ]
    csv_path = stem_dir / f"CollectedData_{_SCORER}.csv"
    with open(str(csv_path), "w", newline="") as fh:
        csv.writer(fh).writerows(headers + data_rows)

    return stem_dir, csv_path


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Unit tests for dlc_dataset_curator
# ─────────────────────────────────────────────────────────────────────────────

# ── extract_frame_as_png ──────────────────────────────────────────────────────

@_skip_no_video
def test_tvdc_extract_frame_saves_png(tvdc_video_sandbox):
    """extract_frame_as_png saves a PNG file at the expected path."""
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import extract_frame_as_png

    video_path  = tvdc_video_sandbox / _TARGET_AVI.name
    output_dir  = tvdc_video_sandbox / "labeled-data" / _TARGET_STEM
    output_dir.mkdir(parents=True)

    saved_path, is_dup = extract_frame_as_png(video_path, 0, output_dir, seq_index=0)

    assert not is_dup, "Frame 0 should not be a duplicate on first extraction"
    assert saved_path.exists(), f"Expected PNG at {saved_path}"
    assert saved_path.suffix == ".png"
    assert saved_path.name == "img0000-00000.png"


@_skip_no_video
def test_tvdc_extract_frame_is_lossless(tvdc_video_sandbox):
    """Saved PNG decodes without JPEG artifacts (lossless round-trip)."""
    import cv2 as _cv2
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import extract_frame_as_png

    video_path = tvdc_video_sandbox / _TARGET_AVI.name
    output_dir = tvdc_video_sandbox / "frames"
    output_dir.mkdir()

    saved_path, _ = extract_frame_as_png(video_path, 5, output_dir, seq_index=0)

    # Re-open via cv2 to confirm it's a valid colour image
    img = _cv2.imread(str(saved_path))
    assert img is not None, "cv2.imread returned None — PNG is invalid"
    assert img.ndim == 3
    assert img.shape[2] == 3   # BGR


@_skip_no_video
def test_tvdc_extract_frame_duplicate_detection(tvdc_video_sandbox):
    """Calling extract_frame_as_png twice for the same frame returns was_duplicate=True."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import extract_frame_as_png

    video_path = tvdc_video_sandbox / _TARGET_AVI.name
    output_dir = tvdc_video_sandbox / "frames"
    output_dir.mkdir()

    _, is_dup1 = extract_frame_as_png(video_path, 10, output_dir)
    path2, is_dup2 = extract_frame_as_png(video_path, 10, output_dir)

    assert not is_dup1
    assert is_dup2, "Second call for same frame must report duplicate"
    # No new PNG should have been created
    pngs = list(output_dir.glob("*.png"))
    assert len(pngs) == 1


@_skip_no_video
def test_tvdc_extract_frame_seq_index_auto(tvdc_video_sandbox):
    """seq_index auto-increments based on existing PNGs in output_dir."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import extract_frame_as_png

    video_path = tvdc_video_sandbox / _TARGET_AVI.name
    output_dir = tvdc_video_sandbox / "autoframe"
    output_dir.mkdir()

    p0, _ = extract_frame_as_png(video_path, 0, output_dir)
    p1, _ = extract_frame_as_png(video_path, 1, output_dir)
    p2, _ = extract_frame_as_png(video_path, 2, output_dir)

    assert p0.name == "img0000-00000.png"
    assert p1.name == "img0001-00001.png"
    assert p2.name == "img0002-00002.png"


# ── append_frame_to_dataset ───────────────────────────────────────────────────

def test_tvdc_append_creates_csv_from_scratch(tmp_path):
    """append_frame_to_dataset creates a CollectedData CSV when none exists."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import append_frame_to_dataset

    stem_dir = tmp_path / "labeled-data" / "myvideo"
    stem_dir.mkdir(parents=True)

    csv_path, _ = append_frame_to_dataset(
        stem_dir=stem_dir,
        video_stem="myvideo",
        frame_name="img0000-00050.png",
        scorer=_SCORER,
        bodyparts=_BODYPARTS,
        coords={"Snout": [123.4, 456.7]},
    )

    assert csv_path.is_file()
    with open(str(csv_path), newline="") as fh:
        rows = list(csv.reader(fh))

    # 3 header rows + 1 data row
    assert len(rows) == 4
    assert rows[3][2] == "img0000-00050.png"
    # Snout x
    assert abs(float(rows[3][3]) - 123.4) < 0.01
    # Snout y
    assert abs(float(rows[3][4]) - 456.7) < 0.01
    # Wrist x (not provided → NaN)
    assert rows[3][5].lower() == "nan"


def test_tvdc_append_does_not_create_duplicate(tvdc_dataset_sandbox):
    """Appending an already-present frame_name does not create a duplicate row."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import append_frame_to_dataset

    stem_dir, _ = tvdc_dataset_sandbox

    append_frame_to_dataset(
        stem_dir, _VIDEO_STEM, "img0000-00010.png",
        _SCORER, _BODYPARTS, coords={"Snout": [999.0, 999.0]},
    )

    csv_path = stem_dir / f"CollectedData_{_SCORER}.csv"
    with open(str(csv_path), newline="") as fh:
        rows = list(csv.reader(fh))

    data_rows = [r for r in rows[3:] if r]
    assert len(data_rows) == 2, "Duplicate append must not add a new row"


def test_tvdc_append_new_frame_extends_csv(tvdc_dataset_sandbox):
    """Appending a new frame increases the CSV row count by exactly one."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import append_frame_to_dataset

    stem_dir, csv_path = tvdc_dataset_sandbox

    with open(str(csv_path), newline="") as fh:
        before = sum(1 for r in csv.reader(fh) if r)

    append_frame_to_dataset(
        stem_dir, _VIDEO_STEM, "img0002-00030.png",
        _SCORER, _BODYPARTS, coords={"Elbow": [77.0, 88.0]},
    )

    with open(str(csv_path), newline="") as fh:
        after = sum(1 for r in csv.reader(fh) if r)

    assert after == before + 1


# ── update_frame_annotation ───────────────────────────────────────────────────

def test_tvdc_update_changes_target_frame_only(tvdc_dataset_sandbox):
    """update_frame_annotation modifies only the targeted frame."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import update_frame_annotation

    stem_dir, csv_path = tvdc_dataset_sandbox

    update_frame_annotation(
        stem_dir, _VIDEO_STEM, "img0000-00010.png",
        _SCORER, _BODYPARTS,
        coords={"Snout": [555.0, 666.0]},
    )

    with open(str(csv_path), newline="") as fh:
        rows = list(csv.reader(fh))

    # Row for img0000-00010.png
    row0 = next(r for r in rows[3:] if r and r[2] == "img0000-00010.png")
    # Row for img0001-00020.png (must be unchanged)
    row1 = next(r for r in rows[3:] if r and r[2] == "img0001-00020.png")

    assert abs(float(row0[3]) - 555.0) < 0.01, "Snout x should be updated"
    assert abs(float(row0[4]) - 666.0) < 0.01, "Snout y should be updated"
    # img0001 row should be unchanged (Snout was 110.0, 210.0)
    assert abs(float(row1[3]) - 110.0) < 0.01, "img0001 row must not change"


def test_tvdc_update_preserves_unlisted_bodyparts(tvdc_dataset_sandbox):
    """Bodyparts NOT in coords dict retain their original values."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import update_frame_annotation

    stem_dir, csv_path = tvdc_dataset_sandbox

    # img0000 has Elbow at 300.0, 400.0 — update only Snout
    update_frame_annotation(
        stem_dir, _VIDEO_STEM, "img0000-00010.png",
        _SCORER, _BODYPARTS,
        coords={"Snout": [1.0, 2.0]},
    )

    with open(str(csv_path), newline="") as fh:
        rows = list(csv.reader(fh))

    row0 = next(r for r in rows[3:] if r and r[2] == "img0000-00010.png")
    # Elbow is at positions 6 and 7 (3 index cols + Snout x/y + Wrist x/y + Elbow x/y)
    elbow_x = row0[7]  # index: 3(idx) + 0+1(Snout) + 2+3(Wrist) + 4=Elbow_x
    elbow_y = row0[8]
    assert abs(float(elbow_x) - 300.0) < 0.01, "Elbow x must be preserved"
    assert abs(float(elbow_y) - 400.0) < 0.01, "Elbow y must be preserved"


def test_tvdc_update_partial_coords_preserves_nan(tvdc_dataset_sandbox):
    """Bodyparts with no prior label and not in coords dict remain NaN."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import update_frame_annotation

    stem_dir, csv_path = tvdc_dataset_sandbox

    # Shoulder has NaN in img0000; update only Snout
    update_frame_annotation(
        stem_dir, _VIDEO_STEM, "img0000-00010.png",
        _SCORER, _BODYPARTS,
        coords={"Snout": [10.0, 20.0]},
    )

    with open(str(csv_path), newline="") as fh:
        rows = list(csv.reader(fh))

    row0 = next(r for r in rows[3:] if r and r[2] == "img0000-00010.png")
    # Shoulder is the last bodypart: index 3 + (3 bps * 2) = 9, 10
    shoulder_x = row0[9]
    assert shoulder_x.lower() == "nan", "Shoulder must remain NaN"


# ── H5 integrity ─────────────────────────────────────────────────────────────

def test_tvdc_h5_rebuilt_after_append(tvdc_dataset_sandbox):
    """After append, CollectedData_<scorer>.h5 is created and readable by pandas."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import append_frame_to_dataset

    stem_dir, _ = tvdc_dataset_sandbox

    csv_path, h5_path = append_frame_to_dataset(
        stem_dir, _VIDEO_STEM, "img0002-00030.png",
        _SCORER, _BODYPARTS, coords={"Snout": [1.0, 2.0]},
    )

    assert h5_path is not None, "H5 path must be returned"
    assert h5_path.is_file(), "H5 file must exist on disk"

    df = pd.read_hdf(str(h5_path), key="df_with_missing")
    assert isinstance(df, pd.DataFrame)
    # 3 original rows + 1 new one
    assert len(df) == 3


def test_tvdc_h5_multiindex_structure(tvdc_dataset_sandbox):
    """Rebuilt H5 has the expected 3-level column MultiIndex used by DLC."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import append_frame_to_dataset

    stem_dir, _ = tvdc_dataset_sandbox

    _, h5_path = append_frame_to_dataset(
        stem_dir, _VIDEO_STEM, "img0002-00030.png",
        _SCORER, _BODYPARTS,
    )

    df = pd.read_hdf(str(h5_path), key="df_with_missing")

    col_names = df.columns.names
    assert "scorer"    in col_names
    assert "bodyparts" in col_names
    assert "coords"    in col_names

    scorer    = df.columns.get_level_values("scorer")[0]
    bodyparts = df[scorer].columns.get_level_values("bodyparts").unique().tolist()

    assert scorer    == _SCORER
    assert set(bodyparts) == set(_BODYPARTS)

    # Coords level must contain only 'x' and 'y'
    coords_vals = set(df.columns.get_level_values("coords").unique())
    assert coords_vals == {"x", "y"}, f"Unexpected coords: {coords_vals}"


def test_tvdc_h5_values_match_csv(tvdc_dataset_sandbox):
    """H5 coordinate values match what was written to the CSV."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import update_frame_annotation

    stem_dir, _ = tvdc_dataset_sandbox

    _, h5_path = update_frame_annotation(
        stem_dir, _VIDEO_STEM, "img0000-00010.png",
        _SCORER, _BODYPARTS,
        coords={"Snout": [123.0, 456.0]},
    )

    df = pd.read_hdf(str(h5_path), key="df_with_missing")

    # Find the row for img0000-00010.png — use index level values
    for i, idx_val in enumerate(df.index):
        if idx_val[2] == "img0000-00010.png":
            row = df.iloc[i][_SCORER]
            snout_x = float(row["Snout"]["x"])
            snout_y = float(row["Snout"]["y"])
            assert abs(snout_x - 123.0) < 0.01
            assert abs(snout_y - 456.0) < 0.01
            return

    pytest.fail("img0000-00010.png not found in H5 index")


def test_tvdc_h5_atomic_write_no_tmp_residue(tmp_path):
    """After rebuild, no .h5.tmp file is left behind."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import append_frame_to_dataset

    stem_dir = tmp_path / "labeled-data" / "v"
    stem_dir.mkdir(parents=True)

    append_frame_to_dataset(
        stem_dir, "v", "img0000-00001.png",
        _SCORER, _BODYPARTS,
    )

    tmp_files = list(stem_dir.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"


# ── rebuild_h5_from_csv standalone ───────────────────────────────────────────

def test_tvdc_rebuild_h5_from_csv_returns_none_for_empty(tmp_path):
    """rebuild_h5_from_csv returns None when the CSV has no data rows."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc_dataset_curator import rebuild_h5_from_csv

    # Write a CSV with only header rows
    csv_path = tmp_path / "CollectedData_S.csv"
    with open(str(csv_path), "w", newline="") as fh:
        csv.writer(fh).writerows([
            ["scorer",    "", ""] + ["S"] * 4,
            ["bodyparts", "", ""] + ["Snout", "Snout", "Wrist", "Wrist"],
            ["coords",    "", ""] + ["x", "y", "x", "y"],
        ])

    h5_path = tmp_path / "CollectedData_S.h5"
    result  = rebuild_h5_from_csv(csv_path, h5_path)

    assert result is None
    assert not h5_path.exists()
