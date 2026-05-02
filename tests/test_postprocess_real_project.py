"""Real on-disk integration tests for the post-process pipeline.

These tests are skipped when the DREADD project is not available, or when
no analyzed video h5 exists in the project. They are required to run before
declaring this feature complete (per CLAUDE.md).

Imports of `dlc.postprocess_dlc` (which imports the heavy `deeplabcut`
package) are deferred to inside test functions so the module collects on
machines that lack `deeplabcut`. Likewise `dlc.postprocess_refine` is only
imported inside its test (uses `pd.read_hdf` which needs `tables`).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

# Ensure src/ and tests/ are on path (matches the pattern used in sibling tests).
_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
for _p in (_SRC, _TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Prefer the project path resolved by conftest; fall back to host/container paths.
try:
    from conftest import ORIGINAL_DLC_PROJECT as _CONFTEST_PROJECT  # noqa: E402
except Exception:  # pragma: no cover — conftest must always be importable
    _CONFTEST_PROJECT = None

_HOST_PATH = Path("/home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07")
_CONTAINER_PATH = Path("/user-data/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07")

if _CONFTEST_PROJECT is not None and Path(_CONFTEST_PROJECT).is_dir():
    PROJECT = Path(_CONFTEST_PROJECT)
elif _HOST_PATH.is_dir():
    PROJECT = _HOST_PATH
else:
    PROJECT = _CONTAINER_PATH

CONFIG = PROJECT / "config.yaml"

pytestmark = pytest.mark.skipif(
    not CONFIG.is_file(),
    reason="DREADD project not at any known path on this host",
)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _find_one_analyzed_h5() -> Path | None:
    """Search the project's videos/ for an analyzed (non-filtered) h5."""
    videos_dir = PROJECT / "videos"
    if not videos_dir.is_dir():
        return None
    for p in videos_dir.rglob("*.h5"):
        if "_filtered" in p.name:
            continue
        if any(tag in p.name for tag in ("resnet", "mobilenet", "efficientnet", "dlcrnetms5", "hrnet")):
            return p
    return None


def test_filterpredictions_does_not_modify_source():
    """DLC filterpredictions must produce output without mutating the source h5."""
    src = _find_one_analyzed_h5()
    if src is None:
        pytest.skip("no analyzed h5 in DREADD project videos/")

    # Deferred imports so collection works without `deeplabcut`.
    from dlc import postprocess as pp
    from dlc import postprocess_dlc as ppd

    before = _sha256(src)
    out_dir = pp.make_run_subfolder(src.parent, "filterpredictions")
    # `run_filterpredictions` itself calls mkdir(exist_ok=False) — pass a fresh
    # path that does not yet exist if the production wrapper rejects pre-existing
    # output dirs. The current wrapper expects `output_dir` to NOT exist, so we
    # remove it first.
    try:
        out_dir.rmdir()
    except OSError:
        pass

    result = ppd.run_filterpredictions(
        config_path=CONFIG,
        input_path=src,
        output_dir=out_dir,
        params={"filtertype": "median", "windowlength": 5, "save_as_csv": False},
    )
    after = _sha256(src)
    assert before == after, "source file was modified by filterpredictions"
    if result["status"] == "success":
        assert result["output"] is not None
        assert Path(result["output"]).is_file()


def test_refine_pipeline_produces_output():
    """refineDLC pipeline must produce output without mutating the source h5."""
    src = _find_one_analyzed_h5()
    if src is None:
        pytest.skip("no analyzed h5 in DREADD project videos/")

    # Deferred imports so collection works without `tables` / pandas hdf5.
    from dlc import postprocess as pp
    from dlc import postprocess_refine as ppr

    before = _sha256(src)
    out_dir = pp.make_run_subfolder(src.parent, "refine_pipeline")

    df = ppr.read_predictions(src)
    out_df = ppr.run_pipeline(df, {
        "likelihood_filter": {"enabled": True, "threshold": 0.6},
        "interpolation":     {"enabled": True, "method": "linear", "limit": 5},
        "smoothing":         {"enabled": True, "window": 5, "polyorder": 2},
    })
    target = out_dir / (src.stem + "_refined.h5")
    ppr.write_predictions(out_df, target)

    after = _sha256(src)
    assert before == after, "source file was modified by refine pipeline"
    assert target.is_file()
