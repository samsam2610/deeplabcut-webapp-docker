"""End-to-end wiring test for the range-triangulate task/helper.

``anipose_src.triangulate_funcs.triangulate`` is MOCKED to emit a tiny anipose
pose-3d CSV (fnum 0..n-1); this verifies the slice offset, canonical merge and
median-filter splice wiring without any real calibration data.

Exercises dlc.triangulate_range.run_triangulate_range directly (the Celery task
is a thin wrapper around it). The pose-2d h5 slicing runs for real (host has a
working `tables`); if it did not, the _slice_pose2d_h5 seam is patchable.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("tables", reason="pose-2d h5 slicing needs a working HDF5/tables")

from dlc import triangulate_range as tr  # noqa: E402
from dlc import canonical_3d as c3d       # noqa: E402


_BPS = ["nose", "tail"]


def _write_dlc_h5(path: Path, nframes: int):
    """Minimal DLC-format analyzed h5 (scorer/bodyparts/coords multiindex)."""
    cols = pd.MultiIndex.from_product(
        [["DLC"], _BPS, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"])
    data = np.arange(nframes * len(cols), dtype=float).reshape(nframes, len(cols))
    df = pd.DataFrame(data, index=pd.RangeIndex(nframes), columns=cols)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_hdf(str(path), key="df_with_missing", mode="w", format="fixed")


def _config():
    return {
        "model_type": "deeplabcut",
        "triangulation": {"cam_regex": "cam[0-9]"},
        "filter3d": {"medfilt": 5, "offset_threshold": 15},
        "pipeline": {"pose_3d": "pose-3d", "pose_3d_filter": "pose-3d-filtered"},
    }


def _fake_triangulate_writer(n):
    """Return a fake triangulate() that writes an anipose pose-3d CSV with n rows,
    a spike in nose_x at the middle row (to prove the median filter ran)."""
    def _fake(config, calib_folder, video_folder, pose_folder,
              fname_dict, output_fname):
        rows = {}
        for bp in _BPS:
            rows[f"{bp}_x"] = [0.0] * n
            rows[f"{bp}_y"] = [0.0] * n
            rows[f"{bp}_z"] = [0.0] * n
            rows[f"{bp}_error"] = [1.0] * n
            rows[f"{bp}_ncams"] = [2.0] * n
            rows[f"{bp}_score"] = [0.9] * n
        df = pd.DataFrame(rows)
        df.loc[n // 2, "nose_x"] = 100.0    # spike
        df["fnum"] = np.arange(n)
        df.to_csv(output_fname, index=False)
    return _fake


@pytest.fixture
def anipose_session(tmp_path):
    """parent/config.toml + parent/session/{calibration,pose-2d,videos}."""
    parent = tmp_path / "proj"
    session = parent / "session"
    (session / "calibration").mkdir(parents=True)
    (session / "pose-2d").mkdir(parents=True)
    (parent / "config.toml").write_text("# anipose config\n")
    (session / "calibration" / "calibration.toml").write_text("# calib\n")

    cam0 = session / "surv1_cam0_20260123_120000.avi"
    cam1 = session / "surv1_cam1_20260123_120000.avi"
    cam0.write_bytes(b""); cam1.write_bytes(b"")
    _write_dlc_h5(session / "pose-2d" / f"{cam0.stem}_analyzed.h5", 200)
    _write_dlc_h5(session / "pose-2d" / f"{cam1.stem}_analyzed.h5", 200)
    return parent, session, cam0, cam1


class TestRunTriangulateRange:
    def test_offset_merge_and_filter_wiring(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        start, n = 100, 6
        with patch.object(tr, "_load_config", return_value=_config()), \
             patch.object(tr, "_triangulate",
                          side_effect=_fake_triangulate_writer(n)):
            result = tr.run_triangulate_range(str(cam0), start, n)

        pair = c3d.pair_name_from_cam0(str(cam0))
        assert result["pair_name"] == pair
        assert result["start_frame"] == start and result["n_frames"] == n
        # cam0 and cam1 collapse to one pair file
        assert "cam0" not in pair and "cam1" not in pair

        raw = pd.read_csv(c3d.canonical_3d_csv_path(session, pair), index_col=0)
        # slice offset: local rows 0..5 → global 100..105
        assert raw.loc[100:105, "nose_x"].notna().all()
        assert raw.loc[0:99, "nose_x"].isna().all()
        # the raw canonical keeps the spike
        assert raw.loc[100 + n // 2, "nose_x"] == 100.0

        filt = pd.read_csv(c3d.filtered_3d_csv_path(session, pair), index_col=0)
        # filtered range present, spike smoothed out
        assert filt.loc[100:105, "nose_x"].notna().all()
        assert abs(filt.loc[100 + n // 2, "nose_x"]) < 1.0
        # outside the range stays empty in the filtered canonical
        assert filt.loc[0:99, "nose_x"].isna().all()

    def test_progress_callbacks_fire(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        seen = []
        with patch.object(tr, "_load_config", return_value=_config()), \
             patch.object(tr, "_triangulate",
                          side_effect=_fake_triangulate_writer(4)):
            tr.run_triangulate_range(
                str(cam0), 0, 4,
                update=lambda p, s, log="": seen.append((p, s)))
        assert seen and seen[-1][0] == 100
        assert any("Triangulat" in s for _, s in seen)

    def test_missing_config_raises(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        (parent / "config.toml").unlink()
        with pytest.raises(FileNotFoundError):
            tr.run_triangulate_range(str(cam0), 0, 4)

    def test_missing_calibration_raises(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        (session / "calibration" / "calibration.toml").unlink()
        with patch.object(tr, "_load_config", return_value=_config()):
            with pytest.raises(FileNotFoundError):
                tr.run_triangulate_range(str(cam0), 0, 4)

    def test_missing_pose2d_raises(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        (session / "pose-2d" / f"{cam0.stem}_analyzed.h5").unlink()
        with patch.object(tr, "_load_config", return_value=_config()):
            with pytest.raises(FileNotFoundError):
                tr.run_triangulate_range(str(cam0), 0, 4)

    def test_cam1_sibling_resolves(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        assert tr._resolve_cam1(cam0) == cam1


def _config_with_filter(enabled, ftype="medfilt"):
    cfg = _config()
    cfg["filter"] = {"enabled": enabled, "type": ftype}
    return cfg


def _fake_filter(config, in_h5, out_h5):
    """Stand-in for the real anipose 2D filter: just copy the slice through."""
    import shutil
    shutil.copyfile(str(in_h5), str(out_h5))
    return Path(out_h5)


class TestFilter2dStep:
    def test_filter_called_per_cam_and_filtered_h5_used(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        captured = {}
        writer = _fake_triangulate_writer(4)

        def _capturing(config, calib, video, pose, fname_dict, out):
            captured["fname_dict"] = dict(fname_dict)
            writer(config, calib, video, pose, fname_dict, out)

        with patch.object(tr, "_load_config",
                          return_value=_config_with_filter(True)), \
             patch.object(tr, "_triangulate", side_effect=_capturing), \
             patch.object(tr, "_filter_pose2d",
                          side_effect=_fake_filter) as mk_filter:
            tr.run_triangulate_range(str(cam0), 0, 4)

        # one filter call per camera
        assert mk_filter.call_count == 2
        # the filtered h5 (not the raw slice) is threaded into fname_dict
        assert captured["fname_dict"]
        assert all(v.endswith("_filtered.h5")
                   for v in captured["fname_dict"].values())

    def test_filter_not_called_when_disabled(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        captured = {}
        writer = _fake_triangulate_writer(4)

        def _capturing(config, calib, video, pose, fname_dict, out):
            captured["fname_dict"] = dict(fname_dict)
            writer(config, calib, video, pose, fname_dict, out)

        with patch.object(tr, "_load_config",
                          return_value=_config_with_filter(False)), \
             patch.object(tr, "_triangulate", side_effect=_capturing), \
             patch.object(tr, "_filter_pose2d",
                          side_effect=_fake_filter) as mk_filter:
            tr.run_triangulate_range(str(cam0), 0, 4)

        assert mk_filter.call_count == 0
        # raw slices used
        assert all(v.endswith("_analyzed.h5")
                   for v in captured["fname_dict"].values())

    def test_range_beyond_analyzed_data_is_skipped(self, anipose_session):
        """A range whose positional slice is empty (start >= analyzed rows) must
        be skipped gracefully — NOT passed to anipose, which chokes on a 0-row
        pose file with an opaque 'no datasets found' HDF error. The source h5 has
        200 rows, so start=250 slices to nothing."""
        parent, session, cam0, cam1 = anipose_session
        with patch.object(tr, "_load_config", return_value=_config()), \
             patch.object(tr, "_triangulate",
                          side_effect=_fake_triangulate_writer(6)) as mk_tri:
            result = tr.run_triangulate_range(str(cam0), 250, 6)

        assert result["skipped"] is True
        assert result["raw_csv"] is None and result["filtered_csv"] is None
        assert result["start_frame"] == 250 and result["n_frames"] == 6
        assert "no 2D poses" in result["reason"]
        # anipose triangulate must never see the empty slice, and no canonical
        # 3D file may be written for a skipped range.
        assert mk_tri.call_count == 0
        pair = c3d.pair_name_from_cam0(str(cam0))
        assert not c3d.canonical_3d_csv_path(session, pair).exists()

    def test_all_nan_2d_range_is_skipped(self, anipose_session):
        """A range that IS within the analyzed data but whose 2D poses are all-NaN
        (frames finalized into _analyzed but never analyzed) has nothing to
        triangulate — it must be skipped, NOT sent to anipose (which would write
        all-NaN 3D → no coverage, inflating the batch's done count)."""
        parent, session, cam0, cam1 = anipose_session
        # Overwrite both cams' _analyzed.h5 with a NaN region in frames 100..199.
        cols = pd.MultiIndex.from_product(
            [["DLC"], _BPS, ["x", "y", "likelihood"]],
            names=["scorer", "bodyparts", "coords"])
        for cam in (cam0, cam1):
            data = np.arange(200 * len(cols), dtype=float).reshape(200, len(cols))
            df = pd.DataFrame(data, index=pd.RangeIndex(200), columns=cols)
            df.iloc[100:200, :] = np.nan   # frames 100..199: no 2D poses
            df.to_hdf(str(session / "pose-2d" / f"{cam.stem}_analyzed.h5"),
                      key="df_with_missing", mode="w", format="fixed")

        with patch.object(tr, "_load_config", return_value=_config()), \
             patch.object(tr, "_triangulate",
                          side_effect=_fake_triangulate_writer(6)) as mk_tri:
            skipped = tr.run_triangulate_range(str(cam0), 120, 6)   # all-NaN window
            ran     = tr.run_triangulate_range(str(cam0), 0, 6)     # real 2D window

        assert skipped["skipped"] is True, "all-NaN 2D range must be skipped"
        assert "no 2D poses" in skipped["reason"]
        assert not ran.get("skipped"), "a range with real 2D must still triangulate"
        assert mk_tri.call_count == 1, "anipose must run only for the real-data range"

    def test_partial_range_at_tail_still_triangulates(self, anipose_session):
        """A range straddling the end (start < rows < start+n) keeps the valid
        rows — only fully-out-of-range slices are skipped. Source has 200 rows;
        start=198 slices to 2 rows, so triangulation still runs (not skipped)."""
        parent, session, cam0, cam1 = anipose_session
        with patch.object(tr, "_load_config", return_value=_config()), \
             patch.object(tr, "_triangulate",
                          side_effect=_fake_triangulate_writer(6)) as mk_tri:
            result = tr.run_triangulate_range(str(cam0), 198, 6)

        assert not result.get("skipped")
        assert mk_tri.call_count == 1

    def test_triangulation_continues_when_filter_raises(self, anipose_session):
        parent, session, cam0, cam1 = anipose_session
        captured = {}
        writer = _fake_triangulate_writer(4)

        def _capturing(config, calib, video, pose, fname_dict, out):
            captured["fname_dict"] = dict(fname_dict)
            writer(config, calib, video, pose, fname_dict, out)

        with patch.object(tr, "_load_config",
                          return_value=_config_with_filter(True)), \
             patch.object(tr, "_triangulate", side_effect=_capturing), \
             patch.object(tr, "_filter_pose2d",
                          side_effect=RuntimeError("boom")):
            result = tr.run_triangulate_range(str(cam0), 0, 4)

        # falls back to the raw slice; triangulation still completes
        assert result["n_frames"] == 4
        assert all(v.endswith("_analyzed.h5")
                   for v in captured["fname_dict"].values())
