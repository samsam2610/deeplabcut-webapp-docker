# Remove Compare-Layer + Per-Layer Threshold — Design Spec

**Date:** 2026-05-20
**Status:** Approved for implementation planning
**Branch:** `feat/test-set-picker`
**Affects:** View-Analyzed card + Inline Analysis card

## Goal

Strip two unused/confusing features from both pose-overlay cards:

1. **Comparison-layer overlay** — the "+ add comparison…" dropdown that let users load multiple h5s side-by-side for visual diff, with the "Edit disabled while comparing" banner that follows.
2. **Customize threshold per layer** — the toggle that swaps the single global threshold slider for per-layer threshold sliders.

User decision: neither feature is wanted; both should be removed cleanly from both cards without disturbing primary-layer rendering, the global threshold slider, marker editing, or any other current behavior.

## §1 — Scope of removal

### Markup (both partials)

| DOM id (va- / ia-) | Notes |
|---|---|
| `*-overlay-compare-block` | wrapping div for the compare UI |
| `*-overlay-add-compare` | "+ add comparison…" `<select>` |
| `*-overlay-add-compare-empty-hint` | fallback text when no variants left |
| `*-overlay-compare-list` | rendered list of compare-layer rows |
| `*-overlay-edit-disabled-banner` | "edit disabled while comparing" notice — banner has no purpose without compare layers |
| `*-overlay-customize-thresholds` | per-layer-threshold checkbox |
| `*-overlay-primary-threshold-slot` | per-primary inline threshold slider host (only inserted when customize is on) |

### JS (`viewer.js` + `inline_analysis_player.js`)

Functions / state to delete:

- `_vaCompare()` / `_iaCompare()` — getter returning non-primary `_vaLayers` entries
- `_vaAddCompare()` / `_iaAddCompare()` — appends a compare layer
- `_vaRemoveCompare()` / `_iaRemoveCompare()` — removes a compare layer
- `_vaRenderCompareRows()` / `_iaRenderCompareRows()` — builds the row UI
- `_vaRefreshAddComparisonOptions()` / `_iaRefreshAddComparisonOptions()` — populates the dropdown
- `_vaRenderPrimaryThresholdInline()` / `_iaRenderPrimaryThresholdInline()` — per-layer threshold slider host
- `_vaPerLayerThresholds` / `_iaPerLayerThresholds` — flag + its toggle handler
- `_vaLayerThreshold(layer)` collapses to "return `_vaGlobalThreshold`" — keep the function only if other call sites benefit; otherwise inline
- `_vaIsEditable()` / `_iaIsEditable()` — function exists solely to gate editing when compare-layers present; with compare gone, every `if (!_vaIsEditable()) return;` guard is dead code and the function itself is removed

Every call site of the deleted symbols must drop with them; no stub-only shims left behind.

The `_vaLayers` / `_iaLayers` array stays — it now only ever holds the single primary layer. Existing iteration code (`_vaLayers.forEach`, `_vaLayers.filter`) continues to work with a 1-element array; no behavioural change.

### Tests

Delete entirely:
- `tests/e2e_viewer_layers_smoke.py`
- `tests/test_viewer_layers_ui_isolation.py`

Add regression guards in the existing `tests/test_inline_analysis_ui_isolation.py` and a sibling new file `tests/test_view_analyzed_no_compare.py`:

- assert neither partial contains substrings `compare`, `customize-threshold`, `add-compare`, `compare-list`, `edit-disabled-banner`, `primary-threshold-slot`
- assert neither JS file references `_*Compare`, `_*IsEditable`, `_*PerLayerThresholds`, `_*RenderCompareRows`, `_*AddCompare`, `_*RemoveCompare`, `_*RefreshAddComparisonOptions`

## §2 — What keeps working (no behaviour change)

Hard invariants — implementation MUST preserve these:

1. **Primary-layer selection** — `*-overlay-primary-select` dropdown still works; picking a different h5 still calls `_vaApplyPrimaryFromSelect()` which still loads bodyparts + first frame's poses.
2. **Global threshold slider** — `*-overlay-threshold` keeps its current behavior: changing the value invalidates pose caches and triggers `_vaLoadFrame(_vaCurrentFrame)`.
3. **Marker-size slider** — unchanged.
4. **Body-part chips** — `*-bp-chips` per-bodypart visibility toggle still works.
5. **Marker drag-to-edit + Save/Discard/Clear-Frame** — unconditionally enabled (no compare layers → no reason to disable). Marker-edit banner still shows the unsaved-edits count + Save button.
6. **Frame navigation** — seek slider, prev/play/next buttons, fps/step inputs, skip-back/forward, zoom slider.
7. **Pose fetching** — `/dlc/viewer/frame-poses-batch` (window prefetch) and `/dlc/viewer/frame-poses/<n>` (single frame) keep their existing query shape: `h5`, `start`, `count`, `threshold`, `parts`.
8. **Dataset Curation panel** — extract / add-to-dataset / batch-add / CSV status+notes / annotation panel — completely untouched.
9. **Inline-analysis-specific surfaces** — snapshot picker, Analyze button, range polling, done-handler that auto-discovers h5 variants + ticks overlay toggle + force-runs `_iaLoadFrame`. Untouched.

## §3 — Cleanup strategy

The work is mostly mechanical deletion. Specifically:

- **Markup**: delete the comp/customize block + its children (HTML).
- **JS**: delete the named functions + state variables. Then sweep call sites:
  - Every `if (!_vaIsEditable()) return;` line drops (~10 occurrences per file).
  - Every reference to `_vaCompare()` / `_iaCompare()` evaluates against an always-empty set; the code path was already a no-op for the single-primary case, so removing the call is safe.
  - Every reference to `_vaPerLayerThresholds` / `_iaPerLayerThresholds` collapses to `false` (global-only path). The dead branches drop.
  - `_vaLayerThreshold(layer)` becomes `_vaGlobalThreshold` everywhere.

The `_vaLayers` array stays. Code that does `_vaLayers.forEach(l => ...)` or `_vaLayers.filter(l => l.visible)` continues to work with a single-element array.

`_vaApplyPrimaryFromSelect` already starts with `_vaLayers.length = 0` then pushes the new primary — that's correct for a single-layer world and stays as-is.

## §4 — Verification

1. **Static checks**:
   - `node --input-type=module --check` on both JS files passes.
   - `pytest tests/test_inline_analysis_ui_isolation.py` (existing 12 tests + new compare-absent guards) passes.
   - New `tests/test_view_analyzed_no_compare.py` passes.
2. **Live UI smoke (Playwright)** against the running flask:
   - **View-Analyzed**: open the card, pick an existing analyzed video from Project Content, confirm markers render on frame 0. Drag the global threshold slider — markers still re-fetch + re-paint. Edit a marker (drag) — Save Adjustments still works.
   - **Inline Analysis**: open the card, browse to the user's `050726/khoai-lang-1*.avi`, scrub to frame 24015, click Analyze (all-skipped), confirm markers paint immediately (the prior race-fix is preserved).
3. **No console errors** in either card.
4. **Confirm via curl** the pose endpoints still respond with canonical `{frames: {<n>: {poses: [...]}}}` shape — server side is untouched, so this is just a sanity check.

## §5 — Out of scope (explicit)

- **No server-side changes**. `/dlc/viewer/frame-poses*` endpoints, the `h5-variants` route, edit-cache routes, save-marker-edits route — all untouched.
- **No data-format changes** in h5 or csv files.
- **No removal of the Primary-layer dropdown** — picking which h5 to view still matters even without comparison.
- **No removal of marker editing** — only the "edit disabled while comparing" guard is gone; editing itself stays.
- **No refactor of `_vaLayers` into a single-variable** — keeping the array means the diff stays focused on deletion; flattening can happen later as tech debt cleanup.

## §6 — Files touched

| Path | Action |
|---|---|
| `src/templates/partials/card_viewer.html` | edit — drop compare + customize-threshold markup |
| `src/templates/partials/card_inline_analysis.html` | edit — drop compare + customize-threshold markup |
| `src/static/js/viewer.js` | edit — drop the listed functions, state, call sites |
| `src/static/js/inline_analysis_player.js` | edit — same as viewer (the file is a clone-with-rename) |
| `tests/e2e_viewer_layers_smoke.py` | **delete** |
| `tests/test_viewer_layers_ui_isolation.py` | **delete** |
| `tests/test_inline_analysis_ui_isolation.py` | edit — add compare-absent regression guards |
| `tests/test_view_analyzed_no_compare.py` | **create** — minimal regression guard for the View-Analyzed card |

## §7 — Acceptance criteria

- Compare-related DOM IDs and JS symbols are all gone (verified by both regression tests and `grep`).
- All Playwright smokes pass.
- No console errors on either card.
- `git diff --stat` shows ~100-200 lines net deleted per JS file + ~30 lines markup per partial + 2 deleted test files. No new code in production paths.
