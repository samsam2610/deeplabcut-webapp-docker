"""Unit tests for the incremental 3D canonical store (dlc/canonical_3d.py).

Mirrors tests/test_canonical_analysis_file.py / test_canonical_unfinalize.py but
for the anipose flat pose-3d CSV format. CSV-only (no HDF5) so it runs on the host
python without `tables`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import canonical_3d as c3d  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

_BPS = ("nose", "tail")
# deliberately non-alphabetical column order to prove order preservation
_COLS = []
for _bp in _BPS:
    _COLS += [f"{_bp}_z", f"{_bp}_x", f"{_bp}_y", f"{_bp}_error",
              f"{_bp}_ncams", f"{_bp}_score"]


def _range_df(frames, value=1.0, error=1.0):
    """Build an anipose-style pose-3d frame indexed by GLOBAL frame number."""
    rows = {}
    for c in _COLS:
        if c.endswith("_error"):
            rows[c] = [float(error)] * len(frames)
        elif c.endswith("_ncams"):
            rows[c] = [2.0] * len(frames)
        elif c.endswith("_score"):
            rows[c] = [0.9] * len(frames)
        else:
            rows[c] = [float(value)] * len(frames)
    df = pd.DataFrame(rows, index=pd.Index(list(frames), name="fnum"))
    return df[_COLS]


# ── pair name ──────────────────────────────────────────────────────────────

class TestPairName:
    def test_cam0_and_cam1_map_to_same_pair(self):
        n0 = c3d.pair_name_from_cam0("/x/surv1_cam0_20260123_120000.avi")
        n1 = c3d.pair_name_from_cam0("/x/surv1_cam1_20260123_120000.avi")
        assert n0 == n1
        assert "_cam_" in n0
        assert "cam0" not in n0 and "cam1" not in n0

    def test_no_cam_token_returns_stem(self):
        assert c3d.pair_name_from_cam0("/x/plainvideo.avi") == "plainvideo"


# ── paths ──────────────────────────────────────────────────────────────────

class TestPaths:
    def test_raw_and_filtered_paths(self, tmp_path):
        raw = c3d.canonical_3d_csv_path(tmp_path, "surv1_cam_x")
        filt = c3d.filtered_3d_csv_path(tmp_path, "surv1_cam_x")
        assert raw == tmp_path / "pose-3d" / "surv1_cam_x_3d.csv"
        assert filt == tmp_path / "pose-3d-filtered" / "surv1_cam_x_3d.csv"


# ── dense merge ────────────────────────────────────────────────────────────

class TestDenseMerge:
    def test_single_range_dense_reindex(self, tmp_path):
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(5, 10)))
        df = pd.read_csv(c3d.canonical_3d_csv_path(tmp_path, "p"), index_col=0)
        # dense 0..9
        assert list(df.index) == list(range(10))
        assert df.loc[0:4, "nose_x"].isna().all()
        assert (df.loc[5:9, "nose_x"] == 1.0).all()

    def test_two_non_overlapping_ranges_coexist(self, tmp_path):
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 5), value=1.0))
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(10, 15), value=3.0))
        df = pd.read_csv(c3d.canonical_3d_csv_path(tmp_path, "p"), index_col=0)
        assert list(df.index) == list(range(15))
        assert (df.loc[0:4, "nose_x"] == 1.0).all()
        assert df.loc[5:9, "nose_x"].isna().all()      # gap stays NaN
        assert (df.loc[10:14, "nose_x"] == 3.0).all()

    def test_rerun_overwrites_only_its_rows(self, tmp_path):
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 5), value=1.0))
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(10, 15), value=3.0))
        # re-run frames 0..4 with new values
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 5), value=9.0))
        df = pd.read_csv(c3d.canonical_3d_csv_path(tmp_path, "p"), index_col=0)
        assert (df.loc[0:4, "nose_x"] == 9.0).all()    # overwritten
        assert (df.loc[10:14, "nose_x"] == 3.0).all()  # untouched

    def test_column_order_preserved_across_runs(self, tmp_path):
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 3)))
        first_cols = list(pd.read_csv(
            c3d.canonical_3d_csv_path(tmp_path, "p"), index_col=0).columns)
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(3, 6)))
        second_cols = list(pd.read_csv(
            c3d.canonical_3d_csv_path(tmp_path, "p"), index_col=0).columns)
        assert first_cols == second_cols == _COLS


# ── medfilt splice ─────────────────────────────────────────────────────────

class TestMedfiltSplice:
    def _config(self):
        return {"filter3d": {"medfilt": 5, "offset_threshold": 15}}

    def test_writes_only_the_range_into_filtered(self, tmp_path):
        # raw canonical spans 0..19
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 20)))
        c3d.medfilt_range_and_splice(tmp_path, "p", 5, 5, self._config())
        filt = pd.read_csv(c3d.filtered_3d_csv_path(tmp_path, "p"), index_col=0)
        # only frames 5..9 present, everything else NaN
        assert filt.loc[5:9, "nose_x"].notna().all()
        assert filt.loc[0:4, "nose_x"].isna().all()
        assert filt.loc[10:, "nose_x"].isna().all()

    def test_median_filter_smooths_spike(self, tmp_path):
        df = _range_df(range(0, 11), value=0.0)
        df.loc[5, "nose_x"] = 100.0  # spike in the middle
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        c3d.medfilt_range_and_splice(tmp_path, "p", 0, 11, self._config())
        filt = pd.read_csv(c3d.filtered_3d_csv_path(tmp_path, "p"), index_col=0)
        # spike removed by the median filter
        assert abs(filt.loc[5, "nose_x"]) < 1.0

    def test_absent_raw_is_noop(self, tmp_path):
        # should not raise
        c3d.medfilt_range_and_splice(tmp_path, "p", 0, 5, self._config())
        assert not c3d.filtered_3d_csv_path(tmp_path, "p").exists()


# ── unfinalize ─────────────────────────────────────────────────────────────

class TestUnfinalize:
    def test_clears_only_its_rows_in_both_canonicals(self, tmp_path):
        cfg = {"filter3d": {"medfilt": 5, "offset_threshold": 15}}
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 20)))
        c3d.medfilt_range_and_splice(tmp_path, "p", 0, 20, cfg)
        cleared = c3d.unfinalize_3d_range(tmp_path, "p", 5, 5)
        assert cleared == 5
        raw = pd.read_csv(c3d.canonical_3d_csv_path(tmp_path, "p"), index_col=0)
        filt = pd.read_csv(c3d.filtered_3d_csv_path(tmp_path, "p"), index_col=0)
        for d in (raw, filt):
            assert d.loc[5:9, "nose_x"].isna().all()   # cleared
            assert d.loc[0:4, "nose_x"].notna().all()  # kept
            assert d.loc[10:14, "nose_x"].notna().all()

    def test_absent_canonical_returns_zero(self, tmp_path):
        assert c3d.unfinalize_3d_range(tmp_path, "p", 0, 5) == 0


# ── coverage ───────────────────────────────────────────────────────────────

class TestCoverage:
    def test_absent_canonical_all_zeros(self, tmp_path):
        cov = c3d.read_3d_coverage(tmp_path, "p", 4)
        assert cov == [0.0, 0.0, 0.0, 0.0]

    def test_buckets_reflect_presence(self, tmp_path):
        # frames 0..9 present, 10..19 NaN
        df = _range_df(range(0, 20))
        df.loc[10:19, [c for c in _COLS]] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        cov = c3d.read_3d_coverage(tmp_path, "p", 4)
        assert cov == [1.0, 1.0, 0.0, 0.0]

    def test_nframes_helper(self, tmp_path):
        assert c3d.canonical_3d_nframes(tmp_path, "p") == 0
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 12)))
        assert c3d.canonical_3d_nframes(tmp_path, "p") == 12

    def test_coverage_scales_to_total_frames(self, tmp_path):
        # 3D only at frames 100..109; the real video is 1000 frames long. The
        # bar must scale to the full video (so it aligns with the seek/finalized
        # timelines), NOT to the canonical's own length (110).
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(100, 110)))
        cov = c3d.read_3d_coverage(tmp_path, "p", 10, total_frames=1000)
        assert cov[1] > 0.0                       # frames 100..109 → bucket 1 (100..199)
        assert cov[9] == 0.0                      # far right MUST be empty (the bug)
        assert sum(1 for b in cov if b > 0) == 1  # exactly one region lit

    def test_coverage_without_total_frames_uses_canonical_len(self, tmp_path):
        # Back-compat: no total_frames → scale to the canonical's dense length.
        c3d.write_range_to_canonical_3d(tmp_path, "p", _range_df(range(0, 20)))
        cov = c3d.read_3d_coverage(tmp_path, "p", 4)
        assert cov == [1.0, 1.0, 1.0, 1.0]


# ── skeleton derivation ──────────────────────────────────────────────────────

class TestDeriveSkeleton:
    def test_single_finger_full_chain(self):
        bps = ["Wrist", "MCP-1", "PIP-1", "DIP-1"]
        assert c3d.derive_skeleton(bps) == [
            ["Wrist", "MCP-1"], ["MCP-1", "PIP-1"], ["PIP-1", "DIP-1"]]

    def test_multiple_fingers_generic_k(self):
        # k derived from names, not hardcoded 1..4 — include k=7.
        bps = ["Wrist", "MCP-1", "PIP-1", "DIP-1", "MCP-7", "PIP-7", "DIP-7"]
        bones = c3d.derive_skeleton(bps)
        assert ["Wrist", "MCP-7"] in bones
        assert ["MCP-7", "PIP-7"] in bones
        assert ["PIP-7", "DIP-7"] in bones
        assert ["Wrist", "MCP-1"] in bones

    def test_absent_joints_omitted(self):
        # No PIP-1 → the MCP-1→PIP-1 and PIP-1→DIP-1 bones must be dropped.
        bps = ["Wrist", "MCP-1", "DIP-1"]
        bones = c3d.derive_skeleton(bps)
        assert ["Wrist", "MCP-1"] in bones
        assert ["MCP-1", "PIP-1"] not in bones
        assert ["PIP-1", "DIP-1"] not in bones

    def test_missing_wrist_drops_first_bone(self):
        bps = ["MCP-1", "PIP-1", "DIP-1"]
        bones = c3d.derive_skeleton(bps)
        assert ["Wrist", "MCP-1"] not in bones
        assert ["MCP-1", "PIP-1"] in bones
        assert ["PIP-1", "DIP-1"] in bones

    def test_non_chain_parts_unconnected(self):
        bps = ["Snout", "Left-Paw", "Pellet"]
        assert c3d.derive_skeleton(bps) == []

    def test_mixed_parts_only_chain_bones(self):
        bps = ["Snout", "Pellet", "Wrist", "MCP-2", "PIP-2", "DIP-2", "Left-Paw"]
        bones = c3d.derive_skeleton(bps)
        # only the finger-2 chain, nothing touching Snout/Pellet/Left-Paw
        assert bones == [["Wrist", "MCP-2"], ["MCP-2", "PIP-2"], ["PIP-2", "DIP-2"]]

    def test_empty_input(self):
        assert c3d.derive_skeleton([]) == []


# ── poses-3d payload ─────────────────────────────────────────────────────────

_POSE_BPS = ("Wrist", "MCP-1", "PIP-1", "DIP-1", "Snout")
_POSE_COLS = []
for _bp in _POSE_BPS:
    _POSE_COLS += [f"{_bp}_x", f"{_bp}_y", f"{_bp}_z",
                   f"{_bp}_error", f"{_bp}_ncams", f"{_bp}_score"]


def _pose_df(frames, base=0.0):
    """Build a pose-3d frame; each bp/frame gets distinct x/y/z so bounds are
    non-degenerate. bp index i, frame offset f → x=base+i, y=base+i+f, z=i*2."""
    rows = {c: [] for c in _POSE_COLS}
    for f_off, _fr in enumerate(frames):
        for i, bp in enumerate(_POSE_BPS):
            rows[f"{bp}_x"].append(base + i)
            rows[f"{bp}_y"].append(base + i + f_off)
            rows[f"{bp}_z"].append(i * 2.0)
            rows[f"{bp}_error"].append(1.0)
            rows[f"{bp}_ncams"].append(2.0)
            rows[f"{bp}_score"].append(0.9)
    df = pd.DataFrame(rows, index=pd.Index(list(frames), name="fnum"))
    return df[_POSE_COLS]


class TestReadPoses3d:
    def test_absent_canonical_empty_at_bounds_null(self, tmp_path):
        out = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        assert out == {"bodyparts": [], "skeleton": [],
                       "frames": [], "points": [], "bounds": None}

    def test_bodyparts_sorted_and_skeleton(self, tmp_path):
        c3d.write_range_to_canonical_3d(tmp_path, "p", _pose_df(range(0, 3)))
        c3d.medfilt_range_and_splice(
            tmp_path, "p", 0, 3, {"filter3d": {"medfilt": 3}})
        out = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        assert out["bodyparts"] == list(_POSE_BPS)
        # skeleton derived from those bodyparts (Snout unconnected)
        assert out["skeleton"] == [
            ["Wrist", "MCP-1"], ["MCP-1", "PIP-1"], ["PIP-1", "DIP-1"]]

    def test_populated_frames_only(self, tmp_path):
        # raw spans 0..9 but only 3..6 have data (rest NaN).
        df = _pose_df(range(0, 10))
        df.loc[0:2, _POSE_COLS] = np.nan
        df.loc[7:9, _POSE_COLS] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        assert out["frames"] == [3, 4, 5, 6]
        assert len(out["points"]) == 4

    def test_points_shape_and_null_for_nan_bodypart(self, tmp_path):
        df = _pose_df(range(0, 2))
        # blank out Snout's coords on the first frame only
        df.loc[0, ["Snout_x", "Snout_y", "Snout_z"]] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        bps = out["bodyparts"]
        snout_j = bps.index("Snout")
        # frame 0 → Snout null, all others present (list aligned to bodyparts)
        row0 = out["points"][0]
        assert len(row0) == len(bps)
        assert row0[snout_j] is None
        assert all(isinstance(p, list) and len(p) == 3
                   for j, p in enumerate(row0) if j != snout_j)
        # frame 1 → Snout present
        assert out["points"][1][snout_j] is not None

    def test_source_raw_vs_filtered_reads_right_file(self, tmp_path):
        # raw has frames 0..4; filtered only has 2..3 (median-spliced subset).
        c3d.write_range_to_canonical_3d(tmp_path, "p", _pose_df(range(0, 5)))
        c3d.medfilt_range_and_splice(
            tmp_path, "p", 2, 2, {"filter3d": {"medfilt": 3}})
        raw = c3d.read_poses_3d(tmp_path, "p", source="raw")
        filt = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        assert raw["frames"] == [0, 1, 2, 3, 4]
        assert filt["frames"] == [2, 3]

    def test_bounds_center_and_size(self, tmp_path):
        # Single frame, known coords: bp i → (i, i, 2i) for i in 0..4.
        c3d.write_range_to_canonical_3d(tmp_path, "p", _pose_df([0]))
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        b = out["bounds"]
        # x,y span 0..4 (mid 2), z spans 0..8 (mid 4); size = max range = 8.
        assert b["center"] == [2.0, 2.0, 4.0]
        assert b["size"] == 8.0

    def test_default_source_is_filtered(self, tmp_path):
        # Only filtered exists → default (no source arg) must find it.
        c3d.write_range_to_canonical_3d(tmp_path, "p", _pose_df(range(0, 3)))
        c3d.medfilt_range_and_splice(
            tmp_path, "p", 0, 3, {"filter3d": {"medfilt": 3}})
        out = c3d.read_poses_3d(tmp_path, "p")
        assert out["frames"] == [0, 1, 2]

    def test_all_nan_canonical_bounds_null(self, tmp_path):
        df = _pose_df(range(0, 5))
        df.loc[:, _POSE_COLS] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        assert out["bodyparts"] == list(_POSE_BPS)  # columns still present
        assert out["frames"] == []
        assert out["points"] == []
        assert out["bounds"] is None

    def test_scores_errors_aligned_to_points(self, tmp_path):
        df = _pose_df(range(0, 2))
        # blank out Snout's coords on frame 0 → its point is null there
        df.loc[0, ["Snout_x", "Snout_y", "Snout_z"]] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        bps = out["bodyparts"]
        snout_j = bps.index("Snout")
        # scores/errors have the same shape as points
        assert len(out["scores"]) == len(out["points"]) == 2
        assert len(out["errors"]) == len(out["points"])
        for srow, erow, prow in zip(out["scores"], out["errors"], out["points"]):
            assert len(srow) == len(erow) == len(prow) == len(bps)
        # frame 0: Snout point null → score & error null there too
        assert out["points"][0][snout_j] is None
        assert out["scores"][0][snout_j] is None
        assert out["errors"][0][snout_j] is None
        # other bodyparts on frame 0 carry the CSV values (score 0.9, error 1.0)
        for j in range(len(bps)):
            if j != snout_j:
                assert out["scores"][0][j] == 0.9
                assert out["errors"][0][j] == 1.0
        # frame 1: Snout present → its score & error are present
        assert out["points"][1][snout_j] is not None
        assert out["scores"][1][snout_j] == 0.9
        assert out["errors"][1][snout_j] == 1.0

    def test_error_max_is_max_finite_error(self, tmp_path):
        df = _pose_df(range(0, 3))
        df.loc[1, "Wrist_error"] = 7.5  # the max across all populated errors
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        assert out["error_max"] == 7.5

    def test_error_max_null_when_all_errors_nan(self, tmp_path):
        df = _pose_df(range(0, 3))
        df.loc[:, [f"{bp}_error" for bp in _POSE_BPS]] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        # points still populated (x/y/z finite), but no finite errors → null
        assert out["points"][0][0] is not None
        assert out["errors"][0][0] is None
        assert out["error_max"] is None


class TestReadPoses3dRawGating:
    """Filtered display must source score/error from the RAW canonical (aligned
    by fnum), keeping positions from the filtered canonical."""

    def _write_filtered(self, tmp_path, df):
        c3d._atomic_write_csv(c3d.filtered_3d_csv_path(tmp_path, "p"), df)

    def test_filtered_scores_errors_come_from_raw(self, tmp_path):
        # RAW: frames 0..4; Wrist at frame 2 has a LOW score + high error.
        raw = _pose_df(range(0, 5))
        raw.loc[2, "Wrist_score"] = 0.1
        raw.loc[2, "Wrist_error"] = 5.0
        c3d.write_range_to_canonical_3d(tmp_path, "p", raw)
        # FILTERED: same frames, DIFFERENT positions (base=10) and DIFFERENT
        # score/error values (0.95 / 2.0) — proving they are NOT used.
        filt = _pose_df(range(0, 5), base=10.0)
        filt.loc[:, [f"{bp}_score" for bp in _POSE_BPS]] = 0.95
        filt.loc[:, [f"{bp}_error" for bp in _POSE_BPS]] = 2.0
        self._write_filtered(tmp_path, filt)

        out = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        bps = out["bodyparts"]
        wj = bps.index("Wrist")
        fi = out["frames"].index(2)
        # score/error for Wrist@2 come from RAW (0.1 / 5.0), not filtered.
        assert out["scores"][fi][wj] == 0.1
        assert out["errors"][fi][wj] == 5.0
        # position comes from the FILTERED canonical (base=10 → Wrist x=10).
        assert out["points"][fi][wj][0] == 10.0
        # a non-gated bp keeps the raw default score (0.9), not filtered 0.95.
        other = next(j for j, b in enumerate(bps) if b != "Wrist")
        assert out["scores"][fi][other] == 0.9
        # error_max reflects the RAW errors (5.0 is the max), not filtered 2.0.
        assert out["error_max"] == 5.0

    def test_filtered_point_masked_where_raw_absent(self, tmp_path):
        # RAW: frames 0..2 detected; 3..4 NOT detected (all-NaN → the animal
        # dropped out). FILTERED holds/interpolates a value at 3..4.
        raw = _pose_df(range(0, 5))
        raw.loc[[3, 4], :] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", raw)
        filt = _pose_df(range(0, 5), base=10.0)   # filtered has a value everywhere
        self._write_filtered(tmp_path, filt)

        out = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        # Frames with NO raw detection are masked out — never shown at the held
        # position. Only raw-detected frames remain.
        assert out["frames"] == [0, 1, 2]
        for row in out["points"]:
            assert any(p is not None for p in row)

    def test_filtered_frame_absent_from_raw_is_masked(self, tmp_path):
        # RAW only covers frames 0..2; FILTERED has 0..4 (interpolated tail).
        c3d.write_range_to_canonical_3d(tmp_path, "p", _pose_df(range(0, 3)))
        filt = _pose_df(range(0, 5), base=10.0)
        self._write_filtered(tmp_path, filt)

        out = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        # Frames 3,4 are absent from raw (no detection) → masked out entirely.
        assert out["frames"] == [0, 1, 2]
        wj = out["bodyparts"].index("Wrist")
        # frame 2 is in raw → real score.
        assert out["scores"][out["frames"].index(2)][wj] == 0.9

    def test_source_raw_scores_from_raw_regression(self, tmp_path):
        raw = _pose_df(range(0, 3))
        raw.loc[1, "Wrist_score"] = 0.2
        raw.loc[1, "Wrist_error"] = 6.0
        c3d.write_range_to_canonical_3d(tmp_path, "p", raw)
        out = c3d.read_poses_3d(tmp_path, "p", source="raw")
        wj = out["bodyparts"].index("Wrist")
        fi = out["frames"].index(1)
        assert out["scores"][fi][wj] == 0.2
        assert out["errors"][fi][wj] == 6.0
        assert out["error_max"] == 6.0

    def test_absent_raw_filtered_scores_all_null(self, tmp_path):
        # Only the FILTERED canonical exists — no raw to gate against.
        filt = _pose_df(range(0, 3), base=10.0)
        self._write_filtered(tmp_path, filt)
        out = c3d.read_poses_3d(tmp_path, "p", source="filtered")
        assert out["frames"] == [0, 1, 2]
        # positions returned, but every score/error is null and error_max None.
        assert all(p is not None for row in out["points"] for p in row)
        assert all(s is None for row in out["scores"] for s in row)
        assert all(e is None for row in out["errors"] for e in row)
        assert out["error_max"] is None


class TestPopulatedSpan:
    def test_span_raw_min_to_max_populated(self, tmp_path):
        df = _pose_df(range(0, 10))
        df.loc[0:2, _POSE_COLS] = np.nan   # frames 0..2 empty
        df.loc[8:9, _POSE_COLS] = np.nan   # frames 8..9 empty
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        # populated frames 3..7 → start=3, n=5
        assert c3d.populated_span(tmp_path, "p", "raw") == (3, 5)

    def test_span_filtered_source(self, tmp_path):
        c3d.write_range_to_canonical_3d(tmp_path, "p", _pose_df(range(0, 6)))
        c3d.medfilt_range_and_splice(
            tmp_path, "p", 2, 2, {"filter3d": {"medfilt": 3}})
        assert c3d.populated_span(tmp_path, "p", "filtered") == (2, 2)

    def test_absent_returns_none(self, tmp_path):
        assert c3d.populated_span(tmp_path, "p", "raw") is None

    def test_all_nan_returns_none(self, tmp_path):
        df = _pose_df(range(0, 5))
        df.loc[:, _POSE_COLS] = np.nan
        c3d.write_range_to_canonical_3d(tmp_path, "p", df)
        assert c3d.populated_span(tmp_path, "p", "raw") is None
