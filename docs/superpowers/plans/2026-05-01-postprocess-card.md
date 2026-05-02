# Post-Process Predictions Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Post-Process Predictions card to the DLC project sidebar that runs DLC's `filterpredictions` and a vendored refineDLC toolkit on a single analyzed file or a folder of analyzed files, writing non-destructive timestamped outputs and streaming progress over Celery.

**Architecture:** Standard DLC blueprint pattern (template partial + JS controller + `dlc/postprocess.py` blueprint). Long-running work runs in one Celery task on the existing `worker` queue. refineDLC functions are vendored verbatim into `src/dlc/_refinedlc/` (no `pip install`) to avoid dep conflicts with DLC's pinned numpy/pandas/scipy. All outputs go under `<input-parent>/postproc/<timestamp>_<tool-tag>/` with a sidecar `run.json`.

**Tech Stack:** Flask blueprint, Celery (PyTorch worker queue), pandas/numpy/scipy (provided transitively by DLC), DeepLabCut Python API, vanilla JS frontend.

**Spec:** `docs/superpowers/specs/2026-05-01-postprocess-card-design.md`

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `src/dlc/postprocess.py` | Flask blueprint with routes (`/dlc/postprocess/scan`, `/run`, `/status/<id>`, `/logs/<id>`, `/cancel/<id>`, `/recent`). |
| `src/dlc/postprocess_dlc.py` | Pure-Python wrapper around `deeplabcut.filterpredictions`. |
| `src/dlc/postprocess_refine.py` | `run_pipeline()` + `run_single()` drivers + I/O helpers (read DLC `.h5`/`.csv` → DataFrame, write back). |
| `src/dlc/_refinedlc/__init__.py` | Re-exports vendored functions. |
| `src/dlc/_refinedlc/filtering.py` | Vendored likelihood filter. |
| `src/dlc/_refinedlc/outliers.py` | Vendored outlier removal. |
| `src/dlc/_refinedlc/interpolation.py` | Vendored interpolation. |
| `src/dlc/_refinedlc/smoothing.py` | Vendored smoothing. |
| `src/dlc/_refinedlc/VENDORED.md` | Upstream URL, commit SHA, file mapping, license header. |
| `src/templates/partials/card_postprocess.html` | Card markup. |
| `src/static/js/postprocess.js` | DOM controller (open/close, parameter swap, fetch helpers, log polling). |
| `tests/test_postprocess_refine.py` | Unit tests for each vendored step + drivers. |
| `tests/test_postprocess_dlc.py` | Unit tests for the DLC wrapper (synthetic h5). |
| `tests/test_postprocess_routes.py` | Flask blueprint tests with Celery mocked. |
| `tests/test_postprocess_real_project.py` | Real-project integration tests (skipped if project absent). |
| `tests/test_postprocess_ui_isolation.py` | Static-template + JSDOM-equivalent assertions. |
| `tests/test_postprocess_vendored_imports.py` | Dep-audit smoke import. |

**Modified files:**

- `src/templates/partials/card_dlc_project.html` — insert one button between lines 119 and 120.
- `src/templates/index.html` — include the new partial.
- `src/app.py` — import & register the new blueprint (lines ~186–200).
- `src/dlc/tasks.py` — register `dlc_postprocess_run` Celery task at the end of file.
- `src/dlc/README.md` — document the new module + routes.

---

## Conventions Used Below

- All commands run from the repo root: `/home/sam/docker-images/deeplabcut-webapp-docker`.
- "Run tests" means: `python -m pytest <path> -v` (host Python is fine for everything except the dep-audit test, which must run inside the worker container).
- Use the Docker `flask` container for any code path that touches `pandas.read_hdf` (`tables` is missing on the host); use the host for everything else.
- Each task ends with a commit. Use small, focused commits.

---

## Task 1: Skeleton blueprint + registration (no behavior)

**Files:**
- Create: `src/dlc/postprocess.py`
- Modify: `src/app.py:186-200`
- Test: `tests/test_postprocess_routes.py` (new)

- [ ] **Step 1: Write a failing test that the blueprint is registered**

Create `tests/test_postprocess_routes.py`:

```python
"""Tests for the post-process predictions blueprint."""
from __future__ import annotations

import pytest


def test_blueprint_registered(client):
    """A GET to /dlc/postprocess/recent should not 404."""
    resp = client.get("/dlc/postprocess/recent")
    # Route exists; either 200 (empty list) or 400 (no active project), but never 404.
    assert resp.status_code != 404
```

The `client` fixture comes from `tests/conftest.py` — verify it exists by reading that file before running. If a different fixture name is used, match it.

- [ ] **Step 2: Run the test to confirm it fails**

Run: `python -m pytest tests/test_postprocess_routes.py::test_blueprint_registered -v`
Expected: FAIL with 404.

- [ ] **Step 3: Create the blueprint with one stub route**

Create `src/dlc/postprocess.py`:

```python
"""Post-process predictions blueprint.

Exposes routes that run DeepLabCut's filterpredictions and a vendored
refineDLC toolkit on analyzed .h5/.csv files. See
docs/superpowers/specs/2026-05-01-postprocess-card-design.md.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

bp = Blueprint("dlc_postprocess", __name__, url_prefix="/dlc/postprocess")


@bp.route("/recent", methods=["GET"])
def recent():
    """Return recent post-process runs for the active project (stub)."""
    return jsonify({"runs": []})
```

- [ ] **Step 4: Register the blueprint in `src/app.py`**

In `src/app.py`, immediately after the line `from dlc.posture_routes import bp as _dlc_posture_bp` (around line 187), add:

```python
from dlc.postprocess import bp as _dlc_postprocess_bp
```

Then immediately after `app.register_blueprint(_dlc_posture_bp)` (around line 200), add:

```python
app.register_blueprint(_dlc_postprocess_bp)
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `python -m pytest tests/test_postprocess_routes.py::test_blueprint_registered -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/dlc/postprocess.py src/app.py tests/test_postprocess_routes.py
git commit -m "feat(postprocess): scaffold blueprint and registration"
```

---

## Task 2: Vendor refineDLC modules + dep audit

This task does not implement any logic; it copies upstream source verbatim and verifies the modules import cleanly inside the worker image.

**Files:**
- Create: `src/dlc/_refinedlc/__init__.py`
- Create: `src/dlc/_refinedlc/filtering.py` (vendored)
- Create: `src/dlc/_refinedlc/outliers.py` (vendored)
- Create: `src/dlc/_refinedlc/interpolation.py` (vendored)
- Create: `src/dlc/_refinedlc/smoothing.py` (vendored)
- Create: `src/dlc/_refinedlc/VENDORED.md`
- Test: `tests/test_postprocess_vendored_imports.py`

- [ ] **Step 1: Pick a refineDLC commit and clone it into a tmp dir**

```bash
mkdir -p /tmp/refinedlc-vendor && cd /tmp/refinedlc-vendor
git clone https://github.com/wer-kle/refineDLC.git
cd refineDLC
git rev-parse HEAD > /tmp/refinedlc-vendor/SHA
git rev-parse HEAD
```

Record the SHA — you'll need it in `VENDORED.md`.

- [ ] **Step 2: Identify which files implement each step**

Read `/tmp/refinedlc-vendor/refineDLC` looking for the four step implementations (likelihood-based filtering, outlier removal, interpolation, smoothing). For each, note:

- The function name and signature.
- Any helper functions it imports.
- Direct dependencies (`numpy`, `pandas`, `scipy.signal`, etc.).

If a step is split across multiple modules, vendor all of them. Do **not** vendor refineDLC's CLI, `__main__`, plotting, or test files.

- [ ] **Step 3: Copy source files into `src/dlc/_refinedlc/`**

For each of the four steps, create a file at `src/dlc/_refinedlc/<step>.py` and copy the function bodies + their direct helpers verbatim. **Preserve any copyright/license header** from the upstream file. Add a one-line comment at the top of each file:

```python
# Vendored from https://github.com/wer-kle/refineDLC at <SHA>.
# See VENDORED.md for license and details.
```

If a vendored file imports another refineDLC module, change the import to a relative import (`from .other_module import ...`).

- [ ] **Step 4: Create `__init__.py` re-exporting public functions**

Create `src/dlc/_refinedlc/__init__.py`:

```python
"""Vendored refineDLC processing functions.

Origin: https://github.com/wer-kle/refineDLC
See VENDORED.md for upstream commit and license.
"""
from .filtering import *  # noqa: F401, F403
from .outliers import *  # noqa: F401, F403
from .interpolation import *  # noqa: F401, F403
from .smoothing import *  # noqa: F401, F403
```

If upstream functions are not already namespaced via `__all__`, replace each `from .<mod> import *` with explicit names (`from .filtering import filter_by_likelihood, ...`).

- [ ] **Step 5: Write `VENDORED.md`**

Create `src/dlc/_refinedlc/VENDORED.md`:

```markdown
# Vendored refineDLC

**Upstream:** https://github.com/wer-kle/refineDLC
**Commit:** <SHA from /tmp/refinedlc-vendor/SHA>
**Date vendored:** 2026-05-01
**License:** <copy from upstream LICENSE file — paste full text or include as separate LICENSE file>

## File Mapping

| This repo | Upstream |
|---|---|
| `filtering.py` | `<upstream/path/to/filter_step.py>` |
| `outliers.py` | `<upstream/path/to/outliers_step.py>` |
| `interpolation.py` | `<upstream/path/to/interp_step.py>` |
| `smoothing.py` | `<upstream/path/to/smoothing_step.py>` |

Replace the upstream paths above with the actual file locations identified in Step 2.

## Why vendored (not pip-installed)

refineDLC's repo may pin pandas/numpy/scipy versions that conflict with the
versions DLC requires. The worker image already provides compatible versions
transitively through `deeplabcut`, so we copy only the processing functions
and reuse the existing dep set.

## Updating

To update to a newer upstream commit:
1. `git clone https://github.com/wer-kle/refineDLC.git`
2. Diff the upstream files against the vendored copies.
3. Re-vendor any updated functions, preserving the relative-import edits.
4. Update the SHA in this file.
5. Run `tests/test_postprocess_vendored_imports.py` and `tests/test_postprocess_refine.py`.
```

- [ ] **Step 6: Write the dep-audit test**

Create `tests/test_postprocess_vendored_imports.py`:

```python
"""Smoke test: every vendored refineDLC module imports cleanly."""
from __future__ import annotations


def test_vendored_modules_import():
    """Importing the vendored package must not raise."""
    from src.dlc import _refinedlc  # noqa: F401
    from src.dlc._refinedlc import filtering, outliers, interpolation, smoothing  # noqa: F401


def test_vendored_modules_expose_callables():
    """Each vendored module must expose at least one public callable."""
    from src.dlc._refinedlc import filtering, outliers, interpolation, smoothing

    for mod in (filtering, outliers, interpolation, smoothing):
        callables = [
            name for name in dir(mod)
            if not name.startswith("_") and callable(getattr(mod, name))
        ]
        assert callables, f"{mod.__name__} exposes no public callables"
```

If `src.dlc` is not the import root in this repo (the project uses bare `dlc`, judging by `from dlc.posture_routes import ...` in `app.py`), change `from src.dlc...` to `from dlc...`.

- [ ] **Step 7: Run the test on the host AND inside the worker container**

```bash
# Host:
python -m pytest tests/test_postprocess_vendored_imports.py -v

# Worker container:
docker exec $(docker ps --filter "name=worker" --filter "name=^/.*worker$" -q | head -1) \
  python -m pytest /app/tests/test_postprocess_vendored_imports.py -v
```

Expected: PASS in both. If the host fails because of a missing dep that exists only in the container, that's fine — what matters is the container passes.

- [ ] **Step 8: Commit**

```bash
git add src/dlc/_refinedlc/ tests/test_postprocess_vendored_imports.py
git commit -m "feat(postprocess): vendor refineDLC processing modules"
```

---

## Task 3: I/O helpers — read/write DLC `.h5` and `.csv`

**Files:**
- Create: `src/dlc/postprocess_refine.py` (initial: I/O helpers only)
- Test: `tests/test_postprocess_refine.py`

- [ ] **Step 1: Write failing tests for the I/O round-trip**

Create `tests/test_postprocess_refine.py`:

```python
"""Tests for postprocess_refine I/O helpers and drivers."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dlc import postprocess_refine as ppr


def _make_dlc_dataframe(scorer="DLC_resnet50", bodyparts=("nose", "tail"), n_frames=10):
    """Build a minimal DLC-shaped DataFrame: MultiIndex columns (scorer, bp, coord)."""
    cols = pd.MultiIndex.from_product(
        [[scorer], bodyparts, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    rng = np.random.default_rng(42)
    data = rng.random((n_frames, len(cols))) * 100
    return pd.DataFrame(data, columns=cols)


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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python -m pytest tests/test_postprocess_refine.py::test_read_write_h5_roundtrip tests/test_postprocess_refine.py::test_read_write_csv_roundtrip -v`
Expected: FAIL (`ImportError: cannot import name 'postprocess_refine'`).

- [ ] **Step 3: Implement the I/O helpers**

Create `src/dlc/postprocess_refine.py`:

```python
"""refineDLC drivers and DLC predictions I/O.

Reads/writes analyzed prediction tables (DLC's MultiIndex layout):
    columns = MultiIndex[(scorer, bodyparts, coords)]
    coords ∈ {"x", "y", "likelihood"}
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_predictions(path: str | Path) -> pd.DataFrame:
    """Load a DLC predictions table from .h5 or .csv into a DataFrame."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".h5":
        # DLC's analyzed h5 has a single key; pandas auto-selects it.
        return pd.read_hdf(p)
    if suf == ".csv":
        # DLC's analyzed CSV has 3 header rows for the MultiIndex.
        return pd.read_csv(p, header=[0, 1, 2], index_col=0)
    raise ValueError(f"Unsupported predictions extension: {suf!r}")


def write_predictions(df: pd.DataFrame, path: str | Path) -> None:
    """Write a DLC predictions DataFrame back to .h5 or .csv preserving format."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    suf = p.suffix.lower()
    if suf == ".h5":
        df.to_hdf(p, key="df_with_missing", mode="w", format="table")
    elif suf == ".csv":
        df.to_csv(p)
    else:
        raise ValueError(f"Unsupported predictions extension: {suf!r}")
```

If `pd.read_csv(..., header=[0,1,2], index_col=0)` doesn't reproduce the MultiIndex exactly (e.g., `names` are lost), adjust by setting `df.columns.names = ["scorer", "bodyparts", "coords"]` after reading. Make the round-trip test pass before moving on.

- [ ] **Step 4: Run the tests until both pass on the host**

Note: the `.h5` test may fail on the host because `tables` is missing (per CLAUDE.md). If so, run those tests inside the `flask` container:

```bash
docker exec $(docker ps --filter "name=flask" -q) \
  python -m pytest /app/tests/test_postprocess_refine.py::test_read_write_h5_roundtrip -v
```

The `.csv` test must pass on the host.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess_refine.py tests/test_postprocess_refine.py
git commit -m "feat(postprocess): predictions I/O helpers (h5/csv)"
```

---

## Task 4: refineDLC step adapters — likelihood filter

This task wraps the vendored likelihood-filter function with a stable, well-typed interface. It also locks down the parameter contract (range checks, defaults).

**Files:**
- Modify: `src/dlc/postprocess_refine.py` (add `step_likelihood_filter`)
- Test: `tests/test_postprocess_refine.py` (add tests)

- [ ] **Step 1: Write failing tests for the likelihood-filter adapter**

Append to `tests/test_postprocess_refine.py`:

```python
def test_likelihood_filter_drops_low_confidence_points():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=5)
    # Force likelihoods: high, high, low, high, low.
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "likelihood")] = [0.9, 0.9, 0.1, 0.9, 0.1]

    out = ppr.step_likelihood_filter(df, threshold=0.5)

    nose_x = out[(scorer, "nose", "x")]
    # Indices 2 and 4 must be NaN; others retained.
    assert nose_x.isna().tolist() == [False, False, True, False, True]
    nose_y = out[(scorer, "nose", "y")]
    assert nose_y.isna().tolist() == [False, False, True, False, True]


def test_likelihood_filter_rejects_invalid_threshold():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.step_likelihood_filter(df, threshold=1.5)
    with pytest.raises(ValueError):
        ppr.step_likelihood_filter(df, threshold=-0.1)
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python -m pytest tests/test_postprocess_refine.py -k likelihood -v`
Expected: FAIL.

- [ ] **Step 3: Implement `step_likelihood_filter`**

Append to `src/dlc/postprocess_refine.py`:

```python
from . import _refinedlc  # noqa: E402


def step_likelihood_filter(df: pd.DataFrame, *, threshold: float = 0.6) -> pd.DataFrame:
    """Drop (set NaN) x/y values where likelihood < threshold.

    Likelihood column itself is left unchanged so downstream steps can re-filter.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    for bp in bodyparts:
        lh = out[(scorer, bp, "likelihood")]
        mask = lh < threshold
        out.loc[mask, (scorer, bp, "x")] = float("nan")
        out.loc[mask, (scorer, bp, "y")] = float("nan")
    return out
```

If the vendored `_refinedlc.filtering` module already provides a function with the exact same semantics, prefer calling it from inside `step_likelihood_filter` rather than re-implementing — but keep the validation, the `.copy()`, and the leave-likelihood-untouched behavior at this layer.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `python -m pytest tests/test_postprocess_refine.py -k likelihood -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess_refine.py tests/test_postprocess_refine.py
git commit -m "feat(postprocess): likelihood-filter step adapter"
```

---

## Task 5: refineDLC step adapter — outlier removal

**Files:**
- Modify: `src/dlc/postprocess_refine.py`
- Test: `tests/test_postprocess_refine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_postprocess_refine.py`:

```python
def test_outlier_removal_flags_zscore_outliers():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=10)
    scorer = df.columns.levels[0][0]
    # Cluster around 50, with one extreme spike.
    df.loc[:, (scorer, "nose", "x")] = [50, 51, 49, 50, 50, 5000, 50, 51, 49, 50]
    df.loc[:, (scorer, "nose", "y")] = 50.0
    df.loc[:, (scorer, "nose", "likelihood")] = 0.99

    out = ppr.step_outlier_removal(df, z_threshold=3.0)

    assert pd.isna(out.loc[5, (scorer, "nose", "x")])
    # Non-outlier rows preserved.
    assert out.loc[0, (scorer, "nose", "x")] == 50


def test_outlier_removal_rejects_negative_threshold():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.step_outlier_removal(df, z_threshold=-1.0)
```

- [ ] **Step 2: Run the tests; confirm failure**

Run: `python -m pytest tests/test_postprocess_refine.py -k outlier -v`
Expected: FAIL.

- [ ] **Step 3: Implement `step_outlier_removal`**

Append to `src/dlc/postprocess_refine.py`:

```python
def step_outlier_removal(df: pd.DataFrame, *, z_threshold: float = 3.0) -> pd.DataFrame:
    """Set x/y to NaN where |z-score| > z_threshold (per bodypart, per coord).

    Operates per bodypart on x and y independently. Rows with NaN inputs are
    skipped (mean/std computed on finite values only).
    """
    if z_threshold <= 0:
        raise ValueError(f"z_threshold must be > 0, got {z_threshold}")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    for bp in bodyparts:
        for axis in ("x", "y"):
            col = (scorer, bp, axis)
            series = out[col]
            mean = series.mean(skipna=True)
            std = series.std(skipna=True)
            if std == 0 or pd.isna(std):
                continue
            mask = (series - mean).abs() > (z_threshold * std)
            out.loc[mask, col] = float("nan")
    return out
```

If the vendored `_refinedlc.outliers` function uses a different algorithm (e.g., MAD instead of z-score), prefer the vendored algorithm — adjust the test inputs/expected to match. The acceptance criterion is: identical algorithm to upstream refineDLC, with our validation + DataFrame contract on top.

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_refine.py -k outlier -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess_refine.py tests/test_postprocess_refine.py
git commit -m "feat(postprocess): outlier-removal step adapter"
```

---

## Task 6: refineDLC step adapter — interpolation

**Files:**
- Modify: `src/dlc/postprocess_refine.py`
- Test: `tests/test_postprocess_refine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_postprocess_refine.py`:

```python
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
    # Gap filled linearly: 0 → 30 over 3 steps → 10, 20.
    assert xs[1] == pytest.approx(10.0)
    assert xs[2] == pytest.approx(20.0)


def test_interpolation_respects_limit():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=6)
    scorer = df.columns.levels[0][0]
    df.loc[:, (scorer, "nose", "x")] = [0.0] + [float("nan")] * 4 + [50.0]
    df.loc[:, (scorer, "nose", "y")] = 0.0

    out = ppr.step_interpolation(df, method="linear", limit=2)

    xs = out[(scorer, "nose", "x")]
    # With limit=2, only the first two NaNs after a non-NaN get filled.
    assert not pd.isna(xs.iloc[1])
    assert not pd.isna(xs.iloc[2])
    assert pd.isna(xs.iloc[3])
    assert pd.isna(xs.iloc[4])
```

- [ ] **Step 2: Run the tests; confirm failure**

Run: `python -m pytest tests/test_postprocess_refine.py -k interpolat -v`
Expected: FAIL.

- [ ] **Step 3: Implement `step_interpolation`**

Append to `src/dlc/postprocess_refine.py`:

```python
def step_interpolation(
    df: pd.DataFrame,
    *,
    method: str = "linear",
    limit: int | None = None,
) -> pd.DataFrame:
    """Interpolate NaN gaps in x and y per bodypart.

    `method` is passed through to pandas.Series.interpolate. `limit` caps
    consecutive NaN fills (None = unlimited).
    """
    if method not in {"linear", "spline", "polynomial", "nearest", "cubic"}:
        raise ValueError(f"unsupported interpolation method: {method!r}")
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be >= 1 or None, got {limit}")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    interp_kwargs = {"method": method}
    if limit is not None:
        interp_kwargs["limit"] = limit
    for bp in bodyparts:
        for axis in ("x", "y"):
            col = (scorer, bp, axis)
            out[col] = out[col].interpolate(**interp_kwargs)
    return out
```

Same caveat as Task 5: if the vendored interpolation function is more sophisticated (e.g., uses `scipy.interpolate.UnivariateSpline`), wrap that call instead — keep the validation contract.

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_refine.py -k interpolat -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess_refine.py tests/test_postprocess_refine.py
git commit -m "feat(postprocess): interpolation step adapter"
```

---

## Task 7: refineDLC step adapter — smoothing

**Files:**
- Modify: `src/dlc/postprocess_refine.py`
- Test: `tests/test_postprocess_refine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_postprocess_refine.py`:

```python
def test_smoothing_reduces_high_frequency_noise():
    df = _make_dlc_dataframe(bodyparts=("nose",), n_frames=21)
    scorer = df.columns.levels[0][0]
    # Sawtooth ±1 on top of a constant; smoothing should flatten it.
    sign = np.array([1 if i % 2 == 0 else -1 for i in range(21)], dtype=float)
    df.loc[:, (scorer, "nose", "x")] = 50.0 + sign
    df.loc[:, (scorer, "nose", "y")] = 50.0

    out = ppr.step_smoothing(df, window=5, polyorder=2)

    xs = out[(scorer, "nose", "x")].to_numpy()
    # Edges may differ; check the interior variance.
    assert xs[5:16].std() < 0.5


def test_smoothing_rejects_invalid_window():
    df = _make_dlc_dataframe()
    with pytest.raises(ValueError):
        ppr.step_smoothing(df, window=4, polyorder=2)  # even window
    with pytest.raises(ValueError):
        ppr.step_smoothing(df, window=3, polyorder=3)  # polyorder >= window
```

- [ ] **Step 2: Run the tests; confirm failure**

Run: `python -m pytest tests/test_postprocess_refine.py -k smooth -v`
Expected: FAIL.

- [ ] **Step 3: Implement `step_smoothing`**

Append to `src/dlc/postprocess_refine.py`:

```python
from scipy.signal import savgol_filter as _savgol_filter  # noqa: E402


def step_smoothing(
    df: pd.DataFrame,
    *,
    window: int = 5,
    polyorder: int = 2,
) -> pd.DataFrame:
    """Savitzky–Golay smoothing applied to x and y per bodypart.

    NaN gaps are preserved (savgol can't handle NaN; we mask, smooth, restore).
    """
    if window % 2 == 0 or window < 3:
        raise ValueError(f"window must be odd and >= 3, got {window}")
    if polyorder >= window:
        raise ValueError(f"polyorder ({polyorder}) must be < window ({window})")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    for bp in bodyparts:
        for axis in ("x", "y"):
            col = (scorer, bp, axis)
            series = out[col]
            finite = series.notna()
            if finite.sum() < window:
                continue
            values = series.to_numpy(dtype=float, copy=True)
            smoothed = values.copy()
            mask = finite.to_numpy()
            smoothed[mask] = _savgol_filter(
                values[mask], window_length=window, polyorder=polyorder, mode="interp"
            )
            out[col] = smoothed
    return out
```

Same pattern: prefer the vendored implementation if it exists; this is the fallback contract.

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_refine.py -k smooth -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess_refine.py tests/test_postprocess_refine.py
git commit -m "feat(postprocess): smoothing step adapter"
```

---

## Task 8: refineDLC pipeline + single drivers

**Files:**
- Modify: `src/dlc/postprocess_refine.py`
- Test: `tests/test_postprocess_refine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_postprocess_refine.py`:

```python
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
```

- [ ] **Step 2: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_refine.py -k "pipeline or single" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the drivers**

Append to `src/dlc/postprocess_refine.py`:

```python
PIPELINE_ORDER = ("likelihood_filter", "outlier_removal", "interpolation", "smoothing")

_STEP_FUNCS = {
    "likelihood_filter": "step_likelihood_filter",
    "outlier_removal":   "step_outlier_removal",
    "interpolation":     "step_interpolation",
    "smoothing":         "step_smoothing",
}


def run_pipeline(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Apply enabled refineDLC steps in fixed order: filter → outliers → interp → smooth.

    `config` shape: {step_name: {"enabled": bool, **step_kwargs}}.
    Disabled or missing steps are skipped.
    """
    out = df
    for step in PIPELINE_ORDER:
        cfg = config.get(step) or {}
        if not cfg.get("enabled"):
            continue
        kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
        func = globals()[_STEP_FUNCS[step]]
        out = func(out, **kwargs)
    return out


def run_single(df: pd.DataFrame, step: str, params: dict) -> pd.DataFrame:
    """Apply exactly one step by name."""
    if step not in _STEP_FUNCS:
        raise ValueError(f"unknown step: {step!r}")
    func = globals()[_STEP_FUNCS[step]]
    return func(df, **params)
```

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_refine.py -k "pipeline or single" -v`
Expected: PASS.

- [ ] **Step 5: Run the full refine test file**

Run: `python -m pytest tests/test_postprocess_refine.py -v`
Expected: ALL PASS (skip h5 round-trip on host if needed; run it in the flask container).

- [ ] **Step 6: Commit**

```bash
git add src/dlc/postprocess_refine.py tests/test_postprocess_refine.py
git commit -m "feat(postprocess): refineDLC pipeline + single-step drivers"
```

---

## Task 9: DLC `filterpredictions` wrapper

**Files:**
- Create: `src/dlc/postprocess_dlc.py`
- Test: `tests/test_postprocess_dlc.py`

- [ ] **Step 1: Write failing tests using a mocked DLC API**

Create `tests/test_postprocess_dlc.py`:

```python
"""Tests for the deeplabcut.filterpredictions wrapper."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from dlc import postprocess_dlc as ppd


def _make_h5(path: Path, scorer="DLC_resnet50", bodyparts=("nose",)):
    cols = pd.MultiIndex.from_product(
        [[scorer], bodyparts, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    df = pd.DataFrame(np.zeros((5, len(cols))), columns=cols)
    df.to_hdf(path, key="df_with_missing", mode="w", format="table")


@pytest.mark.skipif(
    not pytest.importorskip("tables", reason="needs pytables"),
    reason="needs pytables",
)
def test_run_filterpredictions_invokes_dlc_and_relocates_output(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("Task: dummy\n")  # not actually parsed in test
    src = tmp_path / "video1DLC_resnet50_shuffle1_50000.h5"
    _make_h5(src)

    out_dir = tmp_path / "postproc" / "20260501-120000_filterpredictions"

    def fake_filterpredictions(cfg, videos, **kwargs):
        # DLC writes <stem>_filtered.h5 next to the input.
        suffix = "_filtered"
        for v in videos:
            v = Path(v)
            base = v.with_suffix("")
            target = base.with_name(base.name + suffix + ".h5")
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
    # Original untouched.
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
```

- [ ] **Step 2: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_dlc.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement the wrapper**

Create `src/dlc/postprocess_dlc.py`:

```python
"""Wrapper around deeplabcut.filterpredictions with our output-layout contract.

Expected directory layout produced by this module:
    <output_dir>/<input-stem>_filtered.<ext>
    <output_dir>/<input-stem>_filtered.csv  (if save_as_csv)

`output_dir` must be the per-run subfolder built by the caller
(e.g. `<input-parent>/postproc/20260501-143022_filterpredictions/`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import deeplabcut as dlc

ALLOWED_EXTS = {".h5", ".csv"}


def run_filterpredictions(
    *,
    config_path: str | Path,
    input_path: str | Path,
    output_dir: str | Path,
    params: dict,
) -> dict:
    """Run DLC's filterpredictions on a single analyzed file; relocate output.

    Returns: {"status": "success" | "failed", "output": <Path>, "error": str | None}.

    The original file is never modified. DLC writes its output next to the
    input; we move the produced file into `output_dir` and never touch the
    source.
    """
    src = Path(input_path)
    out_dir = Path(output_dir)
    cfg = Path(config_path)

    if src.suffix.lower() not in ALLOWED_EXTS:
        raise ValueError(f"unsupported input extension: {src.suffix!r}")
    if not src.is_file():
        raise FileNotFoundError(src)

    out_dir.mkdir(parents=True, exist_ok=False)  # refuse to overwrite

    # DLC's filterpredictions takes a list of "videos" (it derives the prediction
    # path from the video name). To filter an existing prediction file directly
    # we pass it as the "video" — DLC matches by stem.
    save_as_csv = bool(params.pop("save_as_csv", False))

    try:
        dlc.filterpredictions(
            str(cfg),
            [str(src)],
            save_as_csv=save_as_csv,
            **params,
        )
    except Exception as exc:  # noqa: BLE001 - we want the message in the sidecar
        return {"status": "failed", "output": None, "error": f"{type(exc).__name__}: {exc}"}

    # DLC produces <stem>_filtered.h5 next to the source.
    base = src.with_suffix("")
    produced_h5 = base.with_name(base.name + "_filtered.h5")
    relocated_h5 = out_dir / produced_h5.name

    if not produced_h5.exists():
        return {
            "status": "failed",
            "output": None,
            "error": f"DLC did not produce expected output {produced_h5}",
        }
    shutil.move(str(produced_h5), str(relocated_h5))

    if save_as_csv:
        produced_csv = base.with_name(base.name + "_filtered.csv")
        if produced_csv.exists():
            shutil.move(str(produced_csv), str(out_dir / produced_csv.name))

    return {"status": "success", "output": relocated_h5, "error": None}
```

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_dlc.py -v`
Expected: PASS (one test will skip on the host without `tables`; run it in the flask container too).

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess_dlc.py tests/test_postprocess_dlc.py
git commit -m "feat(postprocess): DLC filterpredictions wrapper"
```

---

## Task 10: Output-folder + sidecar helpers

**Files:**
- Modify: `src/dlc/postprocess.py` (add helpers)
- Test: `tests/test_postprocess_routes.py`

- [ ] **Step 1: Write failing tests for the helpers**

Append to `tests/test_postprocess_routes.py`:

```python
import json
from pathlib import Path

from dlc import postprocess as pp


def test_make_run_subfolder_uses_timestamp_and_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_now_stamp", lambda: "20260501-120000")
    result = pp.make_run_subfolder(tmp_path, "filterpredictions")
    assert result.name == "20260501-120000_filterpredictions"
    assert result.parent == tmp_path / "postproc"
    assert result.is_dir()


def test_make_run_subfolder_refuses_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_now_stamp", lambda: "20260501-120000")
    pp.make_run_subfolder(tmp_path, "filterpredictions")
    with pytest.raises(FileExistsError):
        pp.make_run_subfolder(tmp_path, "filterpredictions")


def test_write_sidecar(tmp_path):
    pp.write_sidecar(tmp_path, {
        "run_id": "x",
        "tool": "deeplabcut",
        "action": "filterpredictions",
        "status": "success",
        "params": {},
        "inputs": [],
    })
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["run_id"] == "x"
```

Add `import pytest` at the top of the file if not already there.

- [ ] **Step 2: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_routes.py -k "subfolder or sidecar" -v`
Expected: FAIL.

- [ ] **Step 3: Implement helpers in `src/dlc/postprocess.py`**

Replace the contents of `src/dlc/postprocess.py` with:

```python
"""Post-process predictions blueprint.

Routes are added in subsequent tasks. This module also exposes pure helpers
(make_run_subfolder, write_sidecar, scan_inputs) used by both the routes and
the Celery task.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Iterable

from flask import Blueprint, jsonify

bp = Blueprint("dlc_postprocess", __name__, url_prefix="/dlc/postprocess")

# Recognised analyzed-prediction filename patterns. Lowercase compare on stem.
_ANALYZED_PATTERNS = ("resnet", "mobilenet", "efficientnet", "dlcrnetms5", "hrnet")


def _now_stamp() -> str:
    """UTC timestamp formatted YYYYMMDD-HHMMSS — exposed for monkeypatching."""
    return _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def make_run_subfolder(input_parent: str | Path, tool_tag: str) -> Path:
    """Create <input_parent>/postproc/<timestamp>_<tool_tag>/ and return it.

    Raises FileExistsError if the subfolder already exists (we never overwrite).
    """
    parent = Path(input_parent) / "postproc" / f"{_now_stamp()}_{tool_tag}"
    parent.mkdir(parents=True, exist_ok=False)
    return parent


def write_sidecar(run_dir: str | Path, payload: dict) -> Path:
    """Write run.json into the run subfolder."""
    p = Path(run_dir) / "run.json"
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p


def scan_inputs(path: str | Path, mode: str) -> list[Path]:
    """Find analyzable .h5/.csv files under `path`.

    mode == "file":   path itself must be an analyzable file.
    mode == "folder": recursive search; existing postproc/ trees are skipped.
    """
    p = Path(path)
    if mode == "file":
        if not p.is_file():
            return []
        if not _looks_analyzed(p):
            return []
        return [p]
    if mode == "folder":
        if not p.is_dir():
            return []
        results: list[Path] = []
        for child in p.rglob("*"):
            if not child.is_file():
                continue
            if "postproc" in child.relative_to(p).parts:
                continue
            if _looks_analyzed(child):
                results.append(child)
        return sorted(results)
    raise ValueError(f"unknown mode: {mode!r}")


def _looks_analyzed(path: Path) -> bool:
    suf = path.suffix.lower()
    if suf not in {".h5", ".csv"}:
        return False
    name = path.name.lower()
    if "_filtered" in name:
        return False  # already a derived file
    return any(p in name for p in _ANALYZED_PATTERNS)


@bp.route("/recent", methods=["GET"])
def recent():
    """Return recent post-process runs for the active project (stub for now)."""
    return jsonify({"runs": []})
```

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_routes.py -v`
Expected: PASS for the helper tests; the registration test still passes.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess.py tests/test_postprocess_routes.py
git commit -m "feat(postprocess): output subfolder + sidecar + scan helpers"
```

---

## Task 11: Celery task `dlc_postprocess_run`

**Files:**
- Modify: `src/dlc/tasks.py` (append at end)
- Test: `tests/test_dlc_celery_tasks.py` (append)

- [ ] **Step 1: Read the existing `tasks.py` patterns**

Read `src/dlc/tasks.py` lines 99–160 and 1002–1080 to study how `update_state` is used and how exceptions are surfaced. The new task must use the same shape: `update_state(state="PROGRESS", meta={...})` and `meta` must always be JSON-serializable.

- [ ] **Step 2: Write failing tests for the task**

Append to `tests/test_dlc_celery_tasks.py`:

```python
def test_dlc_postprocess_run_dispatches_to_drivers(tmp_path, monkeypatch):
    """The task must call the right driver for each tool/action and write a sidecar."""
    from dlc import tasks as dlc_tasks
    from dlc import postprocess as pp

    src = tmp_path / "videoDLC_resnet50_shuffle1_50000.h5"
    src.write_bytes(b"")  # contents irrelevant; we mock the driver

    calls = []

    def fake_filter(*, config_path, input_path, output_dir, params):
        calls.append(("filter", Path(input_path).name))
        out_path = Path(output_dir) / (Path(input_path).stem + "_filtered.h5")
        out_path.write_bytes(b"")
        return {"status": "success", "output": out_path, "error": None}

    monkeypatch.setattr("dlc.postprocess_dlc.run_filterpredictions", fake_filter)
    monkeypatch.setattr(pp, "_now_stamp", lambda: "20260501-120000")

    result = dlc_tasks.dlc_postprocess_run.apply(kwargs={
        "config_path": str(tmp_path / "config.yaml"),
        "tool": "deeplabcut",
        "action": "filterpredictions",
        "params": {"filtertype": "median", "windowlength": 5, "save_as_csv": False},
        "inputs": [str(src)],
    }).get()

    assert result["status"] == "success"
    assert calls == [("filter", src.name)]
    sidecar = tmp_path / "postproc" / "20260501-120000_filterpredictions" / "run.json"
    assert sidecar.is_file()


def test_dlc_postprocess_run_partial_on_per_file_failure(tmp_path, monkeypatch):
    from dlc import tasks as dlc_tasks
    from dlc import postprocess as pp

    s1 = tmp_path / "aDLC_resnet50.h5"
    s2 = tmp_path / "bDLC_resnet50.h5"
    s1.write_bytes(b""); s2.write_bytes(b"")

    def driver(*, config_path, input_path, output_dir, params):
        if Path(input_path).name.startswith("a"):
            return {"status": "success",
                    "output": Path(output_dir) / "a_filtered.h5",
                    "error": None}
        return {"status": "failed", "output": None, "error": "boom"}

    monkeypatch.setattr("dlc.postprocess_dlc.run_filterpredictions", driver)
    monkeypatch.setattr(pp, "_now_stamp", lambda: "20260501-120001")

    result = dlc_tasks.dlc_postprocess_run.apply(kwargs={
        "config_path": str(tmp_path / "config.yaml"),
        "tool": "deeplabcut",
        "action": "filterpredictions",
        "params": {},
        "inputs": [str(s1), str(s2)],
    }).get()

    assert result["status"] == "partial"
    assert len(result["inputs"]) == 2
    assert {i["status"] for i in result["inputs"]} == {"success", "failed"}
```

- [ ] **Step 3: Run; confirm failure**

Run: `python -m pytest tests/test_dlc_celery_tasks.py -k postprocess_run -v`
Expected: FAIL.

- [ ] **Step 4: Implement the task**

Append to `src/dlc/tasks.py`:

```python
@celery.task(bind=True, name="tasks.dlc_postprocess_run", acks_late=False)
def dlc_postprocess_run(
    self,
    *,
    config_path: str,
    tool: str,
    action: str,
    params: dict,
    inputs: list[str],
):
    """Run a post-process action on a list of analyzed .h5/.csv files.

    `tool` ∈ {"deeplabcut", "refineDLC"}.
    `action`:
        - tool=deeplabcut: "filterpredictions"
        - tool=refineDLC : "pipeline" | "likelihood_filter" | "outlier_removal"
                           | "interpolation" | "smoothing"
    Each input gets its own per-run subfolder under <input.parent>/postproc/.
    """
    import json
    from pathlib import Path

    from dlc import postprocess as pp
    from dlc import postprocess_dlc as ppd
    from dlc import postprocess_refine as ppr

    tool_tag = {
        ("deeplabcut", "filterpredictions"): "filterpredictions",
        ("refineDLC", "pipeline"):           "refine_pipeline",
        ("refineDLC", "likelihood_filter"):  "refine_lh",
        ("refineDLC", "outlier_removal"):    "refine_outliers",
        ("refineDLC", "interpolation"):      "refine_interp",
        ("refineDLC", "smoothing"):          "refine_smooth",
    }.get((tool, action))
    if tool_tag is None:
        raise ValueError(f"unsupported tool/action: {tool}/{action}")

    started = _utc_now_iso()
    total = len(inputs)
    per_input_results: list[dict] = []
    overall_status = "success"
    run_dirs: set[Path] = set()

    for idx, raw in enumerate(inputs, start=1):
        src = Path(raw)
        self.update_state(state="PROGRESS", meta={
            "current": idx, "total": total, "file": src.name, "step": action,
        })

        try:
            run_dir = pp.make_run_subfolder(src.parent, tool_tag)
            run_dirs.add(run_dir)

            if tool == "deeplabcut":
                step_result = ppd.run_filterpredictions(
                    config_path=config_path,
                    input_path=src,
                    output_dir=run_dir,
                    params=dict(params),
                )
                output = step_result.get("output")
                err = step_result.get("error")
                file_status = step_result["status"]
            else:  # refineDLC
                df = ppr.read_predictions(src)
                if action == "pipeline":
                    out_df = ppr.run_pipeline(df, params)
                else:
                    out_df = ppr.run_single(df, action, params)
                output = run_dir / (src.stem + "_refined" + src.suffix)
                ppr.write_predictions(out_df, output)
                err = None
                file_status = "success"

            per_input_results.append({
                "path": str(src), "output": str(output) if output else None,
                "status": file_status, "error": err,
            })
            if file_status != "success":
                overall_status = "partial"

        except Exception as exc:  # noqa: BLE001
            overall_status = "partial"
            per_input_results.append({
                "path": str(src), "output": None,
                "status": "failed", "error": f"{type(exc).__name__}: {exc}",
            })

    if overall_status == "partial" and not any(
        r["status"] == "success" for r in per_input_results
    ):
        overall_status = "failed"

    finished = _utc_now_iso()
    payload = {
        "run_id": run_dirs and sorted(run_dirs)[0].name or f"{tool_tag}-empty",
        "started_at": started,
        "finished_at": finished,
        "status": overall_status,
        "tool": tool,
        "action": action,
        "params": params,
        "inputs": per_input_results,
    }
    for d in run_dirs:
        pp.write_sidecar(d, payload)

    self.update_state(state="SUCCESS", meta={
        "current": total, "total": total, "stage": "Done", "log": "",
    })
    return payload


def _utc_now_iso():
    import datetime as _dt
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
```

- [ ] **Step 5: Run; confirm pass**

Run: `python -m pytest tests/test_dlc_celery_tasks.py -k postprocess_run -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/dlc/tasks.py tests/test_dlc_celery_tasks.py
git commit -m "feat(postprocess): celery task dispatching DLC and refineDLC drivers"
```

---

## Task 12: Routes — `/scan`

**Files:**
- Modify: `src/dlc/postprocess.py`
- Test: `tests/test_postprocess_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_postprocess_routes.py`:

```python
def test_scan_file_mode_returns_single_path(client, tmp_path, monkeypatch):
    src = tmp_path / "videoDLC_resnet50_shuffle1.h5"
    src.write_bytes(b"")
    monkeypatch.setattr(pp, "_path_is_allowed", lambda p: True)

    resp = client.post("/dlc/postprocess/scan",
                       json={"path": str(src), "mode": "file"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["files"] == [str(src)]


def test_scan_folder_mode_skips_postproc_subtree(client, tmp_path, monkeypatch):
    a = tmp_path / "videoADLC_resnet50.h5"
    b = tmp_path / "postproc" / "20260101-000000_x" / "videoBDLC_resnet50.h5"
    a.write_bytes(b""); b.parent.mkdir(parents=True); b.write_bytes(b"")
    monkeypatch.setattr(pp, "_path_is_allowed", lambda p: True)

    resp = client.post("/dlc/postprocess/scan",
                       json={"path": str(tmp_path), "mode": "folder"})
    assert resp.status_code == 200
    files = resp.get_json()["files"]
    assert str(a) in files
    assert str(b) not in files


def test_scan_rejects_disallowed_path(client, tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_path_is_allowed", lambda p: False)
    resp = client.post("/dlc/postprocess/scan",
                       json={"path": "/etc", "mode": "folder"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_routes.py -k scan -v`
Expected: FAIL (404).

- [ ] **Step 3: Implement the route + path allowlist hook**

Append to `src/dlc/postprocess.py`:

```python
from flask import request


def _path_is_allowed(path: str | Path) -> bool:
    """Hook for the user-data root allowlist.

    Reuses whatever check the rest of the DLC blueprints use. For now it
    delegates to dlc.utils — adjust if the project's allowlist function lives
    elsewhere.
    """
    try:
        from dlc.utils import is_path_allowed  # type: ignore
    except ImportError:
        return True  # tests monkeypatch this; production must wire it up
    return is_path_allowed(Path(path))


@bp.route("/scan", methods=["POST"])
def scan():
    body = request.get_json(silent=True) or {}
    raw = body.get("path")
    mode = body.get("mode")
    if not raw or mode not in {"file", "folder"}:
        return jsonify({"error": "path and mode (file|folder) are required"}), 400
    if not _path_is_allowed(raw):
        return jsonify({"error": "path is not under an allowed root"}), 400

    files = scan_inputs(raw, mode)
    return jsonify({"files": [str(f) for f in files]})
```

Inspect `src/dlc/utils.py` and replace `is_path_allowed` with whatever function name is actually used by the existing routes (search for "allowed" in `src/dlc/`). The intent is: do **not** invent a new allowlist; reuse the one the project already enforces.

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_routes.py -k scan -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess.py tests/test_postprocess_routes.py
git commit -m "feat(postprocess): scan route for file/folder input"
```

---

## Task 13: Routes — `/run`, `/status`, `/cancel`, `/logs`

**Files:**
- Modify: `src/dlc/postprocess.py`
- Test: `tests/test_postprocess_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_postprocess_routes.py`:

```python
def test_run_dispatches_celery_task(client, tmp_path, monkeypatch):
    src = tmp_path / "videoDLC_resnet50_shuffle1.h5"
    src.write_bytes(b"")
    monkeypatch.setattr(pp, "_path_is_allowed", lambda p: True)

    captured = {}
    class FakeAsyncResult:
        id = "fake-task-id"
    def fake_apply_async(kwargs):
        captured["kwargs"] = kwargs
        return FakeAsyncResult()

    monkeypatch.setattr(
        "dlc.tasks.dlc_postprocess_run.apply_async",
        lambda kwargs=None: fake_apply_async(kwargs),
    )

    resp = client.post("/dlc/postprocess/run", json={
        "tool": "deeplabcut",
        "action": "filterpredictions",
        "params": {"filtertype": "median", "windowlength": 5, "save_as_csv": False},
        "inputs": [str(src)],
        "config_path": "/tmp/config.yaml",
    })
    assert resp.status_code == 200
    assert resp.get_json() == {"task_id": "fake-task-id"}
    assert captured["kwargs"]["tool"] == "deeplabcut"


def test_run_validates_tool(client, monkeypatch):
    monkeypatch.setattr(pp, "_path_is_allowed", lambda p: True)
    resp = client.post("/dlc/postprocess/run", json={
        "tool": "nope", "action": "x", "params": {},
        "inputs": ["/tmp/x.h5"], "config_path": "/tmp/c.yaml",
    })
    assert resp.status_code == 400


def test_status_returns_celery_state(client, monkeypatch):
    class FakeAR:
        state = "PROGRESS"
        info = {"current": 1, "total": 3, "file": "a.h5", "step": "filter"}
    monkeypatch.setattr("dlc.postprocess._async_result", lambda tid: FakeAR())
    resp = client.get("/dlc/postprocess/status/abc")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["state"] == "PROGRESS"
    assert data["progress"]["current"] == 1


def test_cancel_revokes(client, monkeypatch):
    revoked = {}
    monkeypatch.setattr(
        "dlc.postprocess._revoke",
        lambda tid: revoked.setdefault("id", tid),
    )
    resp = client.post("/dlc/postprocess/cancel/abc")
    assert resp.status_code == 200
    assert revoked["id"] == "abc"
```

- [ ] **Step 2: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_routes.py -k "run_ or status or cancel" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the routes**

Append to `src/dlc/postprocess.py`:

```python
_VALID_ACTIONS = {
    "deeplabcut": {"filterpredictions"},
    "refineDLC": {"pipeline", "likelihood_filter", "outlier_removal",
                  "interpolation", "smoothing"},
}


@bp.route("/run", methods=["POST"])
def run():
    body = request.get_json(silent=True) or {}
    tool = body.get("tool")
    action = body.get("action")
    params = body.get("params") or {}
    inputs = body.get("inputs") or []
    config_path = body.get("config_path")

    if tool not in _VALID_ACTIONS or action not in _VALID_ACTIONS.get(tool, set()):
        return jsonify({"error": "unsupported tool/action"}), 400
    if not isinstance(inputs, list) or not inputs:
        return jsonify({"error": "inputs must be a non-empty list"}), 400
    for p in inputs:
        if not _path_is_allowed(p):
            return jsonify({"error": f"path not allowed: {p}"}), 400

    from dlc.tasks import dlc_postprocess_run as _task
    async_result = _task.apply_async(kwargs={
        "config_path": config_path, "tool": tool, "action": action,
        "params": params, "inputs": list(inputs),
    })
    return jsonify({"task_id": async_result.id})


def _async_result(task_id: str):
    from celery_app import celery
    return celery.AsyncResult(task_id)


def _revoke(task_id: str) -> None:
    from celery_app import celery
    celery.control.revoke(task_id, terminate=True)


@bp.route("/status/<task_id>", methods=["GET"])
def status(task_id: str):
    ar = _async_result(task_id)
    info = ar.info if isinstance(ar.info, dict) else {}
    return jsonify({"state": ar.state, "progress": info})


@bp.route("/cancel/<task_id>", methods=["POST"])
def cancel(task_id: str):
    _revoke(task_id)
    return jsonify({"task_id": task_id, "cancelled": True})


@bp.route("/logs/<task_id>", methods=["GET"])
def logs(task_id: str):
    """Tail the task's run.log if it exists.

    Logs are written by the Celery task into the per-run subfolder. For now we
    return whatever lives in `meta.log`; full file-tail support can be added
    once the task writes run.log.
    """
    ar = _async_result(task_id)
    info = ar.info if isinstance(ar.info, dict) else {}
    return jsonify({"log": info.get("log", "")})
```

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_routes.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess.py tests/test_postprocess_routes.py
git commit -m "feat(postprocess): run/status/cancel/logs routes"
```

---

## Task 14: Recent runs route reads sidecars

**Files:**
- Modify: `src/dlc/postprocess.py`
- Test: `tests/test_postprocess_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_postprocess_routes.py`:

```python
def test_recent_returns_sidecars_under_project(client, tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_active_project_root", lambda: tmp_path)
    monkeypatch.setattr(pp, "_path_is_allowed", lambda p: True)

    run_dir = tmp_path / "videos" / "postproc" / "20260501-120000_filterpredictions"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "20260501-120000_filterpredictions",
        "tool": "deeplabcut", "action": "filterpredictions",
        "status": "success", "started_at": "2026-05-01T12:00:00Z",
        "finished_at": "2026-05-01T12:00:30Z",
        "params": {}, "inputs": [],
    }))

    resp = client.get("/dlc/postprocess/recent")
    assert resp.status_code == 200
    runs = resp.get_json()["runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == "20260501-120000_filterpredictions"
```

- [ ] **Step 2: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_routes.py -k recent -v`
Expected: FAIL (current stub returns `[]`).

- [ ] **Step 3: Implement the route**

Replace the existing `recent()` in `src/dlc/postprocess.py` with:

```python
def _active_project_root() -> Path | None:
    """Return the active DLC project root, or None.

    Reuses dlc.ctx — adjust if the canonical accessor lives elsewhere.
    """
    try:
        from dlc.ctx import get_active_project_root  # type: ignore
        return get_active_project_root()
    except ImportError:
        return None


@bp.route("/recent", methods=["GET"])
def recent():
    root = _active_project_root()
    if root is None:
        return jsonify({"runs": []})
    runs: list[dict] = []
    for sidecar in Path(root).rglob("postproc/*/run.json"):
        try:
            payload = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        payload["_sidecar"] = str(sidecar)
        runs.append(payload)
    runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return jsonify({"runs": runs[:20]})
```

Inspect `src/dlc/ctx.py` for the actual accessor name (likely `current_project_root` or similar) and substitute. Do **not** invent a new accessor; reuse what other DLC blueprints already use.

- [ ] **Step 4: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_routes.py -k recent -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/postprocess.py tests/test_postprocess_routes.py
git commit -m "feat(postprocess): recent route reads sidecar metadata"
```

---

## Task 15: Card partial template

**Files:**
- Create: `src/templates/partials/card_postprocess.html`
- Test: `tests/test_postprocess_ui_isolation.py`

- [ ] **Step 1: Read the existing card style**

Read `src/templates/partials/card_viewer.html` (the View Analyzed card). Note the structure: outer `<section class="card dlc-theme hidden" id="...">`, header with close button, tabbed content. The new card mirrors this.

- [ ] **Step 2: Write failing static-template assertions**

Create `tests/test_postprocess_ui_isolation.py`:

```python
"""Static template + DOM-shape assertions for the post-process card.

These tests parse the actual template files (no rendering) to verify:
- The new card partial exists and has the expected IDs.
- The trigger button is wedged between the existing two buttons in the right
  order, no other reordering happened.
- The new card partial is included in index.html.
- All IDs introduced by the new card are unique across all partials.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
INDEX = ROOT / "src" / "templates" / "index.html"


def test_card_postprocess_partial_exists():
    p = PARTIALS / "card_postprocess.html"
    assert p.is_file()
    txt = p.read_text()
    assert 'id="postprocess-card"' in txt
    assert 'id="btn-close-postprocess"' in txt
    assert 'id="pp-tool"' in txt


def test_index_includes_postprocess_partial():
    txt = INDEX.read_text()
    assert "partials/card_postprocess.html" in txt


def test_button_sits_between_view_and_annotate():
    p = PARTIALS / "card_dlc_project.html"
    txt = p.read_text()
    i_view = txt.index('id="btn-open-view-analyzed"')
    i_post = txt.index('id="btn-open-postprocess"')
    i_annot = txt.index('id="btn-open-annotate-video"')
    assert i_view < i_post < i_annot


def test_new_ids_are_unique_across_partials():
    new_ids = {
        "postprocess-card", "btn-close-postprocess", "btn-open-postprocess",
        "pp-tool", "pp-input-mode-file", "pp-input-mode-folder",
        "pp-params-deeplabcut", "pp-params-refine",
        "pp-run", "pp-cancel", "pp-status", "pp-log", "pp-recent",
    }
    seen: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    for nid in new_ids:
        # Each new id must appear exactly once across all partials.
        assert seen.get(nid, 0) == 1, f"id {nid!r} appears {seen.get(nid, 0)} times"
```

- [ ] **Step 3: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_ui_isolation.py -v`
Expected: FAIL (files don't exist).

- [ ] **Step 4: Create the card partial**

Create `src/templates/partials/card_postprocess.html`:

```html
<section class="card dlc-theme hidden" id="postprocess-card">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.3rem">
    <h2>Post-Process Predictions</h2>
    <button class="btn-sm" id="btn-close-postprocess" title="Close">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      Close
    </button>
  </div>
  <p class="subtitle">Filter, smooth, and clean DLC predictions. Outputs are written next to the source file in a timestamped <code>postproc/</code> subfolder.</p>

  <!-- Tool dropdown -->
  <div style="display:flex;gap:.5rem;align-items:center;margin-bottom:.6rem">
    <label for="pp-tool" style="font-size:.78rem;color:var(--text-dim)">Tool</label>
    <select id="pp-tool" style="font-size:.78rem;padding:.2rem .45rem">
      <option value="deeplabcut">deeplabcut</option>
      <option value="refineDLC">refineDLC</option>
    </select>
  </div>

  <!-- Input mode toggle -->
  <div style="display:flex;gap:.4rem;margin-bottom:.5rem">
    <button id="pp-input-mode-file" class="btn-sm active" style="padding:.2rem .65rem;font-size:.75rem">Single File</button>
    <button id="pp-input-mode-folder" class="btn-sm" style="padding:.2rem .65rem;font-size:.75rem">Folder</button>
  </div>
  <input id="pp-input-path" type="text" spellcheck="false" autocomplete="off"
         placeholder="/paste/an/analyzed/.h5 or folder path"
         style="width:100%;font-family:var(--mono);font-size:.75rem;padding:.25rem .45rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;margin-bottom:.6rem"/>

  <!-- DeepLabCut params -->
  <div id="pp-params-deeplabcut" style="margin-bottom:.6rem">
    <label style="font-size:.78rem;color:var(--text-dim);display:block;margin-bottom:.25rem">filterpredictions</label>
    <div style="display:grid;grid-template-columns:auto 1fr;gap:.35rem .55rem;font-size:.77rem">
      <label for="pp-dlc-filtertype">Filter type</label>
      <select id="pp-dlc-filtertype">
        <option value="median">median</option>
        <option value="arima">arima</option>
      </select>
      <label for="pp-dlc-windowlength">Window length</label>
      <input id="pp-dlc-windowlength" type="number" min="3" step="2" value="5"/>
      <label for="pp-dlc-pbound">p-bound</label>
      <input id="pp-dlc-pbound" type="number" min="0" max="1" step="0.001" value="0.001"/>
      <label for="pp-dlc-savecsv"><input id="pp-dlc-savecsv" type="checkbox"/> Also save .csv</label><span></span>
    </div>
  </div>

  <!-- refineDLC params -->
  <div id="pp-params-refine" class="hidden" style="margin-bottom:.6rem">
    <div style="display:flex;gap:.4rem;margin-bottom:.4rem">
      <button id="pp-refine-mode-pipeline" class="btn-sm active" style="padding:.2rem .65rem;font-size:.75rem">Pipeline</button>
      <button id="pp-refine-mode-single" class="btn-sm" style="padding:.2rem .65rem;font-size:.75rem">Single action</button>
    </div>
    <div id="pp-refine-pipeline">
      <label style="font-size:.77rem"><input type="checkbox" data-step="likelihood_filter"/> Likelihood filter — threshold <input type="number" data-param="threshold" step="0.01" min="0" max="1" value="0.6" style="width:5rem"/></label><br/>
      <label style="font-size:.77rem"><input type="checkbox" data-step="outlier_removal"/> Outlier removal — z-threshold <input type="number" data-param="z_threshold" step="0.1" min="0.1" value="3.0" style="width:5rem"/></label><br/>
      <label style="font-size:.77rem"><input type="checkbox" data-step="interpolation"/> Interpolation — method
        <select data-param="method"><option>linear</option><option>nearest</option><option>cubic</option></select>
        limit <input type="number" data-param="limit" min="1" value="5" style="width:4rem"/></label><br/>
      <label style="font-size:.77rem"><input type="checkbox" data-step="smoothing"/> Smoothing — window <input type="number" data-param="window" min="3" step="2" value="5" style="width:4rem"/> polyorder <input type="number" data-param="polyorder" min="1" value="2" style="width:4rem"/></label>
    </div>
    <div id="pp-refine-single" class="hidden">
      <label style="font-size:.77rem">Step
        <select id="pp-refine-single-step">
          <option value="likelihood_filter">Likelihood filter</option>
          <option value="outlier_removal">Outlier removal</option>
          <option value="interpolation">Interpolation</option>
          <option value="smoothing">Smoothing</option>
        </select>
      </label>
      <div id="pp-refine-single-params" style="margin-top:.3rem;font-size:.77rem"></div>
    </div>
  </div>

  <!-- Run row -->
  <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem">
    <button id="pp-run" class="btn-sm" style="padding:.3rem .8rem;font-size:.78rem">Run</button>
    <button id="pp-cancel" class="btn-sm hidden" style="padding:.3rem .8rem;font-size:.78rem">Cancel</button>
    <span id="pp-status" style="font-size:.75rem;color:var(--text-dim)">idle</span>
  </div>
  <pre id="pp-log" class="hidden" style="max-height:160px;overflow:auto;font-size:.7rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;padding:.4rem"></pre>

  <!-- Recent runs -->
  <div style="margin-top:.6rem">
    <label style="font-size:.78rem;color:var(--text-dim)">Recent runs</label>
    <div id="pp-recent" style="max-height:140px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;background:var(--surface-2);padding:.4rem .5rem;font-size:.77rem">
      <p class="explorer-empty">No runs yet.</p>
    </div>
  </div>
</section>
```

- [ ] **Step 5: Wedge the open-button into `card_dlc_project.html`**

In `src/templates/partials/card_dlc_project.html`, find the line containing `<span>View Analyzed Videos/Frames</span>` and the line `<span>Annotate Video</span>`. Insert a new button between the closing `</button>` of the View-Analyzed button and the opening `<button id="btn-open-annotate-video"`:

```html
        <button id="btn-open-postprocess" class="inspect-btn" style="width:100%;gap:.55rem">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <line x1="4" y1="6" x2="20" y2="6"/><circle cx="9" cy="6" r="2" fill="var(--surface)"/>
            <line x1="4" y1="12" x2="20" y2="12"/><circle cx="15" cy="12" r="2" fill="var(--surface)"/>
            <line x1="4" y1="18" x2="20" y2="18"/><circle cx="11" cy="18" r="2" fill="var(--surface)"/>
          </svg>
          <span>Post-Process Predictions</span>
        </button>
```

- [ ] **Step 6: Include the partial in `index.html`**

In `src/templates/index.html`, immediately after `{% include "partials/card_viewer.html" %}` (line 15), add:

```html
    {% include "partials/card_postprocess.html" %}
```

- [ ] **Step 7: Run; confirm pass**

Run: `python -m pytest tests/test_postprocess_ui_isolation.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/templates/partials/card_postprocess.html src/templates/partials/card_dlc_project.html src/templates/index.html tests/test_postprocess_ui_isolation.py
git commit -m "feat(postprocess): card partial, trigger button, index include"
```

---

## Task 16: JS controller — open/close + tool/mode swap

**Files:**
- Create: `src/static/js/postprocess.js`
- Modify: `src/templates/base.html` (or wherever `viewer.js` is loaded — match that pattern)
- Test: `tests/test_postprocess_ui_isolation.py` (extend)

- [ ] **Step 1: Find where existing JS controllers are loaded**

Run: `grep -n "viewer.js\|annotator.js" src/templates/base.html src/templates/index.html`. Note the exact `<script>` tag pattern (defer, src prefix, etc.).

- [ ] **Step 2: Write failing test (controller file presence + open/close wiring)**

Append to `tests/test_postprocess_ui_isolation.py`:

```python
def test_postprocess_js_loaded_in_base_or_index():
    candidates = [
        ROOT / "src" / "templates" / "base.html",
        ROOT / "src" / "templates" / "index.html",
    ]
    haystack = "\n".join(p.read_text() for p in candidates if p.is_file())
    assert "postprocess.js" in haystack


def test_postprocess_js_handles_open_and_close():
    js = (ROOT / "src" / "static" / "js" / "postprocess.js").read_text()
    # Reasonable smoke checks — no JS runtime here.
    assert 'btn-open-postprocess' in js
    assert 'btn-close-postprocess' in js
    assert 'postprocess-card' in js
    # Tool swap: dropdown change must hide/show parameter blocks.
    assert "pp-tool" in js
    assert "pp-params-deeplabcut" in js
    assert "pp-params-refine" in js
```

- [ ] **Step 3: Run; confirm failure**

Run: `python -m pytest tests/test_postprocess_ui_isolation.py -k "js_loaded or handles_open" -v`
Expected: FAIL.

- [ ] **Step 4: Implement the JS controller**

Create `src/static/js/postprocess.js`:

```js
/* Post-Process Predictions card controller. */
(function () {
  "use strict";

  const ppCard       = document.getElementById("postprocess-card");
  const ppOpenBtn    = document.getElementById("btn-open-postprocess");
  const ppCloseBtn   = document.getElementById("btn-close-postprocess");
  const ppTool       = document.getElementById("pp-tool");
  const ppDlcBlock   = document.getElementById("pp-params-deeplabcut");
  const ppRefBlock   = document.getElementById("pp-params-refine");
  const ppModeFile   = document.getElementById("pp-input-mode-file");
  const ppModeFolder = document.getElementById("pp-input-mode-folder");
  const ppInputPath  = document.getElementById("pp-input-path");
  const ppRefinePipelineBtn = document.getElementById("pp-refine-mode-pipeline");
  const ppRefineSingleBtn   = document.getElementById("pp-refine-mode-single");
  const ppRefinePipeline    = document.getElementById("pp-refine-pipeline");
  const ppRefineSingle      = document.getElementById("pp-refine-single");
  const ppRunBtn     = document.getElementById("pp-run");
  const ppCancelBtn  = document.getElementById("pp-cancel");
  const ppStatus     = document.getElementById("pp-status");
  const ppLog        = document.getElementById("pp-log");
  const ppRecent     = document.getElementById("pp-recent");

  if (!ppCard || !ppOpenBtn) return;

  function hideAllOtherCards() {
    document.querySelectorAll("section.card").forEach((c) => {
      if (c !== ppCard) c.classList.add("hidden");
    });
  }
  function openCard() {
    hideAllOtherCards();
    ppCard.classList.remove("hidden");
    refreshRecent();
  }
  function closeCard() { ppCard.classList.add("hidden"); }

  ppOpenBtn.addEventListener("click", openCard);
  ppCloseBtn.addEventListener("click", closeCard);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !ppCard.classList.contains("hidden")) closeCard();
  });

  // Tool swap.
  function syncToolBlocks() {
    const isDLC = ppTool.value === "deeplabcut";
    ppDlcBlock.classList.toggle("hidden", !isDLC);
    ppRefBlock.classList.toggle("hidden", isDLC);
  }
  ppTool.addEventListener("change", syncToolBlocks);
  syncToolBlocks();

  // Input mode toggle.
  let inputMode = "file";
  function setInputMode(mode) {
    inputMode = mode;
    ppModeFile.classList.toggle("active", mode === "file");
    ppModeFolder.classList.toggle("active", mode === "folder");
    ppInputPath.placeholder = mode === "file"
      ? "/paste/an/analyzed/.h5" : "/paste/a/folder/of/analyzed/files";
  }
  ppModeFile.addEventListener("click", () => setInputMode("file"));
  ppModeFolder.addEventListener("click", () => setInputMode("folder"));

  // refineDLC mode toggle.
  let refineMode = "pipeline";
  function setRefineMode(mode) {
    refineMode = mode;
    ppRefinePipelineBtn.classList.toggle("active", mode === "pipeline");
    ppRefineSingleBtn.classList.toggle("active", mode === "single");
    ppRefinePipeline.classList.toggle("hidden", mode !== "pipeline");
    ppRefineSingle.classList.toggle("hidden", mode !== "single");
  }
  ppRefinePipelineBtn.addEventListener("click", () => setRefineMode("pipeline"));
  ppRefineSingleBtn.addEventListener("click", () => setRefineMode("single"));

  // Build params payload.
  function collectParams() {
    if (ppTool.value === "deeplabcut") {
      return {
        filtertype: document.getElementById("pp-dlc-filtertype").value,
        windowlength: Number(document.getElementById("pp-dlc-windowlength").value),
        p_bound: Number(document.getElementById("pp-dlc-pbound").value),
        save_as_csv: document.getElementById("pp-dlc-savecsv").checked,
      };
    }
    if (refineMode === "pipeline") {
      const out = {};
      ppRefinePipeline.querySelectorAll("input[type=checkbox][data-step]").forEach((cb) => {
        const step = cb.dataset.step;
        const cfg = { enabled: cb.checked };
        cb.parentElement.querySelectorAll("[data-param]").forEach((el) => {
          cfg[el.dataset.param] = el.type === "number" ? Number(el.value) : el.value;
        });
        out[step] = cfg;
      });
      return out;
    }
    const single = {};
    ppRefineSingle.querySelectorAll("[data-param]").forEach((el) => {
      single[el.dataset.param] = el.type === "number" ? Number(el.value) : el.value;
    });
    return single;
  }

  function actionForRequest() {
    if (ppTool.value === "deeplabcut") return "filterpredictions";
    if (refineMode === "pipeline") return "pipeline";
    return document.getElementById("pp-refine-single-step").value;
  }

  let activeTaskId = null;
  let pollHandle = null;

  async function refreshRecent() {
    try {
      const r = await fetch("/dlc/postprocess/recent");
      const data = await r.json();
      ppRecent.innerHTML = "";
      if (!data.runs || !data.runs.length) {
        ppRecent.innerHTML = '<p class="explorer-empty">No runs yet.</p>';
        return;
      }
      data.runs.forEach((run) => {
        const row = document.createElement("div");
        row.style.display = "flex";
        row.style.justifyContent = "space-between";
        row.style.gap = ".4rem";
        row.style.padding = ".15rem 0";
        row.innerHTML = `<span style="font-family:var(--mono)">${run.run_id}</span><span>${run.tool}/${run.action}</span><span>${run.status}</span>`;
        ppRecent.appendChild(row);
      });
    } catch (e) { /* silent */ }
  }

  async function runPostprocess() {
    const path = ppInputPath.value.trim();
    if (!path) { ppStatus.textContent = "input path is empty"; return; }
    ppStatus.textContent = "scanning…";

    let scanRes;
    try {
      const r = await fetch("/dlc/postprocess/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, mode: inputMode }),
      });
      scanRes = await r.json();
      if (!r.ok) { ppStatus.textContent = "error: " + (scanRes.error || r.status); return; }
    } catch (e) { ppStatus.textContent = "scan failed"; return; }

    if (!scanRes.files || !scanRes.files.length) { ppStatus.textContent = "no analyzable files"; return; }

    ppStatus.textContent = "queued…";
    ppLog.classList.remove("hidden");
    ppLog.textContent = `Found ${scanRes.files.length} file(s).\n`;

    const body = {
      tool: ppTool.value,
      action: actionForRequest(),
      params: collectParams(),
      inputs: scanRes.files,
      config_path: window.__DLC_CONFIG_PATH__ || "",
    };

    try {
      const r = await fetch("/dlc/postprocess/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) { ppStatus.textContent = "error: " + (data.error || r.status); return; }
      activeTaskId = data.task_id;
      ppCancelBtn.classList.remove("hidden");
      pollStatus();
    } catch (e) { ppStatus.textContent = "dispatch failed"; }
  }

  async function pollStatus() {
    if (!activeTaskId) return;
    try {
      const r = await fetch(`/dlc/postprocess/status/${activeTaskId}`);
      const data = await r.json();
      const p = data.progress || {};
      ppStatus.textContent = `${data.state} ${p.current ?? ""}/${p.total ?? ""} ${p.file ?? ""}`;
      if (data.state === "SUCCESS" || data.state === "FAILURE" || data.state === "REVOKED") {
        activeTaskId = null;
        ppCancelBtn.classList.add("hidden");
        refreshRecent();
        return;
      }
    } catch (e) { /* keep polling */ }
    pollHandle = setTimeout(pollStatus, 1500);
  }

  async function cancelRun() {
    if (!activeTaskId) return;
    try { await fetch(`/dlc/postprocess/cancel/${activeTaskId}`, { method: "POST" }); }
    catch (e) { /* ignore */ }
  }

  ppRunBtn.addEventListener("click", runPostprocess);
  ppCancelBtn.addEventListener("click", cancelRun);
})();
```

- [ ] **Step 5: Load the script**

In whichever template loads `viewer.js` (likely `src/templates/base.html` or `index.html`), add a sibling line:

```html
<script src="{{ url_for('static', filename='js/postprocess.js') }}" defer></script>
```

- [ ] **Step 6: Run the UI tests; confirm pass**

Run: `python -m pytest tests/test_postprocess_ui_isolation.py -v`
Expected: PASS.

- [ ] **Step 7: Smoke-check in browser**

Per CLAUDE.md guidance, confirm in the browser that:
1. The button appears in the sidebar between View Analyzed and Annotate Video.
2. Clicking it opens the post-process card and hides the others.
3. Switching the tool dropdown swaps the parameters block.
4. ESC closes the card.

If the dev server is already running (`docker compose up flask`), open `http://localhost:5000/` and click through. If not, start it. **Report explicitly** if a browser smoke test isn't possible in this environment.

- [ ] **Step 8: Commit**

```bash
git add src/static/js/postprocess.js src/templates/base.html src/templates/index.html tests/test_postprocess_ui_isolation.py
git commit -m "feat(postprocess): card controller — open/close, tool swap, run/poll"
```

---

## Task 17: Real-project integration test

**Files:**
- Create: `tests/test_postprocess_real_project.py`

- [ ] **Step 1: Read the real-project conftest pattern**

Read `tests/conftest.py` — note the `ORIGINAL_DLC_PROJECT` fixture (mentioned in CLAUDE.md). The real-project tests should:

- Skip if `ORIGINAL_DLC_PROJECT` is unset / does not resolve to a real path.
- Compute sha256 of an analyzed `.h5` before and after the run, asserting equality.
- Use the existing project's actual `videos/` outputs.

- [ ] **Step 2: Write the test**

Create `tests/test_postprocess_real_project.py`:

```python
"""Real on-disk integration tests for the post-process pipeline.

These tests are skipped when the DREADD project is not available. They are
required to run before declaring this feature complete (per CLAUDE.md).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not Path("/home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07/config.yaml").is_file(),
    reason="DREADD project not on this host",
)

PROJECT = Path("/home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07")
CONFIG = PROJECT / "config.yaml"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _find_one_analyzed_h5() -> Path | None:
    for p in (PROJECT / "videos").rglob("*.h5"):
        if "_filtered" in p.name:
            continue
        if any(tag in p.name for tag in ("resnet", "mobilenet", "efficientnet")):
            return p
    return None


def test_filterpredictions_does_not_modify_source(tmp_path):
    src = _find_one_analyzed_h5()
    if src is None:
        pytest.skip("no analyzed h5 in DREADD project")

    from dlc import postprocess as pp
    from dlc import postprocess_dlc as ppd

    before = _sha256(src)
    out_dir = pp.make_run_subfolder(src.parent, "filterpredictions")
    result = ppd.run_filterpredictions(
        config_path=CONFIG, input_path=src, output_dir=out_dir,
        params={"filtertype": "median", "windowlength": 5, "save_as_csv": False},
    )
    after = _sha256(src)
    assert before == after, "source file was modified"
    if result["status"] == "success":
        assert result["output"].is_file()


def test_refine_pipeline_produces_output(tmp_path):
    src = _find_one_analyzed_h5()
    if src is None:
        pytest.skip("no analyzed h5 in DREADD project")

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
    assert before == after
    assert target.is_file()
```

- [ ] **Step 3: Run inside the flask container (h5 needs `tables`)**

```bash
docker exec $(docker ps --filter "name=flask" -q) \
  python -m pytest /app/tests/test_postprocess_real_project.py -v
```

Expected: PASS or skip (skip is fine on hosts without the project).

- [ ] **Step 4: Commit**

```bash
git add tests/test_postprocess_real_project.py
git commit -m "test(postprocess): real-project integration tests"
```

---

## Task 18: Documentation

**Files:**
- Modify: `src/dlc/README.md`

- [ ] **Step 1: Read the existing README structure**

Skim `src/dlc/README.md` to match section headings/formatting.

- [ ] **Step 2: Add a section for the new module**

Append (or insert in the natural place):

```markdown
## Post-Process Predictions

Module: `src/dlc/postprocess.py` (Flask), `src/dlc/postprocess_dlc.py` (DLC wrapper),
`src/dlc/postprocess_refine.py` (refineDLC drivers), `src/dlc/_refinedlc/` (vendored).

UI: card `postprocess-card` (button between View Analyzed and Annotate Video).

| Route | Method | Purpose |
|---|---|---|
| `/dlc/postprocess/scan` | POST | List analyzable files in a path. Body `{path, mode: "file"|"folder"}`. |
| `/dlc/postprocess/run` | POST | Dispatch a Celery task. Returns `{task_id}`. |
| `/dlc/postprocess/status/<id>` | GET | Celery state + progress meta. |
| `/dlc/postprocess/logs/<id>` | GET | Tail of run log. |
| `/dlc/postprocess/cancel/<id>` | POST | Revoke the running task. |
| `/dlc/postprocess/recent` | GET | List recent runs from sidecar `run.json` files under the active project. |

Outputs land at `<input-parent>/postproc/<YYYYMMDD-HHMMSS>_<tool-tag>/`.
Source files are never modified.
```

- [ ] **Step 3: Commit**

```bash
git add src/dlc/README.md
git commit -m "docs(postprocess): document new module and routes"
```

---

## Task 19: Final verification

- [ ] **Step 1: Run the full test suite (host + container)**

Host:
```bash
python -m pytest tests/test_postprocess_refine.py tests/test_postprocess_routes.py tests/test_postprocess_ui_isolation.py tests/test_postprocess_vendored_imports.py tests/test_dlc_celery_tasks.py -v
```

Flask container (covers `pandas.read_hdf` paths and DLC):
```bash
docker exec $(docker ps --filter "name=flask" -q) \
  python -m pytest /app/tests/test_postprocess_dlc.py /app/tests/test_postprocess_real_project.py /app/tests/test_postprocess_refine.py -v
```

Worker container (covers vendored imports under DLC's deps):
```bash
docker exec $(docker ps --filter "name=worker" --filter "name=^/.*worker$" -q | head -1) \
  python -m pytest /app/tests/test_postprocess_vendored_imports.py -v
```

All must pass (skips OK).

- [ ] **Step 2: Manual browser smoke**

Per CLAUDE.md, manually exercise the card in the browser: open, switch tools, switch input modes, run a small DLC `filterpredictions` against one real `.h5`, verify the output appears in `postproc/<timestamp>_filterpredictions/` and the original file's size + mtime are unchanged.

If the manual step is not possible in this environment, **report that explicitly** rather than claiming success.

- [ ] **Step 3: Final commit (only if anything changed)**

```bash
git status
# If clean, nothing to commit. Otherwise:
git add -A
git commit -m "chore(postprocess): final cleanups"
```

---

## Self-Review (already performed)

- **Spec coverage:** All design sections (UI, backend, output layout, sidecar, error handling, testing, vendoring) are implemented across Tasks 1–18.
- **Placeholder scan:** Two intentional spots ask the engineer to inspect existing code (`dlc.utils.is_path_allowed` name in Task 12, `dlc.ctx` accessor in Task 14). These are not placeholders for *new* logic — they are explicit instructions to reuse an existing API and to substitute the correct identifier. Marked as such inline.
- **Type consistency:** Step function names (`step_likelihood_filter`, `step_outlier_removal`, `step_interpolation`, `step_smoothing`) are stable from Task 4 onward; pipeline config keys (`likelihood_filter`, `outlier_removal`, `interpolation`, `smoothing`) match between driver, JS controller, and Celery task; tool tags match across `dlc_postprocess_run`, `make_run_subfolder`, and the test fixtures.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-01-postprocess-card.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
