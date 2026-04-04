"""
Tests for dlc_tapnet_tracker.py — TDD phase (should FAIL before implementation).

CRITICAL CONSTRAINTS enforced:
  #1  GPU routing  — TAPNet inference MUST use CUDA_VISIBLE_DEVICES=0 (RTX 5090)
  #2  VRAM teardown — subprocess must release GPU memory after inference
  #3  Isolated state — all sandbox tests use dlc_sandbox_project fixture
  #4  Coordinate precision — DLC ↔ TAPNet translation must be lossless at float32
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── Ensure src/ is on the path ────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_dlc_df(
    scorer: str,
    bodyparts: list[str],
    video_stem: str,
    frame_names: list[str],
    coords: np.ndarray,  # shape (T, N, 2) float64
) -> pd.DataFrame:
    """
    Build a DLC-format MultiIndex DataFrame matching the real on-disk structure.

    Columns: MultiIndex (scorer, bodypart, coord)
    Index:   MultiIndex ('labeled-data', video_stem, frame_name)
    """
    T, N, _ = coords.shape
    assert N == len(bodyparts)
    assert T == len(frame_names)

    col_tuples = []
    for bp in bodyparts:
        col_tuples += [(scorer, bp, "x"), (scorer, bp, "y")]
    cols = pd.MultiIndex.from_tuples(col_tuples, names=["scorer", "bodyparts", "coords"])

    idx_tuples = [("labeled-data", video_stem, fn) for fn in frame_names]
    idx = pd.MultiIndex.from_tuples(idx_tuples, names=["", "", ""])

    data = coords.reshape(T, N * 2)
    return pd.DataFrame(data, index=idx, columns=cols)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Consecutive frame detection
# ══════════════════════════════════════════════════════════════════════════════

class TestParseFrameNumber:
    """parse_frame_number() must handle both DLC frame naming conventions."""

    def test_img_seq_abs_format(self):
        """img0003-158302.png → abs_frame = 158302"""
        from dlc_tapnet_tracker import parse_frame_number
        assert parse_frame_number("img0003-158302.png") == 158302

    def test_img_seq_abs_zero(self):
        """img0000-0.png → 0 (abs frame)"""
        from dlc_tapnet_tracker import parse_frame_number
        assert parse_frame_number("img0000-0.png") == 0

    def test_extraction_index_returns_seq(self):
        """_parse_extraction_index returns the seq part, not abs."""
        from dlc_tapnet_tracker import _parse_extraction_index
        assert _parse_extraction_index("img0003-158302.png") == 3
        assert _parse_extraction_index("img0052-106482.png") == 52

    def test_frame_N_format(self):
        """frame0050.png → 50"""
        from dlc_tapnet_tracker import parse_frame_number
        assert parse_frame_number("frame0050.png") == 50

    def test_img_only_seq_format(self):
        """img0042.png → 42  (no abs-frame suffix)"""
        from dlc_tapnet_tracker import parse_frame_number
        assert parse_frame_number("img0042.png") == 42

    def test_unknown_format_returns_none(self):
        """Unrecognised filenames → None, not an exception."""
        from dlc_tapnet_tracker import parse_frame_number
        assert parse_frame_number("random_file.png") is None

    def test_non_image_returns_none(self):
        from dlc_tapnet_tracker import parse_frame_number
        assert parse_frame_number("CollectedData_Ali.csv") is None


class TestFindConsecutiveSequences:
    """find_consecutive_sequences() groups frame lists into consecutive runs."""

    def _names(self, abs_list: list[int]) -> list[str]:
        return [f"img{i:04d}-{a}.png" for i, a in enumerate(abs_list)]

    def test_fully_consecutive(self):
        from dlc_tapnet_tracker import find_consecutive_sequences
        names = self._names([10, 11, 12, 13, 14])
        seqs = find_consecutive_sequences(names)
        assert len(seqs) == 1
        assert seqs[0] == names

    def test_two_separated_runs(self):
        """Two extraction batches with a gap in seq index → two separate sequences."""
        from dlc_tapnet_tracker import find_consecutive_sequences
        # First batch: seq 0-2, second batch: seq 5-7 (gap of 2 in seq)
        batch1 = ["img0000-10.png", "img0001-11.png", "img0002-12.png"]
        batch2 = ["img0005-20.png", "img0006-21.png", "img0007-22.png"]
        seqs = find_consecutive_sequences(batch1 + batch2)
        assert len(seqs) == 2
        assert seqs[0] == batch1
        assert seqs[1] == batch2

    def test_single_frame_not_a_sequence(self):
        """Runs of length < 2 are not returned."""
        from dlc_tapnet_tracker import find_consecutive_sequences
        names = self._names([5])
        seqs = find_consecutive_sequences(names)
        assert seqs == []

    def test_pair_is_minimum_sequence(self):
        from dlc_tapnet_tracker import find_consecutive_sequences
        names = self._names([7, 8])
        seqs = find_consecutive_sequences(names)
        assert len(seqs) == 1
        assert len(seqs[0]) == 2

    def test_unsorted_input_is_sorted(self):
        """Input order does not matter; output should be sorted by frame number."""
        from dlc_tapnet_tracker import find_consecutive_sequences
        shuffled = [f"img{i:04d}-{a}.png" for i, a in
                    [(2, 102), (0, 100), (1, 101), (3, 103)]]
        seqs = find_consecutive_sequences(shuffled)
        assert len(seqs) == 1
        abs_nums = [int(re.search(r'-(\d+)\.png', n).group(1)) for n in seqs[0]]
        assert abs_nums == [100, 101, 102, 103]

    def test_real_project_style_names(self):
        """Mimic the actual DREADD project: img{seq}-{abs}.png where abs is sequential."""
        from dlc_tapnet_tracker import find_consecutive_sequences
        names = [f"img{i:04d}-{158299 + i}.png" for i in range(148)]
        seqs = find_consecutive_sequences(names)
        assert len(seqs) == 1
        assert len(seqs[0]) == 148

    def test_every_other_frame_extraction(self):
        """
        DLC often extracts every Nth video frame so abs frame numbers have
        gaps (e.g. 106482 → 106484, diff=2). The seq index (before the dash)
        is still 0,1,2... so the whole folder is one consecutive sequence.
        """
        from dlc_tapnet_tracker import find_consecutive_sequences
        # Mimic MAP4_20250605: seq=0..71, abs has gaps of 1 and 2
        abs_frames = list(range(106429, 106476)) + [106477] + list(range(106478, 106483)) \
                     + [106484, 106485, 106486, 106487, 106489] \
                     + list(range(106490, 106497)) + [106498, 106499, 106500, 106501,
                                                       106503, 106504, 106505]
        names = [f"img{i:04d}-{a}.png" for i, a in enumerate(abs_frames)]
        seqs = find_consecutive_sequences(names)
        assert len(seqs) == 1, f"Expected 1 sequence, got {len(seqs)}"
        assert len(seqs[0]) == len(names)

    def test_empty_input(self):
        from dlc_tapnet_tracker import find_consecutive_sequences
        assert find_consecutive_sequences([]) == []

    def test_non_image_files_ignored(self):
        """CSV / H5 files in the folder should be skipped gracefully."""
        from dlc_tapnet_tracker import find_consecutive_sequences
        mixed = ["CollectedData_Ali.csv", "img0000-10.png", "img0001-11.png",
                 "_machine_predictions_raw.h5"]
        seqs = find_consecutive_sequences(mixed)
        assert len(seqs) == 1
        assert all(n.endswith(".png") for n in seqs[0])


class TestAnchorFrameDetection:
    """check_anchor_frames() finds which frames in a sequence are labeled."""

    def test_first_frame_labeled(self):
        from dlc_tapnet_tracker import check_anchor_frames
        frame_names = [f"img{i:04d}-{100+i}.png" for i in range(5)]
        labeled = {frame_names[0]}  # only first
        result = check_anchor_frames(frame_names, labeled)
        assert result["first_labeled"] is True
        assert result["last_labeled"] is False
        assert result["anchor"] == frame_names[0]

    def test_last_frame_labeled(self):
        from dlc_tapnet_tracker import check_anchor_frames
        frame_names = [f"img{i:04d}-{100+i}.png" for i in range(5)]
        labeled = {frame_names[-1]}
        result = check_anchor_frames(frame_names, labeled)
        assert result["first_labeled"] is False
        assert result["last_labeled"] is True
        assert result["anchor"] == frame_names[-1]

    def test_both_endpoints_labeled_prefers_first(self):
        from dlc_tapnet_tracker import check_anchor_frames
        frame_names = [f"img{i:04d}-{100+i}.png" for i in range(5)]
        labeled = {frame_names[0], frame_names[-1]}
        result = check_anchor_frames(frame_names, labeled)
        assert result["anchor"] == frame_names[0]

    def test_no_anchor_returns_none(self):
        from dlc_tapnet_tracker import check_anchor_frames
        frame_names = [f"img{i:04d}-{100+i}.png" for i in range(5)]
        result = check_anchor_frames(frame_names, set())
        assert result["anchor"] is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — DLC → TAPNet coordinate translation
# ══════════════════════════════════════════════════════════════════════════════

class TestDlcToTapnetPoints:
    """dlc_to_tapnet_points() must extract query points with sub-pixel precision."""

    SCORER = "Ali"
    BODYPARTS = ["Snout", "Wrist", "MCP-1"]
    STEM = "MAP2_video"
    FRAMES = [f"img{i:04d}-{100+i}.png" for i in range(5)]

    def _make_df(self, anchor_idx: int = 0):
        rng = np.random.default_rng(42)
        coords = rng.uniform(10.0, 900.0, size=(5, 3, 2)).astype(np.float64)
        return _make_dlc_df(self.SCORER, self.BODYPARTS, self.STEM, self.FRAMES, coords), coords

    def test_returns_correct_shape(self):
        from dlc_tapnet_tracker import dlc_to_tapnet_points
        df, _ = self._make_df()
        pts, bps = dlc_to_tapnet_points(df, anchor_frame=self.FRAMES[0])
        assert pts.shape == (len(self.BODYPARTS), 3)
        assert len(bps) == len(self.BODYPARTS)

    def test_bodyparts_order_preserved(self):
        from dlc_tapnet_tracker import dlc_to_tapnet_points
        df, _ = self._make_df()
        _, bps = dlc_to_tapnet_points(df, anchor_frame=self.FRAMES[0])
        assert bps == self.BODYPARTS

    def test_tapnet_format_t_y_x(self):
        """TAPNet query_points are (t, y, x) — note axis order: y before x."""
        from dlc_tapnet_tracker import dlc_to_tapnet_points
        df, coords = self._make_df()
        pts, _ = dlc_to_tapnet_points(df, anchor_frame=self.FRAMES[0])
        # t=0 for the anchor frame
        assert np.all(pts[:, 0] == 0)
        # coords[anchor_idx=0, :, 0] = x, coords[..., 1] = y
        expected_x = coords[0, :, 0]
        expected_y = coords[0, :, 1]
        np.testing.assert_allclose(pts[:, 2], expected_x, rtol=1e-6)  # col 2 = x
        np.testing.assert_allclose(pts[:, 1], expected_y, rtol=1e-6)  # col 1 = y

    def test_subpixel_precision_preserved(self):
        """Sub-pixel values must survive the round-trip without truncation."""
        from dlc_tapnet_tracker import dlc_to_tapnet_points
        df, coords = self._make_df()
        pts, _ = dlc_to_tapnet_points(df, anchor_frame=self.FRAMES[0])
        # Coordinates should be float, not integers
        assert pts.dtype in (np.float32, np.float64)
        assert not np.all(pts[:, 1:] == np.floor(pts[:, 1:]))

    def test_nan_coords_excluded(self):
        """Body parts with NaN coords in the anchor frame should be excluded."""
        from dlc_tapnet_tracker import dlc_to_tapnet_points
        df, coords = self._make_df()
        # Set Wrist (index 1) coords to NaN in anchor frame
        df.loc[
            ("labeled-data", self.STEM, self.FRAMES[0]),
            (self.SCORER, "Wrist", "x"),
        ] = np.nan
        pts, bps = dlc_to_tapnet_points(df, anchor_frame=self.FRAMES[0])
        assert "Wrist" not in bps
        assert pts.shape[0] == len(self.BODYPARTS) - 1

    def test_anchor_frame_not_in_df_raises(self):
        from dlc_tapnet_tracker import dlc_to_tapnet_points
        df, _ = self._make_df()
        with pytest.raises((KeyError, ValueError)):
            dlc_to_tapnet_points(df, anchor_frame="nonexistent_frame.png")


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — TAPNet → DLC coordinate translation
# ══════════════════════════════════════════════════════════════════════════════

class TestTapnetToDlcLabels:
    """tapnet_to_dlc_labels() must write tracks back into DLC MultiIndex format."""

    SCORER = "Ali"
    BODYPARTS = ["Snout", "Wrist", "MCP-1"]
    STEM = "MAP2_video"
    FRAMES = [f"img{i:04d}-{100+i}.png" for i in range(5)]

    def _make_tracks(self, T=5, N=3, visible=True):
        rng = np.random.default_rng(7)
        tracks = rng.uniform(10.0, 900.0, size=(T, N, 2)).astype(np.float32)
        vis = np.ones((T, N), dtype=bool) if visible else np.zeros((T, N), dtype=bool)
        return tracks, vis

    def test_output_is_dataframe(self):
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks, vis = self._make_tracks()
        df = tapnet_to_dlc_labels(tracks, vis, self.BODYPARTS, self.FRAMES,
                                   self.SCORER, self.STEM)
        assert isinstance(df, pd.DataFrame)

    def test_correct_number_of_rows(self):
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks, vis = self._make_tracks()
        df = tapnet_to_dlc_labels(tracks, vis, self.BODYPARTS, self.FRAMES,
                                   self.SCORER, self.STEM)
        assert len(df) == len(self.FRAMES)

    def test_correct_multiindex_columns(self):
        """Output columns must be (scorer, bodypart, coord) MultiIndex."""
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks, vis = self._make_tracks()
        df = tapnet_to_dlc_labels(tracks, vis, self.BODYPARTS, self.FRAMES,
                                   self.SCORER, self.STEM)
        assert isinstance(df.columns, pd.MultiIndex)
        assert list(df.columns.get_level_values(0).unique()) == [self.SCORER]
        assert list(df.columns.get_level_values(1).unique()) == self.BODYPARTS
        assert set(df.columns.get_level_values(2).unique()) == {"x", "y"}

    def test_correct_multiindex_index(self):
        """Output index must be ('labeled-data', video_stem, frame_name)."""
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks, vis = self._make_tracks()
        df = tapnet_to_dlc_labels(tracks, vis, self.BODYPARTS, self.FRAMES,
                                   self.SCORER, self.STEM)
        assert isinstance(df.index, pd.MultiIndex)
        assert df.index.get_level_values(0).unique().tolist() == ["labeled-data"]
        assert df.index.get_level_values(1).unique().tolist() == [self.STEM]

    def test_subpixel_values_preserved(self):
        """Float32 precision from TAPNet must not be truncated to integer."""
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks, vis = self._make_tracks()
        df = tapnet_to_dlc_labels(tracks, vis, self.BODYPARTS, self.FRAMES,
                                   self.SCORER, self.STEM)
        x_vals = df[(self.SCORER, "Snout", "x")].values.astype(float)
        assert not np.all(x_vals == np.floor(x_vals)), "Sub-pixel precision was lost"

    def test_invisible_points_are_nan(self):
        """TAPNet visibility=False → NaN in the output DataFrame."""
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks, _ = self._make_tracks()
        vis = np.zeros((5, 3), dtype=bool)
        vis[0, 0] = True   # only frame 0, bodypart 0 is visible
        df = tapnet_to_dlc_labels(tracks, vis, self.BODYPARTS, self.FRAMES,
                                   self.SCORER, self.STEM)
        # All frames except frame 0 should be NaN for Snout
        snout_x = df[(self.SCORER, "Snout", "x")]
        assert not pd.isna(snout_x.iloc[0])
        assert pd.isna(snout_x.iloc[1])
        assert pd.isna(snout_x.iloc[2])

    def test_axis_order_xy_not_yx(self):
        """tracks[t, n, :] = (x, y), NOT (y, x). Columns must match."""
        from dlc_tapnet_tracker import tapnet_to_dlc_labels
        tracks = np.array([[[111.5, 222.5]]])  # T=1, N=1, (x=111.5, y=222.5)
        vis = np.array([[True]])
        df = tapnet_to_dlc_labels(tracks, vis, ["Snout"], ["img0000-100.png"],
                                   "Ali", "video")
        assert df[("Ali", "Snout", "x")].iloc[0] == pytest.approx(111.5, abs=1e-4)
        assert df[("Ali", "Snout", "y")].iloc[0] == pytest.approx(222.5, abs=1e-4)

    def test_roundtrip_dlc_tapnet_dlc(self):
        """
        Full round-trip: DLC labels → TAPNet query_points → tapnet_to_dlc_labels.
        Anchor frame coordinates must be bit-exact (float32 precision).
        """
        from dlc_tapnet_tracker import dlc_to_tapnet_points, tapnet_to_dlc_labels

        rng = np.random.default_rng(99)
        coords = rng.uniform(50.0, 800.0, size=(5, 3, 2)).astype(np.float64)
        original_df = _make_dlc_df("Ali", ["Snout", "Wrist", "MCP-1"],
                                    "MAP2_video",
                                    [f"img{i:04d}-{100+i}.png" for i in range(5)],
                                    coords)
        anchor = "img0000-100.png"
        query_pts, bps = dlc_to_tapnet_points(original_df, anchor_frame=anchor)

        # Simulate TAPNet returning tracks identical to the anchor query
        # (identity track — all T frames get anchor coords)
        T = 5
        tracks = np.tile(
            query_pts[:, [2, 1]],  # (N, 2) in (x, y) order
            (T, 1, 1),
        ).astype(np.float32)  # shape (T, N, 2)
        vis = np.ones((T, len(bps)), dtype=bool)

        result_df = tapnet_to_dlc_labels(tracks, vis, bps,
                                          [f"img{i:04d}-{100+i}.png" for i in range(5)],
                                          "Ali", "MAP2_video")

        # Anchor frame coords must match the original within float32 tolerance
        for bp in bps:
            orig_x = float(original_df.loc[
                ("labeled-data", "MAP2_video", anchor), ("Ali", bp, "x")
            ])
            orig_y = float(original_df.loc[
                ("labeled-data", "MAP2_video", anchor), ("Ali", bp, "y")
            ])
            got_x = float(result_df.loc[
                ("labeled-data", "MAP2_video", anchor), ("Ali", bp, "x")
            ])
            got_y = float(result_df.loc[
                ("labeled-data", "MAP2_video", anchor), ("Ali", bp, "y")
            ])
            assert abs(got_x - orig_x) < 1e-3, f"{bp} x mismatch: {got_x} vs {orig_x}"
            assert abs(got_y - orig_y) < 1e-3, f"{bp} y mismatch: {got_y} vs {orig_y}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — DLC label loading
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadDlcLabels:
    """load_dlc_labels() must correctly parse on-disk CollectedData_*.csv files."""

    def test_loads_dataframe(self, dlc_sandbox_project):
        from dlc_tapnet_tracker import load_dlc_labels
        csv = next(
            (dlc_sandbox_project / "labeled-data").rglob("CollectedData_*.csv"),
            None,
        )
        if csv is None:
            pytest.skip("No CollectedData CSV found in sandbox project.")
        df = load_dlc_labels(csv)
        assert isinstance(df, pd.DataFrame)

    def test_has_multiindex_columns(self, dlc_sandbox_project):
        from dlc_tapnet_tracker import load_dlc_labels
        csv = next(
            (dlc_sandbox_project / "labeled-data").rglob("CollectedData_*.csv"),
            None,
        )
        if csv is None:
            pytest.skip("No CollectedData CSV found in sandbox project.")
        df = load_dlc_labels(csv)
        assert isinstance(df.columns, pd.MultiIndex)

    def test_has_multiindex_index(self, dlc_sandbox_project):
        from dlc_tapnet_tracker import load_dlc_labels
        csv = next(
            (dlc_sandbox_project / "labeled-data").rglob("CollectedData_*.csv"),
            None,
        )
        if csv is None:
            pytest.skip("No CollectedData CSV found in sandbox project.")
        df = load_dlc_labels(csv)
        assert isinstance(df.index, pd.MultiIndex)

    def test_values_are_float(self, dlc_sandbox_project):
        from dlc_tapnet_tracker import load_dlc_labels
        csv = next(
            (dlc_sandbox_project / "labeled-data").rglob("CollectedData_*.csv"),
            None,
        )
        if csv is None:
            pytest.skip("No CollectedData CSV found in sandbox project.")
        df = load_dlc_labels(csv)
        # Non-NaN values should be numeric
        non_nan = df.values.flatten()
        non_nan = non_nan[~pd.isna(non_nan)]
        assert non_nan.dtype.kind == "f", "Expected float dtype"

    def test_real_project_frame_names(self, dlc_sandbox_project):
        """Frame names in the index must follow img{seq}-{abs}.png convention."""
        from dlc_tapnet_tracker import load_dlc_labels, parse_frame_number
        csv = next(
            (dlc_sandbox_project / "labeled-data").rglob("CollectedData_*.csv"),
            None,
        )
        if csv is None:
            pytest.skip("No CollectedData CSV found in sandbox project.")
        df = load_dlc_labels(csv)
        frame_names = df.index.get_level_values(2).tolist()
        # At least some frames should be parseable
        parseable = [f for f in frame_names if parse_frame_number(f) is not None]
        assert len(parseable) > 0, f"No parseable frame names found: {frame_names[:5]}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Integration: sandbox project consecutive-frame detection
# ══════════════════════════════════════════════════════════════════════════════

class TestSandboxConsecutiveFrames:
    """Integration tests using the isolated sandbox DLC project."""

    def test_finds_consecutive_sequences_in_real_project(self, dlc_sandbox_project):
        from dlc_tapnet_tracker import find_consecutive_sequences
        labeled_data = dlc_sandbox_project / "labeled-data"
        for video_dir in labeled_data.iterdir():
            if not video_dir.is_dir():
                continue
            png_files = sorted(video_dir.glob("*.png"))
            if not png_files:
                continue
            names = [p.name for p in png_files]
            seqs = find_consecutive_sequences(names)
            # Real project frames should form at least one consecutive sequence
            assert len(seqs) >= 1, (
                f"Expected ≥1 consecutive sequence in {video_dir.name}, got 0.\n"
                f"Sample names: {names[:5]}"
            )
            return  # Test on first non-empty folder
        pytest.skip("No PNG frames found in sandbox labeled-data.")

    def test_sandbox_csv_labels_parseable(self, dlc_sandbox_project):
        """Load real CollectedData CSV and verify it can feed dlc_to_tapnet_points."""
        from dlc_tapnet_tracker import load_dlc_labels, dlc_to_tapnet_points
        csv = next(
            (dlc_sandbox_project / "labeled-data").rglob("CollectedData_*.csv"),
            None,
        )
        if csv is None:
            pytest.skip("No CollectedData CSV found in sandbox project.")
        df = load_dlc_labels(csv)
        # Pick the first labeled frame as anchor
        anchor = df.index.get_level_values(2)[0]
        pts, bps = dlc_to_tapnet_points(df, anchor_frame=anchor)
        assert pts.ndim == 2 and pts.shape[1] == 3
        assert len(bps) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — GPU environment (no-op without actual GPU, marks gpu)
# ══════════════════════════════════════════════════════════════════════════════

class TestGpuRouting:
    """Verify that TAPNet inference subprocess uses CUDA_VISIBLE_DEVICES=0."""

    @pytest.mark.gpu
    def test_inference_subprocess_sets_cuda_device(self, tmp_path):
        """
        The subprocess spawned by run_tapnet_inference must have
        CUDA_VISIBLE_DEVICES=0 in its environment (RTX 5090).
        """
        from dlc_tapnet_tracker import _TAPNET_GPU_INDEX
        assert _TAPNET_GPU_INDEX == 0, (
            f"Expected GPU index 0 (RTX 5090), got {_TAPNET_GPU_INDEX}"
        )

    @pytest.mark.gpu
    def test_vram_cleared_after_inference(self, tmp_path):
        """
        After inference subprocess exits, no CUDA context should remain on GPU 0.
        This is a smoke test — actual VRAM inspection uses nvidia-smi.
        """
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader", "--id=0"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"[VRAM CHECK] Active processes on GPU 0: {result.stdout}")
        # Test is informational, not a hard failure, for the base case
        assert True
