# Inline Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Inline Analysis" card that lets a user scrub a video, run N frames of DLC pose inference forward against a warm-in-memory PyTorch model held by a long-lived Celery task, and merge results into the canonical DLC `.h5` / `.csv` / `_meta.pickle` files.

**Architecture:** A new `dlc_inline_analysis` Flask blueprint owns six thin endpoints (session start/status/stop, range submit/status, video probe) that drive a long-lived Celery task `tasks.dlc_inline_session` over Redis lists/hashes. Activity (idle TTL) is bumped server-side only when a range is submitted; the worker times out after `ttl_seconds` of no range submissions, regardless of whether the card is open. No client-side heartbeat endpoint — that's the Jobs-page pattern, not relevant here. The task boots a single `PoseInferenceRunner` once via DLC's own `utils.get_pose_inference_runner`, then BLPOP-loops range requests, decoding only the requested frames through a thin `_RangeVideoIterator(VideoIterator)` subclass and merging predictions into the canonical files via atomic `os.replace`. The card embeds a copy-then-deferred-migration of `viewer.js`'s player/overlay/curation core, packaged as `makeAnalyzedFramePlayer({...})` — `viewer.js` is untouched in this PR.

**Tech Stack:** Flask blueprint, Celery (pytorch worker), Redis (FakeRedis in tests), pandas + DeepLabCut's `pose_estimation_pytorch.apis` (`utils.get_pose_inference_runner`, `VideoIterator`, `video_inference`), vanilla JS frontend (ES module sharing `state.js` + `components/file_browser.js`).

**Spec:** `docs/superpowers/specs/2026-05-20-inline-analysis-design.md`

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `src/dlc/inline_analysis.py` | Flask blueprint with 6 routes (`/session/start`, `/session/status`, `/session/stop`, `/range`, `/range/status`, `/video-info`). Computes `snap_key`, validates project type/engine by reading the active project's `config.yaml` directly (returns 400 for multi-animal or non-PyTorch), dispatches the warm-worker Celery task, serialises range requests via Redis lists. Activity TTL is bumped only on range submit. |
| `src/static/js/components/analyzed_frame_player.js` | `makeAnalyzedFramePlayer({prefix, frameUrlFn, poseUrlFn, onCsvSaved})` factory — copy-and-parameterise of `viewer.js`'s player/overlay/marker-adjustment/dataset-curation core. No consumer of `viewer.js` is touched. |
| `src/static/js/inline_analysis.js` | DOM controller — file picker (via canonical `makeFileBrowser`), analysis-params block, snapshot picker, warm-indicator polling, range submit/poll, mounts the new player factory. |
| `src/templates/partials/card_inline_analysis.html` | Card markup. |
| `tests/test_inline_analysis_routes.py` | HTTP-endpoint tests with FakeRedis + mocked Celery: 4xx validation, dispatch shape, status polling shape, `/session/start` returns 400 with descriptive error when config.yaml says multi-animal or TF. |
| `tests/test_inline_analysis_worker.py` | Pure-function tests for `_filter_skip_already_done`, `_RangeVideoIterator`, `_atomic_write_h5`, `_run_range` (with stubbed `video_inference`), session/control/result transitions, TTL exit. |
| `tests/test_inline_analysis_session_lifecycle.py` | Snapshot-change tear-down, idle TTL, control-key stop, concurrent-request serialisation. |
| `tests/test_analyzed_frame_player_factory.py` | Static-analysis: factory file exists, exports `makeAnalyzedFramePlayer`, `inline_analysis.js` imports it. Soft consumer-count check. |
| `tests/e2e_inline_analysis_smoke.py` | Frontend smoke (no GPU) with stubbed worker: file picker shows videos w/o h5, analyze label updates with scrub, result status renders, marker overlay re-fetches. |
| `tests/test_inline_analysis_gpu_smoke.py` | `@pytest.mark.gpu` — real warm-worker round-trip against `dlc_sandbox_project`. Caps `n_frames=50`, `batch_size=8`, `TTL=10s`. Asserts 50 new rows in h5, csv updated, `inline_analysis_snapshots` recorded in `_meta.pickle`, worker exits within TTL+5s, disk-delta < 10 MB. |

**Modified files:**

| Path | What changes |
|---|---|
| `src/templates/partials/card_dlc_project.html` | Insert `btn-open-inline-analysis` between `btn-open-analyze` and `btn-open-view-analyzed` (note: the existing `btn-open-postprocess` already sits below View-Analyzed, so the new button slots in above View-Analyzed, not below it — see §1 of the spec). |
| `src/templates/index.html` | `{% include "partials/card_inline_analysis.html" %}` next to the other card includes. |
| `src/static/js/main.js` | One `import './inline_analysis.js';` line, ordered after `viewer.js`. |
| `src/dlc/tasks.py` | Append `tasks.dlc_inline_session` Celery task plus helpers (`_blpop`, `_publish_status`, `_publish_result`, `_bump_activity`, `_control_says_stop`, `_idle_budget`, `_run_range`, `_RangeVideoIterator`, `_filter_skip_already_done`, `_atomic_write_h5`, `_atomic_write_csv`, `_update_meta_pickle`, `_resolve_h5_path`, `_preds_to_df`, `_read_pytorch_config`). |
| `src/app.py` | Register `dlc_inline_analysis` blueprint alongside `_dlc_inference_bp` (lines ~181–195). |
| `src/static/js/viewer.js` | **One change only:** add the DUPLICATION-NOTICE header comment at the top per §4 of the spec. No behavior changes. |
| `docs/policies/file-browser-component.md` | Add a one-paragraph "Related: analyzed-frame-player factory" pointer; broaden the doc's framing slightly to introduce shared frontend factories (lays the groundwork for the deferred rename to `shared-components.md`). |
| `tests/test_file_browser_policy.py` | Soft-register `inline_analysis.js` as a consumer of the file-browser factory. |

**Not modified (called out for clarity):**

- `src/static/js/viewer.js` core behavior — unchanged per §4 Option B.
- `src/templates/partials/card_viewer.html` — unchanged.
- Any TF-engine code paths.

---

## Conventions Used Below

- All commands run from repo root: `/home/sam/docker-images/deeplabcut-webapp-docker`.
- "Run the tests" means `python -m pytest <path> -v` on the host unless the step explicitly says "in the worker container" (anything that touches the real `deeplabcut.pose_estimation_pytorch.apis` import path).
- The host has `tables` only inside the flask/worker containers; tests that call `pd.read_hdf` on real data must run in the worker container. Pure-logic tests with monkeypatched DLC primitives run on the host fine.
- `tests/conftest.py` already provides `fake_redis`, `flask_test_client`, `dlc_sandbox_project`, `sandbox_config_path`. Reuse them — do not invent new ones.
- `pytest.ini` has `addopts = -v --tb=short -m "not gpu"`. The GPU smoke task uses `@pytest.mark.gpu`. Do **not** run it accidentally.
- Each step ends with a commit. Conventional-commit prefixes: `feat(dlc):`, `feat(static):`, `feat(templates):`, `test:`, `docs:`, `refactor:`.
- The branch `feat/inline-analysis` is already checked out. Do not push to remote unless the user asks.

---

## Phase Map (high-level)

| Phase | Theme | Independently shippable? | Risk surface |
|---|---|---|---|
| **0** | `analyzed_frame_player.js` factory — copy + parameterise viewer.js core; no consumer wiring | Yes (factory loads but is unused; static-analysis test passes) | None — viewer.js untouched, no runtime path changes |
| **1** | Backend blueprint + warm-worker Celery task + Redis IPC scaffolding (all tests mocked) | Yes (routes 4xx-validate; dispatch mocked; worker exits cleanly) | DLC internal API symbol existence (smoke check in P1); Redis key shape |
| **2** | `card_inline_analysis.html` + `inline_analysis.js` frontend wiring + open/close + entry-point button | Yes (browser smoke: open card, scrub, click Analyze on a wrong-type project and see the 400 surface in the status line) | DOM-id collision with viewer card; `viewer.js` regression via main.js load order |
| **3** | End-to-end smoke + opt-in GPU smoke + policy + static-analysis tests | Yes (whole feature passes) | GPU smoke disk-fill guard; warm-worker TTL race; multi-tab interaction |
| **4** | Tech-debt notes (header comments, docs broadening, follow-up PR titles) | Yes (docs-only) | None |

Rollback: every phase commit is independently revertable. Phases 1 and 2 each leave dead code if reverted alone (Phase 1's task with no UI; Phase 2's UI hitting nonexistent routes) — revert in reverse order if reverting > one phase.

---

# PHASE 0 — Player Factory (copy-then-deferred-migration)

**Phase goal:** Land `analyzed_frame_player.js` as a parameterised factory. No consumer wires it up yet. `viewer.js` keeps its current behavior unchanged. The static-analysis test asserts the factory exists and exports `makeAnalyzedFramePlayer`.

**Risk surface:** None — viewer.js is untouched, nothing imports the new factory yet.

**Rollback:** `git revert <Phase 0 commits>` removes the unused factory file and the (single) header comment in viewer.js.

---

## Task 0.1: Skeleton factory + static-analysis test

**Files:**
- Create: `src/static/js/components/analyzed_frame_player.js`
- Create: `tests/test_analyzed_frame_player_factory.py`

- [ ] **Step 1: Write the failing static-analysis test**

Create `tests/test_analyzed_frame_player_factory.py`:

```python
"""Enforce the analyzed-frame-player factory contract.

- The canonical factory lives at src/static/js/components/analyzed_frame_player.js.
- It exports `makeAnalyzedFramePlayer` (named export).
- inline_analysis.js (once it exists) imports it.
- Soft consumer-count check: until viewer.js migrates, only 1 consumer.

Parallel to tests/test_file_browser_policy.py.
"""
import re
from pathlib import Path

import pytest

ROOT       = Path(__file__).parent.parent
AFP_PATH   = ROOT / "src" / "static" / "js" / "components" / "analyzed_frame_player.js"
INLINE     = ROOT / "src" / "static" / "js" / "inline_analysis.js"

_IMPORT_RE = re.compile(
    r"import\s*\{[^}]*\bmakeAnalyzedFramePlayer\b[^}]*\}\s*from\s*"
    r"[\"']\./components/analyzed_frame_player\.js[\"']"
)


def test_canonical_factory_exists():
    assert AFP_PATH.is_file(), f"missing factory at {AFP_PATH}"


def test_factory_exports_make_analyzed_frame_player():
    src = AFP_PATH.read_text()
    assert re.search(r"export\s+function\s+makeAnalyzedFramePlayer\b", src), (
        "analyzed_frame_player.js must export `makeAnalyzedFramePlayer` "
        "as a named export"
    )


def test_consumer_count_soft():
    """Until viewer.js migrates, exactly one consumer (inline_analysis.js).

    This test passes either way today; it documents intent and will be
    tightened when the migration lands.
    """
    if not INLINE.is_file():
        pytest.skip("inline_analysis.js not yet present (Phase 2)")
    src = INLINE.read_text()
    assert _IMPORT_RE.search(src), (
        "inline_analysis.js must import makeAnalyzedFramePlayer from "
        "./components/analyzed_frame_player.js"
    )
```

- [ ] **Step 2: Run; confirm RED**

Run: `python -m pytest tests/test_analyzed_frame_player_factory.py -v`
Expected: `test_canonical_factory_exists` FAILS (file does not exist).

- [ ] **Step 3: Create the skeleton factory**

Create `src/static/js/components/analyzed_frame_player.js` with the duplication notice + minimum-viable export:

```javascript
// src/static/js/components/analyzed_frame_player.js
//
// ⚠ DUPLICATION NOTICE
//   This file currently maintains a copy of player/overlay/marker-adjustment/
//   dataset-curation logic that ALSO lives in ../viewer.js. Bug fixes in one
//   must be manually mirrored to the other until viewer.js is migrated to
//   this factory.
//
//   See docs/superpowers/specs/2026-05-20-inline-analysis-design.md
//   (§4 "Player Code Reuse" and "Known tech debt") for the planned migration.
//   Follow-up PR title prefix: `refactor(viewer): migrate to analyzed_frame_player factory`.
//
// USAGE:
//   import { makeAnalyzedFramePlayer } from "./components/analyzed_frame_player.js";
//   const player = makeAnalyzedFramePlayer({
//     prefix: "ia",                            // DOM id prefix (ia-frame-img, ia-overlay-canvas, …)
//     frameUrlFn: (n) => `/annotate/frame?path=${path}&frame=${n}`,
//     poseUrlFn:  (layer, n) => `/dlc/viewer/h5-pose-window?h5=${layer.path}&start=${n}&n=1`,
//     onCsvSaved: () => { /* card refresh hook */ },
//   });
//   player.loadVideo(videoPath, fps, nFrames);
//   player.reloadH5();        // after each inline range completes
//   player.destroy();         // on card close

export function makeAnalyzedFramePlayer(options) {
  // Phase 0: skeleton only — the real body lands in Task 0.2.
  // Returning the documented API surface keeps any accidental early consumer
  // from blowing up at construction time.
  return {
    loadVideo: () => {},
    reloadH5: () => {},
    getCurrentFrame: () => 0,
    setCurrentFrame: () => {},
    destroy: () => {},
  };
}
```

- [ ] **Step 4: Run; confirm two tests pass, one skips**

Run: `python -m pytest tests/test_analyzed_frame_player_factory.py -v`
Expected: `test_canonical_factory_exists` PASS, `test_factory_exports_make_analyzed_frame_player` PASS, `test_consumer_count_soft` SKIP (`inline_analysis.js not yet present`).

- [ ] **Step 5: Commit**

```bash
git add src/static/js/components/analyzed_frame_player.js tests/test_analyzed_frame_player_factory.py
git commit -m "$(cat <<'EOF'
feat(static): scaffold analyzed_frame_player factory + policy test

Empty-bodied factory at src/static/js/components/analyzed_frame_player.js
with the duplication-notice header. No consumer is wired yet — Phase 0 just
locks down the contract so Phase 2 can import against it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 0.2: Port viewer.js core into the factory body

**Files:**
- Modify: `src/static/js/components/analyzed_frame_player.js` (replace skeleton with parameterised port of viewer.js's player core)

**Context:** This is the largest single change in the plan. The strategy is **copy verbatim, then parameterise**, not "refactor while copying." Keep the same variable names, same control flow, same comments — only swap the DOM-id constants for `prefix`-templated lookups and route URL builders for the supplied `frameUrlFn` / `poseUrlFn`.

- [ ] **Step 1: Read the existing viewer.js core**

Read `src/static/js/viewer.js` in three passes to identify the regions to port:

1. **Lines 1–90** — IIFE prelude, DOM id grabs, module-scoped state (`_vaMode`, `_vaCurrentFrame`, `_vaFrameCount`, `_vaFps`, `_vaLayers`, `_vaOverlayEnabled`, marker-edit + curation hooks). Everything from `const vaCard = …` through `let _vaCurationFrameHook = null;`.
2. **Lines 90–~1750** — frame loading + pose-window prefetch + canvas drawing + hover + drag-to-edit + bp chips + threshold sliders + primary/comparison overlay logic + marker-adjustment banner + dataset-curation panel + tab-switching.
3. **Lines ~1768–end** — the open/close handlers (these stay in `viewer.js`; do **not** port them — the consumer card owns open/close).

Identify the boundary: anything inside the IIFE that **only** reacts to player state belongs in the factory; anything that reacts to "the View-Analyzed card opened" belongs to the consumer. The card-level boundary already exists in viewer.js (lines 1768+); the rest is portable.

- [ ] **Step 2: Replace the factory body**

Open `src/static/js/components/analyzed_frame_player.js`. Keep the file header (duplication notice + JSDoc). Replace the placeholder `return {…}` body with the ported core:

```javascript
import { state } from '../state.js';
import { makeFileBrowser } from './file_browser.js';

export function makeAnalyzedFramePlayer(options) {
  const {
    prefix,
    frameUrlFn,
    poseUrlFn,
    onCsvSaved,
  } = options;

  // ── DOM-id helper ─────────────────────────────────────────────────────
  const $id = (suffix) => document.getElementById(`${prefix}-${suffix}`);

  // ── DOM grabs (parameterised by prefix) ───────────────────────────────
  // Mirror the constants from viewer.js lines 5–37 but resolve via $id():
  //   viewer.js                     factory (prefix="ia")
  //   va-frame-img                  ia-frame-img
  //   va-overlay-canvas             ia-overlay-canvas
  //   va-pose-thresh                ia-pose-thresh
  //   va-overlay-primary-select     ia-overlay-primary-select
  //   …etc.
  const frameImg     = $id("frame-img");
  const frameSpinner = $id("frame-spinner");
  const overlayCanvas = $id("overlay-canvas");          // added in Task 2.x partial
  const btnPlay       = $id("btn-play");
  const btnPrev       = $id("btn-prev");
  const btnNext       = $id("btn-next");
  const skipN         = $id("skip-n");
  const frameCounter  = $id("frame-counter");
  const seek          = $id("seek");
  const zoomInput     = $id("zoom");
  const zoomVal       = $id("zoom-val");
  const overlayToggle = $id("overlay-toggle");
  const overlayPrimarySelect = $id("overlay-primary-select");
  const overlayAddCompare    = $id("overlay-add-compare");
  const overlayCompareList   = $id("overlay-compare-list");
  const overlayThreshold     = $id("overlay-threshold");
  const overlayMarkerSize    = $id("overlay-marker-size");
  const markerEditBanner     = $id("marker-edit-banner");
  // (Add all other id-mirrored grabs from viewer.js here.)

  // ── Module-private state (mirror viewer.js lines 39–90) ───────────────
  let _currentFrame = 0;
  let _frameCount   = 0;
  let _fps          = 30;
  let _videoPath    = null;
  let _frameBusy    = false;
  let _playTimer    = null;
  let _seekDragging = false;
  let _zoom         = 100;
  // overlay
  let _overlayEnabled = false;
  let _allBodyParts   = [];
  let _selectedBp     = null;
  const _hiddenParts  = new Set();
  let _markerSize     = 6;
  const _layers       = [];   // [0] = primary, [1+] = comparisons
  // pose cache
  const _POSE_WINDOW = 30;
  let   _prefetchCtrl = null;
  // hooks set by attach methods (curation, metadata)
  let _curationFrameHook = null;
  let _metadataFrameHook = null;
  // edited-but-unsaved marker bookkeeping (mirrors viewer.js)
  const _editedFrames = new Set();
  // listener teardown registry (so destroy() removes them)
  const _teardown = [];
  const _on = (el, ev, fn, opts) => {
    if (!el) return;
    el.addEventListener(ev, fn, opts);
    _teardown.push(() => el.removeEventListener(ev, fn, opts));
  };

  // ── Frame loading (port viewer.js _vaLoadFrame, _vaLoadContent regions) ──
  async function _loadFrame(n) {
    if (_frameBusy) return;
    _frameBusy = true;
    frameSpinner?.classList.remove("hidden");
    try {
      const url = frameUrlFn(n);
      // … paste the viewer.js frame-load body, with `frameImg`/`frameSpinner`/etc. in place of va-prefixed names.
      // After draw:
      await new Promise(requestAnimationFrame);     // paint barrier — same as viewer Task 4
      _prefetchPoseWindow(n + 1);
      if (_curationFrameHook) _curationFrameHook(n);
      if (_metadataFrameHook) _metadataFrameHook(n);
    } finally {
      _frameBusy = false;
      frameSpinner?.classList.add("hidden");
    }
  }

  // ── Pose window prefetch (port viewer.js _vaPrefetchPoseWindow) ───────
  function _prefetchPoseWindow(startFrame) {
    if (_prefetchCtrl) _prefetchCtrl.abort();
    _prefetchCtrl = new AbortController();
    _layers.forEach(layer => {
      const url = poseUrlFn(layer, startFrame);
      // … fetch-and-cache into layer.posesCache (mirror viewer.js).
    });
  }

  // ── Overlay drawing (port viewer.js _vaDrawCurrentFrame + bp logic) ───
  function _drawCurrentFrame() {
    // … mirror viewer.js, drawing on `overlayCanvas` instead of va-overlay-canvas.
  }

  // ── Public API ────────────────────────────────────────────────────────
  function loadVideo(videoPath, fps, nFrames) {
    _videoPath = videoPath;
    _fps = fps;
    _frameCount = nFrames;
    _currentFrame = 0;
    if (seek) { seek.min = 0; seek.max = Math.max(0, nFrames - 1); seek.value = 0; }
    _loadFrame(0);
  }

  function reloadH5() {
    // Drop pose caches and re-prefetch the visible window.
    _layers.forEach(l => l.posesCache.clear());
    _prefetchPoseWindow(_currentFrame);
    _drawCurrentFrame();
  }

  function getCurrentFrame() { return _currentFrame; }
  function setCurrentFrame(n) {
    _currentFrame = Math.max(0, Math.min(n, _frameCount - 1));
    _loadFrame(_currentFrame);
  }

  function destroy() {
    if (_playTimer) { clearTimeout(_playTimer); _playTimer = null; }
    if (_prefetchCtrl) { _prefetchCtrl.abort(); _prefetchCtrl = null; }
    _teardown.forEach(fn => { try { fn(); } catch (e) {} });
    _teardown.length = 0;
    _layers.length = 0;
    _editedFrames.clear();
  }

  // ── Wire DOM listeners (use _on so destroy() cleans them up) ──────────
  _on(btnPlay,  "click",  /* play/pause */ () => { /* … port viewer.js play loop, using _vaPlayStep equivalent */ });
  _on(btnPrev,  "click",  () => setCurrentFrame(_currentFrame - 1));
  _on(btnNext,  "click",  () => setCurrentFrame(_currentFrame + 1));
  _on(seek,     "input",  () => { _seekDragging = true; });
  _on(seek,     "change", () => { _seekDragging = false; setCurrentFrame(parseInt(seek.value, 10)); });
  _on(zoomInput, "input", () => { _zoom = parseInt(zoomInput.value, 10); zoomVal.textContent = `${_zoom} %`; /* apply transform */ });
  _on(overlayToggle, "change", () => { _overlayEnabled = !!overlayToggle.checked; _drawCurrentFrame(); });
  // … all other listeners from viewer.js, gated through _on().

  // Expose hook setters (consumer sets these to integrate with its own panels).
  function setCurationFrameHook(fn)  { _curationFrameHook  = fn; }
  function setMetadataFrameHook(fn)  { _metadataFrameHook  = fn; }

  return {
    loadVideo,
    reloadH5,
    getCurrentFrame,
    setCurrentFrame,
    destroy,
    setCurationFrameHook,
    setMetadataFrameHook,
  };
}
```

**Porting rules (apply during the copy):**

- Do **not** alias `_va*` → `_*` for cosmetic reasons — strip the `_va` prefix to `_` consistently, since these are now module-private inside a function scope, not IIFE-globals.
- Replace every `document.getElementById("va-…")` with `$id("…")` so the prefix is the only knob the consumer turns.
- Replace every hardcoded `/dlc/viewer/h5-…` / `/annotate/frame…` URL build with a call to `frameUrlFn` / `poseUrlFn`.
- Every `addEventListener` becomes `_on(el, ev, fn)` so `destroy()` removes it on card close (this is a behavior gain over `viewer.js`, which never tears down — fine because viewer.js was IIFE-scoped to a singleton card).
- Imports for `state` and `makeFileBrowser` use **relative paths from `components/`** (`'../state.js'`, `'./file_browser.js'`).
- Do NOT duplicate the file-picker logic — the consumer uses `makeFileBrowser` directly. The factory only owns frame + overlay + marker-edit + curation.

- [ ] **Step 3: Re-run the factory contract test**

Run: `python -m pytest tests/test_analyzed_frame_player_factory.py -v`
Expected: `test_canonical_factory_exists` PASS, `test_factory_exports_make_analyzed_frame_player` PASS, `test_consumer_count_soft` SKIP.

- [ ] **Step 4: Add the DUPLICATION-NOTICE header to viewer.js**

Open `src/static/js/viewer.js`. The file currently starts with `"use strict";`. **Above** the `"use strict";` directive, insert the header comment (so the directive remains the first executable statement, which JS engines require):

```javascript
// ⚠ DUPLICATION NOTICE
//   This file and ./components/analyzed_frame_player.js currently maintain
//   duplicate player/overlay/curation logic. Bug fixes in one must be
//   manually mirrored to the other until viewer.js is migrated to the
//   factory.
//
//   See docs/superpowers/specs/2026-05-20-inline-analysis-design.md
//   (§4 and "Known tech debt") for the planned migration.
//   Follow-up PR title prefix:
//     refactor(viewer): migrate to analyzed_frame_player factory
"use strict";
import { state } from './state.js';
import { makeFileBrowser } from './components/file_browser.js';
```

This is a **comment-only change** to viewer.js — no behavior change.

- [ ] **Step 5: Smoke-load the page to confirm viewer.js still parses**

Restart flask (if running) and open `http://localhost:5000/?token=deeplabcut`. The View Analyzed card must still open and load a frame — same as before this PR.

If the dev server is not running locally, skip the manual step but verify the file parses by importing it in a headless Playwright check:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    print("errors:", errs)
    b.close()
```

Expected: `errors: []`.

- [ ] **Step 6: Commit**

```bash
git add src/static/js/components/analyzed_frame_player.js src/static/js/viewer.js
git commit -m "$(cat <<'EOF'
feat(static): port viewer.js player core to analyzed_frame_player factory

Ports the player/overlay/marker-adjustment/dataset-curation regions of
viewer.js into a parameterised makeAnalyzedFramePlayer({prefix, frameUrlFn,
poseUrlFn, onCsvSaved}) factory. viewer.js gets only the DUPLICATION-NOTICE
header — no behavior change. Per §4 of the spec, viewer.js stays in place
and the new card will mount the factory; viewer.js's migration is a
follow-up PR.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 0 acceptance

- `tests/test_analyzed_frame_player_factory.py` — 2 PASS, 1 SKIP (consumer not yet present)
- `src/static/js/viewer.js` — opens and plays a video in the browser exactly as before
- `git diff main -- src/static/js/viewer.js` — only the header comment changes; zero behavior changes
- No new runtime asset is shipped to users yet (the factory is loaded only when Phase 2's `inline_analysis.js` imports it)

---

# PHASE 1 — Backend blueprint + warm-worker Celery task

**Phase goal:** All HTTP endpoints under `/dlc/project/inline-analysis/*` exist and validate input. The Celery task `tasks.dlc_inline_session` exists, can be dispatched, can be unit-tested as a plain function with DLC primitives mocked. No GPU code runs. No frontend wiring yet.

**Risk surface (read carefully):**

1. **DLC internal API existence.** The spec depends on `deeplabcut.pose_estimation_pytorch.apis.utils.get_pose_inference_runner`, `VideoIterator`, and `video_inference`. Task 1.0 below explicitly probes these symbols inside the worker container before any worker logic is written. If they aren't importable from that path, stop and surface the divergence to the parent session — do not invent a workaround.
2. **`_RangeVideoIterator` correctness.** Must yield frames at *non-contiguous* indices (skip-already-done returns a sparse list). Test it with `[3, 5, 9]` and verify the seek order.
3. **Atomic write + csv-lagging-h5.** `_atomic_write_h5` writes via `.tmp + os.replace`. If the worker crashes between h5 and csv writes, the csv is stale relative to h5. We document this in the failure-modes table (§5 of the spec) and accept it — the spec calls it out as a known recovery path ("User regenerates csv via DLC").
4. **TTL / BLPOP idle budget.** `_idle_budget(snap_key, ttl)` must compute `max(0, ttl - (now - last_activity))` on every loop iteration; the BLPOP timeout shortens as the deadline approaches. Test that the worker exits within TTL+1s with no work submitted.
5. **`acks_late=False` choice.** The spec uses `acks_late=False` so the warm-worker task doesn't get redelivered on Celery shutdown (that would re-warm against a vanished Redis queue). Document this in the task docstring.

**Rollback:** Revert the four commits (helpers → task → routes → blueprint registration). The Phase 0 factory file is untouched.

---

## Task 1.0: DLC import probe (smoke check, container only)

**Files:** none (probe-only; goes in a throwaway script)

- [ ] **Step 1: Run the probe inside the worker container**

```bash
docker exec $(docker ps --filter "name=worker" --filter "name=^/.*worker$" -q | head -1) \
  python -c "
from deeplabcut.pose_estimation_pytorch.apis import utils
from deeplabcut.pose_estimation_pytorch.apis import VideoIterator, video_inference
print('get_pose_inference_runner :', hasattr(utils, 'get_pose_inference_runner'))
print('VideoIterator             :', VideoIterator)
print('video_inference           :', video_inference)
import inspect
sig = inspect.signature(utils.get_pose_inference_runner)
print('runner signature          :', sig)
"
```

Expected: all three resolve. The `runner signature` line must include at minimum `model_config`, `snapshot_path`, `batch_size`, `device` (names may differ slightly — record the actual names).

- [ ] **Step 2: Decision point**

If any symbol is missing or the signature names differ materially:

- **Stop. Do not proceed with Phase 1.**
- Surface the divergence to the parent session in your phase summary. Specifically: "DLC version X.Y.Z exposes `<actual>` instead of `<spec>` — spec needs to be amended before the worker is written."

If everything resolves: proceed.

- [ ] **Step 3: Record the actual signature for downstream tasks**

Note the exact `runner.scorer_name`, `runner.bodyparts` attribute names by inspecting the returned runner once instantiated (no model load needed — just `inspect.signature` and a `dir()`). Subsequent tasks will need them.

(No commit — this is a discovery step.)

---

## Task 1.1: Pure helpers — `_filter_skip_already_done`, `_RangeVideoIterator`, `_atomic_write_*`, `_preds_to_df`, `_resolve_h5_path`, `_update_meta_pickle`

**Files:**
- Modify: `src/dlc/tasks.py` (append at end)
- Create: `tests/test_inline_analysis_worker.py`

- [ ] **Step 1: Write failing tests for the pure helpers**

Create `tests/test_inline_analysis_worker.py`:

```python
"""Unit tests for tasks.dlc_inline_session and its pure helpers.

DLC + GPU are fully mocked — these tests run on the host without CUDA.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import tasks as dlc_tasks


# ── _filter_skip_already_done ─────────────────────────────────────────────

class TestFilterSkipAlreadyDone:
    def test_empty_existing_returns_all_target(self):
        result = dlc_tasks._filter_skip_already_done(list(range(5)), existing_df=None)
        assert result == [0, 1, 2, 3, 4]

    def test_target_subset_of_existing_returns_empty(self):
        df = _df_with_index([0, 1, 2, 3, 4])
        assert dlc_tasks._filter_skip_already_done([1, 2, 3], df) == []

    def test_nan_all_rows_are_re_analyzed(self):
        df = _df_with_index([0, 1, 2], all_nan_rows={1})
        result = dlc_tasks._filter_skip_already_done([0, 1, 2], df)
        assert 1 in result
        assert 0 not in result
        assert 2 not in result

    def test_non_contiguous_target_preserves_order(self):
        df = _df_with_index([2, 4])
        result = dlc_tasks._filter_skip_already_done([1, 2, 3, 4, 5], df)
        assert result == [1, 3, 5]


def _df_with_index(frames, all_nan_rows=None):
    all_nan_rows = all_nan_rows or set()
    cols = pd.MultiIndex.from_tuples(
        [("scorer", "nose", "x"), ("scorer", "nose", "y"), ("scorer", "nose", "likelihood")],
        names=["scorer", "bodyparts", "coords"],
    )
    data = np.ones((len(frames), 3))
    for i, f in enumerate(frames):
        if f in all_nan_rows:
            data[i, :] = np.nan
    return pd.DataFrame(data, index=frames, columns=cols)


# ── _RangeVideoIterator ───────────────────────────────────────────────────

class TestRangeVideoIterator:
    def test_yields_only_requested_indices(self, tmp_path):
        # Stub the parent VideoIterator: capture set_to_frame calls, return a fake frame each read.
        video_path = tmp_path / "fake.mp4"
        video_path.write_bytes(b"")
        seeks = []
        reads = 0

        class _ParentStub:
            def __init__(self, *a, **kw): pass
            def set_to_frame(self, n): seeks.append(n)
            def read_frame(self):
                nonlocal reads
                reads += 1
                return np.zeros((4, 4, 3), dtype=np.uint8)
            def reset(self): pass

        with patch.object(dlc_tasks, "VideoIterator", _ParentStub):
            it = dlc_tasks._RangeVideoIterator(str(video_path), indices=[3, 5, 9])
            collected = [f for f in it]
        assert seeks == [3, 5, 9]
        assert reads == 3
        assert len(collected) == 3

    def test_non_contiguous_skip_list_preserves_order(self, tmp_path):
        video_path = tmp_path / "fake.mp4"
        video_path.write_bytes(b"")
        seeks = []

        class _ParentStub:
            def __init__(self, *a, **kw): pass
            def set_to_frame(self, n): seeks.append(n)
            def read_frame(self): return np.zeros((1, 1, 3), dtype=np.uint8)
            def reset(self): pass

        with patch.object(dlc_tasks, "VideoIterator", _ParentStub):
            list(dlc_tasks._RangeVideoIterator(str(video_path), indices=[100, 7, 42]))
        assert seeks == [100, 7, 42], "iterator must preserve caller-supplied order"


# ── _atomic_write_h5 + _atomic_write_csv ──────────────────────────────────

class TestAtomicWrite:
    def test_atomic_write_h5_uses_temp_then_replace(self, tmp_path):
        path = tmp_path / "out.h5"
        df = _df_with_index([0, 1, 2])
        dlc_tasks._atomic_write_h5(path, df)
        assert path.is_file()
        # No leftover .tmp
        assert not (tmp_path / "out.h5.tmp").exists()

    def test_atomic_write_h5_failure_leaves_original_intact(self, tmp_path):
        path = tmp_path / "out.h5"
        df = _df_with_index([0, 1, 2])
        dlc_tasks._atomic_write_h5(path, df)
        original = path.read_bytes()

        with patch("pandas.DataFrame.to_hdf", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                dlc_tasks._atomic_write_h5(path, df)
        assert path.read_bytes() == original, "canonical file must be untouched on failure"

    def test_atomic_write_csv_round_trip(self, tmp_path):
        path = tmp_path / "out.csv"
        df = _df_with_index([0, 1, 2])
        dlc_tasks._atomic_write_csv(path, df)
        assert path.is_file()


# ── _update_meta_pickle ───────────────────────────────────────────────────

class TestMetaPickleUpdate:
    def test_records_snapshot_in_inline_analysis_snapshots_set(self, tmp_path):
        meta_path = tmp_path / "video_meta.pickle"
        with open(meta_path, "wb") as f:
            pickle.dump({"existing_field": 1}, f)
        df = _df_with_index([0, 1])
        dlc_tasks._update_meta_pickle(meta_path, df, snapshot="snapshot-200000.pt")
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)
        assert meta["existing_field"] == 1
        assert "inline_analysis_snapshots" in meta
        assert "snapshot-200000.pt" in meta["inline_analysis_snapshots"]

    def test_creates_meta_when_missing(self, tmp_path):
        meta_path = tmp_path / "video_meta.pickle"
        df = _df_with_index([0])
        dlc_tasks._update_meta_pickle(meta_path, df, snapshot="snap.pt")
        assert meta_path.is_file()
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)
        assert "snap.pt" in meta["inline_analysis_snapshots"]


# ── _resolve_h5_path ──────────────────────────────────────────────────────

class TestResolveH5Path:
    def test_companion_path_uses_scorer_name(self):
        result = dlc_tasks._resolve_h5_path(
            "/data/videos/m3-cam1.mp4", scorer_name="DLC_resnet50_DREADD-AlishuffleN_snapshot-200000",
        )
        assert str(result) == (
            "/data/videos/m3-cam1DLC_resnet50_DREADD-AlishuffleN_snapshot-200000.h5"
        )

    def test_works_with_arbitrary_extension(self):
        result = dlc_tasks._resolve_h5_path("/x/y/video.avi", scorer_name="SCORER")
        assert result.name == "videoSCORER.h5"
```

- [ ] **Step 2: Run; confirm RED**

Run: `python -m pytest tests/test_inline_analysis_worker.py -v`
Expected: ImportError / AttributeError on `dlc_tasks._filter_skip_already_done` etc.

- [ ] **Step 3: Add the pure helpers to `src/dlc/tasks.py`**

Append to the bottom of `src/dlc/tasks.py`:

```python
# ── Inline Analysis: helpers ──────────────────────────────────────────────
# Lives at the bottom of tasks.py alongside dlc_inline_session.
# See docs/superpowers/specs/2026-05-20-inline-analysis-design.md.

import json as _ia_json
import os as _ia_os
import pickle as _ia_pickle
import time as _ia_time
from pathlib import Path as _IAPath

# Lazy DLC imports — kept at module level inside the worker container only.
# At test-time on the host, these are imported by the worker function itself
# (so tasks.py keeps loading cleanly even without DLC installed).
try:
    from deeplabcut.pose_estimation_pytorch.apis import (
        VideoIterator as _DLC_VideoIterator,
        video_inference as _dlc_video_inference,
    )
    from deeplabcut.pose_estimation_pytorch.apis import utils as _dlc_apis_utils
    VideoIterator = _DLC_VideoIterator           # module-level alias so tests can patch
    video_inference = _dlc_video_inference
except ImportError:
    VideoIterator = None                          # tests monkeypatch this
    video_inference = None
    _dlc_apis_utils = None


def _filter_skip_already_done(target_frames, existing_df):
    """Return the subset of target_frames that need re-analysis.

    A frame needs re-analysis if it's missing from existing_df or if every
    value in its row is NaN (matches DLC's own dynamic-cropping semantics).
    """
    if existing_df is None:
        return list(target_frames)
    have = existing_df.index
    return [
        f for f in target_frames
        if f not in have or existing_df.loc[f].isna().all()
    ]


class _RangeVideoIterator:
    """Iterate over a video at *non-contiguous* frame indices.

    Wraps DLC's VideoIterator so successive __next__ calls jump to the
    next requested index via set_to_frame + read_frame. Order is preserved
    from the caller-supplied indices list.
    """
    def __init__(self, video_path, indices):
        if VideoIterator is None:
            raise RuntimeError("deeplabcut not installed — VideoIterator unavailable")
        self._inner = VideoIterator(video_path)
        self._indices = list(indices)
        self._pos = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._pos >= len(self._indices):
            raise StopIteration
        idx = self._indices[self._pos]
        self._pos += 1
        self._inner.set_to_frame(idx)
        return self._inner.read_frame()


def _atomic_write_h5(path, df):
    """Write df to <path> atomically via .tmp + os.replace."""
    path = _IAPath(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_hdf(str(tmp), key="df_with_missing", mode="w", format="table")
    _ia_os.replace(str(tmp), str(path))


def _atomic_write_csv(path, df):
    """Write df.to_csv(path) atomically via .tmp + os.replace."""
    path = _IAPath(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(str(tmp))
    _ia_os.replace(str(tmp), str(path))


def _resolve_h5_path(video_path, scorer_name):
    """Compute the canonical companion .h5 path for (video, scorer)."""
    p = _IAPath(video_path)
    return p.with_name(p.stem + scorer_name + ".h5")


def _resolve_meta_path(h5_path):
    """Map a DLC analyzed .h5 to its sibling _meta.pickle path."""
    h5_path = _IAPath(h5_path)
    return h5_path.with_name(h5_path.stem + "_meta.pickle")


def _update_meta_pickle(meta_path, df, snapshot):
    """Write/update meta.pickle, recording the contributing snapshot.

    Adds the snapshot name to `inline_analysis_snapshots: set[str]` (created
    if missing). Older DLC tools ignore unknown fields.
    """
    meta_path = _IAPath(meta_path)
    if meta_path.is_file():
        try:
            with open(str(meta_path), "rb") as f:
                meta = _ia_pickle.load(f)
        except (OSError, _ia_pickle.UnpicklingError):
            meta = {}
    else:
        meta = {}
    snaps = meta.get("inline_analysis_snapshots")
    if not isinstance(snaps, set):
        snaps = set(snaps or ())
    snaps.add(snapshot)
    meta["inline_analysis_snapshots"] = snaps
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    with open(str(tmp), "wb") as f:
        _ia_pickle.dump(meta, f)
    _ia_os.replace(str(tmp), str(meta_path))


def _preds_to_df(predictions, frame_indices, bodyparts, scorer_name):
    """Convert a list of per-frame predictions into a DLC MultiIndex DataFrame.

    `predictions`: list[dict[bodypart -> (x, y, likelihood)]] in the same
    order as `frame_indices`.
    """
    cols = pd.MultiIndex.from_product(
        [[scorer_name], bodyparts, ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    rows = []
    for pred in predictions:
        row = []
        for bp in bodyparts:
            xyl = pred.get(bp, (float("nan"), float("nan"), 0.0))
            row.extend(xyl)
        rows.append(row)
    return pd.DataFrame(rows, index=list(frame_indices), columns=cols)


def _read_pytorch_config(config_path, shuffle):
    """Locate the pytorch_config.yaml for the given DLC project + shuffle.

    Returns the loaded YAML dict. Worker passes this to
    utils.get_pose_inference_runner.
    """
    import yaml as _yaml
    proj = _IAPath(config_path).parent
    # DLC's PyTorch shuffles live under dlc-models-pytorch/iteration-N/...-shuffle<N>/train/
    # Locate by glob since iteration index varies.
    matches = list(proj.glob(
        f"dlc-models-pytorch/iteration-*/" f"*shuffle{shuffle}/train/pytorch_config.yaml"
    ))
    if not matches:
        raise FileNotFoundError(
            f"No pytorch_config.yaml for shuffle {shuffle} under {proj}"
        )
    # Newest by mtime wins (handles re-trained shuffles).
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    with open(str(matches[0])) as f:
        return _yaml.safe_load(f)
```

Note: `pandas as pd` is already imported elsewhere in `tasks.py` — verify with `grep -n "^import pandas\|^from pandas" src/dlc/tasks.py` before this step and add the import only if it's missing.

- [ ] **Step 4: Run; confirm GREEN**

Run: `python -m pytest tests/test_inline_analysis_worker.py -v`
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/tasks.py tests/test_inline_analysis_worker.py
git commit -m "$(cat <<'EOF'
feat(dlc): inline-analysis pure helpers (skip-already-done, atomic write, meta)

Pure-logic helpers added to src/dlc/tasks.py with full unit coverage:
_filter_skip_already_done, _RangeVideoIterator, _atomic_write_h5/_csv,
_update_meta_pickle, _resolve_h5_path, _resolve_meta_path, _preds_to_df,
_read_pytorch_config. No Celery task yet, no Flask routes yet, no DLC
imports executed at import time.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.2: Worker loop helpers — `_blpop`, `_publish_status`, `_publish_result`, `_bump_activity`, `_control_says_stop`, `_idle_budget`, `_run_range`

**Files:**
- Modify: `src/dlc/tasks.py`
- Modify: `tests/test_inline_analysis_worker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inline_analysis_worker.py`:

```python
# ── Session-lifecycle helpers (Redis-backed) ──────────────────────────────

@pytest.fixture
def fake_redis():
    """Reuses the project-wide FakeRedis from conftest."""
    from tests.conftest import fake_redis as _ff
    return _ff


class TestSessionHelpers:
    def test_publish_status_sets_hash_fields(self, fake_redis):
        dlc_tasks._publish_status(
            fake_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        h = fake_redis._hstore[f"inline:session:u1:k1"]
        assert h["status"] == "ready"
        assert h["project"] == "proj"
        assert h["snapshot_path"] == "snap.pt"
        assert "last_activity" in h

    def test_publish_result_sets_done(self, fake_redis):
        dlc_tasks._publish_result(
            fake_redis, req_id="r1",
            status="done", n_analyzed=42, n_skipped=8,
        )
        h = fake_redis._hstore["inline:result:r1"]
        assert h["status"] == "done"
        assert int(h["n_analyzed"]) == 42
        assert int(h["n_skipped"]) == 8

    def test_publish_result_truncates_long_error(self, fake_redis):
        dlc_tasks._publish_result(
            fake_redis, req_id="r2", status="error",
            error="x" * 5000,
        )
        h = fake_redis._hstore["inline:result:r2"]
        assert len(h["error"]) <= 500

    def test_control_says_stop_consumes_key(self, fake_redis):
        fake_redis.set("inline:control:u1:k1", "stop")
        assert dlc_tasks._control_says_stop(fake_redis, "u1", "k1") is True
        # Second call returns False — key was consumed.
        assert dlc_tasks._control_says_stop(fake_redis, "u1", "k1") is False

    def test_idle_budget_caps_at_ttl(self, fake_redis):
        dlc_tasks._publish_status(
            fake_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        budget = dlc_tasks._idle_budget(fake_redis, "u1", "k1", ttl=300)
        assert 290 <= budget <= 300, f"fresh activity → near-full TTL, got {budget}"

    def test_idle_budget_zero_when_expired(self, fake_redis, monkeypatch):
        dlc_tasks._publish_status(
            fake_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        # Pretend 500s have passed.
        fake_redis._hstore["inline:session:u1:k1"]["last_activity"] = str(
            _ia_time_now() - 500
        )
        budget = dlc_tasks._idle_budget(fake_redis, "u1", "k1", ttl=300)
        assert budget == 1, "expired sessions still get a minimal poll budget of 1s"


def _ia_time_now():
    import time
    return time.time()
```

- [ ] **Step 2: Run; confirm RED**

Run: `python -m pytest tests/test_inline_analysis_worker.py -k "SessionHelpers" -v`
Expected: AttributeError on the helpers.

- [ ] **Step 3: Add the helpers to `src/dlc/tasks.py`**

Append to `src/dlc/tasks.py`:

```python
def _session_key(user_id, snap_key):     return f"inline:session:{user_id}:{snap_key}"
def _queue_key  (user_id, snap_key):     return f"inline:queue:{user_id}:{snap_key}"
def _control_key(user_id, snap_key):     return f"inline:control:{user_id}:{snap_key}"
def _result_key (req_id):                return f"inline:result:{req_id}"


def _publish_status(redis_, user_id, snap_key, status, **fields):
    """Set the session hash status + last_activity, refresh TTL."""
    mapping = {"status": status, "last_activity": str(_ia_time.time()), **fields}
    key = _session_key(user_id, snap_key)
    redis_.hset(key, mapping=mapping)
    # TTL of 30s past the longest expected idle budget; redis library accepts
    # `expire` on most flavors but FakeRedis may not — guard.
    try:
        redis_.expire(key, 3600)
    except Exception:
        pass


def _publish_result(redis_, req_id, status, n_analyzed=0, n_skipped=0, error=""):
    """Set the result hash. Errors are truncated to 500 chars."""
    mapping = {
        "status":     status,
        "n_analyzed": str(int(n_analyzed)),
        "n_skipped":  str(int(n_skipped)),
        "error":      str(error)[:500],
    }
    key = _result_key(req_id)
    redis_.hset(key, mapping=mapping)
    try:
        redis_.expire(key, 300)
    except Exception:
        pass


def _bump_activity(redis_, user_id, snap_key):
    key = _session_key(user_id, snap_key)
    redis_.hset(key, "last_activity", str(_ia_time.time()))


def _control_says_stop(redis_, user_id, snap_key):
    """One-shot consume of inline:control:<…>. Returns True iff the key was 'stop'."""
    key = _control_key(user_id, snap_key)
    val = redis_.get(key)
    if val is None:
        return False
    # decode bytes if real redis; FakeRedis stores strings.
    if isinstance(val, bytes):
        val = val.decode("utf-8", "replace")
    if val != "stop":
        return False
    redis_.delete(key)
    return True


def _idle_budget(redis_, user_id, snap_key, ttl):
    """Seconds remaining before TTL eviction, clamped to >= 1."""
    key = _session_key(user_id, snap_key)
    last = redis_.hget(key, "last_activity") if hasattr(redis_, "hget") else None
    # FakeRedis stores hashes in ._hstore directly:
    if last is None:
        try:
            last = redis_._hstore.get(key, {}).get("last_activity")
        except AttributeError:
            last = None
    if last is None:
        return ttl
    try:
        elapsed = _ia_time.time() - float(last)
    except (TypeError, ValueError):
        return ttl
    return max(1, int(ttl - elapsed))


def _blpop(redis_, queue_key, timeout):
    """Wrapper around redis BLPOP that returns the raw value or None on timeout.

    FakeRedis (tests) implements blpop via simple polling; in production the
    real client blocks server-side.
    """
    res = redis_.blpop(queue_key, timeout=timeout) if hasattr(redis_, "blpop") else None
    if not res:
        # Tests may use a fake that exposes .lists directly; degrade to lpop.
        try:
            return redis_._lists.get(queue_key, []).pop(0) if redis_._lists.get(queue_key) else None
        except AttributeError:
            return None
    # real redis returns (key, value); decode bytes
    _, val = res
    if isinstance(val, bytes):
        val = val.decode("utf-8", "replace")
    return val
```

**Note:** `FakeRedis` in `tests/conftest.py` may or may not implement `blpop`. If it doesn't, the worker tests in Task 1.3 will use `lpush + immediate lpop` paths via the fallback branch; the production code uses real BLPOP.

- [ ] **Step 4: Extend FakeRedis with the minimum blpop/hget surface if needed**

Run: `python -c "from tests.conftest import _make_fake_redis_class as _; print('ok')" 2>&1 | head -3`. If FakeRedis lacks `blpop` or `hget`, add stubs in `tests/conftest.py`:

```python
# inside FakeRedis class:
def blpop(self, key, timeout=0):
    items = self._lists.get(key) or []
    if not items:
        return None
    return (key, items.pop(0))

def hget(self, name, key):
    return self._hstore.get(name, {}).get(key)

def lpush(self, key, value):
    self._lists.setdefault(key, []).insert(0, value)

def expire(self, key, seconds):
    return 1
```

Only add the methods that aren't already there — `grep "def blpop\|def hget\|def lpush\|def expire" tests/conftest.py` first.

- [ ] **Step 5: Run; confirm GREEN**

Run: `python -m pytest tests/test_inline_analysis_worker.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/dlc/tasks.py tests/test_inline_analysis_worker.py tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(dlc): inline-analysis session lifecycle helpers (Redis IPC)

Adds _publish_status, _publish_result, _bump_activity, _control_says_stop,
_idle_budget, _blpop helpers used by the warm-worker loop. FakeRedis grows
the minimum surface needed by the tests (blpop, hget, lpush, expire).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.3: `_run_range` + `dlc_inline_session` Celery task

**Files:**
- Modify: `src/dlc/tasks.py`
- Modify: `tests/test_inline_analysis_worker.py`

- [ ] **Step 1: Write failing tests for `_run_range`**

Append to `tests/test_inline_analysis_worker.py`:

```python
class TestRunRange:
    def test_run_range_writes_h5_and_csv(self, tmp_path):
        video_path = tmp_path / "v.mp4"; video_path.write_bytes(b"")
        runner = MagicMock()
        runner.scorer_name = "SCORER"
        runner.bodyparts = ["nose", "tail"]

        # Mock video_inference to return 3 predictions (one per frame).
        def fake_video_inference(vit, pose_runner):
            return [
                {"nose": (1.0, 2.0, 0.9), "tail": (3.0, 4.0, 0.8)},
                {"nose": (5.0, 6.0, 0.9), "tail": (7.0, 8.0, 0.8)},
                {"nose": (9.0, 10.0, 0.9), "tail": (11.0, 12.0, 0.8)},
            ]
        with patch.object(dlc_tasks, "video_inference", fake_video_inference), \
             patch.object(dlc_tasks, "_RangeVideoIterator", lambda p, indices: iter([None] * len(indices))):
            req = {
                "req_id": "r1", "video_path": str(video_path),
                "start_frame": 100, "n_frames": 3, "batch_size": 8,
                "save_as_csv": True, "snapshot_path": "snap.pt",
            }
            n_analyzed, n_skipped = dlc_tasks._run_range(runner, req)
        assert n_analyzed == 3
        assert n_skipped == 0
        h5_path = dlc_tasks._resolve_h5_path(str(video_path), "SCORER")
        csv_path = h5_path.with_suffix(".csv")
        assert h5_path.is_file()
        assert csv_path.is_file()

    def test_run_range_skips_already_done(self, tmp_path):
        video_path = tmp_path / "v.mp4"; video_path.write_bytes(b"")
        runner = MagicMock()
        runner.scorer_name = "S"
        runner.bodyparts = ["nose"]
        # Pre-seed existing h5 with frames 100, 101.
        h5_path = dlc_tasks._resolve_h5_path(str(video_path), "S")
        df_seed = _df_with_index([100, 101])
        dlc_tasks._atomic_write_h5(h5_path, df_seed)

        called_with = []
        def fake_video_inference(vit, pose_runner):
            called_with.append(list(vit))   # capture iterated frames
            return [{"nose": (0.0, 0.0, 0.5)}]   # one prediction (frame 102 only)
        with patch.object(dlc_tasks, "video_inference", fake_video_inference), \
             patch.object(dlc_tasks, "_RangeVideoIterator", lambda p, indices: iter([None] * len(indices))):
            req = {
                "req_id": "r1", "video_path": str(video_path),
                "start_frame": 100, "n_frames": 3, "batch_size": 8,
                "save_as_csv": False, "snapshot_path": "snap.pt",
            }
            n_analyzed, n_skipped = dlc_tasks._run_range(runner, req)
        assert n_analyzed == 1
        assert n_skipped == 2

    def test_run_range_no_op_when_everything_done(self, tmp_path):
        video_path = tmp_path / "v.mp4"; video_path.write_bytes(b"")
        runner = MagicMock()
        runner.scorer_name = "S"
        runner.bodyparts = ["nose"]
        h5_path = dlc_tasks._resolve_h5_path(str(video_path), "S")
        df_seed = _df_with_index([100, 101, 102])
        dlc_tasks._atomic_write_h5(h5_path, df_seed)
        req = {
            "req_id": "r1", "video_path": str(video_path),
            "start_frame": 100, "n_frames": 3, "batch_size": 8,
            "save_as_csv": False, "snapshot_path": "snap.pt",
        }
        # video_inference must NOT be called.
        with patch.object(dlc_tasks, "video_inference",
                          side_effect=AssertionError("must not be called")):
            n_analyzed, n_skipped = dlc_tasks._run_range(runner, req)
        assert n_analyzed == 0
        assert n_skipped == 3


class TestInlineSessionTask:
    def test_session_exits_on_ttl_with_no_work(self, fake_redis, tmp_path):
        # Worker called with TTL=1, no queue items → should exit within ~1s.
        runner_factory = MagicMock(return_value=MagicMock(scorer_name="S", bodyparts=["nose"]))
        with patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)), \
             patch.object(dlc_tasks, "_read_pytorch_config", return_value={}):
            t0 = _ia_time_now()
            dlc_tasks._dlc_inline_session_inner(
                fake_redis,
                user_id="u1",
                config_path=str(tmp_path / "config.yaml"),
                snap_key="k1",
                snapshot_path="snap.pt",
                shuffle=1,
                batch_size=8,
                ttl=1,
            )
            elapsed = _ia_time_now() - t0
        assert elapsed < 3.0, f"expected exit within ~1s+slop, took {elapsed}"
        h = fake_redis._hstore[f"inline:session:u1:k1"]
        assert h["status"] == "expired"

    def test_session_exits_on_control_stop(self, fake_redis, tmp_path):
        runner_factory = MagicMock(return_value=MagicMock(scorer_name="S", bodyparts=["nose"]))
        fake_redis.set("inline:control:u1:k1", "stop")
        with patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)), \
             patch.object(dlc_tasks, "_read_pytorch_config", return_value={}):
            dlc_tasks._dlc_inline_session_inner(
                fake_redis, user_id="u1", config_path="cfg",
                snap_key="k1", snapshot_path="snap.pt",
                shuffle=1, batch_size=8, ttl=60,
            )
        h = fake_redis._hstore[f"inline:session:u1:k1"]
        assert h["status"] == "stopped"

    def test_session_runs_one_range_then_exits(self, fake_redis, tmp_path):
        video_path = tmp_path / "v.mp4"; video_path.write_bytes(b"")
        req = {
            "req_id": "r1", "video_path": str(video_path),
            "start_frame": 0, "n_frames": 1, "batch_size": 8,
            "save_as_csv": False, "snapshot_path": "snap.pt",
        }
        fake_redis.lpush("inline:queue:u1:k1", _ia_json.dumps(req))
        runner = MagicMock(scorer_name="S", bodyparts=["nose"])
        runner_factory = MagicMock(return_value=runner)
        with patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)), \
             patch.object(dlc_tasks, "_read_pytorch_config", return_value={}), \
             patch.object(dlc_tasks, "video_inference",
                          return_value=[{"nose": (1.0, 2.0, 0.9)}]), \
             patch.object(dlc_tasks, "_RangeVideoIterator",
                          lambda p, indices: iter([None] * len(indices))):
            dlc_tasks._dlc_inline_session_inner(
                fake_redis, user_id="u1", config_path="cfg",
                snap_key="k1", snapshot_path="snap.pt",
                shuffle=1, batch_size=8, ttl=2,
            )
        r = fake_redis._hstore["inline:result:r1"]
        assert r["status"] == "done"
        assert int(r["n_analyzed"]) == 1
```

- [ ] **Step 2: Run; confirm RED**

Run: `python -m pytest tests/test_inline_analysis_worker.py -k "TestRunRange or TestInlineSessionTask" -v`
Expected: AttributeError on `_run_range`, `_dlc_inline_session_inner`.

- [ ] **Step 3: Append `_run_range` + the Celery task to `src/dlc/tasks.py`**

```python
def _run_range(runner, req):
    """Inference + merge for one range request.

    Returns (n_analyzed, n_skipped). Raises on hard failure (caught by the
    task loop, which publishes status=error).
    """
    h5_path  = _resolve_h5_path(req["video_path"], runner.scorer_name)
    existing = pd.read_hdf(str(h5_path)) if h5_path.exists() else None

    target     = list(range(req["start_frame"], req["start_frame"] + req["n_frames"]))
    to_analyze = _filter_skip_already_done(target, existing)
    n_skipped  = len(target) - len(to_analyze)
    if not to_analyze:
        return 0, n_skipped

    vit = _RangeVideoIterator(req["video_path"], indices=to_analyze)
    predictions = video_inference(vit, pose_runner=runner)

    df_new   = _preds_to_df(predictions, to_analyze, runner.bodyparts, runner.scorer_name)
    df_merge = df_new if existing is None else df_new.combine_first(existing)
    _atomic_write_h5(h5_path, df_merge)
    if req.get("save_as_csv"):
        _atomic_write_csv(h5_path.with_suffix(".csv"), df_merge)
    meta_path = _resolve_meta_path(h5_path)
    _update_meta_pickle(meta_path, df_merge, snapshot=req["snapshot_path"])
    return len(to_analyze), n_skipped


def _dlc_inline_session_inner(redis_, user_id, config_path, snap_key,
                              snapshot_path, shuffle, batch_size, ttl):
    """Pure-function body of the warm-worker task, testable without Celery.

    Boots the runner once, then BLPOP-loops range requests until TTL elapses
    or a control:stop signal is received.
    """
    queue_key   = _queue_key(user_id, snap_key)
    control_key = _control_key(user_id, snap_key)   # noqa: F841 - consumed inside helper

    _publish_status(redis_, user_id, snap_key, "warming",
                    snapshot_path=snapshot_path, project=str(_IAPath(config_path).parent.name),
                    started_at=str(_ia_time.time()))
    try:
        model_config = _read_pytorch_config(config_path, shuffle)
        runner = _dlc_apis_utils.get_pose_inference_runner(
            model_config, snapshot_path,
            batch_size=batch_size, device=None,
        )
    except Exception as exc:
        _publish_status(redis_, user_id, snap_key, "error",
                        last_error=str(exc)[:500])
        return

    _publish_status(redis_, user_id, snap_key, "ready",
                    snapshot_path=snapshot_path, project=str(_IAPath(config_path).parent.name))

    cached_batch_size = batch_size
    exit_reason = "expired"
    while True:
        if _control_says_stop(redis_, user_id, snap_key):
            exit_reason = "stopped"
            break
        budget = _idle_budget(redis_, user_id, snap_key, ttl)
        item = _blpop(redis_, queue_key, timeout=budget)
        if item is None:
            exit_reason = "expired"
            break
        try:
            req = _ia_json.loads(item)
        except Exception:
            continue

        if req.get("batch_size") and req["batch_size"] != cached_batch_size:
            try:
                runner = _dlc_apis_utils.get_pose_inference_runner(
                    model_config, snapshot_path,
                    batch_size=req["batch_size"], device=None,
                )
                cached_batch_size = req["batch_size"]
            except Exception as exc:
                _publish_result(redis_, req["req_id"], "error", error=str(exc))
                continue

        try:
            n_analyzed, n_skipped = _run_range(runner, req)
            _publish_result(redis_, req["req_id"], "done",
                            n_analyzed=n_analyzed, n_skipped=n_skipped)
        except Exception as exc:
            _publish_result(redis_, req["req_id"], "error", error=str(exc))
        _bump_activity(redis_, user_id, snap_key)

    _publish_status(redis_, user_id, snap_key, exit_reason)


@celery.task(bind=True, name="tasks.dlc_inline_session", acks_late=False)
def dlc_inline_session(self, user_id, config_path, snap_key, snapshot_path,
                       shuffle, batch_size, ttl):
    """Long-lived warm-worker session for one (user, project, snapshot) triple.

    acks_late=False — we don't want this task redelivered on broker restart;
    a fresh /session/start dispatches a new one.

    See docs/superpowers/specs/2026-05-20-inline-analysis-design.md §3.
    """
    redis_ = _redis_client_from_celery_app(self)
    _dlc_inline_session_inner(
        redis_, user_id, config_path, snap_key, snapshot_path,
        shuffle, batch_size, ttl,
    )


def _redis_client_from_celery_app(task):
    """Resolve a Redis client from inside a Celery task.

    Production: reuse the broker connection's underlying client.
    Tests: never call the @celery.task path; they call _dlc_inline_session_inner
    directly with FakeRedis.
    """
    import redis as _redis_mod
    url = _ia_os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    return _redis_mod.Redis.from_url(url, decode_responses=True)
```

- [ ] **Step 4: Run; confirm GREEN**

Run: `python -m pytest tests/test_inline_analysis_worker.py -v`
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/tasks.py tests/test_inline_analysis_worker.py
git commit -m "$(cat <<'EOF'
feat(dlc): inline-analysis warm-worker Celery task + _run_range

Adds tasks.dlc_inline_session (Celery-bound) and its testable inner
function _dlc_inline_session_inner. The inner loop BLPOP-pulls range
requests, calls _run_range, publishes results to Redis, and exits cleanly
on TTL or control-key stop. acks_late=False per spec rationale.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.4: Flask blueprint `src/dlc/inline_analysis.py` + 6 routes

**Files:**
- Create: `src/dlc/inline_analysis.py`
- Create: `tests/test_inline_analysis_routes.py`
- Modify: `src/app.py`

- [ ] **Step 1: Write failing route tests**

Create `tests/test_inline_analysis_routes.py`:

```python
"""HTTP-endpoint tests for the inline-analysis blueprint.

Celery is mocked (we capture .send_task calls). Redis is FakeRedis.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _auth(client):
    """Set the session uid so _user_id() doesn't generate fresh ones per call."""
    with client.session_transaction() as sess:
        sess["uid"] = "u1"


def _snap_key(config_path, shuffle, snapshot_path):
    raw = f"{config_path}|{shuffle}|{snapshot_path}".encode()
    return hashlib.sha1(raw).hexdigest()


@pytest.fixture
def ia_client(flask_test_client, dlc_sandbox_project):
    """Test client with an active DLC project set in Redis."""
    client, app_module, redis, data_dir, user_data_dir = flask_test_client
    _auth(client)
    import shutil
    dest = data_dir / dlc_sandbox_project.name
    shutil.copytree(str(dlc_sandbox_project), str(dest))
    cfg = dest / "config.yaml"
    redis.set(
        f"webapp:dlc_project:u1",
        json.dumps({
            "config_path":  str(cfg),
            "project_path": str(dest),
            "project":      dest.name,
        }),
    )
    yield client, app_module, redis, dest


class TestSessionStart:
    def test_dispatches_celery_task_with_snap_key(self, ia_client):
        client, _app, redis, project = ia_client
        sent = []
        with patch("dlc.inline_analysis._celery_send_task",
                   side_effect=lambda *a, **kw: sent.append((a, kw)) or MagicMock(id="celery-id")):
            resp = client.post("/dlc/project/inline-analysis/session/start", json={
                "snapshot_path": "snap-200000.pt",
                "shuffle":       1,
                "ttl_seconds":   300,
            })
        assert resp.status_code == 202, resp.get_json()
        data = resp.get_json()
        assert data["snap_key"] == _snap_key(
            str(project / "config.yaml"), 1, "snap-200000.pt",
        )
        assert data["status"] in {"warming", "ready"}
        assert sent and sent[0][1]["kwargs"]["snap_key"] == data["snap_key"]

    def test_400_when_no_active_project(self, flask_test_client):
        client, _app, _r, _d, _u = flask_test_client
        _auth(client)
        resp = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "x", "shuffle": 1, "ttl_seconds": 300,
        })
        assert resp.status_code == 400

    def test_409_when_project_is_multianimal(self, ia_client):
        client, _app, _redis, project = ia_client
        # Project-type check reads config.yaml directly — mutate the file, not Redis.
        cfg = project / "config.yaml"
        import yaml
        data = yaml.safe_load(cfg.read_text()) or {}
        data["multianimalproject"] = True
        cfg.write_text(yaml.safe_dump(data))
        resp = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "s", "shuffle": 1, "ttl_seconds": 300,
        })
        assert resp.status_code == 409
        assert "single-animal" in resp.get_json()["error"]

    def test_409_when_engine_is_tensorflow(self, ia_client):
        client, _app, _redis, project = ia_client
        cfg = project / "config.yaml"
        import yaml
        data = yaml.safe_load(cfg.read_text()) or {}
        data["engine"] = "tensorflow"
        cfg.write_text(yaml.safe_dump(data))
        resp = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "s", "shuffle": 1, "ttl_seconds": 300,
        })
        assert resp.status_code == 409
        assert "PyTorch" in resp.get_json()["error"]


class TestSessionStatus:
    def test_returns_warming_when_hash_says_so(self, ia_client):
        client, _app, redis, _ = ia_client
        sk = "abc123"
        redis.hset(f"inline:session:u1:{sk}", mapping={
            "status": "warming", "last_activity": "0",
        })
        resp = client.get(f"/dlc/project/inline-analysis/session/status?snap_key={sk}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "warming"
        assert "idle_remaining_s" in body


class TestSessionStop:
    def test_sets_control_key_to_stop(self, ia_client):
        client, _app, redis, _ = ia_client
        resp = client.post("/dlc/project/inline-analysis/session/stop", json={"snap_key": "k1"})
        assert resp.status_code == 204
        assert redis.get("inline:control:u1:k1") == "stop"


class TestRangeSubmit:
    def test_pushes_to_queue_and_returns_req_id(self, ia_client):
        client, _app, redis, project = ia_client
        # Pre-create a fake video file under the project so security check passes.
        v = project / "videos" / "fake.mp4"
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_bytes(b"")
        resp = client.post("/dlc/project/inline-analysis/range", json={
            "snap_key": "k1",
            "video_path": str(v),
            "start_frame": 0, "n_frames": 10, "batch_size": 8,
            "save_as_csv": True,
        })
        assert resp.status_code == 202, resp.get_json()
        rid = resp.get_json()["req_id"]
        items = redis._lists.get("inline:queue:u1:k1", [])
        assert len(items) == 1
        payload = json.loads(items[0])
        assert payload["req_id"] == rid
        assert payload["video_path"] == str(v)
        assert payload["start_frame"] == 0
        assert payload["n_frames"] == 10

    def test_403_on_path_outside_data_root(self, ia_client):
        client, _app, _r, _project = ia_client
        resp = client.post("/dlc/project/inline-analysis/range", json={
            "snap_key": "k1",
            "video_path": "/etc/passwd",
            "start_frame": 0, "n_frames": 1, "batch_size": 8,
            "save_as_csv": False,
        })
        assert resp.status_code in (400, 403)


class TestRangeStatus:
    def test_returns_done_with_counts(self, ia_client):
        client, _app, redis, _ = ia_client
        redis.hset("inline:result:r1", mapping={
            "status": "done", "n_analyzed": "42", "n_skipped": "8", "error": "",
        })
        resp = client.get("/dlc/project/inline-analysis/range/status?req_id=r1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "done"
        assert body["n_analyzed"] == 42
        assert body["n_skipped"] == 8

    def test_returns_pending_when_no_hash_yet(self, ia_client):
        client, _app, _r, _ = ia_client
        resp = client.get("/dlc/project/inline-analysis/range/status?req_id=missing")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "pending"


class TestVideoInfo:
    def test_returns_basic_metadata(self, ia_client, tmp_path):
        client, _app, _r, project = ia_client
        v = project / "videos" / "vinfo.mp4"
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_bytes(b"")
        with patch("dlc.inline_analysis._probe_video",
                   return_value={"nframes": 1000, "fps": 30.0, "width": 640, "height": 480}):
            resp = client.get(f"/dlc/project/inline-analysis/video-info?path={v}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["nframes"] == 1000
        assert body["fps"] == 30.0
        assert "has_h5_at_snapshot" in body
```

- [ ] **Step 2: Run; confirm RED**

Run: `python -m pytest tests/test_inline_analysis_routes.py -v`
Expected: ImportError (`No module named dlc.inline_analysis`).

- [ ] **Step 3: Create `src/dlc/inline_analysis.py`**

```python
"""DLC Inline Analysis blueprint.

Routes (all under /dlc/project/inline-analysis/):
  POST /session/start
  GET  /session/status   (read-only; does not bump activity)
  POST /session/stop
  POST /range            (bumps activity)
  GET  /range/status
  GET  /video-info

Activity (idle TTL) is bumped ONLY on /range submit. The worker
times out after `ttl_seconds` of no range submission, regardless
of whether the card is open. No client-side heartbeat — that's
the Jobs-page pattern and isn't needed here.

See docs/superpowers/specs/2026-05-20-inline-analysis-design.md.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, session as flask_session

from . import ctx as _ctx
from .utils import _dlc_project_security_check

bp = Blueprint("dlc_inline_analysis", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────

def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _snap_key(config_path: str, shuffle: int, snapshot_path: str) -> str:
    raw = f"{config_path}|{int(shuffle)}|{snapshot_path}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _active_project() -> dict | None:
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _disable_reason(project: dict) -> tuple[int, str] | None:
    """Return (status_code, error) if the project can't run inline analysis.

    Reads config.yaml on disk directly — no separate route exposes
    multianimal/engine, so neither does the Redis-cached project state.
    """
    cfg_path = Path(project.get("config_path", ""))
    if not cfg_path.is_file():
        return 400, "Active project has no readable config.yaml."
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception as exc:
        return 400, f"Could not parse config.yaml: {exc}"
    if cfg.get("multianimalproject"):
        return 409, (
            "Inline Analysis is single-animal only in v1. "
            "Use the Analyze Video/Frames card for multi-animal projects."
        )
    if (cfg.get("engine") or "pytorch").lower() != "pytorch":
        return 409, "Inline Analysis requires the PyTorch engine."
    return None


def _celery_send_task(name, *, kwargs, queue):
    """Indirection so tests can patch this single function."""
    from celery_app import celery        # local import — same pattern as other DLC blueprints
    return celery.send_task(name, kwargs=kwargs, queue=queue)


def _probe_video(path: Path) -> dict:
    """Cheap video metadata probe (nframes, fps, width, height)."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    info = {
        "nframes": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        "fps":     float(cap.get(cv2.CAP_PROP_FPS) or 0),
        "width":   int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
        "height":  int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
    }
    cap.release()
    return info


# ── Routes ────────────────────────────────────────────────────────────────

@bp.route("/dlc/project/inline-analysis/session/start", methods=["POST"])
def session_start():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    block = _disable_reason(project)
    if block:
        return jsonify({"error": block[1]}), block[0]

    body = request.get_json(silent=True) or {}
    snapshot_path = (body.get("snapshot_path") or "").strip()
    shuffle = int(body.get("shuffle") or 1)
    ttl = int(body.get("ttl_seconds") or 300)
    if not snapshot_path:
        return jsonify({"error": "snapshot_path required"}), 400

    config_path = project["config_path"]
    snap_key = _snap_key(config_path, shuffle, snapshot_path)
    user_id = _user_id()
    session_key = f"inline:session:{user_id}:{snap_key}"
    redis = _ctx.redis_client()

    existing = redis.hgetall(session_key) if hasattr(redis, "hgetall") else None
    # FakeRedis stores hashes in ._hstore directly
    if not existing:
        try:
            existing = redis._hstore.get(session_key)
        except AttributeError:
            existing = None

    if existing and (existing.get("status") if isinstance(existing, dict) else None) in {"warming", "ready"}:
        return jsonify({
            "session_id": snap_key, "snap_key": snap_key,
            "status": existing.get("status", "warming"),
        }), 202

    # Mark warming up front so the poll sees a non-empty hash even if dispatch is slow.
    redis.hset(session_key, mapping={
        "status": "warming",
        "snapshot_path": snapshot_path,
        "project": Path(config_path).parent.name,
        "started_at": str(time.time()),
        "last_activity": str(time.time()),
    })

    _celery_send_task(
        "tasks.dlc_inline_session",
        kwargs={
            "user_id":       user_id,
            "config_path":   config_path,
            "snap_key":      snap_key,
            "snapshot_path": snapshot_path,
            "shuffle":       shuffle,
            "batch_size":    int(body.get("batch_size") or 8),
            "ttl":           ttl,
        },
        queue="pytorch",
    )
    return jsonify({
        "session_id": snap_key, "snap_key": snap_key, "status": "warming",
    }), 202


@bp.route("/dlc/project/inline-analysis/session/status", methods=["GET"])
def session_status():
    snap_key = (request.args.get("snap_key") or "").strip()
    if not snap_key:
        return jsonify({"error": "snap_key required"}), 400
    redis = _ctx.redis_client()
    key = f"inline:session:{_user_id()}:{snap_key}"
    h = redis.hgetall(key) if hasattr(redis, "hgetall") else None
    if not h:
        try:
            h = redis._hstore.get(key) or {}
        except AttributeError:
            h = {}
    if not h:
        return jsonify({"status": "absent", "idle_remaining_s": 0})
    last = float(h.get("last_activity") or 0)
    ttl = 300                                          # we expose a hint; worker drives the real TTL
    idle_remaining = max(0, int(ttl - (time.time() - last)))
    out = {
        "status": h.get("status", "unknown"),
        "idle_remaining_s": idle_remaining,
    }
    if h.get("last_error"):
        out["last_error"] = h["last_error"]
    return jsonify(out)


@bp.route("/dlc/project/inline-analysis/session/stop", methods=["POST"])
def session_stop():
    body = request.get_json(silent=True) or {}
    snap_key = (body.get("snap_key") or "").strip()
    if not snap_key:
        return jsonify({"error": "snap_key required"}), 400
    redis = _ctx.redis_client()
    redis.set(f"inline:control:{_user_id()}:{snap_key}", "stop", ex=60)
    return ("", 204)


@bp.route("/dlc/project/inline-analysis/range", methods=["POST"])
def range_submit():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    snap_key = (body.get("snap_key") or "").strip()
    video_path = (body.get("video_path") or "").strip()
    if not snap_key or not video_path:
        return jsonify({"error": "snap_key and video_path required"}), 400
    p = Path(video_path)
    if not p.is_file():
        return jsonify({"error": f"video not found: {video_path}"}), 400
    if not _sec_check(p):
        return jsonify({"error": "video path is outside the data root"}), 403

    try:
        start_frame = int(body.get("start_frame", 0))
        n_frames    = int(body.get("n_frames", 0))
        batch_size  = int(body.get("batch_size", 8))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame, n_frames, batch_size must be ints"}), 400
    if n_frames <= 0 or n_frames > 10_000:
        return jsonify({"error": "n_frames must be in 1..10000"}), 400

    req_id = uuid.uuid4().hex
    payload = {
        "req_id":        req_id,
        "video_path":    str(p),
        "start_frame":   start_frame,
        "n_frames":      n_frames,
        "batch_size":    batch_size,
        "save_as_csv":   bool(body.get("save_as_csv", False)),
        "snapshot_path": project.get("snapshot_path") or body.get("snapshot_path", ""),
    }
    redis = _ctx.redis_client()
    redis.lpush(f"inline:queue:{_user_id()}:{snap_key}", json.dumps(payload))
    return jsonify({"req_id": req_id}), 202


@bp.route("/dlc/project/inline-analysis/range/status", methods=["GET"])
def range_status():
    req_id = (request.args.get("req_id") or "").strip()
    if not req_id:
        return jsonify({"error": "req_id required"}), 400
    redis = _ctx.redis_client()
    key = f"inline:result:{req_id}"
    h = redis.hgetall(key) if hasattr(redis, "hgetall") else None
    if not h:
        try:
            h = redis._hstore.get(key) or {}
        except AttributeError:
            h = {}
    if not h:
        return jsonify({"status": "pending"})
    return jsonify({
        "status":     h.get("status", "pending"),
        "n_analyzed": int(h.get("n_analyzed") or 0),
        "n_skipped":  int(h.get("n_skipped") or 0),
        "error":      h.get("error", ""),
    })


@bp.route("/dlc/project/inline-analysis/video-info", methods=["GET"])
def video_info():
    raw = (request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "path required"}), 400
    p = Path(raw)
    if not p.is_file():
        return jsonify({"error": "not a file"}), 404
    if not _sec_check(p):
        return jsonify({"error": "video path is outside the data root"}), 403
    info = _probe_video(p)
    # Cheap "has_h5_at_snapshot" probe — looks for any sibling .h5 with the video stem.
    sibling_h5s = list(p.parent.glob(p.stem + "*.h5"))
    info["has_h5_at_snapshot"] = bool(sibling_h5s)
    return jsonify(info)
```

Note: if FakeRedis lacks `hgetall`, the route falls back to reading `redis._hstore` directly (already exercised in the tests via `redis._hstore.get`). In production the real Redis client provides `hgetall`.

If FakeRedis does not have an `hgetall` shim, add it in `tests/conftest.py`:

```python
def hgetall(self, name):
    return dict(self._hstore.get(name, {}))
```

- [ ] **Step 4: Register the blueprint in `src/app.py`**

In `src/app.py`, after `from dlc.inference import bp as _dlc_inference_bp` (line ~181) add:

```python
from dlc.inline_analysis import bp as _dlc_inline_analysis_bp
```

After `app.register_blueprint(_dlc_inference_bp)` (line ~195) add:

```python
app.register_blueprint(_dlc_inline_analysis_bp)
```

- [ ] **Step 5: Run; confirm GREEN**

```bash
python -m pytest tests/test_inline_analysis_routes.py -v
python -m pytest tests/test_inline_analysis_worker.py -v
```

Both should pass.

- [ ] **Step 6: Commit**

```bash
git add src/dlc/inline_analysis.py tests/test_inline_analysis_routes.py src/app.py tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(dlc): inline-analysis Flask blueprint with 6 routes

Adds src/dlc/inline_analysis.py with session start/status/stop,
range submit/status, and video-info routes. Validates active project,
returns 409 with descriptive error for multi-animal / TF (read from config.yaml), computes snap_key as
sha1(config_path|shuffle|snapshot_path). Registered in src/app.py
alongside the existing analyze blueprint.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.5: Session lifecycle integration tests

**Files:**
- Create: `tests/test_inline_analysis_session_lifecycle.py`

- [ ] **Step 1: Write the lifecycle tests**

These tests stitch together the route + worker layer (with DLC primitives mocked) to verify scenarios from §2 of the spec.

```python
"""Session-lifecycle integration tests for inline analysis.

Stitches Flask routes + worker code with DLC mocked. Covers:
  - Snapshot change while warm (control:stop on old, dispatch on new)
  - Idle TTL exit
  - control:stop teardown
  - Concurrent range requests serialise via the queue
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def ia_client(flask_test_client, dlc_sandbox_project):
    """Same shape as test_inline_analysis_routes.ia_client."""
    client, app_module, redis, data_dir, _user_data_dir = flask_test_client
    with client.session_transaction() as s:
        s["uid"] = "u1"
    import shutil
    dest = data_dir / dlc_sandbox_project.name
    shutil.copytree(str(dlc_sandbox_project), str(dest))
    cfg = dest / "config.yaml"
    redis.set("webapp:dlc_project:u1", json.dumps({
        "config_path":  str(cfg),
        "project_path": str(dest),
        "project":      dest.name,
    }))
    yield client, app_module, redis, dest


def test_snapshot_change_signals_stop_to_old_worker(ia_client):
    """Starting a session with a different snapshot must SET control:stop on the old snap_key."""
    client, _app, redis, _project = ia_client
    sent = []
    with patch("dlc.inline_analysis._celery_send_task",
               side_effect=lambda *a, **kw: sent.append(kw) or MagicMock(id="cid")):
        r1 = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "snap-A.pt", "shuffle": 1, "ttl_seconds": 300,
        })
        snap_a = r1.get_json()["snap_key"]
        # Simulate the worker reaching 'ready'.
        redis.hset(f"inline:session:u1:{snap_a}", "status", "ready")

        client.post("/dlc/project/inline-analysis/session/stop", json={"snap_key": snap_a})
        assert redis.get(f"inline:control:u1:{snap_a}") == "stop"

        r2 = client.post("/dlc/project/inline-analysis/session/start", json={
            "snapshot_path": "snap-B.pt", "shuffle": 1, "ttl_seconds": 300,
        })
        snap_b = r2.get_json()["snap_key"]
        assert snap_a != snap_b
    assert len(sent) == 2


def test_concurrent_range_requests_serialise_via_queue(ia_client):
    """Two POSTs to /range with the same snap_key both land in the same Redis list, in order."""
    client, _app, redis, project = ia_client
    v = project / "videos" / "v.mp4"
    v.parent.mkdir(parents=True, exist_ok=True); v.write_bytes(b"")
    snap_key = "sk1"
    for n in (10, 20):
        client.post("/dlc/project/inline-analysis/range", json={
            "snap_key": snap_key, "video_path": str(v),
            "start_frame": 0, "n_frames": n, "batch_size": 8,
            "save_as_csv": False,
        })
    items = redis._lists.get(f"inline:queue:u1:{snap_key}", [])
    assert len(items) == 2
    # Redis LPUSH puts newest at head; the order of POSTs is items[1], items[0].
    parsed = [json.loads(i) for i in items]
    assert {p["n_frames"] for p in parsed} == {10, 20}


def test_idle_ttl_exit_publishes_expired_status(ia_client, tmp_path):
    """When the worker loop exhausts its TTL, the session hash status = 'expired'."""
    from dlc import tasks as dlc_tasks
    _, _app, redis, _project = ia_client
    runner = MagicMock(scorer_name="S", bodyparts=["nose"])
    with patch.object(dlc_tasks, "_dlc_apis_utils",
                      MagicMock(get_pose_inference_runner=MagicMock(return_value=runner))), \
         patch.object(dlc_tasks, "_read_pytorch_config", return_value={}):
        dlc_tasks._dlc_inline_session_inner(
            redis, user_id="u1", config_path="cfg",
            snap_key="sk-ttl", snapshot_path="snap.pt",
            shuffle=1, batch_size=8, ttl=1,
        )
    h = redis._hstore["inline:session:u1:sk-ttl"]
    assert h["status"] == "expired"


def test_control_stop_takes_priority_over_pending_queue(ia_client, tmp_path):
    """If control:stop is set before BLPOP, the worker exits without processing queued items."""
    from dlc import tasks as dlc_tasks
    _, _app, redis, _project = ia_client
    redis.set("inline:control:u1:sk-stop", "stop")
    # Queue one item that, if processed, would call our SHOULD-NOT-BE-CALLED runner.
    redis.lpush("inline:queue:u1:sk-stop", json.dumps({
        "req_id": "r1", "video_path": str(tmp_path / "v.mp4"),
        "start_frame": 0, "n_frames": 1, "batch_size": 8,
        "save_as_csv": False, "snapshot_path": "snap.pt",
    }))
    runner = MagicMock(scorer_name="S", bodyparts=["nose"])
    with patch.object(dlc_tasks, "_dlc_apis_utils",
                      MagicMock(get_pose_inference_runner=MagicMock(return_value=runner))), \
         patch.object(dlc_tasks, "_read_pytorch_config", return_value={}), \
         patch.object(dlc_tasks, "video_inference",
                      side_effect=AssertionError("must not run after control:stop")):
        dlc_tasks._dlc_inline_session_inner(
            redis, user_id="u1", config_path="cfg",
            snap_key="sk-stop", snapshot_path="snap.pt",
            shuffle=1, batch_size=8, ttl=60,
        )
    h = redis._hstore["inline:session:u1:sk-stop"]
    assert h["status"] == "stopped"
```

- [ ] **Step 2: Run; confirm GREEN**

Run: `python -m pytest tests/test_inline_analysis_session_lifecycle.py -v`
Expected: all four pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_inline_analysis_session_lifecycle.py
git commit -m "$(cat <<'EOF'
test(dlc): inline-analysis session lifecycle integration tests

Stitches Flask routes + worker loop (DLC mocked) to verify snapshot
change tear-down, idle TTL exit, control:stop priority, and concurrent
range queueing. All four cases from §2 of the spec covered.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 1 acceptance

- `python -m pytest tests/test_inline_analysis_worker.py tests/test_inline_analysis_routes.py tests/test_inline_analysis_session_lifecycle.py -v` → all PASS
- `grep -rn "_dlc_inline_analysis_bp" src/app.py` → blueprint registered
- The seven routes return well-shaped JSON 4xx for bad input (404/400/403/409) and 2xx for valid input — confirmed by the routes-test file
- No DLC import executes at `python -c "import dlc.tasks"` on a host without DLC (the import is guarded in a try/except in tasks.py)
- The DLC-symbol probe (Task 1.0) passed inside the worker container; if it failed, this phase did not start

---

# PHASE 2 — Frontend: card partial + JS controller + entry-point button

**Phase goal:** The "Inline Analysis" button appears in the sidebar between Analyze and View-Analyzed. Clicking it opens a card that:
- Picks a video via the canonical file browser (with "Hide videos without h5" UNCHECKED by default per §1)
- Shows the params block (snapshot, batch size, frames-per-click, keep-warm). For multi-animal / TF projects, the user sees the server's 409 error in the existing "Last run" status line after clicking Analyze — no preflight banner
- Updates the Analyze button label as the user scrubs
- POSTs `/session/start` on first interaction, polls `/session/status`
- POSTs `/range` on click, polls `/range/status`, calls `player.reloadH5()` on completion
- Calls `/session/stop` on `beforeunload` / Close

**Risk surface:**

- **DOM-id collisions.** The factory uses prefix `ia` (e.g., `ia-frame-img`). Verify no existing card uses any `ia-…` id (`grep -rn 'id="ia-' src/templates/`).
- **main.js load order.** `inline_analysis.js` must load AFTER `viewer.js` so `state.js` is fully populated. The existing `main.js` import order makes this straightforward.
- **Hide-no-h5 default.** Default UNCHECKED per §1 (opposite of View-Analyzed). The factory itself doesn't own this — the card does.

**Rollback:** Revert Phase 2 commits in reverse order. Backend (Phase 1) remains; the routes are simply unused.

---

## Task 2.1: `card_inline_analysis.html` partial + entry-point button + index include

**Files:**
- Create: `src/templates/partials/card_inline_analysis.html`
- Modify: `src/templates/partials/card_dlc_project.html` (insert button between Analyze and View-Analyzed)
- Modify: `src/templates/index.html` (include the partial)
- Create / extend: `tests/test_inline_analysis_ui_isolation.py` (static-template assertions)

- [ ] **Step 1: Write failing UI-isolation tests**

Create `tests/test_inline_analysis_ui_isolation.py`:

```python
"""Static-template + DOM-shape assertions for the Inline Analysis card.

No JS runtime here — these tests parse the template files directly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT     = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
INDEX    = ROOT / "src" / "templates" / "index.html"


def test_card_inline_analysis_partial_exists():
    p = PARTIALS / "card_inline_analysis.html"
    assert p.is_file()
    txt = p.read_text()
    assert 'id="inline-analysis-card"' in txt
    assert 'id="btn-close-inline-analysis"' in txt
    # Player elements with the "ia-" prefix the factory expects.
    assert 'id="ia-frame-img"' in txt
    assert 'id="ia-overlay-canvas"' in txt
    assert 'id="ia-btn-play"' in txt
    # Params block.
    assert 'id="ia-snapshot"' in txt
    assert 'id="ia-batch-size"' in txt
    assert 'id="ia-frames-per-click"' in txt
    assert 'id="ia-keep-warm-seconds"' in txt
    assert 'id="ia-btn-analyze-range"' in txt
    # No ia-disable-banner — project-type errors surface via the existing
    # "Last run" status line, sourced from /session/start's 409 body.
    assert 'id="ia-disable-banner"' not in txt


def test_index_includes_inline_analysis_partial():
    txt = INDEX.read_text()
    assert "partials/card_inline_analysis.html" in txt


def test_button_sits_between_analyze_and_view_analyzed():
    p = PARTIALS / "card_dlc_project.html"
    txt = p.read_text()
    i_analyze = txt.index('id="btn-open-analyze"')
    i_inline  = txt.index('id="btn-open-inline-analysis"')
    i_view    = txt.index('id="btn-open-view-analyzed"')
    assert i_analyze < i_inline < i_view, (
        "Inline Analysis button must sit between Analyze and View-Analyzed"
    )


def test_hide_no_h5_is_unchecked_by_default():
    """Spec §1: default UNCHECKED (opposite of View-Analyzed)."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    # Find the hide-no-h5 checkbox tag.
    m = re.search(r'<input[^>]*id="ia-hide-no-h5"[^>]*>', txt)
    assert m, "ia-hide-no-h5 checkbox must exist"
    assert "checked" not in m.group(0), (
        "ia-hide-no-h5 must NOT have `checked` — default unchecked per spec §1"
    )


def test_no_create_labeled_controls_in_card():
    """Spec §1: 'Create labeled video / frame' is explicitly omitted."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert "ia-create-labeled" not in txt
    assert "Create labeled video" not in txt
    assert "Create labeled frame" not in txt


def test_new_ids_are_unique_across_partials():
    new_ids = {
        "inline-analysis-card", "btn-close-inline-analysis", "btn-open-inline-analysis",
        "ia-snapshot", "ia-batch-size",
        "ia-frames-per-click", "ia-keep-warm-seconds",
        "ia-warm-indicator", "ia-btn-analyze-range", "ia-last-run-status",
        "ia-file-browser-pane", "ia-hide-no-h5",
        "ia-frame-img", "ia-overlay-canvas", "ia-btn-play",
        "ia-btn-prev", "ia-btn-next", "ia-seek", "ia-frame-counter",
        "ia-zoom", "ia-zoom-val", "ia-skip-n", "ia-frame-spinner",
        "ia-overlay-toggle", "ia-overlay-primary-select",
        "ia-overlay-add-compare", "ia-overlay-compare-list",
        "ia-overlay-threshold", "ia-overlay-marker-size",
        "ia-marker-edit-banner",
    }
    seen: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    for nid in new_ids:
        assert seen.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen.get(nid, 0)} times across partials"
        )
```

- [ ] **Step 2: Run; confirm RED**

Run: `python -m pytest tests/test_inline_analysis_ui_isolation.py -v`
Expected: failures — files don't exist.

- [ ] **Step 3: Create the card partial**

Create `src/templates/partials/card_inline_analysis.html`. Use `card_viewer.html` as your structural reference for spacing, classes, and the close-button SVG; mirror the SECTIONS from §1 of the spec:

```html
<section class="card dlc-theme hidden" id="inline-analysis-card">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.3rem">
    <h2>Inline Analysis</h2>
    <button class="btn-sm" id="btn-close-inline-analysis" title="Close">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      Close
    </button>
  </div>
  <p class="subtitle">Scrub a video, run DLC on N frames forward against a warm-in-memory model, and merge into the canonical .h5/.csv.</p>

  <!-- File picker -->
  <div style="margin-bottom:.6rem">
    <label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);margin-bottom:.3rem;cursor:pointer">
      <input type="checkbox" id="ia-hide-no-h5"
             style="accent-color:var(--accent);width:13px;height:13px"/>
      Hide videos without h5
    </label>
    <div style="display:flex;gap:.4rem;margin-bottom:.3rem">
      <input id="ia-video-path" type="text" placeholder="/paste/a/video.mp4"
             style="flex:1;font-family:var(--mono);font-size:.78rem;padding:.3rem .5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text)"/>
      <button class="btn-sm" id="ia-browse-up" style="padding:.2rem .45rem;font-size:.75rem" title="Up">↑ Up</button>
      <button class="btn-sm" id="ia-browse-btn">Browse</button>
    </div>
    <div id="ia-file-browser-pane" class="hidden" style="max-height:240px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;background:var(--surface-2);padding:.4rem .5rem;font-size:.77rem"></div>
  </div>

  <!-- Analysis params -->
  <div style="margin-bottom:.6rem;padding:.5rem;border:1px solid var(--border);border-radius:6px;background:var(--surface-2)">
    <div style="font-size:.78rem;color:var(--text-dim);margin-bottom:.4rem">Analysis Parameters</div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-snapshot">Snapshot</label>
      <select id="ia-snapshot" style="flex:1"></select>
      <button class="btn-sm" id="ia-refresh-snapshots" title="Refresh">↺</button>
    </div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-batch-size">Batch size</label>
      <input type="number" id="ia-batch-size" value="8" min="1" max="256" style="width:5rem"/>
    </div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-frames-per-click">Frames per click</label>
      <input type="number" id="ia-frames-per-click" value="500" min="1" max="10000" style="width:6rem"/>
    </div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-keep-warm-seconds">Keep worker warm (s)</label>
      <input type="number" id="ia-keep-warm-seconds" value="300" min="10" max="3600" style="width:6rem"/>
      <span id="ia-warm-indicator" style="margin-left:auto;font-size:.74rem;color:var(--text-dim)">○ cold</span>
    </div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-save-csv">Save as CSV</label>
      <input type="checkbox" id="ia-save-csv" checked style="width:auto"/>
    </div>
    <button id="ia-btn-analyze-range" class="btn-sm" style="width:100%;margin-top:.3rem">▶ Analyze 500 frames from frame 0</button>
    <div id="ia-last-run-status" style="margin-top:.3rem;font-size:.74rem;color:var(--text-dim)"></div>
  </div>

  <!-- Frame player (mirrors viewer.js DOM with `ia-` prefix) -->
  <div id="ia-player-section">
    <div id="ia-video-wrap" style="position:relative">
      <img id="ia-frame-img" alt="" style="max-width:100%;display:block"/>
      <canvas id="ia-overlay-canvas" style="position:absolute;left:0;top:0;pointer-events:auto"></canvas>
      <div id="ia-frame-spinner" class="hidden">Loading…</div>
    </div>
    <div style="display:flex;align-items:center;gap:.45rem;margin-top:.3rem">
      <button class="btn-sm" id="ia-btn-prev">◀</button>
      <button class="btn-sm" id="ia-btn-play">▶</button>
      <button class="btn-sm" id="ia-btn-next">▶</button>
      <label style="font-size:.75rem">step <input type="number" id="ia-skip-n" value="1" min="1" max="100" style="width:50px"></label>
      <span id="ia-frame-counter" style="font-family:var(--mono);font-size:.78rem">Frame 0 / 0</span>
      <span style="flex:1"></span>
      <label style="font-size:.75rem">zoom
        <input type="range" id="ia-zoom" min="50" max="300" value="100" step="25" style="width:80px">
        <span id="ia-zoom-val">100 %</span>
      </label>
    </div>
    <input type="range" id="ia-seek" min="0" max="0" value="0" step="1" style="width:100%;margin-top:.2rem"/>
  </div>

  <!-- Kinematic markers -->
  <div style="margin-top:.5rem;padding:.4rem;border:1px solid var(--border);border-radius:6px">
    <label style="display:flex;align-items:center;gap:.4rem;font-size:.75rem">
      <input type="checkbox" id="ia-overlay-toggle"> Show markers
      threshold <input type="range" id="ia-overlay-threshold" min="0" max="1" step="0.05" value="0.6" style="width:80px">
      marker size <input type="number" id="ia-overlay-marker-size" value="6" min="1" max="20" style="width:50px">
    </label>
    <div style="display:flex;gap:.4rem;align-items:center;margin-top:.3rem;font-size:.75rem">
      Primary <select id="ia-overlay-primary-select" style="flex:1"></select>
      <select id="ia-overlay-add-compare" style="flex:1"><option value="">+ add comparison…</option></select>
    </div>
    <div id="ia-overlay-compare-list" style="display:flex;flex-direction:column;gap:.2rem;margin-top:.2rem"></div>
  </div>

  <!-- Marker adjustment banner -->
  <div id="ia-marker-edit-banner" class="hidden" style="margin-top:.4rem;padding:.4rem .5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;font-size:.74rem">
    <span id="ia-edit-count">0</span> frames edited — unsaved
    <button id="ia-btn-save-edits" class="btn-sm" style="margin-left:.4rem">Save</button>
    <button id="ia-btn-discard-edits" class="btn-sm" style="margin-left:.2rem">Discard</button>
  </div>

  <!-- (Dataset Curation panel — opt-in, lazy mount via factory.setCurationFrameHook) -->
  <div style="margin-top:.4rem;font-size:.74rem;color:var(--text-dim)">
    <label><input type="checkbox" id="ia-curation-toggle"> Dataset Curation</label>
  </div>
</section>
```

- [ ] **Step 4: Add the entry-point button**

In `src/templates/partials/card_dlc_project.html`, locate the line containing `id="btn-open-analyze"` and the closing `</button>` of the Analyze button (currently lines 108–113). Immediately AFTER that closing `</button>` and BEFORE `<button id="btn-open-view-analyzed"` (line 114), insert:

```html
        <button id="btn-open-inline-analysis" class="inspect-btn" style="width:100%;gap:.55rem">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="3" width="20" height="14" rx="2"/>
            <polygon points="10 8 16 11 10 14" fill="currentColor" stroke="none"/>
            <line x1="6" y1="21" x2="18" y2="21"/>
          </svg>
          <span>Inline Analysis</span>
        </button>
```

This SVG (play-on-screen) is visually distinct from the View-Analyzed eye icon.

- [ ] **Step 5: Include the partial in `index.html`**

`grep -n "card_viewer.html\|card_analyze.html\|card_postprocess.html" src/templates/index.html` to find the include block. Add `{% include "partials/card_inline_analysis.html" %}` between the Analyze and View-Analyzed includes (mirrors the button order).

- [ ] **Step 6: Run UI-isolation tests; confirm GREEN**

Run: `python -m pytest tests/test_inline_analysis_ui_isolation.py -v`
Expected: all 6 tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html \
       src/templates/partials/card_dlc_project.html \
       src/templates/index.html \
       tests/test_inline_analysis_ui_isolation.py
git commit -m "$(cat <<'EOF'
feat(templates): inline-analysis card partial + entry-point button

Adds card_inline_analysis.html (file picker, params block, player DOM with
ia- prefix, kinematic-marker controls). Inserts btn-open-inline-analysis
between Analyze and View-Analyzed. UI-isolation tests assert button order,
default-unchecked hide-no-h5, no Create-labeled-video controls, unique IDs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.2: `inline_analysis.js` controller — open/close + file picker + range orchestration

**Files:**
- Create: `src/static/js/inline_analysis.js`
- Modify: `src/static/js/main.js`

- [ ] **Step 1: Write the controller skeleton**

Create `src/static/js/inline_analysis.js`:

```javascript
// src/static/js/inline_analysis.js
//
// Inline Analysis card controller.
//
// Owns:
//   - Open/close + ESC + hide-other-cards orchestration
//   - File picker (via makeFileBrowser) + hide-no-h5 toggle
//   - Snapshot picker + batch + frames-per-click + keep-warm inputs
//   - Project-type errors (multi-animal / TF) surface server-side as a
//     409 from /session/start and render in the existing lastRun status line
//   - Warm-indicator polling
//   - Range submit + status polling, calls player.reloadH5() on done
//   - Mounts makeAnalyzedFramePlayer({prefix: "ia", ...}) on first video load
//
// See docs/superpowers/specs/2026-05-20-inline-analysis-design.md.

import { state } from './state.js';
import { makeFileBrowser } from './components/file_browser.js';
import { makeAnalyzedFramePlayer } from './components/analyzed_frame_player.js';

(function () {
  "use strict";

  const card        = document.getElementById("inline-analysis-card");
  const openBtn     = document.getElementById("btn-open-inline-analysis");
  const closeBtn    = document.getElementById("btn-close-inline-analysis");
  const videoPath   = document.getElementById("ia-video-path");
  const browserPane = document.getElementById("ia-file-browser-pane");
  const browseBtn   = document.getElementById("ia-browse-btn");
  const browseUp    = document.getElementById("ia-browse-up");
  const hideNoH5    = document.getElementById("ia-hide-no-h5");
  const snapshotSel = document.getElementById("ia-snapshot");
  const batchSize   = document.getElementById("ia-batch-size");
  const framesInput = document.getElementById("ia-frames-per-click");
  const keepWarm    = document.getElementById("ia-keep-warm-seconds");
  const warmIndicator = document.getElementById("ia-warm-indicator");
  const btnAnalyze  = document.getElementById("ia-btn-analyze-range");
  const lastRun     = document.getElementById("ia-last-run-status");
  const saveCsv     = document.getElementById("ia-save-csv");

  if (!card || !openBtn) return;

  let _player = null;
  let _snapKey = null;
  let _statusPoll = null;
  let _activeReqId = null;
  let _activeReqPoll = null;

  // ── Open / close ───────────────────────────────────────────────────────
  function hideAllOtherCards() {
    document.querySelectorAll("section.card").forEach((c) => {
      if (c !== card) c.classList.add("hidden");
    });
  }

  async function openCard() {
    hideAllOtherCards();
    card.classList.remove("hidden");
    refreshSnapshots();
  }

  function closeCard() {
    card.classList.add("hidden");
    // Best-effort tell worker to wind down.
    if (_snapKey) {
      try {
        fetch("/dlc/project/inline-analysis/session/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ snap_key: _snapKey }),
        });
      } catch (e) { /* ignore */ }
    }
    stopStatusPolling();
    stopRangePolling();
    if (_player) { _player.destroy(); _player = null; }
  }

  openBtn.addEventListener("click", openCard);
  closeBtn.addEventListener("click", closeCard);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !card.classList.contains("hidden")) closeCard();
  });
  window.addEventListener("beforeunload", () => {
    if (_snapKey) {
      navigator.sendBeacon?.(
        "/dlc/project/inline-analysis/session/stop",
        new Blob([JSON.stringify({ snap_key: _snapKey })], { type: "application/json" }),
      );
    }
  });

  // (Project-type gating is purely server-side: /session/start reads
  // config.yaml directly and returns 409 for multi-animal / TF. The error
  // text from that response renders in `lastRun` — see `ensureSession`.)

  // ── File picker (canonical component) ──────────────────────────────────
  const picker = makeFileBrowser({
    inputEl: videoPath,
    paneEl:  browserPane,
    dirOnly: false,
    // Show video files; if hide-no-h5 ticked we filter again at the route level
    // (the canonical browser doesn't know about h5 — that's fine, we filter post-hoc).
    fileFilter: (name) => {
      const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
      return [".mp4", ".avi", ".mov", ".mkv"].includes(ext);
    },
    onPick: (path) => {
      videoPath.value = path;
      loadVideo(path);
    },
  });
  browseBtn.addEventListener("click", () => picker.openAt("/user-data"));
  browseUp.addEventListener("click", () => picker.up());
  // hide-no-h5: default unchecked per spec §1; re-render the picker when toggled.
  hideNoH5.checked = false;
  hideNoH5.addEventListener("change", () => picker.refresh());

  // ── Snapshot picker ────────────────────────────────────────────────────
  async function refreshSnapshots() {
    snapshotSel.innerHTML = "";
    try {
      // Reuse the same endpoint analyze card uses to enumerate snapshots.
      const r = await fetch("/dlc/project/snapshots");
      const data = await r.json();
      (data.snapshots || []).forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.path;                        // project-relative
        opt.textContent = s.label || s.path;
        snapshotSel.appendChild(opt);
      });
    } catch (e) { /* silent */ }
  }
  document.getElementById("ia-refresh-snapshots").addEventListener("click", refreshSnapshots);

  // ── Session start + status polling ─────────────────────────────────────
  async function ensureSession() {
    const snapshot = snapshotSel.value;
    if (!snapshot) return null;
    const r = await fetch("/dlc/project/inline-analysis/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snapshot_path: snapshot,
        shuffle: 1,
        ttl_seconds: parseInt(keepWarm.value, 10) || 300,
        batch_size: parseInt(batchSize.value, 10) || 8,
      }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      lastRun.textContent =
        data.error || `Could not start session (HTTP ${r.status})`;
      lastRun.className = "fe-extract-status err";
      return null;
    }
    const data = await r.json();
    _snapKey = data.snap_key;
    startStatusPolling();
    return _snapKey;
  }

  function startStatusPolling() {
    stopStatusPolling();
    _statusPoll = setInterval(async () => {
      if (!_snapKey) return;
      try {
        const r = await fetch(`/dlc/project/inline-analysis/session/status?snap_key=${_snapKey}`);
        const data = await r.json();
        const status = data.status || "absent";
        const mm = Math.floor((data.idle_remaining_s || 0) / 60);
        const ss = String((data.idle_remaining_s || 0) % 60).padStart(2, "0");
        if (status === "ready")      warmIndicator.textContent = `● warm · ${mm}:${ss}`;
        else if (status === "warming") warmIndicator.textContent = `… warming`;
        else                            warmIndicator.textContent = `○ ${status}`;
      } catch (e) { /* keep polling */ }
    }, 2000);
  }
  function stopStatusPolling() {
    if (_statusPoll) { clearInterval(_statusPoll); _statusPoll = null; }
  }

  // ── Player mount + frame counter → button label sync ───────────────────
  async function loadVideo(path) {
    if (!_player) {
      _player = makeAnalyzedFramePlayer({
        prefix: "ia",
        frameUrlFn: (n) => `/annotate/frame?path=${encodeURIComponent(path)}&frame=${n}`,
        poseUrlFn:  (layer, n) =>
          `/dlc/viewer/h5-pose-window?h5=${encodeURIComponent(layer.path)}&start=${n}&n=30`,
        onCsvSaved: () => { /* no-op */ },
      });
    }
    try {
      const r = await fetch(`/dlc/project/inline-analysis/video-info?path=${encodeURIComponent(path)}`);
      const info = await r.json();
      _player.loadVideo(path, info.fps || 30, info.nframes || 0);
      syncAnalyzeButtonLabel();
    } catch (e) { /* silent */ }
  }

  function syncAnalyzeButtonLabel() {
    if (!_player) return;
    const n = parseInt(framesInput.value, 10) || 0;
    const k = _player.getCurrentFrame();
    btnAnalyze.textContent = `▶ Analyze ${n} frames from frame ${k}`;
  }
  framesInput.addEventListener("input", syncAnalyzeButtonLabel);
  document.getElementById("ia-seek").addEventListener("input", syncAnalyzeButtonLabel);
  document.getElementById("ia-btn-next").addEventListener("click", syncAnalyzeButtonLabel);
  document.getElementById("ia-btn-prev").addEventListener("click", syncAnalyzeButtonLabel);

  // ── Submit a range ─────────────────────────────────────────────────────
  btnAnalyze.addEventListener("click", async () => {
    if (!videoPath.value.trim()) { lastRun.textContent = "Pick a video first."; return; }
    if (!_player) { lastRun.textContent = "Loading player…"; await loadVideo(videoPath.value.trim()); }
    const sk = await ensureSession();
    if (!sk) return;
    const startFrame = _player.getCurrentFrame();
    const nFrames    = parseInt(framesInput.value, 10) || 500;
    const r = await fetch("/dlc/project/inline-analysis/range", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snap_key:    sk,
        video_path:  videoPath.value.trim(),
        start_frame: startFrame,
        n_frames:    nFrames,
        batch_size:  parseInt(batchSize.value, 10) || 8,
        save_as_csv: !!saveCsv.checked,
      }),
    });
    const data = await r.json();
    if (!r.ok) { lastRun.textContent = `Error: ${data.error || r.status}`; return; }
    _activeReqId = data.req_id;
    lastRun.textContent = `Running (${nFrames} frames from ${startFrame})…`;
    startRangePolling();
  });

  function startRangePolling() {
    stopRangePolling();
    _activeReqPoll = setInterval(async () => {
      if (!_activeReqId) { stopRangePolling(); return; }
      try {
        const r = await fetch(`/dlc/project/inline-analysis/range/status?req_id=${_activeReqId}`);
        const d = await r.json();
        if (d.status === "done") {
          lastRun.textContent = `Last run: ${d.n_analyzed} analyzed, ${d.n_skipped} skipped`;
          if (_player) _player.reloadH5();
          _activeReqId = null;
          stopRangePolling();
        } else if (d.status === "error") {
          lastRun.textContent = `Error: ${d.error || "unknown"}`;
          _activeReqId = null;
          stopRangePolling();
        }
      } catch (e) { /* keep polling */ }
    }, 500);
  }
  function stopRangePolling() {
    if (_activeReqPoll) { clearInterval(_activeReqPoll); _activeReqPoll = null; }
  }

})();
```

- [ ] **Step 2: Register the module in `main.js`**

In `src/static/js/main.js`, add a new import line AFTER `import './viewer.js';` and BEFORE `import './postprocess.js';`:

```javascript
import './inline_analysis.js';
```

The position matters: `state.js` and `file_browser.js` are already loaded by the time `viewer.js` runs, and `inline_analysis.js` depends on the same modules.

- [ ] **Step 3: Re-run the factory contract test**

Run: `python -m pytest tests/test_analyzed_frame_player_factory.py -v`
Expected: now all 3 PASS (the soft consumer-check no longer skips — it asserts `inline_analysis.js` imports the factory).

- [ ] **Step 4: Add inline_analysis.js to the file-browser policy soft consumer list**

Open `tests/test_file_browser_policy.py`. Find the section that enumerates consumers (the `_assert_imports_factory` calls for `ANALYZE`, `VIEWER`, `ANNOTATOR`, `POSTPROC`). Add a parallel assertion:

```python
# Near the top, alongside the other path constants:
INLINE_ANALYSIS = ROOT / "src" / "static" / "js" / "inline_analysis.js"

# In the body of the consumer-imports test block:
def test_inline_analysis_imports_factory():
    _assert_imports_factory(INLINE_ANALYSIS)
```

(Use whichever style the existing file uses — class-method or top-level function. Match it.)

- [ ] **Step 5: Run the full UI + policy + factory test set**

```bash
python -m pytest \
  tests/test_inline_analysis_ui_isolation.py \
  tests/test_analyzed_frame_player_factory.py \
  tests/test_file_browser_policy.py \
  -v
```

Expected: all PASS.

- [ ] **Step 6: Manual browser smoke (if dev server is up)**

```bash
docker compose restart flask    # only if the dev server is running on this machine
```

Open `http://localhost:5000/?token=deeplabcut` and click the new "Inline Analysis" button:

- Card opens, other cards hide.
- Disable banner is shown for a multi-animal/TF project; hidden for a PyTorch single-animal project.
- File browser opens on click; picking a video populates `ia-video-path` and triggers `loadVideo`.
- Frames-per-click input + scrub update the "Analyze N from K" label live.

If the dev server isn't running, run a headless Playwright check that the page loads without console errors and the new button exists:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    has = pg.evaluate("!!document.getElementById('btn-open-inline-analysis')")
    print("button present:", has, "errors:", errs)
    b.close()
```

Expected: `button present: True`, `errors: []`.

- [ ] **Step 7: Commit**

```bash
git add src/static/js/inline_analysis.js src/static/js/main.js tests/test_file_browser_policy.py
git commit -m "$(cat <<'EOF'
feat(static): inline-analysis card JS controller

Adds src/static/js/inline_analysis.js: open/close + hide-other-cards
orchestration, file picker via canonical makeFileBrowser, server-side
for multi-animal/TF, snapshot/batch/frames/keep-warm inputs, warm-
indicator polling, range submit/poll, mounts makeAnalyzedFramePlayer
({prefix: "ia"}) on first video load. Registered in main.js after
viewer.js. File-browser policy test soft-registers the new consumer.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 acceptance

- `python -m pytest tests/test_inline_analysis_ui_isolation.py tests/test_analyzed_frame_player_factory.py tests/test_file_browser_policy.py -v` → all PASS
- Browser smoke: open card on a clean PyTorch single-animal project → params block visible (no banner); pick a video → player loads frame 0; scrub → "Analyze N from K" updates live. On a multi-animal or TF project, clicking Analyze surfaces the server's 409 error in the lastRun status line
- `grep -c "section.card" src/templates/index.html` → includes count incremented by 1
- No regression in `tests/test_postprocess_ui_isolation.py` or `tests/test_viewer_layers_ui_isolation.py` (the new card's IDs are namespaced under `ia-`/`inline-analysis-card`)

---

# PHASE 3 — E2E + opt-in GPU smoke

**Phase goal:** Ship a frontend smoke that drives the card with a stubbed worker (the routes return seeded fake results) and an opt-in `@pytest.mark.gpu` smoke that exercises the real warm-worker round-trip against `dlc_sandbox_project`. Disk-fill guard < 10 MB.

**Risk surface:**

- **GPU smoke disk-fill.** §6 of the spec mandates `assert final_du - initial_du < 10 * 1024 * 1024`. If the test fails this, **do not relax the threshold** — investigate temp-file leakage first. The `dlc_test_session_*` cleanup hook in `tests/conftest.py` should already handle it; the worker itself must not leave `.tmp` files behind (the `os.replace` in `_atomic_write_*` enforces this).
- **TTL race.** `assert worker_exits_within_ttl + 5s` — pick `TTL=10s` so the test runs in ~15s total, not 5 minutes.
- **GPU exclusivity.** Other DLC tests on GPU 0 may collide. `pytest.ini` marker `gpu` is excluded by default (`addopts = -m "not gpu"`); the GPU smoke runs only with explicit `-m gpu`.

**Rollback:** Drop the two test files. Backend + frontend remain shippable.

---

## Task 3.1: Frontend e2e smoke (no GPU)

**Files:**
- Create: `tests/e2e_inline_analysis_smoke.py`

- [ ] **Step 1: Write the smoke**

The smoke uses Playwright (already in use by `e2e_viewer_layers_smoke.py`). Reuse that file's structure verbatim — same browser launch, same error-trap, same multi-phase printing.

```python
"""Inline Analysis frontend smoke — no GPU, stubbed routes via fake_redis.

Runs against a live dev server (docker compose up flask) — skip if the
server isn't reachable.

Phases:
  A. Open card on a single-animal PyTorch project → no console errors → params block visible (no banner). On a multi-animal/TF project, clicking Analyze shows the server 409 error text in the lastRun line.
  B. File-browser opens; hide-no-h5 toggles
  C. Scrubbing the seek bar updates the Analyze button label live
  D. (Stubbed worker) clicking Analyze → range/status returns done → player.reloadH5() called
"""
from __future__ import annotations

import socket
import sys
import time
from contextlib import closing


def _server_alive(host="localhost", port=5000) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(0.5)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def main():
    if not _server_alive():
        print("SKIP: dev server not running on localhost:5000")
        return 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 1100})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        pg.goto("http://localhost:5000/?token=deeplabcut")
        pg.wait_for_load_state("networkidle")

        # Phase A
        pg.click("#btn-open-inline-analysis")
        time.sleep(0.6)
        card_visible = pg.evaluate("() => !document.getElementById('inline-analysis-card').classList.contains('hidden')")
        print(f"[A] card visible: {card_visible}, console errors: {errs}")
        assert card_visible
        assert not errs, f"console errors after open: {errs}"

        # Phase B
        pg.click("#ia-hide-no-h5")
        time.sleep(0.3)
        checked = pg.evaluate("() => document.getElementById('ia-hide-no-h5').checked")
        print(f"[B] hide-no-h5 toggled to: {checked}")

        # Phase C — synthetic scrub
        pg.fill("#ia-frames-per-click", "250")
        pg.evaluate("() => { const s = document.getElementById('ia-seek'); s.value = 100; s.dispatchEvent(new Event('input')); }")
        time.sleep(0.3)
        label = pg.text_content("#ia-btn-analyze-range") or ""
        print(f"[C] analyze label after scrub+frames=250: {label!r}")
        assert "250" in label, "frames-per-click must be reflected in button label"

        # Phase D would require a video file under the project; if none, skip with a note.
        # (Run the gpu smoke or a real-data e2e for end-to-end coverage.)

        print("\nALL CHECKS PASSED")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the smoke**

```bash
python tests/e2e_inline_analysis_smoke.py 2>&1 | tail -20
```

Expected (with dev server running): `ALL CHECKS PASSED`. Without dev server: `SKIP: dev server not running on localhost:5000`.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e_inline_analysis_smoke.py
git commit -m "$(cat <<'EOF'
test: inline-analysis frontend e2e smoke (Playwright)

Phases A–C cover open/close + hide-no-h5 toggle + scrub-updates-label.
Skips cleanly when no dev server is reachable.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3.2: GPU smoke — real warm-worker round-trip

**Files:**
- Create: `tests/test_inline_analysis_gpu_smoke.py`

- [ ] **Step 1: Write the GPU smoke**

```python
"""GPU smoke for inline analysis — real warm-worker round-trip.

Runs ONLY with `pytest -m gpu`. Caps n_frames=50, batch_size=8, TTL=10s.
Asserts:
  - 50 new rows in the canonical .h5
  - .csv updated
  - _meta.pickle records the snapshot in inline_analysis_snapshots
  - Worker exits within TTL + 5s
  - Disk delta < 10 MB
"""
from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.gpu


def _du_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(str(path)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _has_gpu() -> bool:
    if not os.environ.get("CUDA_VISIBLE_DEVICES"):
        return False
    try:
        out = subprocess.check_output(["nvidia-smi", "-L"], timeout=5)
        return b"GPU" in out
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _has_gpu(), reason="No GPU detected")
def test_inline_analysis_gpu_smoke(dlc_sandbox_project, fake_redis, tmp_path):
    """Boots the warm-worker against a real sandbox project, runs 50 frames, asserts outputs."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc import tasks as dlc_tasks

    project = dlc_sandbox_project
    # Pick a video and snapshot from the sandbox project.
    videos = list((project / "videos").glob("*.mp4")) + list((project / "videos").glob("*.avi"))
    if not videos:
        pytest.skip("sandbox project has no analyzable videos")
    video = videos[0]
    snapshots = sorted((project / "dlc-models-pytorch").rglob("snapshot-*.pt"))
    if not snapshots:
        pytest.skip("sandbox project has no PyTorch snapshots")
    snapshot = snapshots[-1]

    initial_du = _du_bytes(project)

    # Queue one range request before booting the worker (so BLPOP immediately returns).
    req = {
        "req_id": "gpu-smoke-r1",
        "video_path": str(video),
        "start_frame": 0,
        "n_frames": 50,
        "batch_size": 8,
        "save_as_csv": True,
        "snapshot_path": str(snapshot.relative_to(project)),
    }
    import json
    fake_redis.lpush("inline:queue:u1:k1", json.dumps(req))

    t0 = time.time()
    dlc_tasks._dlc_inline_session_inner(
        fake_redis,
        user_id="u1",
        config_path=str(project / "config.yaml"),
        snap_key="k1",
        snapshot_path=str(snapshot.relative_to(project)),
        shuffle=1,
        batch_size=8,
        ttl=10,
    )
    elapsed = time.time() - t0
    assert elapsed < 15.0, f"worker should exit within TTL+5s (10+5), took {elapsed:.1f}s"

    # Assert result hash.
    h = fake_redis._hstore.get("inline:result:gpu-smoke-r1", {})
    assert h.get("status") == "done", f"unexpected status: {h}"
    assert int(h.get("n_analyzed", 0)) == 50

    # Assert canonical files updated.
    h5_files = list(video.parent.glob(video.stem + "*.h5"))
    assert h5_files, "no h5 produced"
    df = pd.read_hdf(str(h5_files[-1]))
    assert len(df) >= 50

    csv_files = list(video.parent.glob(video.stem + "*.csv"))
    assert csv_files, "csv not produced (save_as_csv=True)"

    meta_files = list(video.parent.glob(video.stem + "*_meta.pickle"))
    assert meta_files
    with open(meta_files[-1], "rb") as f:
        meta = pickle.load(f)
    assert str(snapshot.relative_to(project)) in (meta.get("inline_analysis_snapshots") or set())

    final_du = _du_bytes(project)
    delta = final_du - initial_du
    assert delta < 10 * 1024 * 1024, f"disk delta {delta} > 10 MB (likely leaked temp files)"
```

- [ ] **Step 2: Run the smoke (only if a GPU is available)**

```bash
CUDA_VISIBLE_DEVICES=0 python -m pytest -m gpu tests/test_inline_analysis_gpu_smoke.py -v
```

Expected on a GPU host: PASS. On a GPU-less host: collected-but-skipped or pre-collection skip.

**Important:** Do NOT run this in CI by default — it's opt-in via `-m gpu`. The default `pytest` command excludes it via `pytest.ini`'s `addopts = -m "not gpu"`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_inline_analysis_gpu_smoke.py
git commit -m "$(cat <<'EOF'
test: inline-analysis GPU smoke — real warm-worker round-trip

Opt-in (@pytest.mark.gpu) end-to-end test that boots the real DLC PyTorch
runner against dlc_sandbox_project, runs 50 frames, and asserts h5/csv/
_meta.pickle outputs + worker exit + disk-delta < 10 MB. Excluded from
default test runs via pytest.ini's `addopts = -m "not gpu"`.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 acceptance

- `python -m pytest tests/e2e_inline_analysis_smoke.py` → PASS (or clean SKIP if no dev server)
- `CUDA_VISIBLE_DEVICES=0 python -m pytest -m gpu tests/test_inline_analysis_gpu_smoke.py -v` → PASS on the GPU host
- `python -m pytest tests/test_dlc_celery_tasks.py tests/test_analyzed_marker_adjustment.py tests/e2e_viewer_layers_smoke.py -v` → all PASS (no regression in existing tests)
- Disk-fill: after the GPU smoke completes, `/tmp/dlc_test_session_*` is cleaned by the session-finish hook

---

# PHASE 4 — Tech-debt notes + docs

**Phase goal:** Make the deferred-migration tech debt visible. Update the file-browser policy doc to mention the new factory. No new code.

**Risk surface:** None.

**Rollback:** Trivial — docs-only.

---

## Task 4.1: Broaden the file-browser policy doc

**Files:**
- Modify: `docs/policies/file-browser-component.md`

- [ ] **Step 1: Append a "Related: analyzed-frame-player factory" section**

Open `docs/policies/file-browser-component.md`. Find the section "Adding capabilities to the component" (near the end). Insert a new sibling section immediately AFTER it:

```markdown
## Related: other shared frontend factories

The same "one canonical factory, hard-policed by static tests" pattern is
applied to other multi-card frontend logic. As of 2026-05-20:

- `src/static/js/components/analyzed_frame_player.js`
  Export: `makeAnalyzedFramePlayer({ prefix, frameUrlFn, poseUrlFn, onCsvSaved })`
  Consumers (current): `inline_analysis.js`.
  Consumers (after deferred migration per
  `docs/superpowers/specs/2026-05-20-inline-analysis-design.md` §4):
  `viewer.js` too.
  Policy test: `tests/test_analyzed_frame_player_factory.py`.

When this doc grows beyond just the file browser, rename it to
`docs/policies/shared-components.md` and update the policy-test imports
that reference the file path. See the "Known tech debt" section of the
inline-analysis spec for the migration plan.
```

- [ ] **Step 2: Commit**

```bash
git add docs/policies/file-browser-component.md
git commit -m "$(cat <<'EOF'
docs(policy): cross-reference analyzed_frame_player factory

Adds a 'Related: other shared frontend factories' section to the
file-browser-component policy doc, pointing at the new
makeAnalyzedFramePlayer factory and its policy test. Lays groundwork
for the deferred rename to shared-components.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 acceptance

- `docs/policies/file-browser-component.md` references `analyzed_frame_player.js` and its policy test
- `git log --oneline feat/inline-analysis ^main` shows the documented commit cadence (≈12 commits across 4 phases)
- Branch is ready to PR or rebase

---

# Final Verification (run after all phases)

- [ ] **Step 1: Full host pytest pass (excluding GPU)**

```bash
python -m pytest \
  tests/test_inline_analysis_worker.py \
  tests/test_inline_analysis_routes.py \
  tests/test_inline_analysis_session_lifecycle.py \
  tests/test_inline_analysis_ui_isolation.py \
  tests/test_analyzed_frame_player_factory.py \
  tests/test_file_browser_policy.py \
  tests/test_dlc_celery_tasks.py \
  tests/test_analyzed_marker_adjustment.py \
  -v 2>&1 | tail -20
```

Expected: all PASS, no regressions.

- [ ] **Step 2: Manual browser end-to-end (per CLAUDE.md)**

With a real PyTorch single-animal project active:

1. Sidebar shows Inline Analysis button between Analyze and View-Analyzed.
2. Open card → params block visible, warm indicator `○ cold`.
3. Pick a video → player loads frame 0; "Analyze 500 frames from frame 0".
4. Scrub to frame 1240 → button updates to "Analyze 500 frames from frame 1240".
5. Click Analyze → indicator goes `… warming` → `● warm · MM:SS`; status shows "Running…"; on completion, status shows "Last run: N analyzed, K skipped"; markers re-fetched.
6. Close card → POST `/session/stop` fires; worker exits within seconds.

If any step fails, file as a follow-up.

- [ ] **Step 3: GPU smoke (if a GPU is available)**

```bash
CUDA_VISIBLE_DEVICES=0 python -m pytest -m gpu tests/test_inline_analysis_gpu_smoke.py -v
```

Expected: PASS, disk delta < 10 MB.

- [ ] **Step 4: Confirm no production code regression**

```bash
git diff main -- src/static/js/viewer.js | grep -v "^[-+]//"
```

Expected: zero non-comment changes in `viewer.js`.

```bash
git diff main -- src/templates/partials/card_viewer.html
```

Expected: empty.

---

## Explicit Non-Goals (do NOT drift into these — per spec §7)

- Multi-animal projects (banner disables card).
- TensorFlow engine (`/session/start` refuses with 409).
- Image folders as analysis target (videos only).
- Live streaming markers during a run (wait until done).
- Shared frame decode cache between worker and Flask.
- Detector batch_size field.
- "Create labeled video / frame" controls.
- Tracking / filtering / smoothing (Post-Process card after).
- Cross-session warm-worker admin controls.
- Autoscaling or queueing of warm workers.

If a task tempts you toward any of the above, stop and surface it back to the parent — it belongs in a follow-up spec.

---

## Self-Review

**Spec coverage:**

| Spec section | Implemented in task |
|---|---|
| §1 Card layout — file picker w/ unchecked-by-default toggle | T2.1 (test enforces unchecked default) |
| §1 Card layout — Analysis Parameters block | T2.1 |
| §1 Card layout — frame player + marker overlay + marker-edit + curation | T0.2 (factory) + T2.1 (DOM) + T2.2 (mount) |
| §1 Card layout — NO Create-labeled controls | T2.1 (test enforces absence) |
| §1 Project-type gating (multi-animal / TF) | T1.4 — `/session/start` reads `config.yaml`, returns 409 with descriptive error. T2.2 surfaces it in the lastRun status line. No client-side preflight. |
| §2 Data flow & lifecycle events | T1.3 + T1.4 + T1.5 |
| §2 Redis keys (session/queue/result/control) | T1.2 (helpers) + T1.3 (worker uses them) + T1.4 (routes use them) |
| §3 HTTP endpoints (6 routes) | T1.4 |
| §3 Worker `dlc_inline_session` + `_run_range` | T1.3 |
| §3 Reuse DLC primitives (`utils.get_pose_inference_runner`, `VideoIterator`, `video_inference`) | T1.0 (probe) + T1.1 (subclass) + T1.3 (use) |
| §4 Player code reuse (Option B copy-then-deferred-migration) | T0.1 + T0.2 (factory) + T4.1 (policy doc) |
| §4 Header comments on both files | T0.2 (viewer.js) + T0.1 (factory) |
| §4 Static-analysis check for factory + consumer | T0.1 |
| §5 Canonical DLC output files | T1.1 (`_resolve_h5_path`, `_resolve_meta_path`) |
| §5 Atomic write protocol | T1.1 (`_atomic_write_h5`/`_csv` + meta) |
| §5 Skip-already-done semantics | T1.1 |
| §5 Disk-hygiene guards | T3.2 (GPU smoke asserts < 10 MB delta) |
| §5 Failure modes | T1.1 (atomic-write failure test) + worker error path (T1.3) |
| §6 Tests (6 new files) | T0.1, T1.1–1.5, T2.1, T3.1, T3.2 |
| §6 GPU smoke specifics | T3.2 |
| §7 Out of scope | Documented above + UI-isolation test enforces no Create-labeled controls |
| Known tech debt — viewer.js migration follow-up | T0.2 header + T4.1 policy doc note |
| Known tech debt — policy doc broaden | T4.1 |
| Known tech debt — meta-pickle versioning | Not implemented (forward-compat by ignoring unknown fields — matches spec) |
| Known tech debt — DLC internal API smoke | T1.0 (one-shot probe) — recurring smoke is a follow-up |

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" violations. Two areas where this plan instructs the engineer to inspect an existing API and substitute the actual name:

- T2.2: `/dlc/project/snapshots` is verified to exist at `src/dlc/training.py:88`. The project-type check uses no frontend route — it's purely server-side in T1.4 (`_disable_reason` reads `config.yaml` directly).
- T1.4: `_celery_send_task` imports `from celery_app import celery` — confirm `celery_app.py` is the actual module name in this repo (the existing inference blueprint uses the same import pattern; copy it verbatim).

**Type / name consistency:**

- `snap_key` everywhere (never `session_id` in storage; `session_id` is only the API-response alias).
- Worker function names: `_filter_skip_already_done`, `_RangeVideoIterator`, `_atomic_write_h5`, `_atomic_write_csv`, `_update_meta_pickle`, `_resolve_h5_path`, `_resolve_meta_path`, `_preds_to_df`, `_read_pytorch_config`, `_run_range`, `_publish_status`, `_publish_result`, `_bump_activity`, `_control_says_stop`, `_idle_budget`, `_blpop`, `_dlc_inline_session_inner` — used consistently across T1.1–1.5.
- Route paths: all under `/dlc/project/inline-analysis/`. Consistent in T1.4 (definition) and T2.2 (consumption).
- DOM prefix: `ia-` for the new card. Consistent in T2.1 (markup) and T2.2 (factory `prefix: "ia"` and JS `getElementById` calls).
- Redis key shapes: `inline:session:{user_id}:{snap_key}`, `inline:queue:{user_id}:{snap_key}`, `inline:control:{user_id}:{snap_key}`, `inline:result:{req_id}` — match spec §2 verbatim. Used consistently across T1.2/T1.3/T1.4.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-20-inline-analysis.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task within each phase. Review between phase boundaries (esp. between Phase 0 and Phase 1, and between Phase 2 and Phase 3 — those are the natural review checkpoints).

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`. Stop for human review at each Phase Acceptance section.

Which approach?
