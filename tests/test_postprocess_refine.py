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


def test_smoothing_reduces_high_frequency_noise():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=21)
    scorer = df.columns.levels[0][0]
    sign = np.array([1 if i % 2 == 0 else -1 for i in range(21)], dtype=float)
    df.loc[:, (scorer, "nose", "x")] = 50.0 + sign
    df.loc[:, (scorer, "nose", "y")] = 50.0

    out = ppr.step_smoothing(df, window=5, polyorder=2)

    xs = out[(scorer, "nose", "x")].to_numpy()
    assert xs[5:16].std() < 0.5


def test_smoothing_rejects_invalid_window():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.step_smoothing(df, window=4, polyorder=2)  # even window
    with pytest.raises(ValueError):
        ppr.step_smoothing(df, window=3, polyorder=3)  # polyorder >= window


def test_run_pipeline_applies_steps_in_fixed_order(monkeypatch):
    """Steps must execute in: filter → outliers → interp → smooth."""
    df = _make_dlc_dataframe()
    order = []

    def make_spy(name, real):
        def wrapped(d, **kwargs):
            order.append(name)
            return real(d, **kwargs)
        return wrapped

    monkeypatch.setattr(ppr, "step_likelihood_filter",
                        make_spy("filter", ppr.step_likelihood_filter))
    monkeypatch.setattr(ppr, "step_outlier_removal",
                        make_spy("outliers", ppr.step_outlier_removal))
    monkeypatch.setattr(ppr, "step_interpolation",
                        make_spy("interp", ppr.step_interpolation))
    monkeypatch.setattr(ppr, "step_smoothing",
                        make_spy("smooth", ppr.step_smoothing))

    cfg = {
        "likelihood_filter": {"enabled": True, "threshold": 0.5},
        "outlier_removal":   {"enabled": True, "z_threshold": 3.0},
        "interpolation":     {"enabled": True, "method": "linear", "limit": 5},
        "smoothing":         {"enabled": True, "window": 5, "polyorder": 2},
    }
    ppr.run_pipeline(df, cfg)
    assert order == ["filter", "outliers", "interp", "smooth"]


def test_run_pipeline_skips_disabled_steps(monkeypatch):
    df = _make_dlc_dataframe()
    called = []
    monkeypatch.setattr(ppr, "step_likelihood_filter",
                        lambda d, **kw: called.append("filter") or d)
    monkeypatch.setattr(ppr, "step_smoothing",
                        lambda d, **kw: called.append("smooth") or d)

    cfg = {
        "likelihood_filter": {"enabled": False, "threshold": 0.5},
        "smoothing":         {"enabled": True, "window": 5, "polyorder": 2},
    }
    ppr.run_pipeline(df, cfg)
    assert called == ["smooth"]


def test_run_single_dispatches_to_named_step():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=5)
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "likelihood")] = [0.9, 0.9, 0.1, 0.9, 0.1]
    out = ppr.run_single(df, "likelihood_filter", {"threshold": 0.5})
    assert pd.isna(out.loc[2, (scorer, "nose", "x")])


def test_run_single_unknown_step():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.run_single(df, "nope", {})
