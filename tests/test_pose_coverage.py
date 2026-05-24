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
