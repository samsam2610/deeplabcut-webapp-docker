"""Tests for postprocess_refine I/O helpers and drivers."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dlc import postprocess_refine as ppr  # noqa: E402


def _make_dlc_dataframe(scorer="DLC_resnet50", bodyparts=("nose", "tail"), n_frames=10):
    """Build a minimal DLC-shaped DataFrame: MultiIndex columns (scorer, bp, coord)."""
    cols = pd.MultiIndex.from_product(
        [[scorer], bodyparts, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    rng = np.random.default_rng(42)
    data = rng.random((n_frames, len(cols))) * 100
    return pd.DataFrame(data, columns=cols)


def _tables_available() -> bool:
    """Return True iff PyTables can be imported AND initialised on this host.

    The host commonly has a stale `tables` install whose C extension fails
    against the local numpy ABI ("numpy.dtype size changed"). A bare
    `importorskip("tables")` is not enough — the import itself raises
    ValueError. So we try importing and treat any failure as "not available".
    """
    try:
        import tables  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(
    not _tables_available(),
    reason="PyTables not importable on host (run inside flask container)",
)
def test_read_write_h5_roundtrip(tmp_path):
    df = _make_dlc_dataframe()
    path = tmp_path / "preds.h5"
    ppr.write_predictions(df, path)
    df2 = ppr.read_predictions(path)
    pd.testing.assert_frame_equal(df, df2)


def test_read_write_csv_roundtrip(tmp_path):
    df = _make_dlc_dataframe()
    path = tmp_path / "preds.csv"
    ppr.write_predictions(df, path)
    df2 = ppr.read_predictions(path)
    pd.testing.assert_frame_equal(df, df2)


def test_likelihood_filter_drops_low_confidence_points():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=5)
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "likelihood")] = [0.9, 0.9, 0.1, 0.9, 0.1]

    out = ppr.step_likelihood_filter(df, threshold=0.5)

    nose_x = out[(scorer, "nose", "x")]
    assert nose_x.isna().tolist() == [False, False, True, False, True]
    nose_y = out[(scorer, "nose", "y")]
    assert nose_y.isna().tolist() == [False, False, True, False, True]


def test_likelihood_filter_rejects_invalid_threshold():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.step_likelihood_filter(df, threshold=1.5)
    with pytest.raises(ValueError):
        ppr.step_likelihood_filter(df, threshold=-0.1)


def test_outlier_removal_flags_zscore_outliers():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=10)
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "x")] = [50, 51, 49, 50, 50, 5000, 50, 51, 49, 50]
    df.loc[:, (scorer, "nose", "y")] = 50.0
    df.loc[:, (scorer, "nose", "likelihood")] = 0.99

    out = ppr.step_outlier_removal(df, z_threshold=3.0)

    assert pd.isna(out.loc[5, (scorer, "nose", "x")])
    assert out.loc[0, (scorer, "nose", "x")] == 50


def test_outlier_removal_rejects_negative_threshold():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.step_outlier_removal(df, z_threshold=-1.0)


def test_interpolation_fills_nan_gaps():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=5)
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "x")] = [0.0, float("nan"), float("nan"), 30.0, 40.0]
    df.loc[:, (scorer, "nose", "y")] = 0.0

    out = ppr.step_interpolation(df, method="linear", limit=3)

    xs = out[(scorer, "nose", "x")].tolist()
    assert xs[0] == 0.0
    assert xs[3] == 30.0
    assert xs[4] == 40.0
    assert xs[1] == pytest.approx(10.0)
    assert xs[2] == pytest.approx(20.0)


def test_interpolation_respects_limit():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=6)
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "x")] = [0.0] + [float("nan")] * 4 + [50.0]
    df.loc[:, (scorer, "nose", "y")] = 0.0

    out = ppr.step_interpolation(df, method="linear", limit=2)

    xs = out[(scorer, "nose", "x")]
    assert not pd.isna(xs.iloc[1])
    assert not pd.isna(xs.iloc[2])
    assert pd.isna(xs.iloc[3])
    assert pd.isna(xs.iloc[4])
