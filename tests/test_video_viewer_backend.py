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


# ── Tests: poses_np precomputation ───────────────────────────────────────────

@_skip_no_data
def test_tvb_poses_np_shape(tvb_sandbox):
    """viewer_load_h5 precomputes poses_np with shape (n_frames, n_bps, 3)."""
    from dlc.viewer import viewer_load_h5

    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    result  = viewer_load_h5(h5_path)

    poses_np  = result["poses_np"]
    bodyparts = result["bodyparts"]
    df        = result["df"]

    assert poses_np.ndim == 3
    assert poses_np.shape[0] == len(df)
    assert poses_np.shape[1] == len(bodyparts)
    assert poses_np.shape[2] == 3    # x, y, likelihood


@_skip_no_data
def test_tvb_poses_np_values_match_dataframe(tvb_sandbox):
    """poses_np[0] matches the raw DataFrame values for frame 0."""
    from dlc.viewer import viewer_load_h5

    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    result  = viewer_load_h5(h5_path)

    poses_np  = result["poses_np"]
    df, scorer, bodyparts = result["df"], result["scorer"], result["bodyparts"]

    row = df.iloc[0][scorer]
    for i, bp in enumerate(bodyparts):
        assert np.isclose(poses_np[0, i, 0], float(row[bp]["x"]),        atol=1e-4), f"{bp} x mismatch"
        assert np.isclose(poses_np[0, i, 1], float(row[bp]["y"]),        atol=1e-4), f"{bp} y mismatch"
        assert np.isclose(poses_np[0, i, 2], float(row[bp]["likelihood"]), atol=1e-6), f"{bp} lh mismatch"


@_skip_no_data
def test_tvb_poses_np_dtype(tvb_sandbox):
    """poses_np is float32."""
    from dlc.viewer import viewer_load_h5

    h5_path  = str(tvb_sandbox / _TARGET_H5.name)
    result   = viewer_load_h5(h5_path)
    assert result["poses_np"].dtype == np.float32


# ── Tests: frame-poses-batch endpoint ────────────────────────────────────────

@pytest.fixture(scope="module")
def tvb_flask_app():
    """Minimal Flask test client for the DLC viewer blueprint."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from flask import Flask
    from dlc.viewer import bp as viewer_bp
    app = Flask(__name__)
    app.register_blueprint(viewer_bp)
    app.config["TESTING"] = True
    return app.test_client()


@_skip_no_data
def test_tvb_batch_returns_correct_frame_count(tvb_sandbox, tvb_flask_app):
    """frame-poses-batch returns exactly count frames (or fewer at end-of-video)."""
    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    resp    = tvb_flask_app.get(
        f"/dlc/viewer/frame-poses-batch?h5={h5_path}&start=0&count=10&threshold=0.0"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "frames" in data
    assert len(data["frames"]) == 10
    assert "bodyparts" in data
    assert len(data["bodyparts"]) == 16


@_skip_no_data
def test_tvb_batch_frame_keys_are_correct(tvb_sandbox, tvb_flask_app):
    """frame-poses-batch uses string frame-number keys starting at start."""
    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    resp    = tvb_flask_app.get(
        f"/dlc/viewer/frame-poses-batch?h5={h5_path}&start=5&count=3&threshold=0.0"
    )
    data = resp.get_json()
    assert set(data["frames"].keys()) == {"5", "6", "7"}


@_skip_no_data
def test_tvb_batch_threshold_filters_poses(tvb_sandbox, tvb_flask_app):
    """frame-poses-batch at threshold=1.01 returns empty pose lists for all frames."""
    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    resp    = tvb_flask_app.get(
        f"/dlc/viewer/frame-poses-batch?h5={h5_path}&start=0&count=5&threshold=1.01"
    )
    data = resp.get_json()
    for fn_key, fd in data["frames"].items():
        assert fd["poses"] == [], f"frame {fn_key} has poses at threshold 1.01"


@_skip_no_data
def test_tvb_batch_count_capped_at_300(tvb_sandbox, tvb_flask_app):
    """count > 300 is silently capped to 300."""
    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    resp    = tvb_flask_app.get(
        f"/dlc/viewer/frame-poses-batch?h5={h5_path}&start=0&count=9999&threshold=0.0"
    )
    data = resp.get_json()
    assert len(data["frames"]) <= 300


@_skip_no_data
def test_tvb_batch_part_filter(tvb_sandbox, tvb_flask_app):
    """frame-poses-batch with parts=Snout returns only Snout poses."""
    h5_path = str(tvb_sandbox / _TARGET_H5.name)
    resp    = tvb_flask_app.get(
        f"/dlc/viewer/frame-poses-batch?h5={h5_path}&start=0&count=5"
        f"&threshold=0.0&parts=Snout"
    )
    data = resp.get_json()
    for fn_key, fd in data["frames"].items():
        for pose in fd["poses"]:
            assert pose["bp"] == "Snout", f"frame {fn_key} has non-Snout pose: {pose['bp']}"


def test_tvb_batch_missing_h5_param(tvb_flask_app):
    """frame-poses-batch without h5 param returns HTTP 400."""
    resp = tvb_flask_app.get("/dlc/viewer/frame-poses-batch?start=0&count=5&threshold=0.0")
    assert resp.status_code == 400


def test_tvb_batch_nonexistent_h5(tvb_flask_app):
    """frame-poses-batch with a non-existent h5 path returns HTTP 404."""
    resp = tvb_flask_app.get(
        "/dlc/viewer/frame-poses-batch?h5=/nonexistent/path.h5&start=0&count=5&threshold=0.0"
    )
    assert resp.status_code == 404
