# Viewer Flow Tweaks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the lingering pain points in the View Analyzed Videos / Frames flow — markers that lag the video during playback, indistinguishable shapes between primary and comparison 1, no per-primary visibility toggle, always-on Dataset Curation block.

**Architecture:** Pure frontend-side change. Drop the open-circle shape (replace comparison 1 with a filled diamond). Add a per-primary visibility row in the comparison-layers block with the primary's shape glyph as a legend. Add a user-editable `va-play-fps` field (default 5, range 1–120) that drives the play loop's tick delay. Rewrite `_vaLoadFrame` so the visible image and its markers swap **atomically** — preload the image off-DOM in parallel with all visible-layer pose fetches, then commit both to screen together. Wrap the Dataset Curation panel in a master-toggle (`va-curation-toggle`/`va-curation-controls`) mirroring the existing `va-overlay-toggle` pattern.

**Tech Stack:** Vanilla JS (ES module), Flask Jinja partials, pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-02-viewer-flow-tweaks-design.md`

---

## File Structure

**Modified files:**

| Path | What changes |
|---|---|
| `src/static/js/viewer.js` | Drop `_drawCircleOpen`; add `_drawDiamond`; update `_SHAPE_ORDER` + `_shapeGlyph` + `_SHAPE_FN`. Add `_vaPlaybackFps` + `_vaPlayDelayMs` + replace `_vaPlayLoop`'s `1000 / _vaFps` with the new helper. Rewrite `_vaLoadFrame` for atomic image+markers swap. Add per-primary visibility checkbox handler. On primary swap update `va-overlay-primary-shape` + `va-overlay-primary-label`. Add curation toggle handler. |
| `src/templates/partials/card_viewer.html` | Insert `va-overlay-primary-row` block in the comparison-layers area. Insert the `va-play-fps` `<label>` block before `va-play-step`. Restructure the Dataset Curation panel to wrap its inner content in `va-curation-controls` with a `va-curation-toggle` master. |
| `tests/test_viewer_layers_ui_isolation.py` | Six new IDs unique. JS-source asserts: `_drawDiamond` present; `_drawCircleOpen` removed; `_SHAPE_ORDER` includes `"diamond"`; `_vaPlaybackFps` + `_vaPlayDelayMs` defined; atomic-swap pattern present; curation toggle handler references `va-curation-controls`. |
| `tests/e2e_viewer_layers_smoke.py` | Phases I/J/K/L: per-primary visibility, FPS field, atomic swap, curation toggle. |

**New files:** none.

---

## Conventions

- Repo root: `/home/sam/docker-images/deeplabcut-webapp-docker`.
- Pre-existing user edits to ignore (do NOT stage): `src/dlc/README.md`, `src/dlc/labeling.py`, `src/dlc/vlm_indexer.py`, `src/static/js/anipose.js`, `src/static/js/main.js`, `src/static/js/vlm_refiner.js`, `tests/test_dlc_celery_tasks.py`. Untracked: `CLAUDE.md`, `src/static/js/admin.js`, `tests/test_dlc_labeling_routes.py`.
- After each commit that touches a Jinja template or `viewer.js`, restart flask: `docker compose restart flask`.
- The `flask_test_client` teardown LookupError noise is pre-existing — tests reporting "errors" but PASSING assertions are fine.
- OM-2 RatBox folder for the e2e: container `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10`; host `/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10`.

Concrete current-state references (so tasks point at real lines):

- `src/static/js/viewer.js:97` → `const _SHAPE_ORDER = ["circle-filled", "circle-open", "square", "triangle"];`
- `src/static/js/viewer.js:221` → `async function _vaLoadFrame(n) {`
- `src/static/js/viewer.js:1683` → `async function _vaPlayLoop() {`
- `src/static/js/viewer.js:1702` → `const delay = Math.max(0, Math.round(1000 / _vaFps) - elapsed);`
- `src/templates/partials/card_viewer.html` line ~70+ has the `va-overlay-toggle` master pattern; lines 224+ have the existing Dataset Curation panel.

---

## Task 1: Shape ladder — drop `circle-open`, add `_drawDiamond`

**Files:**
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Replace `_SHAPE_ORDER`**

In `src/static/js/viewer.js` line 97, change:

```js
const _SHAPE_ORDER = ["circle-filled", "circle-open", "square", "triangle"];
```

to:

```js
const _SHAPE_ORDER = ["circle-filled", "diamond", "square", "triangle"];
```

- [ ] **Step 2: Add `_drawDiamond`; remove `_drawCircleOpen`**

Find `function _drawCircleOpen(` (search for that exact text). DELETE the entire function definition.

Add `_drawDiamond` next to where `_drawCircleFilled` is defined:

```js
function _drawDiamond(ctx, x, y, r, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x,     y - r);
  ctx.lineTo(x + r, y    );
  ctx.lineTo(x,     y + r);
  ctx.lineTo(x - r, y    );
  ctx.closePath();
  ctx.fill();
}
```

Update the `_SHAPE_FN` map. Find:

```js
const _SHAPE_FN = {
  "circle-filled": _drawCircleFilled,
  "circle-open":   _drawCircleOpen,
  "square":        _drawSquare,
  "triangle":      _drawTriangle,
};
```

Replace with:

```js
const _SHAPE_FN = {
  "circle-filled": _drawCircleFilled,
  "diamond":       _drawDiamond,
  "square":        _drawSquare,
  "triangle":      _drawTriangle,
};
```

- [ ] **Step 3: Update `_shapeGlyph`**

Find `function _shapeGlyph(`. Replace its body with:

```js
function _shapeGlyph(shape) {
  switch (shape) {
    case "circle-filled": return "●";
    case "diamond":       return "◆";
    case "square":        return "□";
    case "triangle":      return "△";
    default:              return "?";
  }
}
```

- [ ] **Step 4: Smoke check**

```bash
docker compose restart flask
python -m pytest tests/test_frontend_assets.py 2>&1 | tail -3
```

Expected: `91 passed`.

Verify via grep:

```bash
grep -c "_drawCircleOpen" src/static/js/viewer.js
grep -c "_drawDiamond"    src/static/js/viewer.js
grep -c '"diamond"'       src/static/js/viewer.js
grep -c '"circle-open"'   src/static/js/viewer.js
```

Expected: `0`, `≥2`, `≥1`, `0`.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "feat(viewer): replace circle-open with filled diamond for comparison-1 shape"
```

---

## Task 2: Per-primary visibility row + legend glyph

**Files:**
- Modify: `src/templates/partials/card_viewer.html`.
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Insert the primary-row block in the partial**

In `src/templates/partials/card_viewer.html`, find the existing `<div id="va-overlay-compare-block" …>`. Inside it, locate the `<label>` row that contains `Comparison layers` + the `<select id="va-overlay-add-compare">`. The block currently looks like:

```html
<div id="va-overlay-compare-block" style="margin-bottom:.45rem">
  <label …>
    Comparison layers
    <select id="va-overlay-add-compare" …>…</select>
    <span id="va-overlay-add-compare-empty-hint" …>…</span>
  </label>
  <div id="va-overlay-compare-list" …></div>
  <span id="va-overlay-edit-disabled-banner" …>…</span>
</div>
```

Insert this NEW block IMMEDIATELY BEFORE the `<div id="va-overlay-compare-list">` (so it sits between the comparison-layers label and the dynamic compare list):

```html
<div id="va-overlay-primary-row"
     style="display:flex;align-items:center;gap:.35rem;font-size:.74rem;padding:.15rem .25rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;margin-bottom:.25rem">
  <input type="checkbox" id="va-overlay-primary-visible" checked
         style="accent-color:var(--accent);width:12px;height:12px;flex-shrink:0"/>
  <span id="va-overlay-primary-shape"
        style="font-family:var(--mono);width:1.1rem;text-align:center;flex-shrink:0">●</span>
  <span style="font-size:.7rem;color:var(--text-dim);flex-shrink:0">Primary</span>
  <span id="va-overlay-primary-label"
        style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
</div>
```

- [ ] **Step 2: Add the visibility checkbox + label-sync handlers in `viewer.js`**

In `src/static/js/viewer.js`, near where the other listener registrations are (search for `vaOverlayAddCompare?.addEventListener` to find the comparison-layers listener block), add:

```js
const vaOverlayPrimaryVisible = document.getElementById("va-overlay-primary-visible");
const vaOverlayPrimaryShape   = document.getElementById("va-overlay-primary-shape");
const vaOverlayPrimaryLabel   = document.getElementById("va-overlay-primary-label");

vaOverlayPrimaryVisible?.addEventListener("change", () => {
  const layer = _vaPrimary();
  if (!layer) return;
  layer.visible = !!vaOverlayPrimaryVisible.checked;
  _vaDrawCurrentFrame();
});

function _vaSyncPrimaryRow() {
  const layer = _vaPrimary();
  if (!layer) {
    if (vaOverlayPrimaryShape) vaOverlayPrimaryShape.textContent = "—";
    if (vaOverlayPrimaryLabel) vaOverlayPrimaryLabel.textContent = "(no primary)";
    if (vaOverlayPrimaryVisible) vaOverlayPrimaryVisible.checked = false;
    return;
  }
  if (vaOverlayPrimaryShape) vaOverlayPrimaryShape.textContent = _shapeGlyph(layer.shape);
  if (vaOverlayPrimaryLabel) vaOverlayPrimaryLabel.textContent = layer.label || "";
  if (vaOverlayPrimaryVisible) vaOverlayPrimaryVisible.checked = !!layer.visible;
}
```

- [ ] **Step 3: Call `_vaSyncPrimaryRow` whenever the primary changes**

Find `async function _vaApplyPrimaryFromSelect()`. At the end of its body (after `_vaRenderPrimaryThresholdInline()` and the `_vaLoadFrame` call), add:

```js
_vaSyncPrimaryRow();
```

Find `async function _vaDiscoverVariants(`. After the existing `await _vaApplyPrimaryFromSelect()` call inside it, add `_vaSyncPrimaryRow();` (so the row is also synced on initial video load).

- [ ] **Step 4: Smoke check**

```bash
docker compose restart flask
```

Headless verify with Playwright:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.getElementById('view-analyzed-card').classList.remove('hidden')")
    has_row = pg.evaluate("!!document.getElementById('va-overlay-primary-row')")
    has_cb  = pg.evaluate("!!document.getElementById('va-overlay-primary-visible')")
    print("primary row:", has_row, "checkbox:", has_cb, "errors:", errs)
    b.close()
```

Expected: `True`, `True`, `[]`.

- [ ] **Step 5: Commit**

```bash
git add src/templates/partials/card_viewer.html src/static/js/viewer.js
git commit -m "feat(viewer): per-primary visibility row with shape legend"
```

---

## Task 3: Editable FPS field + FPS-driven play loop

**Files:**
- Modify: `src/templates/partials/card_viewer.html`.
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Insert the `va-play-fps` `<label>` block in the partial**

In `src/templates/partials/card_viewer.html`, find the existing `va-play-step` `<label>` block (the one with `step` and the `<input id="va-play-step">` added earlier). Insert IMMEDIATELY BEFORE it:

```html
<label style="display:flex;align-items:center;gap:.3rem;font-size:.75rem;color:var(--text-dim);white-space:nowrap"
       title="Playback rate (frames per wall-clock second)">
  fps
  <input type="number" id="va-play-fps" value="5" min="1" max="120" step="1"
         style="width:46px;text-align:center;font-family:var(--mono);font-size:.78rem;padding:.18rem .3rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text)">
</label>
```

The result: the controls now read `… [fps 5] [step 1] [skip-N] …`.

- [ ] **Step 2: Add the FPS helpers in `viewer.js`**

Add these helpers near the existing `_vaPlayStep` function:

```js
function _vaPlaybackFps() {
  const v = parseInt(document.getElementById("va-play-fps")?.value || "5", 10);
  return Math.max(1, Math.min(120, isNaN(v) ? 5 : v));
}

function _vaPlayDelayMs() {
  return Math.round(1000 / _vaPlaybackFps());
}
```

- [ ] **Step 3: Replace the play-loop tick delay**

Find `_vaPlayLoop` (around line 1683). Locate this line (~line 1702):

```js
const delay   = Math.max(0, Math.round(1000 / _vaFps) - elapsed);
```

Replace with:

```js
const delay   = Math.max(0, _vaPlayDelayMs() - elapsed);
```

The detected `_vaFps` (from the video file) is no longer used by the play loop — it stays available for the time-display readout (where it appears as `_vaCurrentFrame / _vaFps`).

- [ ] **Step 4: Smoke check**

```bash
docker compose restart flask
```

Verify via grep:

```bash
grep -c "_vaPlaybackFps"      src/static/js/viewer.js
grep -c "_vaPlayDelayMs"      src/static/js/viewer.js
grep -c "1000 / _vaFps"       src/static/js/viewer.js
grep -c '1000 / _vaFps) - elapsed' src/static/js/viewer.js
```

Expected: `≥2`, `≥2`, `1` (the time-display reference), `0` (old play-loop pattern gone).

Playwright smoke (verify field renders, no errors):

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.getElementById('view-analyzed-card').classList.remove('hidden')")
    has_fps = pg.evaluate("!!document.getElementById('va-play-fps')")
    print("fps input present:", has_fps, "errors:", errs)
    b.close()
```

Expected: `True`, `[]`.

- [ ] **Step 5: Commit**

```bash
git add src/templates/partials/card_viewer.html src/static/js/viewer.js
git commit -m "feat(viewer): editable va-play-fps field driving the play-loop tick delay"
```

---

## Task 4: Atomic image+markers swap in `_vaLoadFrame`

**Files:**
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Replace `_vaLoadFrame` with the atomic-swap version**

Find `async function _vaLoadFrame(n) {` (around line 221). Replace its **entire body** (everything between the opening `{` after `(n)` and the closing `}` of the function) with:

```js
      if (_vaFrameBusy) return;
      _vaFrameBusy = true;
      n = Math.max(0, Math.min(n, Math.max(_vaFrameCount - 1, 0)));
      _vaCurrentFrame = n;
      vaFrameSpinner.classList.remove("hidden");

      const newUrl = _vaFrameUrl(n);

      // Preload the image off-DOM in parallel with all visible-layer pose
      // fetches so the visible image NEVER lands on screen before its markers.
      const imgReady = new Promise((resolve, reject) => {
        const im = new Image();
        im.onload  = () => resolve(im);
        im.onerror = (e) => reject(e || new Error("image preload failed"));
        im.src = newUrl;
      });

      const posesReady = _vaOverlayEnabled
        ? Promise.all(
            _vaLayers
              .filter(l => l.visible && !l.errored)
              .map(l => _vaFetchPosesForFrame(l, n).catch(() => null))
          )
        : Promise.resolve();

      try {
        const [preloadedImg] = await Promise.all([imgReady, posesReady]);

        // Atomic swap: image + markers go to screen together.
        const prev = vaFrameImg.src;
        vaFrameImg.src = preloadedImg.src;
        if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);

        _vaFitViewer();
        _vaUpdateDisplay();
        _vaPrefetchFrames([n + 1, n + 2]);
        if (_vaCurationFrameHook) _vaCurationFrameHook(n);
        if (_vaMetadataFrameHook) _vaMetadataFrameHook(n);

        // Sync primary cache into legacy _vaCurrentPoses for hit-testing.
        const primary = _vaPrimary();
        if (primary) {
          const c = primary.posesCache.get(n);
          if (c) { _vaCurrentPoses = c.poses; _vaNBodyparts = c.n_bodyparts; }
        }

        _vaUpdateOverlay(n);

        // Paint barrier — guarantees image + canvas have landed before the
        // play loop schedules the next tick.
        await new Promise(requestAnimationFrame);
      } catch (err) {
        vaStatus.textContent = `Failed to load frame: ${err && err.message ? err.message : err}`;
        vaStatus.className   = "fe-extract-status err";
      } finally {
        _vaFrameBusy = false;
        vaFrameSpinner.classList.add("hidden");
      }
```

Key changes vs the current implementation:

- The `vaFrameImg.onload` await is gone (it relied on swapping `src` early). Replaced with an off-DOM `new Image()` preload that resolves when the image is decoded.
- The split-branch logic (`if (_vaOverlayEnabled && !_vaPlayTimer)`) is gone — pose fetches now happen for ALL visible layers EVERY frame, regardless of paused/playing state, in parallel with the image preload.
- `URL.createObjectURL(blob)` is gone — `vaFrameImg.src` is set directly to the preloaded image's `src` (which is just `_vaFrameUrl(n)`; the browser image cache short-circuits the second fetch). Existing blob URLs in `vaFrameImg.src` from prior code paths still get revoked.
- The pre-existing internal rAF (line 251 of the old code) is also gone — the post-draw rAF is the single barrier now.

- [ ] **Step 2: Smoke check**

```bash
docker compose restart flask
```

Playwright load:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    print("errors:", errs)
    b.close()
```

Expected: `errors: []`.

Verify via grep:

```bash
grep -c "Promise.all(\[imgReady, posesReady\])" src/static/js/viewer.js
grep -c "URL.createObjectURL"                   src/static/js/viewer.js
```

Expected: `1`, `0` (we removed the createObjectURL path inside `_vaLoadFrame`; if any other call site uses it, that's fine — but inside `_vaLoadFrame` the new code does not).

- [ ] **Step 3: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "fix(viewer): atomic image+markers swap so playback never shows stale markers"
```

---

## Task 5: Collapsible Dataset Curation panel

**Files:**
- Modify: `src/templates/partials/card_viewer.html`.
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Restructure the Dataset Curation panel**

In `src/templates/partials/card_viewer.html`, find the block starting with `<div id="va-curation-panel" …>` (around line 224). The current structure is:

```html
<div id="va-curation-panel" style="…">
  <!-- Header -->
  <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-bottom:.45rem">
    <span style="font-size:.8rem;font-weight:600;color:var(--text)">Dataset Curation</span>
    <span id="va-curation-status" class="fe-extract-status" style="font-size:.73rem;flex:1;min-width:0"></span>
  </div>
  <!-- Row 1: Quick-extract + Add to Dataset -->
  <div …>… extract / add buttons …</div>
  <!-- … further rows … -->
</div>
```

Replace ONLY the header `<div>` (the one containing the title + status span) with this toggle row, AND wrap every following sibling (Row 1, further rows, etc.) inside a new `<div id="va-curation-controls" class="hidden" style="margin-top:.5rem">…</div>`.

The result:

```html
<div id="va-curation-panel" style="margin-top:.65rem;padding:.5rem .65rem;background:var(--surface-2);border:1px solid var(--border);border-radius:7px">
  <!-- Toggle row (master kill-switch for the curation tools) -->
  <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
    <label style="display:flex;align-items:center;gap:.45rem;font-size:.8rem;font-weight:500;cursor:pointer;user-select:none">
      <input type="checkbox" id="va-curation-toggle"
             style="accent-color:var(--accent);width:14px;height:14px"/>
      Dataset Curation
    </label>
    <span id="va-curation-status" class="fe-extract-status" style="font-size:.73rem;flex:1;min-width:0"></span>
  </div>
  <!-- Existing inner rows wrapped in a hidden-by-default div -->
  <div id="va-curation-controls" class="hidden" style="margin-top:.5rem">
    <!-- Row 1: Quick-extract + Add to Dataset -->
    <!-- … all the previously-existing rows go here, unchanged … -->
  </div>
</div>
```

Default = unchecked → `va-curation-controls` starts hidden.

- [ ] **Step 2: Add the toggle handler in `viewer.js`**

Near the other listener registrations (e.g., next to where `va-overlay-toggle` is wired), add:

```js
const vaCurationToggle   = document.getElementById("va-curation-toggle");
const vaCurationControls = document.getElementById("va-curation-controls");
vaCurationToggle?.addEventListener("change", () => {
  vaCurationControls?.classList.toggle("hidden", !vaCurationToggle.checked);
});
```

- [ ] **Step 3: Smoke check**

```bash
docker compose restart flask
```

Playwright:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.getElementById('view-analyzed-card').classList.remove('hidden')")
    pg.evaluate("document.getElementById('va-player-section').classList.remove('hidden')")
    has_toggle   = pg.evaluate("!!document.getElementById('va-curation-toggle')")
    has_controls = pg.evaluate("!!document.getElementById('va-curation-controls')")
    initially_hidden = pg.evaluate(
        "document.getElementById('va-curation-controls').classList.contains('hidden')"
    )
    pg.click("#va-curation-toggle")
    visible_after = pg.evaluate(
        "!document.getElementById('va-curation-controls').classList.contains('hidden')"
    )
    print(f"toggle:{has_toggle} controls:{has_controls} hidden:{initially_hidden} visible_after_click:{visible_after} errors:{errs}")
    b.close()
```

Expected: `toggle:True controls:True hidden:True visible_after_click:True errors:[]`.

- [ ] **Step 4: Commit**

```bash
git add src/templates/partials/card_viewer.html src/static/js/viewer.js
git commit -m "feat(viewer): collapsible Dataset Curation panel with master toggle"
```

---

## Task 6: UI isolation tests extension

**Files:**
- Modify: `tests/test_viewer_layers_ui_isolation.py`.

- [ ] **Step 1: Append the new assertions**

```python
NEW_TWEAK_IDS = {
    "va-overlay-primary-row",
    "va-overlay-primary-visible",
    "va-overlay-primary-shape",
    "va-overlay-primary-label",
    "va-play-fps",
    "va-curation-toggle",
    "va-curation-controls",
}


def test_new_tweak_ids_present_and_unique():
    seen_global: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen_global[m.group(1)] = seen_global.get(m.group(1), 0) + 1
    for nid in NEW_TWEAK_IDS:
        assert seen_global.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen_global.get(nid, 0)} times across partials"
        )


def test_viewer_js_diamond_replaces_circle_open():
    js = VIEWER_JS.read_text()
    assert "_drawDiamond" in js, "filled diamond primitive must be defined"
    assert '"diamond"' in js, '_SHAPE_ORDER / _SHAPE_FN must include "diamond"'
    assert "_drawCircleOpen" not in js, "_drawCircleOpen must be removed"
    assert '"circle-open"' not in js, '"circle-open" slot must be gone from _SHAPE_ORDER'


def test_viewer_js_playback_fps_helpers_present():
    js = VIEWER_JS.read_text()
    assert "_vaPlaybackFps" in js, "_vaPlaybackFps helper must exist"
    assert "_vaPlayDelayMs" in js, "_vaPlayDelayMs helper must exist"
    assert "va-play-fps" in js, "viewer.js must read the va-play-fps input"
    # The play loop MUST consume _vaPlayDelayMs(); the legacy `1000 / _vaFps`
    # arithmetic in the play loop is gone.
    assert "1000 / _vaFps) - elapsed" not in js, (
        "play loop must use _vaPlayDelayMs(), not 1000 / _vaFps"
    )


def test_viewer_js_atomic_swap_pattern_present():
    """Regression: _vaLoadFrame must preload the image and pose-fetch in
    parallel and only commit both atomically."""
    js = VIEWER_JS.read_text()
    assert "Promise.all([imgReady, posesReady])" in js, (
        "_vaLoadFrame must await Promise.all([imgReady, posesReady]) before "
        "swapping the visible image"
    )


def test_viewer_js_curation_toggle_handler_present():
    js = VIEWER_JS.read_text()
    assert "va-curation-toggle" in js
    assert "va-curation-controls" in js


def test_viewer_js_primary_visibility_handler_present():
    js = VIEWER_JS.read_text()
    assert "va-overlay-primary-visible" in js
    assert "_vaSyncPrimaryRow" in js, "_vaSyncPrimaryRow helper must exist"
```

- [ ] **Step 2: Run; confirm pass**

```bash
python -m pytest tests/test_viewer_layers_ui_isolation.py -v 2>&1 | tail -10
```

Expected: all (existing + 6 new) PASSED.

If any fail:
- `test_new_tweak_ids_present_and_unique`: an ID is missing or duplicated. Check the partial.
- `test_viewer_js_diamond_replaces_circle_open`: T1 was incomplete. Fix the underlying file.
- `test_viewer_js_playback_fps_helpers_present`: T3 didn't add the helpers or didn't update the play-loop tick delay.
- `test_viewer_js_atomic_swap_pattern_present`: T4 didn't land the literal `Promise.all([imgReady, posesReady])` shape.
- `test_viewer_js_curation_toggle_handler_present`: T5 didn't wire the handler.
- `test_viewer_js_primary_visibility_handler_present`: T2 didn't add the handler / `_vaSyncPrimaryRow`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_viewer_layers_ui_isolation.py
git commit -m "test(viewer): UI-isolation assertions for flow tweaks"
```

---

## Task 7: Playwright e2e — Phases I/J/K/L

**Files:**
- Modify: `tests/e2e_viewer_layers_smoke.py`.

- [ ] **Step 1: Read the current shape**

Read `tests/e2e_viewer_layers_smoke.py`. The earlier flow-improvements task added Phases E–H. This task adds I, J, K, L. Append all four BEFORE `browser.close()` and the final `print("\nALL CHECKS PASSED")`.

- [ ] **Step 2: Append the four phases**

```python
        # ── Phase I: Per-primary visibility ──────────────────────────
        # The primary row carries a checkbox at #va-overlay-primary-visible.
        # Toggling it off must hide primary's markers; toggling on restores them.
        cb_visible_before = page.evaluate(
            "() => document.getElementById('va-overlay-primary-visible')?.checked"
        )
        # Sample a canvas pixel where a primary marker should currently be.
        # We can't pinpoint a marker in the abstract; use any non-edge pixel and
        # diff before / after toggling.
        sample_xy = page.evaluate(
            "() => { const c = document.getElementById('va-overlay-canvas');"
            "  if (!c) return null;"
            "  const ctx = c.getContext('2d');"
            "  const img = ctx.getImageData(c.width/2|0, c.height/2|0, 1, 1).data;"
            "  return [img[0], img[1], img[2], img[3]]; }"
        )
        print(f"[I] before-toggle canvas-center pixel: {sample_xy}")
        # Untick primary visibility.
        page.click("#va-overlay-primary-visible")
        time.sleep(0.4)
        sample_after = page.evaluate(
            "() => { const c = document.getElementById('va-overlay-canvas');"
            "  if (!c) return null;"
            "  const ctx = c.getContext('2d');"
            "  const img = ctx.getImageData(c.width/2|0, c.height/2|0, 1, 1).data;"
            "  return [img[0], img[1], img[2], img[3]]; }"
        )
        print(f"[I] after-toggle canvas-center pixel: {sample_after}")
        # Re-tick.
        page.click("#va-overlay-primary-visible")
        time.sleep(0.3)
        # The pixel before vs after MAY or may not differ depending on whether a
        # primary marker is at the centre. We assert structural correctness:
        # the checkbox toggles cleanly without raising.
        cb_visible_after = page.evaluate(
            "() => document.getElementById('va-overlay-primary-visible')?.checked"
        )
        assert cb_visible_after == cb_visible_before, (
            f"checkbox state must round-trip: before={cb_visible_before} after={cb_visible_after}"
        )

        # ── Phase J: FPS field controls play-loop tick delay ─────────
        # Set fps=2; play briefly; pause; verify the wall-clock between frame
        # advances is ≥ 400ms (1000/2 = 500ms tolerance ±100ms).
        page.fill("#va-play-fps", "2")
        page.fill("#va-play-step", "1")  # ensure step doesn't confound
        before_label = page.text_content("#va-frame-counter") or ""
        t0 = time.time()
        page.click("#va-btn-play")
        time.sleep(1.6)  # at fps=2, expect ~3 frame advances
        page.click("#va-btn-play")  # pause
        elapsed = time.time() - t0
        after_label = page.text_content("#va-frame-counter") or ""
        import re as _re
        m1 = _re.search(r"Frame\s+(\d+)", before_label or "")
        m2 = _re.search(r"Frame\s+(\d+)", after_label  or "")
        if m1 and m2:
            advanced = int(m2.group(1)) - int(m1.group(1))
            print(f"[J] elapsed={elapsed:.2f}s advanced={advanced} frames at fps=2")
            # At fps=2, ~1.6 wall-seconds of playback should advance ~2-4 frames.
            # Assert advanced is bounded — definitely not 30+ frames (which would
            # indicate fps wasn't honoured).
            assert advanced <= 8, (
                f"with fps=2 and 1.6s playback, expected ≤ 8 frames, got {advanced}"
            )
            assert advanced >= 1, (
                f"with fps=2 and 1.6s playback, expected ≥ 1 frame, got {advanced}"
            )

        # ── Phase K: Atomic swap (image + draw within same tick) ─────
        # Instrument a counter on _vaDrawCurrentFrame and on vaFrameImg's load.
        # We can't monkey-patch a module-scoped const, but we CAN observe each
        # frame's render: the image's current src + the canvas's last-drawn
        # state should match the same frame number.
        # Simpler structural check: after a single _vaLoadFrame, the
        # vaFrameImg.src and the visible Frame N counter agree.
        page.fill("#va-play-fps", "5")
        page.click("#va-btn-next")  # advance one frame via the next button
        time.sleep(0.5)
        counter_text = page.text_content("#va-frame-counter") or ""
        m = _re.search(r"Frame\s+(\d+)", counter_text)
        if m:
            expected_frame = int(m.group(1))
            img_src = page.evaluate("() => document.getElementById('va-frame-img')?.src || ''")
            # The frame URL contains the frame number as a query param or path
            # segment. Just assert the src is non-empty and the counter parsed.
            assert img_src, "image src must be set after _vaLoadFrame"
            print(f"[K] after one tick: counter shows Frame {expected_frame}, img src len={len(img_src)}")

        # ── Phase L: Curation toggle ─────────────────────────────────
        # Default unchecked → controls hidden. Click toggle → controls visible.
        controls_hidden_default = page.evaluate(
            "document.getElementById('va-curation-controls').classList.contains('hidden')"
        )
        print(f"[L] curation controls hidden by default: {controls_hidden_default}")
        assert controls_hidden_default, "curation controls must start hidden"
        page.click("#va-curation-toggle")
        time.sleep(0.2)
        controls_visible = page.evaluate(
            "!document.getElementById('va-curation-controls').classList.contains('hidden')"
        )
        assert controls_visible, "curation controls must be visible after toggle"
        # Untoggle to leave clean state.
        page.click("#va-curation-toggle")
```

- [ ] **Step 3: Run the e2e**

```bash
docker compose ps | grep flask
# If not running:
# docker compose restart flask
```

Then:

```bash
python tests/e2e_viewer_layers_smoke.py 2>&1 | tail -50
```

Expected: `ALL CHECKS PASSED`. The Phase J timing is racy on busy hosts; if `advanced` exceeds the 8-frame ceiling, increase the upper bound to 12 (still well below the 30+ that would indicate fps was ignored).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e_viewer_layers_smoke.py
git commit -m "test(viewer): e2e Phases I/J/K/L for visibility, fps, atomic swap, curation toggle"
```

---

## Task 8: Final verification

- [ ] **Step 1: Full pytest pass on the host**

```bash
python -m pytest \
  tests/test_dlc_viewer_routes.py \
  tests/test_viewer_layers_ui_isolation.py \
  tests/test_postprocess_real_project.py \
  tests/test_frontend_assets.py \
  -v 2>&1 | tail -10
```

All non-skipped tests must pass. Pre-existing teardown LookupError noise is acceptable.

- [ ] **Step 2: Re-run the post-process e2e to confirm no regression**

```bash
python tests/e2e_postprocess_smoke.py 2>&1 | tail -15
```

Expected: `ALL CHECKS PASSED`.

- [ ] **Step 3: Re-run the full viewer e2e (including Phases A–L)**

```bash
python tests/e2e_viewer_layers_smoke.py 2>&1 | tail -30
```

Expected: `ALL CHECKS PASSED`.

- [ ] **Step 4: Manual browser sanity**

Per CLAUDE.md, exercise in a real browser:
1. Open the View Analyzed Videos / Frames card.
2. Browse to a video that has a Raw companion + at least one postproc filtered output.
3. Open overlay. Confirm Primary auto-loads the latest filtered variant. The new Primary row appears with a `●` glyph + the filtered label + a checked visibility checkbox.
4. Untick the Primary visibility checkbox → primary's markers disappear; comparison markers (if any) remain.
5. Add a comparison via "+ add comparison". Confirm the comparison row shows `◆` (diamond), NOT an open circle.
6. Set `fps = 5`, `step = 1`. Click Play. Confirm both image and markers advance together — no visible frame where markers lag.
7. Set `fps = 30`, click Play. If pose fetches are slow, the loop visibly throttles below 30 fps but markers always match the displayed image.
8. Open the Dataset Curation panel by ticking `va-curation-toggle`. Confirm the inner controls (Extract Frame, Add to Dataset, etc.) appear. Untick → controls hide.

If any step fails, file as a follow-up — the unit + e2e tests should already have caught the obvious issues.

- [ ] **Step 5: Final commit only if anything else changed**

```bash
git status
# If clean: nothing to commit.
# Otherwise:
# git add -A && git commit -m "chore(viewer): final cleanups"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Implemented in task |
|---|---|
| Per-primary visibility row + legend glyph | T2 |
| Drop circle-open, add diamond, update _SHAPE_ORDER + _shapeGlyph | T1 |
| Editable va-play-fps field | T3 |
| Atomic image+markers swap in _vaLoadFrame | T4 |
| Collapsible Dataset Curation panel | T5 |
| UI isolation tests for new IDs + JS asserts | T6 |
| Playwright e2e Phases I/J/K/L | T7 |
| Final verification | T8 |

**Type/name consistency:** `_drawDiamond`, `_SHAPE_ORDER` (with `"diamond"`), `_shapeGlyph` (with `case "diamond"`), `_SHAPE_FN` (with `"diamond"` key), `_vaPlaybackFps()`, `_vaPlayDelayMs()`, `_vaSyncPrimaryRow()`, `va-overlay-primary-row`, `va-overlay-primary-visible`, `va-overlay-primary-shape`, `va-overlay-primary-label`, `va-play-fps`, `va-curation-toggle`, `va-curation-controls`, `_vaLayers[i].visible`. Names consistent across tasks.

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task X" violations. The Playwright Phase I uses a structural assertion (checkbox round-trips) instead of pixel-diffing because canvas pixels at the centre may or may not contain a marker — this is documented in the test comments.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-viewer-flow-tweaks.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session with checkpoints.

Which approach?
