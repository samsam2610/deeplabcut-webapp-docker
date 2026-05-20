"""Tests for src/dlc/test_set_split.py — pure split-assembly function."""
from __future__ import annotations
import pandas as pd
import pytest

from dlc.test_set_split import build_indices_from_dataframe


def _fake_data(stems_frames: list[tuple[str, list[str]]]) -> pd.DataFrame:
    """Make a fake merged-H5 DataFrame whose row MultiIndex matches DLC's."""
    rows = []
    for stem, images in stems_frames:
        for img in images:
            rows.append(("labeled-data", stem, img))
    idx = pd.MultiIndex.from_tuples(rows)
    return pd.DataFrame({"dummy": [0] * len(rows)}, index=idx)


def test_random_returns_none():
    data = _fake_data([("a", ["img1.png", "img2.png"])])
    assert build_indices_from_dataframe(data, marks=[], mode="random", train_fraction=0.8) is None


def test_manual_partitions_all_frames():
    data = _fake_data([("a", ["img1.png", "img2.png", "img3.png", "img4.png"])])
    marks = [("a", "img2.png"), ("a", "img4.png")]
    train, test, stats = build_indices_from_dataframe(data, marks=marks, mode="manual", train_fraction=0.8)
    # Positions: img1=0, img2=1, img3=2, img4=3
    assert set(test) == {1, 3}
    assert set(train) == {0, 2}
    assert stats["dropped_marks"] == 0
    assert stats["total_frames"] == 4


def test_manual_empty_marks_raises():
    data = _fake_data([("a", ["img1.png", "img2.png"])])
    with pytest.raises(ValueError, match="manual"):
        build_indices_from_dataframe(data, marks=[], mode="manual", train_fraction=0.8)


def test_hybrid_below_quota_fills_with_random():
    # 10 frames, train_fraction 0.8 → target_test = 2; user marks 1 → 1 random added
    data = _fake_data([("a", [f"img{i:04d}.png" for i in range(10)])])
    marks = [("a", "img0000.png")]
    train, test, stats = build_indices_from_dataframe(
        data, marks=marks, mode="hybrid", train_fraction=0.8, seed=42
    )
    assert len(test) == 2
    assert 0 in test  # forced mark must be present
    assert set(train) | set(test) == set(range(10))
    assert set(train) & set(test) == set()


def test_hybrid_at_quota_no_filler():
    # 10 frames, target_test = 2; user marks 2 → no random
    data = _fake_data([("a", [f"img{i:04d}.png" for i in range(10)])])
    marks = [("a", "img0000.png"), ("a", "img0001.png")]
    train, test, stats = build_indices_from_dataframe(
        data, marks=marks, mode="hybrid", train_fraction=0.8, seed=42
    )
    assert sorted(test) == [0, 1]
    assert set(train) == set(range(2, 10))


def test_hybrid_overflow_honored():
    # 10 frames, target_test = 2; user marks 5 → all 5 stay in test
    data = _fake_data([("a", [f"img{i:04d}.png" for i in range(10)])])
    marks = [("a", f"img{i:04d}.png") for i in range(5)]
    train, test, stats = build_indices_from_dataframe(
        data, marks=marks, mode="hybrid", train_fraction=0.8, seed=42
    )
    assert sorted(test) == [0, 1, 2, 3, 4]
    assert set(train) == {5, 6, 7, 8, 9}
    # Derived fraction is 5/10 = 0.5, NOT 0.8


def test_hybrid_deterministic_with_seed():
    data = _fake_data([("a", [f"img{i:04d}.png" for i in range(20)])])
    marks = [("a", "img0000.png")]
    train1, test1, _ = build_indices_from_dataframe(data, marks=marks, mode="hybrid", train_fraction=0.8, seed=42)
    train2, test2, _ = build_indices_from_dataframe(data, marks=marks, mode="hybrid", train_fraction=0.8, seed=42)
    assert train1 == train2 and test1 == test2


def test_marks_pointing_at_missing_frames_dropped():
    data = _fake_data([("a", ["img1.png", "img2.png"])])
    marks = [("a", "img1.png"), ("a", "img_deleted.png"), ("ghost_stem", "x.png")]
    train, test, stats = build_indices_from_dataframe(data, marks=marks, mode="manual", train_fraction=0.8)
    assert set(test) == {0}
    assert stats["dropped_marks"] == 2


def test_manual_dataset_with_multiple_folders():
    data = _fake_data([
        ("a", ["img1.png", "img2.png"]),
        ("b", ["imgA.png", "imgB.png", "imgC.png"]),
    ])
    # Positions: a/img1=0, a/img2=1, b/imgA=2, b/imgB=3, b/imgC=4
    marks = [("a", "img2.png"), ("b", "imgC.png")]
    train, test, _ = build_indices_from_dataframe(data, marks=marks, mode="manual", train_fraction=0.8)
    assert set(test) == {1, 4}
    assert set(train) == {0, 2, 3}


def test_ratio_rounding_seven_frames():
    # 7 frames × 0.8 → target_test = round(0.2 * 7) = round(1.4) = 1
    data = _fake_data([("a", [f"img{i:04d}.png" for i in range(7)])])
    train, test, _ = build_indices_from_dataframe(
        data, marks=[], mode="hybrid", train_fraction=0.8, seed=42
    )
    assert len(test) == 1
    assert len(train) == 6
