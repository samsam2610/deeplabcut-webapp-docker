"""Host-testable parts of the peak emitter.

The inference itself needs torch + DeepLabCut and is NOT covered here; it is
verified by re-running the 0.344 px comparison against the pose h5 on the
deployed container. What is covered is everything around it.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc.peaks_emit import (
    extract_peaks,
    heatmap_to_image,
    _merge,
    _peaks_sidecar_path,
    _read,
    _write_atomic,
)


def test_extract_peaks_finds_the_single_maximum():
    hm = np.zeros((20, 20), np.float32)
    hm[7, 11] = 1.0
    xy, sc = extract_peaks(hm, k=3, min_distance=3)
    np.testing.assert_allclose(xy[0], [11.0, 7.0])   # x = column, y = row
    assert sc[0] == pytest.approx(1.0)
    assert np.isnan(xy[1]).all() and sc[1] == 0.0


def test_extract_peaks_suppresses_neighbours_of_one_blob():
    """Without NMS a plain top-k returns the peak and its neighbours, so k
    'candidates' would be one detection counted k times."""
    hm = np.zeros((20, 20), np.float32)
    hm[10, 10] = 1.0
    hm[10, 11] = 0.99
    hm[11, 10] = 0.98
    xy, sc = extract_peaks(hm, k=3, min_distance=3)
    assert np.isfinite(xy).all(axis=1).sum() == 1


def test_extract_peaks_returns_two_separated_blobs_in_score_order():
    hm = np.zeros((30, 30), np.float32)
    hm[5, 5] = 0.6
    hm[20, 20] = 0.9
    xy, sc = extract_peaks(hm, k=3, min_distance=3)
    np.testing.assert_allclose(xy[0], [20.0, 20.0])
    np.testing.assert_allclose(xy[1], [5.0, 5.0])
    assert sc[0] > sc[1]


def test_extract_peaks_on_a_flat_heatmap_returns_nothing():
    xy, sc = extract_peaks(np.zeros((10, 10), np.float32), k=3)
    assert np.isnan(xy).all() and (sc == 0).all()


def test_heatmap_to_image_maps_cell_centres_at_stride_two():
    out = heatmap_to_image(np.array([[3.0, 4.0]]), 2.0)
    np.testing.assert_allclose(out[0], [7.0, 9.0])   # (cell + 0.5) * stride


def test_heatmap_to_image_preserves_nan_padding():
    out = heatmap_to_image(np.array([[np.nan, np.nan]]), 2.0)
    assert np.isnan(out).all()


# --- sidecar path, atomic write/read, and merge --------------------------

def _mk_sidecar(frames, bodyparts=("nose", "tail"), k=3, score_fill=0.0,
                 meta_extra=None):
    """Build a minimal-but-valid sidecar dict, as emit_peaks_for_video would."""
    n, b = len(frames), len(bodyparts)
    xy = np.full((n, b, k, 2), np.nan, np.float32)
    score = np.full((n, b, k), score_fill, np.float32)
    if n and b and k:
        xy[0, 0, 0] = (1.5, 2.5)  # one real peak so NaN-padding isn't the only case
    meta = {"k": k, "min_distance": 3, "snapshot": "snap.pt", "stride": 2.0,
            "locref_std": 7.2801}
    if meta_extra:
        meta.update(meta_extra)
    return {
        "frames": np.asarray(frames, np.int32),
        "xy": xy,
        "score": score,
        "bodyparts": list(bodyparts),
        "meta": meta,
    }


def test_peaks_sidecar_path_appends_peaks_suffix():
    assert _peaks_sidecar_path("/data/vidDLC_x.h5") == Path(
        "/data/vidDLC_x_peaks.npz")


def test_write_atomic_then_read_round_trips(tmp_path):
    d = _mk_sidecar([1, 2, 3])
    dst = tmp_path / "vidDLC_x_peaks.npz"
    _write_atomic(dst, d)

    got = _read(dst)
    np.testing.assert_array_equal(got["frames"], d["frames"])
    assert got["frames"].dtype == np.int32
    np.testing.assert_allclose(got["xy"][0, 0, 0], [1.5, 2.5])
    assert np.isnan(got["xy"][0, 0, 1]).all()   # NaN padding preserved
    assert got["xy"].dtype == np.float32
    np.testing.assert_array_equal(got["score"], d["score"])
    assert got["score"].dtype == np.float32
    assert got["bodyparts"] == ["nose", "tail"]
    assert all(isinstance(b, str) for b in got["bodyparts"])
    assert got["meta"] == d["meta"]


def test_write_atomic_leaves_no_stray_temp_file(tmp_path):
    """Regression test for the bug where `dst.with_suffix(".npz.tmp")` made
    np.savez_compressed actually write "*.npz.tmp.npz" (numpy appends .npz to
    any path not already ending in .npz), so the rename target never existed.
    """
    dst = tmp_path / "vidDLC_x_peaks.npz"
    _write_atomic(dst, _mk_sidecar([1, 2]))

    entries = sorted(p.name for p in tmp_path.iterdir())
    assert entries == [dst.name]


def test_write_atomic_replaces_existing_sidecar(tmp_path):
    dst = tmp_path / "vidDLC_x_peaks.npz"
    _write_atomic(dst, _mk_sidecar([1, 2], score_fill=1.0))
    _write_atomic(dst, _mk_sidecar([5, 6, 7], score_fill=2.0))

    got = _read(dst)
    np.testing.assert_array_equal(got["frames"], [5, 6, 7])
    assert (got["score"] == 2.0).all()
    entries = sorted(p.name for p in tmp_path.iterdir())
    assert entries == [dst.name]   # replaced, not left alongside a stray file


def test_merge_unions_disjoint_frames_sorted():
    old = _mk_sidecar([1, 3])
    new = _mk_sidecar([2, 4])
    merged = _merge(old, new)
    np.testing.assert_array_equal(merged["frames"], [1, 2, 3, 4])


def test_merge_prefers_new_run_on_overlapping_frames():
    old = _mk_sidecar([1, 2, 3], score_fill=1.0)
    new = _mk_sidecar([2, 3, 4], score_fill=9.0)
    merged = _merge(old, new)

    np.testing.assert_array_equal(merged["frames"], [1, 2, 3, 4])
    by_frame = dict(zip(merged["frames"].tolist(), merged["score"]))
    assert by_frame[1] == pytest.approx(1.0)   # only in old -> kept from old
    assert by_frame[2] == pytest.approx(9.0)   # overlap -> NEW wins
    assert by_frame[3] == pytest.approx(9.0)   # overlap -> NEW wins
    assert by_frame[4] == pytest.approx(9.0)   # only in new -> kept from new


def test_merge_raises_on_bodypart_mismatch():
    old = _mk_sidecar([1, 2], bodyparts=("nose", "tail"))
    new = _mk_sidecar([3], bodyparts=("nose",))
    with pytest.raises(ValueError):
        _merge(old, new)


def test_merge_raises_on_k_mismatch():
    old = _mk_sidecar([1, 2], k=3)
    new = _mk_sidecar([3], k=5)
    with pytest.raises(ValueError):
        _merge(old, new)
