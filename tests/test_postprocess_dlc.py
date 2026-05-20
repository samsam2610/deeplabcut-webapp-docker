"""Tests for run_filterpredictions (local scipy medfilt, no DLC dependency)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure src/ is on path (matches the pattern used in test_postprocess_refine.py).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dlc import postprocess_dlc as ppd  # noqa: E402


def _make_dlc_dataframe(scorer="DLC_resnet50", bodyparts=("nose", "tail"), n_frames=20):
    cols = pd.MultiIndex.from_product(
        [[scorer], bodyparts, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    rng = np.random.default_rng(42)
    return pd.DataFrame(rng.random((n_frames, len(cols))) * 100, columns=cols)


def _tables_available() -> bool:
    try:
        import tables  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(not _tables_available(), reason="needs pytables")
def test_run_filterpredictions_writes_output_h5(tmp_path):
    src = tmp_path / "video1DLC_resnet50_shuffle1_50000.h5"
    df = _make_dlc_dataframe()
    df.to_hdf(src, key="df_with_missing", mode="w", format="table")

    out_dir = tmp_path / "postproc" / "20260501-120000_filterpredictions"

    result = ppd.run_filterpredictions(
        input_path=src,
        output_dir=out_dir,
        params={"filtertype": "median", "windowlength": 5, "save_as_csv": False},
    )

    assert result["status"] == "success", result
    out_path = out_dir / "video1DLC_resnet50_shuffle1_50000_filtered.h5"
    assert out_path.exists()
    # Source untouched.
    assert src.exists()
    # Filtered output preserves shape.
    out_df = pd.read_hdf(out_path)
    assert out_df.shape == df.shape


@pytest.mark.skipif(not _tables_available(), reason="needs pytables")
def test_run_filterpredictions_leaves_likelihood_alone(tmp_path):
    src = tmp_path / "predDLC_resnet50.h5"
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=11)
    df.to_hdf(src, key="df_with_missing", mode="w", format="table")
    likelihoods_before = df.xs("likelihood", level="coords", axis=1).copy()

    out_dir = tmp_path / "postproc" / "x"
    result = ppd.run_filterpredictions(
        input_path=src, output_dir=out_dir,
        params={"filtertype": "median", "windowlength": 3, "save_as_csv": False},
    )
    assert result["status"] == "success"

    out_df = pd.read_hdf(result["output"])
    likelihoods_after = out_df.xs("likelihood", level="coords", axis=1)
    pd.testing.assert_frame_equal(likelihoods_before, likelihoods_after)


@pytest.mark.skipif(not _tables_available(), reason="needs pytables")
def test_run_filterpredictions_works_when_output_dir_exists(tmp_path):
    """Regression: the celery task pre-creates output_dir via make_run_subfolder."""
    src = tmp_path / "video1DLC_resnet50_shuffle1_50000.h5"
    _make_dlc_dataframe().to_hdf(src, key="df_with_missing", mode="w", format="table")

    out_dir = tmp_path / "postproc" / "20260501-120000_filterpredictions"
    out_dir.mkdir(parents=True)  # pre-created, mimicking production

    result = ppd.run_filterpredictions(
        input_path=src, output_dir=out_dir,
        params={"filtertype": "median", "windowlength": 5, "save_as_csv": False},
    )
    assert result["status"] == "success"
    assert (out_dir / "video1DLC_resnet50_shuffle1_50000_filtered.h5").exists()


def test_run_filterpredictions_validates_extension(tmp_path):
    bad = tmp_path / "not-a-prediction.txt"
    bad.write_text("hi")
    with pytest.raises(ValueError):
        ppd.run_filterpredictions(
            input_path=bad,
            output_dir=tmp_path / "out",
            params={},
        )


def test_run_filterpredictions_arima_returns_failed(tmp_path):
    """ARIMA mode is not yet supported and must fail with a clear message."""
    src = tmp_path / "predDLC_resnet50.csv"
    df = _make_dlc_dataframe()
    df.to_csv(src)
    out_dir = tmp_path / "out"

    result = ppd.run_filterpredictions(
        input_path=src, output_dir=out_dir,
        params={"filtertype": "arima", "save_as_csv": False},
    )
    assert result["status"] == "failed"
    assert "ARIMA" in (result["error"] or "")


@pytest.mark.skipif(not _tables_available(), reason="needs pytables")
def test_run_filterpredictions_csv_input_csv_output(tmp_path):
    """CSV in → median-filtered output written to h5 (and csv if save_as_csv)."""
    src = tmp_path / "predDLC_resnet50.csv"
    df = _make_dlc_dataframe(bodyparts=("nose",))
    df.to_csv(src)

    out_dir = tmp_path / "out"
    result = ppd.run_filterpredictions(
        input_path=src, output_dir=out_dir,
        params={"filtertype": "median", "windowlength": 5, "save_as_csv": True},
    )
    assert result["status"] == "success", result
    assert (out_dir / "predDLC_resnet50_filtered.h5").exists()
    assert (out_dir / "predDLC_resnet50_filtered.csv").exists()
