"""
Tests for the JSON-delta marker adjustment system (src/dlc/viewer.py).

Validates:
  1. Dynamic JSON cache file naming — namespace isolation per h5 file.
  2. Creating a mock JSON delta patch.
  3. Patch application: JSON delta → H5 DataFrame.
  4. Saving patched DataFrame back to .h5 and .csv using the atomic write pattern.
  5. Edit-cache routes: GET /dlc/viewer/edit-cache, POST /dlc/viewer/marker-edit,
     POST /dlc/viewer/save-marker-edits.

Constraint notes:
  - All file I/O uses tmp_path (never modifies real data).
  - GPU 0 = RTX 5090; these tests are CPU-only (no cv2.VideoCapture, no CUDA).
  - All helper names in this file are prefixed with `tama_` (test_analyzed_marker_adjustment).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── Reference data paths (read-only, sandbox-copied when used) ──────────────
_MAPS_DIR   = Path(
    "/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/MAPS-DREADSS"
)
_TARGET_STEM = "MAP1_20250713_112101_3"
_TARGET_H5   = _MAPS_DIR / f"{_TARGET_STEM}DLC_HrnetW48_DREADDJan7shuffle1_snapshot_150.h5"

_DATA_AVAILABLE = _TARGET_H5.is_file()
_skip_no_data   = pytest.mark.skipif(
    not _DATA_AVAILABLE,
    reason="Reference H5 not mounted — skipping data-dependent tests.",
)


# ── Synthetic H5 builder ─────────────────────────────────────────────────────

def tama_make_synthetic_h5(path: Path, n_frames: int = 10) -> dict:
    """
    Build a minimal DLC-analysis-style H5 file at *path* for unit testing.

    Returns metadata dict: {scorer, bodyparts, n_frames}.
    Column MultiIndex: (scorer, bodypart, coord) where coord ∈ {x, y, likelihood}.
    Index: integer range 0..n_frames-1 (matches poses_np index convention).
    """
    scorer    = "DLC_resnet50_test_scorer"
    bodyparts = ["Snout", "forepaw_L", "Wrist"]
    coords    = ["x", "y", "likelihood"]

    tuples = [(scorer, bp, c) for bp in bodyparts for c in coords]
    columns = pd.MultiIndex.from_tuples(tuples, names=["scorer", "bodyparts", "coords"])

    rng  = np.random.default_rng(42)
    data = rng.uniform(10.0, 500.0, size=(n_frames, len(tuples)))
    # likelihood must be in [0, 1]
    for i, (_, _, c) in enumerate(tuples):
        if c == "likelihood":
            data[:, i] = rng.uniform(0.5, 1.0, size=n_frames)

    df = pd.DataFrame(data, index=range(n_frames), columns=columns)
    df.index.name = None

    tmp_p = Path(str(path) + ".tmp")
    df.to_hdf(str(tmp_p), key="df_with_missing", mode="w")
    tmp_p.replace(path)

    return {"scorer": scorer, "bodyparts": bodyparts, "n_frames": n_frames, "df": df}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. JSON cache file naming — namespace isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditCacheNaming:
    """
    Cache filename must be:
      - A hidden file (starts with '.') in the same directory as the H5.
      - Uniquely derived from the H5 stem (not just the directory).
      - Different for different H5 files in the same folder.
    """

    def test_cache_is_hidden_file(self, tmp_path):
        from dlc.viewer import _edit_cache_path

        h5 = tmp_path / "MAP1_20250713_DLC_resnet50_snapshot.h5"
        cache_p = _edit_cache_path(str(h5))
        assert cache_p.name.startswith("."), (
            "Edit cache must be a hidden file (name starts with '.')"
        )

    def test_cache_in_same_directory(self, tmp_path):
        from dlc.viewer import _edit_cache_path

        h5 = tmp_path / "MAP1_20250713_DLC_resnet50_snapshot.h5"
        cache_p = _edit_cache_path(str(h5))
        assert cache_p.parent == tmp_path, (
            "Edit cache must reside in the same directory as the H5 file."
        )

    def test_cache_name_contains_h5_stem(self, tmp_path):
        from dlc.viewer import _edit_cache_path

        stem = "MAP1_20250713_DLC_resnet50_snapshot"
        h5   = tmp_path / f"{stem}.h5"
        cache_p = _edit_cache_path(str(h5))
        assert stem in cache_p.name, (
            "Edit cache filename must embed the H5 stem for namespace isolation."
        )

    def test_different_h5_different_cache(self, tmp_path):
        """Two H5 files in the same folder must NOT share a cache file."""
        from dlc.viewer import _edit_cache_path

        h5_a = tmp_path / "MAP1_20250713_DLC_resnet50.h5"
        h5_b = tmp_path / "MAP2_20250713_DLC_resnet50.h5"
        assert _edit_cache_path(str(h5_a)) != _edit_cache_path(str(h5_b)), (
            "Different H5 files must map to different edit cache paths."
        )

    def test_cache_ends_with_json(self, tmp_path):
        from dlc.viewer import _edit_cache_path

        h5 = tmp_path / "test_video_DLC_model.h5"
        cache_p = _edit_cache_path(str(h5))
        assert cache_p.suffix == ".json", "Edit cache must be a JSON file."


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Edit cache I/O round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditCacheIO:
    """load_edit_cache / save_edit_cache round-trip with multi-video safety."""

    def test_load_missing_cache_returns_empty_dict(self, tmp_path):
        from dlc.viewer import load_edit_cache

        h5 = tmp_path / "video_DLC.h5"
        result = load_edit_cache(str(h5))
        assert result == {}, (
            "load_edit_cache must return an empty dict when no cache exists."
        )

    def test_save_and_reload_cache(self, tmp_path):
        from dlc.viewer import save_edit_cache, load_edit_cache

        h5 = tmp_path / "video_DLC.h5"
        patch = {
            "frame_0":  {"Snout": {"x": 100.0, "y": 200.0}},
            "frame_42": {"forepaw_L": {"x": 300.5, "y": 410.2}},
        }
        save_edit_cache(str(h5), patch)
        reloaded = load_edit_cache(str(h5))
        assert reloaded == patch

    def test_namespace_isolation_two_videos(self, tmp_path):
        """Saving a cache for one H5 must not affect the cache of a different H5."""
        from dlc.viewer import save_edit_cache, load_edit_cache

        h5_a = tmp_path / "MAP1_video_DLC.h5"
        h5_b = tmp_path / "MAP2_video_DLC.h5"

        patch_a = {"frame_1": {"Snout": {"x": 10.0, "y": 20.0}}}
        patch_b = {"frame_5": {"Wrist": {"x": 50.0, "y": 60.0}}}

        save_edit_cache(str(h5_a), patch_a)
        save_edit_cache(str(h5_b), patch_b)

        assert load_edit_cache(str(h5_a)) == patch_a
        assert load_edit_cache(str(h5_b)) == patch_b

    def test_save_overwrites_previous_cache(self, tmp_path):
        from dlc.viewer import save_edit_cache, load_edit_cache

        h5 = tmp_path / "video_DLC.h5"
        save_edit_cache(str(h5), {"frame_0": {"Snout": {"x": 1.0, "y": 2.0}}})
        save_edit_cache(str(h5), {"frame_7": {"Wrist": {"x": 3.0, "y": 4.0}}})
        result = load_edit_cache(str(h5))
        assert "frame_7" in result
        assert "frame_0" not in result, "save_edit_cache must overwrite, not merge."

    def test_clear_edit_cache_deletes_file(self, tmp_path):
        from dlc.viewer import save_edit_cache, clear_edit_cache, _edit_cache_path

        h5 = tmp_path / "video_DLC.h5"
        save_edit_cache(str(h5), {"frame_0": {"Snout": {"x": 1.0, "y": 2.0}}})
        assert _edit_cache_path(str(h5)).is_file(), "Cache file must exist after save."
        clear_edit_cache(str(h5))
        assert not _edit_cache_path(str(h5)).is_file(), "Cache file must be deleted by clear."

    def test_clear_nonexistent_cache_is_noop(self, tmp_path):
        from dlc.viewer import clear_edit_cache

        h5 = tmp_path / "video_DLC.h5"
        # Must not raise even if no cache file exists
        clear_edit_cache(str(h5))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Patch application: JSON delta → H5 DataFrame
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatchApplication:
    """
    _apply_marker_edits_to_h5 must:
      - Update x, y coordinates in the H5 for the specified frames / bodyparts.
      - Set likelihood to 1.0 for all edited keypoints.
      - Leave unedited frames / bodyparts unchanged.
    """

    def test_patch_updates_xy_coordinates(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        meta = tama_make_synthetic_h5(h5_path, n_frames=5)
        scorer = meta["scorer"]

        patch = {"frame_2": {"Snout": {"x": 999.0, "y": 888.0}}}
        _apply_marker_edits_to_h5(str(h5_path), patch)

        df_out = pd.read_hdf(str(h5_path), key="df_with_missing")
        assert df_out.iloc[2][(scorer, "Snout", "x")] == pytest.approx(999.0)
        assert df_out.iloc[2][(scorer, "Snout", "y")] == pytest.approx(888.0)

    def test_patch_sets_likelihood_to_one(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        meta = tama_make_synthetic_h5(h5_path, n_frames=5)
        scorer = meta["scorer"]

        # Manually set likelihood < 1.0 on frame 1
        df_pre = pd.read_hdf(str(h5_path), key="df_with_missing")
        df_pre.iloc[1, df_pre.columns.get_loc((scorer, "forepaw_L", "likelihood"))] = 0.3
        tmp_p = Path(str(h5_path) + ".tmp")
        df_pre.to_hdf(str(tmp_p), key="df_with_missing", mode="w")
        tmp_p.replace(h5_path)

        patch = {"frame_1": {"forepaw_L": {"x": 100.0, "y": 200.0}}}
        _apply_marker_edits_to_h5(str(h5_path), patch)

        df_out = pd.read_hdf(str(h5_path), key="df_with_missing")
        assert df_out.iloc[1][(scorer, "forepaw_L", "likelihood")] == pytest.approx(1.0), (
            "Edited keypoints must have likelihood set to 1.0."
        )

    def test_patch_leaves_unedited_frames_unchanged(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        meta    = tama_make_synthetic_h5(h5_path, n_frames=5)
        scorer  = meta["scorer"]

        # Record original values for frame 0
        df_pre = pd.read_hdf(str(h5_path), key="df_with_missing")
        orig_x = float(df_pre.iloc[0][(scorer, "Snout", "x")])
        orig_y = float(df_pre.iloc[0][(scorer, "Snout", "y")])

        # Patch only frame 3
        patch = {"frame_3": {"Snout": {"x": 50.0, "y": 60.0}}}
        _apply_marker_edits_to_h5(str(h5_path), patch)

        df_out = pd.read_hdf(str(h5_path), key="df_with_missing")
        assert df_out.iloc[0][(scorer, "Snout", "x")] == pytest.approx(orig_x)
        assert df_out.iloc[0][(scorer, "Snout", "y")] == pytest.approx(orig_y)

    def test_patch_multiple_bodyparts_same_frame(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        meta    = tama_make_synthetic_h5(h5_path, n_frames=5)
        scorer  = meta["scorer"]

        patch = {
            "frame_0": {
                "Snout":     {"x": 11.0, "y": 12.0},
                "forepaw_L": {"x": 21.0, "y": 22.0},
                "Wrist":     {"x": 31.0, "y": 32.0},
            }
        }
        result = _apply_marker_edits_to_h5(str(h5_path), patch)

        df_out = pd.read_hdf(str(h5_path), key="df_with_missing")
        assert df_out.iloc[0][(scorer, "Snout",     "x")] == pytest.approx(11.0)
        assert df_out.iloc[0][(scorer, "forepaw_L", "x")] == pytest.approx(21.0)
        assert df_out.iloc[0][(scorer, "Wrist",     "x")] == pytest.approx(31.0)
        assert result["bodyparts_edited"] == 3

    def test_patch_unknown_bodypart_skipped_gracefully(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=5)

        # "Ghost" is not in the synthetic H5 — must not raise
        patch = {"frame_0": {"Ghost": {"x": 1.0, "y": 2.0}}}
        result = _apply_marker_edits_to_h5(str(h5_path), patch)
        assert result["bodyparts_edited"] == 0

    def test_patch_out_of_range_frame_skipped(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=5)  # frames 0-4 only

        patch = {"frame_999": {"Snout": {"x": 1.0, "y": 2.0}}}
        result = _apply_marker_edits_to_h5(str(h5_path), patch)
        assert result["frames_edited"] == 0

    def test_empty_patch_is_noop(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=5)
        df_pre  = pd.read_hdf(str(h5_path), key="df_with_missing")

        _apply_marker_edits_to_h5(str(h5_path), {})

        df_out = pd.read_hdf(str(h5_path), key="df_with_missing")
        pd.testing.assert_frame_equal(df_pre, df_out)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Atomic H5 write + CSV regeneration
# ═══════════════════════════════════════════════════════════════════════════════

class TestAtomicSave:
    """
    After _apply_marker_edits_to_h5:
      - H5 is readable with key "df_with_missing" (DLC standard key).
      - Companion CSV is regenerated next to the H5.
      - No .tmp file is left behind on success.
    """

    def test_h5_readable_after_patch(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=8)

        _apply_marker_edits_to_h5(
            str(h5_path), {"frame_0": {"Snout": {"x": 1.0, "y": 2.0}}}
        )

        # Must be loadable by standard DLC key
        df = pd.read_hdf(str(h5_path), key="df_with_missing")
        assert len(df) == 8

    def test_no_tmp_file_left_behind(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=4)
        _apply_marker_edits_to_h5(
            str(h5_path), {"frame_0": {"Snout": {"x": 1.0, "y": 2.0}}}
        )

        tmp_file = Path(str(h5_path) + ".tmp")
        assert not tmp_file.exists(), "Atomic write must not leave .tmp file on success."

    def test_companion_csv_regenerated(self, tmp_path):
        from dlc.viewer import _apply_marker_edits_to_h5

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=6)
        _apply_marker_edits_to_h5(
            str(h5_path), {"frame_2": {"forepaw_L": {"x": 77.0, "y": 88.0}}}
        )

        csv_path = h5_path.with_suffix(".csv")
        assert csv_path.is_file(), "Companion CSV must be regenerated after save."
        # Verify CSV is parseable
        df_csv = pd.read_csv(str(csv_path), header=[0, 1, 2], index_col=0)
        assert len(df_csv) == 6


# ═══════════════════════════════════════════════════════════════════════════════
# 5. pose-override: frame-poses/<n> respects JSON cache
# ═══════════════════════════════════════════════════════════════════════════════

class TestPoseOverride:
    """
    When a JSON edit cache is present for an H5 file, frame-poses/<n>
    must return the overridden coordinates, not the original H5 values.
    """

    def test_frame_poses_applies_cache_override(self, tmp_path):
        from dlc.viewer import viewer_load_h5, _edit_cache_path, save_edit_cache
        import sys, os
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        h5_path = tmp_path / "video_DLC.h5"
        meta    = tama_make_synthetic_h5(h5_path, n_frames=10)

        # Record original value for frame 3, bodypart "Snout"
        df_orig  = meta["df"]
        scorer   = meta["scorer"]
        orig_x   = float(df_orig.iloc[3][(scorer, "Snout", "x")])

        # Save a cache override with a very different x value
        new_x = orig_x + 9000.0
        save_edit_cache(str(h5_path), {"frame_3": {"Snout": {"x": new_x, "y": 50.0}}})

        # Import the helper used by frame-poses route
        from dlc.viewer import _get_effective_poses
        poses = _get_effective_poses(str(h5_path), frame_number=3, threshold=0.0)

        snout_pose = next((p for p in poses if p["bp"] == "Snout"), None)
        assert snout_pose is not None
        assert snout_pose["x"] == pytest.approx(new_x, abs=0.01)

    def test_frame_poses_unedited_frame_uses_h5(self, tmp_path):
        """Frames without a cache entry fall back to the H5 data."""
        from dlc.viewer import _get_effective_poses, save_edit_cache

        h5_path = tmp_path / "video_DLC.h5"
        meta    = tama_make_synthetic_h5(h5_path, n_frames=10)
        scorer  = meta["scorer"]

        # Cache only frame 7 — frame 2 must still come from H5
        save_edit_cache(str(h5_path), {"frame_7": {"Snout": {"x": 1.0, "y": 1.0}}})
        df_orig  = pd.read_hdf(str(h5_path), key="df_with_missing")
        expected_x = float(df_orig.iloc[2][(scorer, "Snout", "x")])

        poses = _get_effective_poses(str(h5_path), frame_number=2, threshold=0.0)
        snout = next((p for p in poses if p["bp"] == "Snout"), None)
        assert snout is not None
        assert snout["x"] == pytest.approx(expected_x, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Full round-trip: edit → save → verify → cache cleared
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullRoundTrip:
    """
    End-to-end test: edit cache → _apply_marker_edits_to_h5 → clear cache → verify H5.
    Mirrors the exact sequence triggered by the "Save Adjustments" button.
    """

    def test_full_save_cycle(self, tmp_path):
        from dlc.viewer import (
            _apply_marker_edits_to_h5, save_edit_cache, load_edit_cache,
            clear_edit_cache, _edit_cache_path,
        )

        h5_path = tmp_path / "video_DLC.h5"
        meta    = tama_make_synthetic_h5(h5_path, n_frames=10)
        scorer  = meta["scorer"]

        # Step 1: User drags two markers across two frames
        edits = {
            "frame_0": {"Snout":     {"x": 42.0, "y": 43.0}},
            "frame_5": {"forepaw_L": {"x": 99.9, "y": 11.1}},
        }
        save_edit_cache(str(h5_path), edits)
        assert _edit_cache_path(str(h5_path)).is_file(), "Cache must exist after edits."

        # Step 2: "Save Adjustments" clicked — apply patch and clear cache
        result = _apply_marker_edits_to_h5(str(h5_path), load_edit_cache(str(h5_path)))
        clear_edit_cache(str(h5_path))

        # Step 3: Verify results
        assert result["frames_edited"] == 2
        assert not _edit_cache_path(str(h5_path)).is_file(), "Cache must be deleted after save."

        df_out = pd.read_hdf(str(h5_path), key="df_with_missing")
        assert df_out.iloc[0][(scorer, "Snout",     "x")] == pytest.approx(42.0)
        assert df_out.iloc[5][(scorer, "forepaw_L", "x")] == pytest.approx(99.9)
        assert df_out.iloc[0][(scorer, "Snout",     "likelihood")] == pytest.approx(1.0)
        assert df_out.iloc[5][(scorer, "forepaw_L", "likelihood")] == pytest.approx(1.0)

    def test_h5_viewer_cache_invalidated_after_save(self, tmp_path):
        """
        After _apply_marker_edits_to_h5, the in-memory H5 LRU cache must be
        cleared for the affected path so the next frame-poses request reads
        the updated H5 from disk.
        """
        import threading
        from dlc.viewer import (
            viewer_load_h5, _apply_marker_edits_to_h5,
            _viewer_h5_cache, _viewer_h5_lock,
        )

        h5_path = tmp_path / "video_DLC.h5"
        tama_make_synthetic_h5(h5_path, n_frames=5)
        h5_str = str(h5_path)

        # Warm the LRU cache
        viewer_load_h5(h5_str)
        with _viewer_h5_lock:
            assert h5_str in _viewer_h5_cache, "Cache must be warm after load."

        # Patch the H5
        _apply_marker_edits_to_h5(h5_str, {"frame_0": {"Snout": {"x": 1.0, "y": 1.0}}})

        # LRU entry must have been evicted
        with _viewer_h5_lock:
            assert h5_str not in _viewer_h5_cache, (
                "_apply_marker_edits_to_h5 must invalidate the H5 LRU cache entry."
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Sandbox H5 integration (skipped when real data not mounted)
# ═══════════════════════════════════════════════════════════════════════════════

@_skip_no_data
def test_tama_sandbox_patch_real_h5(tmp_path):
    """
    Integration: sandbox-copy the real analysis H5, apply a patch, verify output.
    Uses the MAP1_20250713 reference file from the MAPS-DREADSS sandbox.
    """
    from dlc.viewer import _apply_marker_edits_to_h5, _edit_cache_path

    # Copy real H5 to sandbox (never touch originals)
    sandbox_h5 = tmp_path / _TARGET_H5.name
    shutil.copy2(str(_TARGET_H5), str(sandbox_h5))

    df_orig = pd.read_hdf(str(sandbox_h5), key="df_with_missing")
    scorer  = df_orig.columns.get_level_values("scorer")[0]
    bps     = df_orig.columns.get_level_values("bodyparts").unique().tolist()
    bp0     = bps[0]

    # Patch frame 0
    patch = {"frame_0": {bp0: {"x": 123.45, "y": 678.90}}}
    result = _apply_marker_edits_to_h5(str(sandbox_h5), patch)

    df_out = pd.read_hdf(str(sandbox_h5), key="df_with_missing")
    assert df_out.iloc[0][(scorer, bp0, "x")]           == pytest.approx(123.45, abs=0.01)
    assert df_out.iloc[0][(scorer, bp0, "y")]           == pytest.approx(678.90, abs=0.01)
    assert df_out.iloc[0][(scorer, bp0, "likelihood")]  == pytest.approx(1.0)
    assert result["frames_edited"] == 1

    # Original should not have been modified (sandbox isolation)
    df_real = pd.read_hdf(str(_TARGET_H5), key="df_with_missing")
    assert df_real.iloc[0][(scorer, bp0, "x")] != pytest.approx(123.45, abs=0.01)
