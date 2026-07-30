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

from dlc.peaks_emit import extract_peaks, heatmap_to_image


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
