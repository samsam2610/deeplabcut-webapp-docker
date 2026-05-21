# Canonical Analysis File — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give each video one canonical `<videostem>_analyzed.h5` + `.csv` that all inline analysis writes into, with a manual "Initialize analysis file" button (locked once the file exists) in the Analyze, Inline-2D, and Inline-3D cards.

**Architecture:** A standalone `src/dlc/canonical.py` module holds the pure file mechanics (path, fixed scorer, dense-empty builder, scorer re-label, merge+dense write). The inline worker (`tasks.py::_run_range`) writes through it instead of the scorer-named path. New synchronous Flask routes `/dlc/project/analysis-file/{initialize,status}` create the dense-empty file (409 if it already exists). UI buttons in three cards call those routes.

**Tech Stack:** Python (pandas, numpy, OpenCV for frame-count), Flask blueprints, vanilla ES-module JS, pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-21-canonical-analysis-file-design.md`

**Repo / branch:** main webapp `deeplabcut-webapp-docker` on `feat/3d-inline-analysis`; dlc-3D module `deeplabcut-webapp-docker-supports/dlc-3D` on `feat/3d-inline-analysis`. Stay on these branches; do NOT push.

**Scope:** Phase 1 only (inline 2D/3D redirect + init/status routes + UI in all three cards). Phase 2 (full Analyze Video pipeline redirect) is a separate later plan.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/dlc/canonical.py` | **new** — pure canonical-file mechanics (no Celery/Flask deps) |
| `src/dlc/tasks.py` | modify `_run_range` to write via `canonical.write_to_canonical` |
| `src/dlc/inline_analysis.py` | add `/dlc/project/analysis-file/initialize` + `/status` routes |
| `src/dlc/viewer.py` | verify discovery glob matches `_analyzed` (adjust only if needed) |
| `src/templates/partials/card_analyze.html` + `src/static/js/analyze.js` | per-queued-video init button + status |
| `src/templates/partials/card_inline_analysis.html` + `src/static/js/inline_analysis_player.js` | init button in params |
| `dlc-3D/src/templates/partials/card_inline_analysis_3d.html` + `dlc-3D/src/static/inline_analysis_3d.js` | "Initialize analysis files (both cameras)" button |
| `tests/test_canonical_analysis_file.py` | **new** — unit tests for canonical.py + routes |

---

## Task 1: `canonical.py` module + unit tests

**Files:**
- Create: `src/dlc/canonical.py`
- Test: `tests/test_canonical_analysis_file.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_canonical_analysis_file.py`:

```python
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from src.dlc import canonical


def test_canonical_h5_path():
    p = canonical.canonical_h5_path("/data/vids/clipA.avi")
    assert str(p) == "/data/vids/clipA_analyzed.h5"
    assert str(canonical.canonical_csv_path("/data/vids/clipA.avi")) == "/data/vids/clipA_analyzed.csv"


def test_canonical_scorer_from_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: AliLab\nbodyparts:\n- nose\n- tail\n")
    assert canonical.canonical_scorer(str(cfg)) == "AliLab"


def test_canonical_scorer_fallback(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("bodyparts:\n- nose\n")
    assert canonical.canonical_scorer(str(cfg)) == "DLC_analyzed"


def test_read_bodyparts(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nbodyparts:\n- nose\n- tailbase\n")
    assert canonical.read_bodyparts(str(cfg)) == ["nose", "tailbase"]


def test_build_empty_dense_df_shape():
    df = canonical.build_empty_dense_df("S", ["nose", "tail"], 5)
    assert df.shape == (5, 6)                      # 2 bodyparts * 3 coords
    assert list(df.columns.names) == ["scorer", "bodyparts", "coords"]
    assert df.columns.get_level_values("scorer").unique().tolist() == ["S"]
    assert df.isna().all().all()                   # all NaN
    assert list(df.index) == [0, 1, 2, 3, 4]       # dense rows


def test_relabel_scorer():
    cols = pd.MultiIndex.from_product([["OLD"], ["nose"], ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    df = pd.DataFrame(np.ones((2, 3)), columns=cols)
    out = canonical.relabel_scorer(df, "OLD", "NEW")
    assert out.columns.get_level_values("scorer").unique().tolist() == ["NEW"]


def test_write_to_canonical_creates_then_merges(tmp_path):
    vid = tmp_path / "clip.avi"; vid.write_bytes(b"x")
    cols = pd.MultiIndex.from_product([["SNAP"], ["nose"], ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    # first write: frame 2 only
    df1 = pd.DataFrame([[1.0, 2.0, 0.9]], index=pd.Index([2]), columns=cols)
    h5, csv = canonical.write_to_canonical(str(vid), df1, source_scorer="SNAP",
                                           canonical_scorer="CANON", save_as_csv=True)
    assert h5.exists() and csv.exists()
    got = pd.read_hdf(str(h5))
    assert got.columns.get_level_values("scorer").unique().tolist() == ["CANON"]
    assert len(got) == 3                                  # dense 0..2
    assert got.iloc[0].isna().all()                       # frame 0 NaN
    assert got.iloc[2, 0] == 1.0                          # frame 2 filled
    # second write: frame 0 — merges, keeps frame 2
    df2 = pd.DataFrame([[5.0, 6.0, 0.8]], index=pd.Index([0]), columns=cols)
    canonical.write_to_canonical(str(vid), df2, source_scorer="SNAP",
                                 canonical_scorer="CANON", save_as_csv=False)
    got2 = pd.read_hdf(str(h5))
    assert got2.iloc[0, 0] == 5.0 and got2.iloc[2, 0] == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_canonical_analysis_file.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dlc.canonical'` (or import error).

- [ ] **Step 3: Implement `src/dlc/canonical.py`**

```python
"""Canonical per-video analysis file mechanics.

One analysis file per video: <videostem>_analyzed.h5 + .csv, DeepLabCut
format. All analysis funnels through write_to_canonical(), which re-labels
the column scorer level to a fixed project scorer and merges into the
dense canonical h5. Standalone (pandas/numpy/yaml only) so both the Celery
worker (tasks.py) and the Flask routes (inline_analysis.py) can import it
without pulling DLC/Celery deps.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

CANONICAL_SCORER_FALLBACK = "DLC_analyzed"
_COORDS = ["x", "y", "likelihood"]


def canonical_h5_path(video_path) -> Path:
    p = Path(video_path)
    return p.with_name(p.stem + "_analyzed.h5")


def canonical_csv_path(video_path) -> Path:
    return canonical_h5_path(video_path).with_suffix(".csv")


def _read_config(config_path) -> dict:
    import yaml
    return yaml.safe_load(Path(config_path).read_text()) or {}


def canonical_scorer(config_path) -> str:
    try:
        cfg = _read_config(config_path)
    except Exception:
        return CANONICAL_SCORER_FALLBACK
    s = cfg.get("scorer")
    return str(s) if s else CANONICAL_SCORER_FALLBACK


def read_bodyparts(config_path) -> list[str]:
    cfg = _read_config(config_path)
    bps = cfg.get("bodyparts") or []
    return [str(b) for b in bps]


def build_empty_dense_df(scorer: str, bodyparts: list[str], nframes: int) -> pd.DataFrame:
    cols = pd.MultiIndex.from_product(
        [[scorer], list(bodyparts), _COORDS],
        names=["scorer", "bodyparts", "coords"],
    )
    data = np.full((max(nframes, 0), len(bodyparts) * 3), np.nan, dtype=float)
    return pd.DataFrame(data, index=pd.RangeIndex(max(nframes, 0)), columns=cols)


def relabel_scorer(df: pd.DataFrame, old_scorer: str, new_scorer: str) -> pd.DataFrame:
    if old_scorer == new_scorer:
        return df
    return df.rename(columns={old_scorer: new_scorer}, level=0)


def _atomic_write_h5(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_hdf(str(tmp), key="df_with_missing", mode="w", format="table")
    os.replace(str(tmp), str(path))


def _atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(str(tmp))
    os.replace(str(tmp), str(path))


def write_empty(video_path, *, scorer: str, bodyparts: list[str], nframes: int,
                save_as_csv: bool = True):
    """Create the dense all-NaN canonical file. Returns (h5_path, csv_path)."""
    h5 = canonical_h5_path(video_path)
    df = build_empty_dense_df(scorer, bodyparts, nframes)
    _atomic_write_h5(h5, df)
    csv = canonical_csv_path(video_path)
    if save_as_csv:
        _atomic_write_csv(csv, df)
    return h5, csv


def write_to_canonical(video_path, df: pd.DataFrame, *, source_scorer: str,
                       canonical_scorer: str, save_as_csv: bool = True):
    """Re-label scorer → canonical, merge into the dense canonical h5, write.

    Returns (h5_path, csv_path).
    """
    h5 = canonical_h5_path(video_path)
    df = relabel_scorer(df, source_scorer, canonical_scorer)
    existing = pd.read_hdf(str(h5)) if h5.exists() else None
    merged = df if existing is None else df.combine_first(existing)
    if len(merged):
        max_idx = int(merged.index.max())
        merged = merged.reindex(pd.RangeIndex(start=0, stop=max_idx + 1,
                                              name=merged.index.name))
    _atomic_write_h5(h5, merged)
    csv = canonical_csv_path(video_path)
    if save_as_csv:
        _atomic_write_csv(csv, merged)
    return h5, csv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_canonical_analysis_file.py -q`
Expected: 7 passed. (pandas HDF needs PyTables — it's already a project dep.)

- [ ] **Step 5: Commit**

```bash
git add src/dlc/canonical.py tests/test_canonical_analysis_file.py
git commit -m "feat(dlc): canonical analysis-file module (<video>_analyzed.h5/csv)"
```

---

## Task 2: Route inline `_run_range` through the canonical file

**Files:**
- Modify: `src/dlc/tasks.py` (`_run_range` + its call site in `_dlc_inline_session_inner`)
- Test: `tests/test_canonical_analysis_file.py` (add one)

- [ ] **Step 1: Add failing test for the redirect contract**

Append to `tests/test_canonical_analysis_file.py`:

```python
def test_run_range_writes_canonical_not_scorer_named(monkeypatch, tmp_path):
    """_run_range must write <stem>_analyzed.h5 (canonical), not <stem><scorer>.h5."""
    from src.dlc import tasks
    vid = tmp_path / "clipB.avi"; vid.write_bytes(b"x")
    cols = pd.MultiIndex.from_product([["SNAPSCORER"], ["nose"], ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    fake_df = pd.DataFrame([[1.0, 2.0, 0.9]], index=pd.Index([0]), columns=cols)
    # Patch DLC primitives _run_range resolves from globals
    monkeypatch.setitem(tasks.__dict__, "_RangeVideoIterator", lambda *a, **k: object())
    monkeypatch.setitem(tasks.__dict__, "video_inference", lambda *a, **k: object())
    monkeypatch.setitem(tasks.__dict__, "_dlc_create_df_from_prediction",
                        lambda **k: fake_df.copy())
    req = {"video_path": str(vid), "start_frame": 0, "n_frames": 1,
           "save_as_csv": True, "snapshot_path": "/snap", "req_id": "r1"}
    n_an, n_sk = tasks._run_range(runner=object(), scorer="SNAPSCORER", model_cfg={},
                                  multi_animal=False, canonical_scorer="CANON", req=req)
    assert n_an == 1
    assert (tmp_path / "clipB_analyzed.h5").exists()
    # no scorer-named file
    assert not (tmp_path / "clipBSNAPSCORER.h5").exists()
    got = pd.read_hdf(str(tmp_path / "clipB_analyzed.h5"))
    assert got.columns.get_level_values("scorer").unique().tolist() == ["CANON"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_canonical_analysis_file.py::test_run_range_writes_canonical_not_scorer_named -q`
Expected: FAIL — `_run_range()` has no `canonical_scorer` kwarg (TypeError).

- [ ] **Step 3: Modify `_run_range` in `src/dlc/tasks.py`**

Add `from . import canonical as _canonical` near the top imports of tasks.py (with the other module imports).

Change the signature and the write paths. Replace the whole `_run_range` function body's h5 handling. Specifically:

Change the signature line:
```python
def _run_range(runner, *, scorer, model_cfg, multi_animal, req):
```
to:
```python
def _run_range(runner, *, scorer, model_cfg, multi_animal, canonical_scorer, req):
```

Change the h5-path line:
```python
    h5_path  = _resolve_h5_path(req["video_path"], scorer)
```
to:
```python
    h5_path  = _canonical.canonical_h5_path(req["video_path"])
```

Replace the final write block (the part after `df_range.index = ...`) — i.e. the `df_merge = ... ; reindex ; _atomic_write_h5 ; _atomic_write_csv ; meta` block — with a single canonical write:
```python
    df_range.index = _ia_pd.Index(to_analyze, name=df_range.index.name)
    _canonical.write_to_canonical(
        req["video_path"], df_range,
        source_scorer=scorer, canonical_scorer=canonical_scorer,
        save_as_csv=bool(req.get("save_as_csv")),
    )
    meta_path = _resolve_meta_path(h5_path)
    _update_meta_pickle(meta_path, df_range, snapshot=req["snapshot_path"])
    return len(to_analyze), n_skipped
```

Also handle the all-skipped dense-heal branch: it currently dense-ifies `existing` and writes via `_atomic_write_h5(h5_path, dense)`. Since `h5_path` is now the canonical path, that branch already self-heals the canonical file — leave its `_atomic_write_h5(h5_path, dense)` / `_atomic_write_csv(h5_path.with_suffix(".csv"), dense)` as-is (they operate on the canonical path now).

- [ ] **Step 4: Update the call site in `_dlc_inline_session_inner`**

Before the BLPOP loop (where `scorer` is already known), compute the canonical scorer once:
```python
    canonical_scorer = _canonical.canonical_scorer(config_path)
```
(Place it near where `scorer` is derived, before the `while` loop.)

Change the call:
```python
            n_analyzed, n_skipped = _run_range(
                runner, scorer=scorer, model_cfg=model_cfg,
                multi_animal=multi_animal, req=req,
            )
```
to:
```python
            n_analyzed, n_skipped = _run_range(
                runner, scorer=scorer, model_cfg=model_cfg,
                multi_animal=multi_animal, canonical_scorer=canonical_scorer, req=req,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_canonical_analysis_file.py -q`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/dlc/tasks.py tests/test_canonical_analysis_file.py
git commit -m "feat(dlc): inline _run_range writes the canonical analysis file"
```

---

## Task 3: Initialize + status routes

**Files:**
- Modify: `src/dlc/inline_analysis.py`
- Test: `tests/test_canonical_analysis_file.py` (add route tests if a Flask app fixture is available; otherwise a logic-level test)

- [ ] **Step 1: Add the routes to `src/dlc/inline_analysis.py`**

Add near the other routes (reuse the existing `_active_project`, `_security_check`, `_ctx`). Add `from . import canonical as _canonical` to the imports.

```python
@bp.route("/dlc/project/analysis-file/status", methods=["GET"])
def analysis_file_status():
    raw = (request.args.get("video_path") or "").strip()
    if not raw:
        return jsonify({"error": "video_path required"}), 400
    ok, _ = _security_check(raw)
    if not ok:
        return jsonify({"error": "path not allowed"}), 403
    h5 = _canonical.canonical_h5_path(raw)
    csv = _canonical.canonical_csv_path(raw)
    return jsonify({
        "initialized": h5.exists(),
        "h5_path": str(h5),
        "csv_path": str(csv),
    })


@bp.route("/dlc/project/analysis-file/initialize", methods=["POST"])
def analysis_file_initialize():
    body = request.get_json(silent=True) or {}
    raw = (body.get("video_path") or "").strip()
    if not raw:
        return jsonify({"error": "video_path required"}), 400
    ok, _ = _security_check(raw)
    if not ok:
        return jsonify({"error": "path not allowed"}), 403

    h5 = _canonical.canonical_h5_path(raw)
    if h5.exists():
        return jsonify({"error": "already initialized", "h5_path": str(h5)}), 409

    project = _active_project()
    if not project or not project.get("config_path"):
        return jsonify({"error": "no active project"}), 400
    config_path = project["config_path"]
    bodyparts = _canonical.read_bodyparts(config_path)
    if not bodyparts:
        return jsonify({"error": "project has no bodyparts"}), 422
    scorer = _canonical.canonical_scorer(config_path)

    import cv2
    cap = cv2.VideoCapture(raw)
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if nframes <= 0:
        return jsonify({"error": "could not read video frame count"}), 422

    h5_path, csv_path = _canonical.write_empty(
        raw, scorer=scorer, bodyparts=bodyparts, nframes=nframes, save_as_csv=True)
    return jsonify({
        "h5_path": str(h5_path), "csv_path": str(csv_path), "nframes": nframes,
    }), 201
```

Note: `_security_check` returns `(allowed, resolved_path_or_msg)` per the helper at the top of inline_analysis.py — confirm its return shape and adapt the unpacking if it differs (it wraps `_dlc_project_security_check`).

- [ ] **Step 2: Verify the blueprint registers (import smoke)**

Run: `python -c "from src.dlc import inline_analysis; print([r for r in dir(inline_analysis) if 'analysis_file' in r])"`
Expected: prints `['analysis_file_initialize', 'analysis_file_status']`.

- [ ] **Step 3: Commit**

```bash
git add src/dlc/inline_analysis.py
git commit -m "feat(dlc): /dlc/project/analysis-file initialize + status routes (locked)"
```

---

## Task 4: Verify viewer discovery finds `_analyzed.h5`

**Files:**
- Read/verify: `src/dlc/viewer.py` (`_h5_variants_for_video`)

- [ ] **Step 1: Confirm the glob matches**

Run:
```bash
python -c "from pathlib import Path; import fnmatch; print(fnmatch.fnmatch('clip_analyzed.h5','clip*.h5'))"
```
Expected: `True` (so `parent.glob(f'{stem}*.h5')` matches `<stem>_analyzed.h5`).

- [ ] **Step 2: Confirm `_analyzed` is not excluded**

Read `_h5_variants_for_video` in `src/dlc/viewer.py`. It excludes names containing `_filtered` or `_refined`. Confirm `_analyzed` is NOT in the exclusion set. If discovery additionally derives a `type`/label per variant, ensure `_analyzed` falls through to the default `"raw"` type (no special handling needed). If `_analyzed` IS accidentally excluded, remove it from the exclusion; otherwise no code change.

- [ ] **Step 3: Commit (only if a change was needed)**

```bash
git add src/dlc/viewer.py
git commit -m "fix(dlc): viewer discovery includes <video>_analyzed.h5" || echo "no change needed"
```

---

## Task 5: Analyze card — per-video "Initialize analysis file" button

**Files:**
- Modify: `src/templates/partials/card_analyze.html`, `src/static/js/analyze.js`

- [ ] **Step 1: Render an init control per queued row.**

In `analyze.js`, find `_avRenderBatchList()` (renders `#av-batch-list` rows from `_avBatchList`). For each row, in addition to the existing remove (✕) button, add a small button `<button class="btn-sm av-init-file" data-path="<abs>">○ Init analysis file</button>` and a status span. After rendering, for each row call the status endpoint and set the button state:

```javascript
// after building each row element `rowEl` for path `p`:
const initBtn = document.createElement("button");
initBtn.className = "btn-sm av-init-file";
initBtn.dataset.path = p;
initBtn.style.cssText = "padding:.15rem .45rem;font-size:.72rem;margin-left:.4rem";
initBtn.textContent = "○ Init analysis file";
rowEl.appendChild(initBtn);
fetch(`/dlc/project/analysis-file/status?video_path=${encodeURIComponent(p)}`)
  .then(r => r.json()).then(d => {
    if (d.initialized) { initBtn.textContent = "✓ Analysis file ready"; initBtn.disabled = true; }
  }).catch(() => {});
initBtn.addEventListener("click", async () => {
  initBtn.disabled = true; initBtn.textContent = "…";
  const r = await fetch("/dlc/project/analysis-file/initialize", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_path: p }),
  });
  if (r.ok || r.status === 409) {
    initBtn.textContent = "✓ Analysis file ready";
  } else {
    const e = await r.json().catch(() => ({}));
    initBtn.textContent = `⚠ ${e.error || r.status}`; initBtn.disabled = false;
  }
});
```

- [ ] **Step 2: Verify in the browser (Playwright).** With a project active, queue a video in the Analyze card, confirm the row shows "○ Init analysis file", click it → flips to "✓ Analysis file ready" and the file exists on disk. (Use a real video under `/user-data/...`.)

- [ ] **Step 3: Commit**

```bash
git add src/static/js/analyze.js src/templates/partials/card_analyze.html
git commit -m "feat(static): Init analysis file button per queued video in Analyze card"
```

---

## Task 6: Inline 2D — "Initialize analysis file" button

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html`, `src/static/js/inline_analysis_player.js`

- [ ] **Step 1: Add a button to the params block.** In `card_inline_analysis.html`, inside the Analysis Parameters block (after the `ia-last-run-status` div), add:
```html
        <button id="ia-init-analysis-file" class="btn-sm" style="width:100%;margin-top:.3rem">○ Initialize analysis file</button>
```

- [ ] **Step 2: Wire it in `inline_analysis_player.js`** (in the analysis-dispatch IIFE, alongside the other element lookups). It acts on the currently selected video (`_iaCurrentVideoPath || _iaBrowseVideoPath`). Add:
```javascript
      const iaInitFileBtn = document.getElementById("ia-init-analysis-file");
      async function _iaRefreshInitFileBtn() {
        if (!iaInitFileBtn) return;
        const v = _iaCurrentVideoPath || _iaBrowseVideoPath;
        if (!v) { iaInitFileBtn.disabled = true; iaInitFileBtn.textContent = "○ Initialize analysis file"; return; }
        try {
          const d = await (await fetch(`/dlc/project/analysis-file/status?video_path=${encodeURIComponent(v)}`)).json();
          iaInitFileBtn.disabled = !!d.initialized;
          iaInitFileBtn.textContent = d.initialized ? "✓ Analysis file ready" : "○ Initialize analysis file";
        } catch (e) {}
      }
      iaInitFileBtn?.addEventListener("click", async () => {
        const v = _iaCurrentVideoPath || _iaBrowseVideoPath;
        if (!v) return;
        iaInitFileBtn.disabled = true; iaInitFileBtn.textContent = "…";
        const r = await fetch("/dlc/project/analysis-file/initialize", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video_path: v }),
        });
        if (r.ok || r.status === 409) { iaInitFileBtn.textContent = "✓ Analysis file ready"; }
        else { const e = await r.json().catch(() => ({})); iaInitFileBtn.textContent = `⚠ ${e.error || r.status}`; iaInitFileBtn.disabled = false; }
      });
      // refresh when a video is opened: hook the existing frame-counter observer
      if (iaFrameCounter) new MutationObserver(_iaRefreshInitFileBtn)
        .observe(iaFrameCounter, { childList: true, characterData: true, subtree: true });
```

- [ ] **Step 3: Verify (Playwright):** open inline 2D, pick a video → button enabled "○ Initialize analysis file"; click → "✓ Analysis file ready", file on disk; reopen same video → button already shows ✓ disabled.

- [ ] **Step 4: Commit**

```bash
git add src/static/js/inline_analysis_player.js src/templates/partials/card_inline_analysis.html
git commit -m "feat(static): Initialize analysis file button in Inline Analysis (2D)"
```

---

## Task 7: Inline 3D — "Initialize analysis files (both cameras)" button

**Files:**
- Modify (supports repo): `dlc-3D/src/templates/partials/card_inline_analysis_3d.html`, `dlc-3D/src/static/inline_analysis_3d.js`

- [ ] **Step 1: Add the button to the params block.** In `card_inline_analysis_3d.html`, after `ia3d-last-run-status`, add:
```html
        <button id="ia3d-init-analysis-file" class="btn-sm" disabled style="width:100%;margin-top:.3rem">○ Initialize analysis files (both cameras)</button>
        <div id="ia3d-init-file-status" class="subtitle" style="margin-top:.2rem"></div>
```

- [ ] **Step 2: Wire it in `inline_analysis_3d.js`** (in the dispatch IIFE; it already resolves `_cam0Path()` + `_siblingPath`). Add:
```javascript
      const initFileBtn = document.getElementById("ia3d-init-analysis-file");
      const initFileStatus = document.getElementById("ia3d-init-file-status");
      async function _initStatus(v) {
        try { return (await (await fetch(`/dlc/project/analysis-file/status?video_path=${encodeURIComponent(v)}`)).json()).initialized; }
        catch (e) { return false; }
      }
      async function _refreshInitFileBtn() {
        if (!initFileBtn) return;
        const cam0 = _cam0Path();
        if (!cam0) { initFileBtn.disabled = true; return; }
        initFileBtn.disabled = false;
        const a = await _initStatus(cam0);
        const b = _siblingPath ? await _initStatus(_siblingPath) : true;
        if (a && b) { initFileBtn.textContent = "✓ Analysis files ready"; initFileBtn.disabled = true; }
        else { initFileBtn.textContent = "○ Initialize analysis files (both cameras)"; }
      }
      async function _initOne(v) {
        const r = await fetch("/dlc/project/analysis-file/initialize", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video_path: v }),
        });
        return r.ok || r.status === 409;   // 409 = already initialized = fine
      }
      initFileBtn?.addEventListener("click", async () => {
        const cam0 = _cam0Path(); if (!cam0) return;
        initFileBtn.disabled = true; initFileBtn.textContent = "…";
        const okCam0 = await _initOne(cam0);
        const okCam1 = _siblingPath ? await _initOne(_siblingPath) : true;
        initFileStatus.textContent = `cam0 ${okCam0 ? "✓" : "⚠"}` + (_siblingPath ? ` · cam1 ${okCam1 ? "✓" : "⚠"}` : "");
        await _refreshInitFileBtn();
      });
      // re-evaluate when the sibling resolves (reuse the frame-counter observer hook)
      if (iaFrameCounter) new MutationObserver(_refreshInitFileBtn)
        .observe(iaFrameCounter, { childList: true, characterData: true, subtree: true });
```

- [ ] **Step 3: `node --input-type=module --check < dlc-3D/src/static/inline_analysis_3d.js`** → PARSE OK.

- [ ] **Step 4: Recreate the dlc-3d container** (template change): `cd ../deeplabcut-webapp-docker && docker compose restart dlc-3d`; wait for HTTP 200.

- [ ] **Step 5: Verify (Playwright):** open inline 3D, pick a cam0 video with a sibling → "Initialize analysis files (both cameras)" enabled; click → creates `<cam0>_analyzed.h5` and `<cam1>_analyzed.h5`; button flips to ✓.

- [ ] **Step 6: Commit (supports repo)**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker-supports
git add dlc-3D/src/static/inline_analysis_3d.js dlc-3D/src/templates/partials/card_inline_analysis_3d.html
git commit -m "feat(dlc-3d): Initialize analysis files (both cameras) button"
```

---

## Task 8: End-to-end live smoke + static guards

**Files:**
- Modify: `tests/test_canonical_analysis_file.py` (static guards)

- [ ] **Step 1: Add static guards** asserting each card wires the init button + endpoints:
```python
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[1]

def test_init_button_wired_in_all_cards():
    analyze_js = (_ROOT / "src/static/js/analyze.js").read_text()
    assert "/dlc/project/analysis-file/initialize" in analyze_js
    ia2d = (_ROOT / "src/static/js/inline_analysis_player.js").read_text()
    assert "ia-init-analysis-file" in ia2d and "/dlc/project/analysis-file/initialize" in ia2d
    ia2d_html = (_ROOT / "src/templates/partials/card_inline_analysis.html").read_text()
    assert 'id="ia-init-analysis-file"' in ia2d_html
```
(The 3D card lives in the other repo; its guard goes in `dlc-3D/tests/test_inline_analysis_3d_ui_isolation.py` asserting `ia3d-init-analysis-file` + the initialize endpoint.)

- [ ] **Step 2: Run the unit + guard suite**

Run: `python -m pytest tests/test_canonical_analysis_file.py -q`
Expected: all pass.

- [ ] **Step 3: Live consolidation smoke (Playwright, inline 2D).** With the project active: open inline 2D, pick the user's `050726/khoai-lang-1_cam0_*.avi`, click Initialize → `<stem>_analyzed.h5` appears; scrub to ~frame 24015, run a 10-frame analysis; confirm markers paint AND the only new h5 is `<stem>_analyzed.h5` (no scorer-named file created by the inline run). Capture: `ls` the video dir before/after for `*_analyzed.h5` vs `*DLC_*.h5`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_canonical_analysis_file.py
git commit -m "test(dlc): static guards for canonical analysis-file UI wiring"
```

---

## Self-review (plan author)

- **Spec coverage:** §1 canonical mechanics → Task 1; §2 routes+lock → Task 3; §3 inline redirect → Task 2 (Phase 2 full-analyze is explicitly out of this plan); §4 UI three cards → Tasks 5/6/7; §5 discovery → Task 4; §9 testing → Tasks 1/2/3/8.
- **Placeholder scan:** the only non-literal is "confirm `_security_check` return shape" in Task 3 (a real verification step against existing code, not a code placeholder) — the engineer adapts the 2-line unpack to the actual helper.
- **Name consistency:** `canonical_h5_path`/`canonical_csv_path`/`canonical_scorer`/`read_bodyparts`/`build_empty_dense_df`/`relabel_scorer`/`write_empty`/`write_to_canonical` used identically across Tasks 1-3; `_run_range(..., canonical_scorer=...)` matches between Task 2's signature change and call site; endpoints `/dlc/project/analysis-file/{initialize,status}` consistent across Tasks 3/5/6/7/8.
