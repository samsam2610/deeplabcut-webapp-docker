# Finalize Analysis (2D inline) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Finalize analysis" minicard to the 2D inline-analysis card that lets the user edit markers on the working layer and copy a chosen frame range into the canonical `_analyzed` file.

**Architecture:** A new backend endpoint copies a frame range from a layer h5 into `_analyzed` by reusing the existing `canonical.write_to_canonical` (relabel→merge→dense). The frontend adds a finalize toggle that gates marker editing, relocates the marker-edit controls below the marker list, and wires an "Add range to `_analyzed`" button that first commits edits to the layer, then calls the new endpoint. Backend is unit-tested with pytest; the frontend (no DOM runtime in tests) is guarded by static source-assertion tests, matching this repo's `tests/test_inline_analysis_ui_isolation.py` pattern.

**Tech Stack:** Flask blueprint route + pandas (backend); vanilla JS (frontend); pytest static-source + behavioral assertions.

---

## Conventions

- **Repo / dir:** all commands run from `/home/sam/docker-images/deeplabcut-webapp-docker` (branch `feat/3d-inline-analysis` — do NOT create a new branch).
- **Worker restart NOT needed:** these changes are in Flask routes + static assets, not the Celery worker's `tasks.py`. (Flask serves templates/static fresh per request.)
- **Frontend scope structure (important):** `src/static/js/inline_analysis_player.js` is one big outer player IIFE. Outer-scope vars declared near the top (e.g. `_iaCurrentFrame` line 41, `_iaCurrentVideoPath` line 65, `_iaLocalEdits`, `_iaOverlayEnabled`, function `_iaPrimary()` line 88) are visible to the nested sub-IIFEs (Dataset Curation `})()` at line 2106, ANALYSIS DISPATCH `(function(){` at line 2175 … `})()` at line 2417). The dispatch IIFE's own consts (e.g. `iaFramesPerCk`) are NOT visible outside it — so cross-scope element reads must use `document.getElementById(...)`.
- **Tests are fast/static** except Task 1 (real pytest). Run only the named files; do not run the whole suite except in Task 6.
- **Known pre-existing failure:** `tests/test_inline_analysis_ui_isolation.py::test_worker_dense_ifies_h5_for_positional_consumers` fails on `main` already (asserts on `src/dlc/tasks.py`, unrelated). Treat only OTHER failures as real.

---

## Task 1: Backend — `finalize-range` endpoint + testable helper

**Files:**
- Modify: `src/dlc/inline_analysis.py` (add helper `_finalize_range_to_canonical` + route `finalize_range`)
- Test: `tests/test_inline_analysis_finalize.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inline_analysis_finalize.py`:

```python
"""Behavior of the finalize-range helper: copy a layer-h5 range into the
canonical _analyzed file (curated range wins, out-of-range frames preserved)."""
import numpy as np
import pandas as pd
from pathlib import Path

from src.dlc import inline_analysis, canonical


def _layer_df(scorer, frames, bodyparts=("nose", "tail")):
    cols = pd.MultiIndex.from_product(
        [[scorer], list(bodyparts), ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"])
    data = np.zeros((len(list(frames)), len(bodyparts) * 3))
    idx = list(frames)
    for i, f in enumerate(idx):
        data[i, :] = float(f)  # value == frame number, for easy assertions
    return pd.DataFrame(data, index=pd.Index(idx, name="frame"), columns=cols)


def test_finalize_range_writes_canonical(tmp_path):
    video = tmp_path / "clip.mp4"; video.write_bytes(b"")
    scorer = "DLC_resnet50_xshuffle1_snapshot100"
    layer = tmp_path / f"clip{scorer}.h5"
    _layer_df(scorer, range(0, 10)).to_hdf(str(layer), key="df_with_missing", mode="w")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: CANON\nbodyparts:\n- nose\n- tail\n")

    h5, csv, n = inline_analysis._finalize_range_to_canonical(
        str(video), str(layer), start_frame=3, n_frames=2, config_path=str(cfg))

    assert n == 2
    assert Path(h5).name == "clip_analyzed.h5"
    assert Path(csv).is_file()
    out = pd.read_hdf(str(h5))
    assert out.columns.get_level_values(0).unique().tolist() == ["CANON"]
    assert list(out.index) == [0, 1, 2, 3, 4]              # dense 0..max
    assert out.loc[3, ("CANON", "nose", "x")] == 3         # curated range copied
    assert out.loc[4, ("CANON", "nose", "x")] == 4
    assert np.isnan(out.loc[0, ("CANON", "nose", "x")])    # out-of-range not copied


def test_finalize_range_preserves_existing(tmp_path):
    video = tmp_path / "clip.mp4"; video.write_bytes(b"")
    scorer = "SRC"
    layer = tmp_path / f"clip{scorer}.h5"
    _layer_df(scorer, range(0, 10)).to_hdf(str(layer), key="df_with_missing", mode="w")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: CANON\nbodyparts:\n- nose\n- tail\n")

    pre = _layer_df("CANON", [8]); pre.loc[8, :] = 999.0
    canon_h5 = canonical.canonical_h5_path(str(video))
    pre.to_hdf(str(canon_h5), key="df_with_missing", mode="w")

    inline_analysis._finalize_range_to_canonical(
        str(video), str(layer), start_frame=3, n_frames=2, config_path=str(cfg))

    out = pd.read_hdf(str(canon_h5))
    assert out.loc[8, ("CANON", "nose", "x")] == 999       # preserved
    assert out.loc[3, ("CANON", "nose", "x")] == 3          # new curated value
```

- [ ] **Step 2: Run the tests, verify they FAIL**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_finalize.py -v
```
Expected: FAIL with `AttributeError: module 'src.dlc.inline_analysis' has no attribute '_finalize_range_to_canonical'`.

- [ ] **Step 3: Add the helper + route**

In `src/dlc/inline_analysis.py`, add the helper near the other module helpers (before the routes section). It only needs pandas + the already-imported `_canonical`:

```python
def _finalize_range_to_canonical(video_path, source_h5, start_frame, n_frames, config_path):
    """Copy rows [start_frame, start_frame+n_frames) from a layer h5 into the
    canonical _analyzed file. Curated range wins; out-of-range frames preserved;
    _analyzed created dense if missing. Returns (h5_path, csv_path, n_written)."""
    import pandas as pd
    df = pd.read_hdf(str(source_h5))
    source_scorer = df.columns.get_level_values(0)[0]
    wanted = set(range(int(start_frame), int(start_frame) + int(n_frames)))
    sliced = df[df.index.isin(wanted)]
    canon = _canonical.canonical_scorer(config_path)
    h5_path, csv_path = _canonical.write_to_canonical(
        video_path, sliced,
        source_scorer=source_scorer, canonical_scorer=canon, save_as_csv=True)
    return h5_path, csv_path, int(len(sliced))
```

Then add the route alongside the other inline routes (e.g. after `analysis_file_initialize`):

```python
@bp.route("/dlc/project/inline-analysis/finalize-range", methods=["POST"])
def finalize_range():
    project = _active_project()
    if not project or not project.get("config_path"):
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    video_path = (body.get("video_path") or "").strip()
    source_h5  = (body.get("source_h5") or "").strip()
    if not video_path or not source_h5:
        return jsonify({"error": "video_path and source_h5 required"}), 400
    vp, sp = Path(video_path), Path(source_h5)
    if not vp.is_file() or not sp.is_file():
        return jsonify({"error": "video_path or source_h5 not found"}), 400
    if not _sec_check(vp) or not _sec_check(sp):
        return jsonify({"error": "path outside the data root"}), 403
    try:
        start_frame = int(body.get("start_frame", 0))
        n_frames    = int(body.get("n_frames", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame and n_frames must be ints"}), 400
    if start_frame < 0 or n_frames <= 0 or n_frames > 10_000:
        return jsonify({"error": "start_frame >= 0 and n_frames in 1..10000"}), 400
    try:
        h5_path, csv_path, n_written = _finalize_range_to_canonical(
            video_path, source_h5, start_frame, n_frames, project["config_path"])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"h5_path": str(h5_path), "csv_path": str(csv_path),
                    "n_frames_written": n_written}), 200
```

- [ ] **Step 4: Run the tests, verify they PASS**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_finalize.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/inline_analysis.py tests/test_inline_analysis_finalize.py
git commit -m "feat(inline-analysis): finalize-range endpoint copies a layer range into _analyzed

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: HTML — Finalize minicard below the curation panel

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html` (insert after line 376, the `<!-- ── end Dataset Curation Panel ── -->` comment, before the `</div>` that closes `#ia-player-section`)
- Test: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_finalize_minicard_present_after_curation():
    html = CARD.read_text()
    for needed in ["ia-finalize-toggle", "ia-finalize-controls", "ia-finalize-start",
                   "ia-finalize-count", "ia-finalize-add-btn", "ia-finalize-status"]:
        assert f'id="{needed}"' in html, f"missing {needed!r}"
    assert html.find('id="ia-curation-panel"') < html.find('id="ia-finalize-toggle"'), \
        "finalize panel must come after the curation panel"
```

- [ ] **Step 2: Run the test, verify it FAILS**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_finalize_minicard_present_after_curation -v
```
Expected: FAIL (`ia-finalize-toggle` not present).

- [ ] **Step 3: Insert the minicard markup**

In `src/templates/partials/card_inline_analysis.html`, immediately after the line
`        <!-- ── end Dataset Curation Panel ──────────────────────────── -->` (line 376) and before the next `</div>`, insert:

```html

        <!-- ── Finalize Analysis Panel ─────────────────────────────── -->
        <div id="ia-finalize-panel" style="margin-top:.65rem;padding:.5rem .65rem;background:var(--surface-2);border:1px solid var(--border);border-radius:7px">
          <label style="display:flex;align-items:center;gap:.45rem;font-size:.8rem;font-weight:500;cursor:pointer;user-select:none">
            <input type="checkbox" id="ia-finalize-toggle" style="accent-color:var(--accent);width:14px;height:14px"/>
            Finalize analysis
          </label>
          <div id="ia-finalize-controls" class="hidden" style="margin-top:.5rem">
            <div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin-bottom:.4rem">
              <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap">Start frame</label>
              <input type="number" id="ia-finalize-start" value="0" min="0"
                style="width:5rem;font-size:.76rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.22rem .4rem" />
              <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap">Frames</label>
              <input type="number" id="ia-finalize-count" value="500" min="1" max="10000"
                style="width:5rem;font-size:.76rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.22rem .4rem" />
              <button class="btn-sm btn-create" id="ia-finalize-add-btn"
                title="Save marker edits to the current layer, then copy this frame range into the _analyzed file">
                Add range to _analyzed
              </button>
            </div>
            <div id="ia-finalize-status" class="fe-extract-status"></div>
          </div>
        </div>
        <!-- ── end Finalize Analysis Panel ─────────────────────────── -->
```

- [ ] **Step 4: Run the test, verify it PASSES**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_finalize_minicard_present_after_curation -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html tests/test_inline_analysis_ui_isolation.py
git commit -m "feat(inline-analysis): add Finalize analysis minicard

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: HTML — relocate marker-edit controls below the marker list

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html` (remove the `#ia-marker-edit-banner` block at lines ~172-195; add a `#ia-marker-edit-controls` row after `#ia-bp-list-wrap`, i.e. after line 241, before `#ia-status` at line 243)
- Test: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_marker_edit_controls_moved_below_marker_list():
    html = CARD.read_text()
    assert 'id="ia-marker-edit-banner"' not in html, "old top banner must be removed"
    assert 'id="ia-marker-edit-controls"' in html, "relocated controls row must exist"
    pos_list  = html.find('id="ia-bp-list-wrap"')
    pos_ctrls = html.find('id="ia-marker-edit-controls"')
    pos_curil = html.find('id="ia-curation-panel"')
    assert 0 < pos_list < pos_ctrls < pos_curil, \
        "controls must sit after the marker list and before the curation panel"
    for needed in ["ia-marker-edit-count", "ia-save-adjustments-btn",
                   "ia-discard-adjustments-btn", "ia-clear-frame-btn"]:
        assert f'id="{needed}"' in html, f"missing {needed!r}"
```

- [ ] **Step 2: Run the test, verify it FAILS**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_marker_edit_controls_moved_below_marker_list -v
```
Expected: FAIL (`ia-marker-edit-banner` still present; `ia-marker-edit-controls` absent).

- [ ] **Step 3: Move + restyle the controls**

(a) DELETE the entire top banner block — from the line beginning `        <div id="ia-marker-edit-banner" class="hidden"` (line ~172) through its closing `</div>` (the one right before `<div id="ia-video-wrap"`, line ~195). Remove the surrounding `<!-- Marker Adjustment Banner ... -->` comment too.

(b) INSERT, immediately AFTER the `#ia-bp-list-wrap` block's closing `</div>` (line ~241) and BEFORE `<span id="ia-status" ...>` (line ~243), this relocated row (renamed to `ia-marker-edit-controls`, restyled as a plain row, hidden by default):

```html
        <!-- Marker-edit controls — shown only when Finalize analysis is on -->
        <div id="ia-marker-edit-controls" class="hidden"
          style="display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;margin-top:.35rem;margin-bottom:.2rem;font-size:.77rem">
          <span id="ia-marker-edit-count" style="color:var(--accent);font-weight:500">0 frames edited</span>
          <button class="btn-sm" id="ia-save-adjustments-btn"
            style="background:var(--accent);color:#fff;font-weight:500;padding:.28rem .7rem"
            title="Write marker adjustments back to the .h5 and .csv files">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="margin-right:.3rem"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save Adjustments
          </button>
          <button class="btn-sm" id="ia-discard-adjustments-btn"
            style="opacity:.75;padding:.28rem .6rem"
            title="Discard all pending marker adjustments">Discard</button>
          <button class="btn-sm" id="ia-clear-frame-btn"
            style="opacity:.65;padding:.28rem .6rem"
            title="Double-click to erase all markers on the current frame">Clear Frame</button>
        </div>
```

- [ ] **Step 4: Run the test, verify it PASSES**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_marker_edit_controls_moved_below_marker_list -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html tests/test_inline_analysis_ui_isolation.py
git commit -m "feat(inline-analysis): relocate marker-edit controls below the marker list

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: JS — shared state, edit-gating, banner retarget

**Files:**
- Modify: `src/static/js/inline_analysis_player.js`
- Test: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_js_edit_gating_uses_finalize_flag():
    src = PLAYER_JS.read_text()
    assert "_iaFinalizeEnabled" in src, "finalize gating flag must exist"
    assert "_iaEditingAllowed" in src, "editing-allowed helper must exist"
    # the relocated controls element id is now referenced (old banner id gone)
    assert 'getElementById("ia-marker-edit-controls")' in src
    assert 'getElementById("ia-marker-edit-banner")' not in src
```

- [ ] **Step 2: Run the test, verify it FAILS**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_js_edit_gating_uses_finalize_flag -v
```
Expected: FAIL (`_iaFinalizeEnabled` absent).

- [ ] **Step 3: Add the flag, helper, retarget, and gates**

(a) Near the other outer-scope state vars (just after `let _iaCurrentVideoPath = null;`, line 65), add:

```javascript
    let _iaFinalizeEnabled = false;   // marker editing gated on the Finalize toggle
    let _iaLastRunStart    = null;    // start_frame of the last submitted range
    let _iaLastRunN        = null;    // n_frames of the last submitted range
```

(b) Add an editing-allowed helper near `_iaPrimary()` (line 88):

```javascript
    function _iaEditingAllowed() { return _iaOverlayEnabled && _iaFinalizeEnabled; }
```

(c) Retarget the banner const (line 705): change
`const iaMarkerEditBanner  = document.getElementById("ia-marker-edit-banner");`
to
`const iaMarkerEditBanner  = document.getElementById("ia-marker-edit-controls");`

(d) Replace `_iaUpdateEditBanner` (lines 714-723) so visibility follows the finalize flag (not the edit count), while still updating the count text:

```javascript
    function _iaUpdateEditBanner() {
      if (!iaMarkerEditBanner) return;
      iaMarkerEditBanner.classList.toggle("hidden", !_iaFinalizeEnabled);
      const n = _iaEditCount();
      if (iaMarkerEditCount) iaMarkerEditCount.textContent = `${n} frame${n !== 1 ? "s" : ""} edited`;
    }
```

(e) Gate the marker-MUTATION handlers on `_iaEditingAllowed()` (do NOT touch render/hover/draw paths). Make these exact replacements:

- Click-to-place handler (line ~801): change `if (!_iaOverlayEnabled || !_iaCurrentPoses.length) return;` → `if (!_iaEditingAllowed() || !_iaCurrentPoses.length) return;`
- Mousedown drag-start (line ~824): change `if (!_iaOverlayEnabled || !_iaCurrentPoses.length || e.button !== 0) return;` → `if (!_iaEditingAllowed() || !_iaCurrentPoses.length || e.button !== 0) return;`
- Drag-move guard (line ~835): change `if (!_iaOverlayEnabled) return;` → `if (!_iaEditingAllowed()) return;`
- Right-click delete (line ~890): change `if (!_iaOverlayEnabled || !_iaSelectedBp || !_iaPrimary()) return;` → `if (!_iaEditingAllowed() || !_iaSelectedBp || !_iaPrimary()) return;`
- Clear-frame dblclick (line ~968): change `if (!_iaOverlayEnabled || !_iaPrimary() || !_iaCurrentPoses.length) return;` → `if (!_iaEditingAllowed() || !_iaPrimary() || !_iaCurrentPoses.length) return;`

Leave the RENDER guards unchanged: lines ~232, ~277, ~302, ~594 keep `_iaOverlayEnabled` (markers must still display when the overlay is on but finalize is off).

- [ ] **Step 4: Run the test + the relocate test, verify PASS**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_js_edit_gating_uses_finalize_flag -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/inline_analysis_player.js tests/test_inline_analysis_ui_isolation.py
git commit -m "feat(inline-analysis): gate marker editing on the Finalize toggle

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: JS — finalize toggle wiring, button flow, autopopulate

**Files:**
- Modify: `src/static/js/inline_analysis_player.js`
- Test: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_js_finalize_flow_and_autopopulate():
    src = PLAYER_JS.read_text()
    assert 'getElementById("ia-finalize-toggle")' in src
    assert 'getElementById("ia-finalize-add-btn")' in src
    assert "/dlc/project/inline-analysis/finalize-range" in src   # copy step
    assert "/dlc/viewer/save-marker-edits" in src                 # commit-to-layer step
    assert "_iaLastRunStart" in src and "_iaLastRunN" in src       # autopopulate source
    assert "_iaPopulateFinalizeFields" in src
```

- [ ] **Step 2: Run the test, verify it FAILS**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_js_finalize_flow_and_autopopulate -v
```
Expected: FAIL (`ia-finalize-toggle` not wired).

- [ ] **Step 3a: Add finalize wiring in the OUTER IIFE**

Place this block in the outer player IIFE, right after the Discard/Clear-Frame button wiring (after the `iaClearFrameBtn` block, around line 980, still inside the outer IIFE — before the `})(); // end Dataset Curation` nested IIFEs). It uses only outer-scope vars + `getElementById` (so it is safe across scopes):

```javascript
    // ── Finalize Analysis: toggle (gates editing) + range copy ──────────
    const iaFinalizeToggle   = document.getElementById("ia-finalize-toggle");
    const iaFinalizeControls = document.getElementById("ia-finalize-controls");
    const iaFinalizeAddBtn   = document.getElementById("ia-finalize-add-btn");
    const iaFinalizeStatus   = document.getElementById("ia-finalize-status");

    function _iaPopulateFinalizeFields() {
      const startEl = document.getElementById("ia-finalize-start");
      const countEl = document.getElementById("ia-finalize-count");
      const fpc     = document.getElementById("ia-frames-per-click");
      if (startEl) startEl.value = (_iaLastRunStart != null ? _iaLastRunStart : (_iaCurrentFrame || 0));
      if (countEl) countEl.value = (_iaLastRunN != null ? _iaLastRunN : (parseInt(fpc?.value, 10) || 500));
    }

    iaFinalizeToggle?.addEventListener("change", () => {
      _iaFinalizeEnabled = iaFinalizeToggle.checked;
      iaFinalizeControls?.classList.toggle("hidden", !_iaFinalizeEnabled);
      // Auto-enable the kinematic overlay so there are markers to edit.
      if (_iaFinalizeEnabled && iaOverlayToggle && !iaOverlayToggle.checked) {
        iaOverlayToggle.checked = true;
        iaOverlayToggle.dispatchEvent(new Event("change"));
      }
      if (_iaFinalizeEnabled) _iaPopulateFinalizeFields();
      _iaUpdateEditBanner();   // show/hide the relocated marker-edit controls
    });

    iaFinalizeAddBtn?.addEventListener("click", async () => {
      const layer = _iaPrimary();
      if (!layer) {
        if (iaFinalizeStatus) {
          iaFinalizeStatus.textContent = "Select a layer first.";
          iaFinalizeStatus.className   = "fe-extract-status err";
        }
        return;
      }
      const startFrame = parseInt(document.getElementById("ia-finalize-start")?.value, 10) || 0;
      const nFrames    = parseInt(document.getElementById("ia-finalize-count")?.value, 10) || 0;
      iaFinalizeAddBtn.disabled = true;
      if (iaFinalizeStatus) { iaFinalizeStatus.textContent = "Finalizing…"; iaFinalizeStatus.className = "fe-extract-status"; }
      try {
        // 1) commit pending marker edits to the current layer
        await fetch("/dlc/viewer/save-marker-edits", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ h5: layer.path }),
        });
        // 2) copy the chosen range into the _analyzed file
        const r = await fetch("/dlc/project/inline-analysis/finalize-range", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            video_path: _iaCurrentVideoPath || _iaBrowseVideoPath,
            source_h5:  layer.path,
            start_frame: startFrame, n_frames: nFrames,
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (iaFinalizeStatus) {
          if (!r.ok) { iaFinalizeStatus.textContent = `Error: ${d.error || r.status}`; iaFinalizeStatus.className = "fe-extract-status err"; }
          else { iaFinalizeStatus.textContent = `Added ${d.n_frames_written} frames to _analyzed`; iaFinalizeStatus.className = "fe-extract-status"; }
        }
      } catch (e) {
        if (iaFinalizeStatus) { iaFinalizeStatus.textContent = `Error: ${e}`; iaFinalizeStatus.className = "fe-extract-status err"; }
      } finally {
        iaFinalizeAddBtn.disabled = false;
      }
    });
```

- [ ] **Step 3b: Capture last-run params in the submit handler (DISPATCH IIFE)**

In the range-submit click handler (around line 2302, where `const startFrame = _iaCurrentFrame || 0;` and `const nFrames = parseInt(iaFramesPerCk?.value, 10) || 500;` are computed), add right after those two lines:

```javascript
        _iaLastRunStart = startFrame;
        _iaLastRunN     = nFrames;
```

And in the range-poll "done" branch (around line 2329, after setting `iaLastRun.textContent = ...`), add:

```javascript
              if (typeof _iaPopulateFinalizeFields === "function") _iaPopulateFinalizeFields();
```

(Both `_iaLastRunStart`/`_iaLastRunN` and `_iaPopulateFinalizeFields` are outer-scope, so they are visible inside the nested DISPATCH IIFE.)

- [ ] **Step 4: Run the test, verify it PASSES**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_ui_isolation.py::test_js_finalize_flow_and_autopopulate -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/inline_analysis_player.js tests/test_inline_analysis_ui_isolation.py
git commit -m "feat(inline-analysis): wire Finalize toggle, range-copy button, autopopulate

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Full-suite sweep

**Files:** none (verification only).

- [ ] **Step 1: Run both affected test files**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_inline_analysis_finalize.py tests/test_inline_analysis_ui_isolation.py -v
```
Expected: `test_inline_analysis_finalize.py` all pass (2). `test_inline_analysis_ui_isolation.py` all pass EXCEPT the single pre-existing `test_worker_dense_ifies_h5_for_positional_consumers` (unrelated, asserts on `src/dlc/tasks.py`).

- [ ] **Step 2: Confirm the pre-existing failure is the only red**

If any failure OTHER than `test_worker_dense_ifies_h5_for_positional_consumers` appears, fix it in the owning task's files and re-run. No commit for this task.

---

## Self-review notes (author)

- **Spec coverage:** Feature 1 (minicard) → Task 2. Feature 2 (edit-gating) → Task 4. Feature 3 (relocate controls) → Task 3 + Task 4(c/d). Feature 4 (button flow) → Task 5. Feature 5 (backend endpoint) → Task 1. Feature 6 (autopopulate) → Task 5 (3a `_iaPopulateFinalizeFields` + 3b last-run capture). Testing section → Tasks 1-5 tests + Task 6 sweep.
- **Type/name consistency:** `_iaFinalizeEnabled`, `_iaEditingAllowed()`, `_iaPopulateFinalizeFields`, `_iaLastRunStart`, `_iaLastRunN`, `_finalize_range_to_canonical`, route `/dlc/project/inline-analysis/finalize-range`, ids `ia-finalize-{toggle,controls,start,count,add-btn,status}`, `ia-marker-edit-controls` — used consistently across tasks.
- **Scope:** 2D only; `canonical.py` untouched (reused); no worker/`tasks.py` change → no worker restart needed.
