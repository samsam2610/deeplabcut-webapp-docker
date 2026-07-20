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
