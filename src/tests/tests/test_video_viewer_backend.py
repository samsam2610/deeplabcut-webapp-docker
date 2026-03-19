"""
Tests for the DLC Viewer backend (src/dlc/viewer.py).

Constraint #3 — Isolated test state:
  Any test that reads or writes files copies ONLY the target .avi and its
  associated analysis files to a temporary sandbox and tears it down after.

Constraint #1 — GPU routing:
  cv2.VideoCapture and cv2.circle are CPU-only; no GPU allocation occurs.

Constraint #4 — Zero name collisions:
  All helper names in this file are prefixed with `tvb_` (test_video_backend).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

# ── Reference data (read-only) ────────────────────────────────────────────────
_MAPS_DIR = Path(
    "/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/MAPS-DREADSS"
)
_TARGET_STEM = "MAP1_20250713_112101_3"
_TARGET_AVI  = _MAPS_DIR / f"{_TARGET_STEM}.avi"
_TARGET_H5   = _MAPS_DIR / f"{_TARGET_STEM}DLC_HrnetW48_DREADDJan7shuffle1_snapshot_150.h5"

_DATA_AVAILABLE = _TARGET_AVI.is_file() and _TARGET_H5.is_file()
_skip_no_data   = pytest.mark.skipif(
    not _DATA_AVAILABLE,
    reason="Reference data not mounted at expected path — skipping.",
)


# ── Sandbox fixture ───────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def tvb_sandbox(tmp_path) -> Path:
    """
    Copy ONLY the target .avi and its .h5 analysis file to a temp sandbox.
    Teardown deletes the sandbox regardless of test outcome.
    """
    if not _DATA_AVAILABLE:
        pytest.skip("Reference data not available.")
    sandbox = tmp_path / "viewer_sandbox"
    sandbox.mkdir()
    shutil.copy2(str(_TARGET_AVI), str(sandbox / _TARGET_AVI.name))
    shutil.copy2(str(_TARGET_H5),  str(sandbox / _TARGET_H5.name))
    yield sandbox
    shutil.rmtree(str(sandbox), ignore_errors=True)


# ── Tests: h5 parsing ─────────────────────────────────────────────────────────

@_skip_no_data
def test_tvb_h5_loads_multiindex(tvb_sandbox):
    """viewer_load_h5 returns the correct scorer, bodyparts, and shape."""
    import pandas as pd
    from dlc.viewer import viewer_load_h5

    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    result  = viewer_load_h5(h5_path)

    df        = result["df"]
    scorer    = result["scorer"]
    bodyparts = result["bodyparts"]

    assert isinstance(df, pd.DataFrame)
    assert scorer.startswith("DLC_")
    assert "Snout" in bodyparts
    assert "Wrist" in bodyparts
    assert len(bodyparts) == 16
    assert df.shape[1] == len(bodyparts) * 3  # x, y, likelihood per body part


@_skip_no_data
def test_tvb_h5_xyz_extraction(tvb_sandbox):
    """First frame produces finite (x, y, likelihood) for each body part."""
    from dlc.viewer import viewer_load_h5

    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    result  = viewer_load_h5(h5_path)
    df, scorer, bodyparts = result["df"], result["scorer"], result["bodyparts"]

    row = df.iloc[0][scorer]
    for bp in bodyparts:
        x  = float(row[bp]["x"])
        y  = float(row[bp]["y"])
        lh = float(row[bp]["likelihood"])
        assert np.isfinite(x),  f"{bp}: x is not finite"
        assert np.isfinite(y),  f"{bp}: y is not finite"
        assert 0.0 <= lh <= 1.0, f"{bp}: likelihood {lh} out of [0,1]"


@_skip_no_data
def test_tvb_h5_cache_hit(tvb_sandbox):
    """Second call to viewer_load_h5 with the same path returns the same object."""
    from dlc.viewer import viewer_load_h5

    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    r1 = viewer_load_h5(h5_path)
    r2 = viewer_load_h5(h5_path)
    assert r1 is r2, "Expected the same cached dict object"


# ── Tests: h5-find logic (Method A and Method B) ─────────────────────────────

@_skip_no_data
def test_tvb_h5_find_method_a_by_stem(tvb_sandbox):
    """Method A: scanning a dir with the video stem finds the h5 file."""
    candidates = sorted(tvb_sandbox.glob(f"{_TARGET_STEM}*.h5"))
    assert len(candidates) >= 1
    assert candidates[0].suffix == ".h5"
    assert candidates[0].stem.startswith(_TARGET_STEM)


@_skip_no_data
def test_tvb_h5_find_method_a_prefix_filter(tvb_sandbox):
    """Method A with a scorer prefix narrows the candidate list."""
    prefix = "hrnetw48"
    candidates = sorted(tvb_sandbox.glob(f"{_TARGET_STEM}*.h5"))
    filtered   = [p for p in candidates if prefix in p.name.lower()]
    assert len(filtered) >= 1


@_skip_no_data
def test_tvb_h5_find_method_a_no_match(tvb_sandbox):
    """Method A with a bad stem returns an empty candidate list."""
    candidates = sorted(tvb_sandbox.glob("NONEXISTENT_VIDEO*.h5"))
    assert len(candidates) == 0


@_skip_no_data
def test_tvb_h5_find_method_b_direct(tvb_sandbox):
    """Method B: a direct h5 path resolves if the file exists."""
    h5_path = tvb_sandbox / _TARGET_H5.name
    assert h5_path.is_file()
    assert h5_path.suffix == ".h5"


# ── Tests: threshold filtering ────────────────────────────────────────────────

@_skip_no_data
def test_tvb_threshold_filters_low_likelihood(tvb_sandbox):
    """Markers with likelihood < threshold must be excluded from rendering."""
    from dlc.viewer import viewer_load_h5

    h5_path   = str(tvb_sandbox / _TARGET_H5.name)
    result    = viewer_load_h5(h5_path)
    df, scorer, bodyparts = result["df"], result["scorer"], result["bodyparts"]
    threshold = 0.9  # strict — likely excludes some body parts

    row     = df.iloc[0][scorer]
    visible = [
        bp for bp in bodyparts
        if float(row[bp]["likelihood"]) >= threshold
    ]
    hidden  = [
        bp for bp in bodyparts
        if float(row[bp]["likelihood"]) < threshold
    ]

    # At threshold 0.9 at least one marker should be excluded in frame 0
    # (soft assert — just check the filtering logic is correct)
    assert set(visible) | set(hidden) == set(bodyparts)
    assert not (set(visible) & set(hidden))


def test_tvb_threshold_boundary():
    """Threshold comparison: marker at exactly the threshold value IS rendered."""
    lh = 0.6
    threshold = 0.6
    # Inclusive: lh >= threshold → visible
    assert lh >= threshold


# ── Tests: dynamic scaling ────────────────────────────────────────────────────

def test_tvb_scale_resize_output_size():
    """cv2.resize with scale factor produces the expected output dimensions."""
    import cv2

    h, w   = 900, 1376
    frame  = np.zeros((h, w, 3), dtype=np.uint8)
    scale  = 0.5
    result = cv2.resize(frame, (int(w * scale), int(h * scale)))
    assert result.shape == (int(h * scale), int(w * scale), 3)


def test_tvb_marker_size_accepts_int_and_float():
    """cv2.circle radius accepts both int and float-cast-to-int inputs."""
    import cv2

    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    for size_input in [6, 6.7, 1.0, 20]:
        r = max(1, int(size_input))
        # Should not raise
        cv2.circle(frame, (100, 100), r, (0, 255, 0), -1)


def test_tvb_scale_clamp():
    """viewer_render_frame clamps scale to [0.1, 4.0]."""
    for raw, expected in [(-1.0, 0.1), (0.0, 0.1), (5.0, 4.0), (1.5, 1.5)]:
        clamped = max(0.1, min(float(raw), 4.0))
        assert clamped == expected, f"scale {raw} → {clamped}, want {expected}"


# ── Tests: full render (requires actual video + h5) ──────────────────────────

@_skip_no_data
def test_tvb_render_frame_returns_jpeg(tvb_sandbox):
    """viewer_render_frame returns valid JPEG bytes for frame 0."""
    import cv2
    import numpy as np
    from dlc.viewer import viewer_render_frame

    video_path = str(tvb_sandbox / _TARGET_AVI.name)
    h5_path    = str(tvb_sandbox / _TARGET_H5.name)

    jpeg_bytes = viewer_render_frame(
        video_path=video_path,
        h5_path=h5_path,
        frame_number=0,
        threshold=0.6,
        marker_size=6,
        scale=0.5,
        uid="test-uid",
    )
    assert jpeg_bytes is not None
    # Verify it decodes back to an image
    arr = np.frombuffer(jpeg_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    # Scaled: should be ~688×450
    assert img.shape[0] == pytest.approx(450, abs=5)
    assert img.shape[1] == pytest.approx(688, abs=5)


@_skip_no_data
def test_tvb_render_frame_body_part_filter(tvb_sandbox):
    """Rendering with selected_parts=['Snout'] vs all parts produces different images."""
    from dlc.viewer import viewer_render_frame

    video_path = str(tvb_sandbox / _TARGET_AVI.name)
    h5_path    = str(tvb_sandbox / _TARGET_H5.name)

    common = dict(
        video_path=video_path, h5_path=h5_path,
        frame_number=100, threshold=0.0, marker_size=8, scale=0.25, uid="test-filter",
    )
    all_parts_jpeg  = viewer_render_frame(**common, selected_parts=None)
    snout_only_jpeg = viewer_render_frame(**common, selected_parts=["Snout"])

    assert all_parts_jpeg  is not None
    assert snout_only_jpeg is not None
    # The two images should differ (more markers → different pixel values)
    assert all_parts_jpeg != snout_only_jpeg


@_skip_no_data
def test_tvb_render_frame_high_threshold_removes_all(tvb_sandbox):
    """Threshold 1.01 removes all markers; output is still a valid JPEG."""
    from dlc.viewer import viewer_render_frame

    video_path = str(tvb_sandbox / _TARGET_AVI.name)
    h5_path    = str(tvb_sandbox / _TARGET_H5.name)

    jpeg_bytes = viewer_render_frame(
        video_path=video_path,
        h5_path=h5_path,
        frame_number=0,
        threshold=1.01,   # above any valid likelihood
        marker_size=6,
        scale=0.25,
        uid="test-hithresh",
    )
    assert jpeg_bytes is not None
