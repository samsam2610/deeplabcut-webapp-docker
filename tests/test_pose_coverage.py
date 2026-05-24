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
