"""Tests for the deeplabcut.filterpredictions wrapper."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# Skip the entire module on hosts without DeepLabCut (e.g. dev laptops).
# postprocess_dlc imports `deeplabcut` at module top, matching dlc/tasks.py.
pytest.importorskip("deeplabcut")

# Ensure src/ is on path (matches the pattern used in test_postprocess_refine.py).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dlc import postprocess_dlc as ppd  # noqa: E402


def _make_h5(path: Path, scorer="DLC_resnet50", bodyparts=("nose",)):
    cols = pd.MultiIndex.from_product(
        [[scorer], bodyparts, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    df = pd.DataFrame(np.zeros((5, len(cols))), columns=cols)
    df.to_hdf(path, key="df_with_missing", mode="w", format="table")


def _tables_available() -> bool:
    try:
        import tables  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(not _tables_available(), reason="needs pytables")
def test_run_filterpredictions_invokes_dlc_and_relocates_output(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("Task: dummy\n")
    src = tmp_path / "video1DLC_resnet50_shuffle1_50000.h5"
    _make_h5(src)

    out_dir = tmp_path / "postproc" / "20260501-120000_filterpredictions"

    def fake_filterpredictions(cfg, videos, **kwargs):
        for v in videos:
            v = Path(v)
            base = v.with_suffix("")
            target = base.with_name(base.name + "_filtered" + ".h5")
            target.write_bytes(b"fake-h5-bytes")

    with patch("dlc.postprocess_dlc.dlc.filterpredictions",
               side_effect=fake_filterpredictions):
        result = ppd.run_filterpredictions(
            config_path=config,
            input_path=src,
            output_dir=out_dir,
            params={"filtertype": "median", "windowlength": 5, "save_as_csv": False},
        )

    assert result["status"] == "success"
    relocated = out_dir / "video1DLC_resnet50_shuffle1_50000_filtered.h5"
    assert relocated.exists()
    assert src.exists()
    assert src.stat().st_size > 0


def test_run_filterpredictions_validates_extension(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("Task: dummy\n")
    bad = tmp_path / "not-a-prediction.txt"
    bad.write_text("hi")
    with pytest.raises(ValueError):
        ppd.run_filterpredictions(
            config_path=config,
            input_path=bad,
            output_dir=tmp_path / "out",
            params={},
        )
