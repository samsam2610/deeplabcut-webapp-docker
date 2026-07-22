"""Tests for the _coverage_buckets helper and /dlc/viewer/pose-coverage route."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dlc import viewer as v


def test_coverage_buckets_thresholds_and_downsamples():
    poses = np.zeros((6, 2, 3), dtype=np.float32)
    poses[:, 0, 2] = [0.9, 0.2, np.nan, 0.0, 0.7, 0.05]
    poses[:, 1, 2] = [0.1, 0.2, 0.8, 0.0, 0.0, 0.05]
    # threshold 0.6 → covered per frame: [T,F,T,F,T,F]; 6 frames→3 buckets of 2 → [1,1,1]
    assert v._coverage_buckets(poses, threshold=0.6, n_buckets=3) == [1, 1, 1]
    assert v._coverage_buckets(poses, threshold=0.95, n_buckets=3) == [0, 0, 0]
    assert len(v._coverage_buckets(poses, threshold=0.6, n_buckets=100)) == 6  # capped at n_frames


def test_coverage_buckets_empty():
    assert v._coverage_buckets(np.zeros((0, 2, 3), dtype=np.float32), 0.6, 10) == []


def test_coverage_buckets_presence_mode_ignores_threshold():
    poses = np.zeros((4, 2, 3), dtype=np.float32)
    poses[:, 0, 0] = [1.0, np.nan, 5.0, np.nan]   # bp0 x
    poses[:, 1, 0] = [np.nan, np.nan, np.nan, 2.0] # bp1 x
    poses[:, :, 2] = 0.0  # all likelihoods 0 (would be uncovered in likelihood mode)
    # presence: covered iff >=1 finite x → [T,F,T,T]; 4 frames→4 buckets
    assert v._coverage_buckets(poses, threshold=0.6, n_buckets=4, mode="presence") == [1, 0, 1, 1]
    # default (likelihood) mode with zero likelihoods → none covered
    assert v._coverage_buckets(poses, threshold=0.6, n_buckets=4) == [0, 0, 0, 0]


def test_coverage_first_frames_returns_first_covered_frame_per_bucket():
    # A bucket can span hundreds of frames; the client needs a REAL covered frame
    # to seek to, not the bucket centre. first_frames[b] = earliest covered frame
    # in bucket b, or -1 when the bucket is uncovered.
    poses = np.zeros((6, 2, 3), dtype=np.float32)
    poses[:, 0, 2] = [0.9, 0.2, np.nan, 0.0, 0.7, 0.05]
    poses[:, 1, 2] = [0.1, 0.2, 0.8, 0.0, 0.0, 0.05]
    # threshold 0.6 → covered [T,F,T,F,T,F]; 3 buckets of 2 → first covered = [0,2,4]
    assert v._coverage_first_frames(poses, threshold=0.6, n_buckets=3) == [0, 2, 4]
    # nothing above 0.95 → every bucket uncovered → -1
    assert v._coverage_first_frames(poses, threshold=0.95, n_buckets=3) == [-1, -1, -1]


def test_coverage_first_frames_presence_and_empty():
    poses = np.zeros((4, 2, 3), dtype=np.float32)
    poses[:, 0, 0] = [1.0, np.nan, 5.0, np.nan]
    poses[:, 1, 0] = [np.nan, np.nan, np.nan, 2.0]
    poses[:, :, 2] = 0.0
    # presence covered [T,F,T,T]; 4 buckets of 1 → [0,-1,2,3]
    assert v._coverage_first_frames(poses, threshold=0.6, n_buckets=4, mode="presence") == [0, -1, 2, 3]
    assert v._coverage_first_frames(np.zeros((0, 2, 3), dtype=np.float32), 0.6, 10) == []


# ── video-frame-space scaling (h5 rows ≠ video frames) ───────────────────────────
# The seek bar is drawn over the VIDEO frame count, but an h5 may have FEWER rows
# than the video has frames (DLC analyzed a prefix) or a non-contiguous index.
# total_frames + frame_ids place each mark at its ABSOLUTE video frame so it lands
# under the seek playhead, not compressed into the h5 length.

def test_coverage_buckets_scaled_to_total_frames_leaves_tail_empty():
    # 4 h5 rows, all covered, but the video has 8 frames → bucket over 8, not 4.
    poses = np.zeros((4, 2, 3), dtype=np.float32)
    poses[:, 0, 2] = 0.9  # all covered
    # 8 buckets over 8 frames; rows sit at abs frames 0..3 → first half covered only.
    buckets = v._coverage_buckets(poses, threshold=0.6, n_buckets=8, total_frames=8)
    assert buckets == [1, 1, 1, 1, 0, 0, 0, 0]


def test_coverage_first_frames_scaled_covered_frame_near_end_lands_correctly():
    # A covered frame near the END of the h5 must land near its correct VIDEO
    # fraction, not at ~1.0. h5 last row = frame 95; video has 100 frames.
    n, total = 96, 100
    poses = np.zeros((n, 2, 3), dtype=np.float32)
    poses[95, 0, 2] = 0.9   # only the last row covered → abs frame 95
    frames = v._coverage_first_frames(poses, threshold=0.6, n_buckets=100, total_frames=total)
    covered = [f for f in frames if f >= 0]
    assert covered == [95]
    # the mark's video-frame fraction is 95/99 ≈ 0.96, NOT 95/95 = 1.0
    b_idx = frames.index(95)
    assert abs(b_idx / len(frames) - 95 / total) < 0.02


def test_coverage_uses_absolute_frame_ids_not_positional():
    # Non-contiguous index: 3 rows at abs video frames 10, 50, 90 (video len 100).
    # first_frames must report the ABSOLUTE frames, not positional 0/1/2.
    poses = np.zeros((3, 2, 3), dtype=np.float32)
    poses[:, 0, 2] = 0.9  # all covered
    frame_ids = np.array([10, 50, 90], dtype=np.int64)
    frames = v._coverage_first_frames(
        poses, threshold=0.6, n_buckets=100, frame_ids=frame_ids, total_frames=100)
    assert sorted(f for f in frames if f >= 0) == [10, 50, 90]
    buckets = v._coverage_buckets(
        poses, threshold=0.6, n_buckets=100, frame_ids=frame_ids, total_frames=100)
    # covered buckets sit at abs frames 10/50/90 → buckets 10/50/90 of 100.
    assert [i for i, x in enumerate(buckets) if x] == [10, 50, 90]


def test_coverage_defaults_unchanged_when_no_scale_given():
    # Backward-compat: without frame_ids/total_frames, behaviour is positional/span.
    poses = np.zeros((6, 2, 3), dtype=np.float32)
    poses[:, 0, 2] = [0.9, 0.2, np.nan, 0.0, 0.7, 0.05]
    poses[:, 1, 2] = [0.1, 0.2, 0.8, 0.0, 0.0, 0.05]
    assert v._coverage_buckets(poses, threshold=0.6, n_buckets=3) == [1, 1, 1]
    assert v._coverage_first_frames(poses, threshold=0.6, n_buckets=3) == [0, 2, 4]
