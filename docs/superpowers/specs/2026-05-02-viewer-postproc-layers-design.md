# Viewer Post-Process Layers — Design

**Date:** 2026-05-02
**Branch:** `feat/posture-match-refiner` (continues from the post-process card work)
**Status:** Approved (brainstorm); pending implementation plan

## Problem

The "View Analyzed Videos / Frames" card overlays kinematic markers on a video by reading a single `.h5` file. Today there's exactly one h5 in scope per session, picked by auto-detect (companion next to the video) or manual Browse. Now that the post-process card produces additional `*_filtered.h5` / `*_refined.h5` outputs under `<video_parent>/postproc/<ts>_*/`, users want to:

1. **Discover** every analyzable h5 near a video without manually navigating into `postproc/<ts>_*/`.
2. **Switch** between variants (raw vs filtered vs refine_pipeline, etc.) without re-loading the video.
3. **Compare** variants side by side on the same frame, so the smoothing/filtering effect is visually obvious.
4. **Customize** the likelihood threshold per layer when filtered/refined runs have different confidence distributions.

## Goals

- Surface every detected h5 variant for the loaded video without changing the existing manual-Browse fallback.
- Single dropdown for the **primary** layer + multi-select for **comparison** layers (Q2 = C).
- Render comparison layers in the **same per-bodypart color palette** as primary, differentiated by **shape** (Q3 = B): primary = filled circle, comparison 1 = open circle, 2 = square, 3 = triangle.
- **Edit mode auto-disables** whenever any comparison layer is active (Q4 = B).
- **Shared global threshold by default**, with a "Customize per layer" toggle that exposes per-row sliders (Q5 = C).
- Backend stays minimal: one new variant-discovery route; existing pose / edit routes unchanged.

## Non-Goals

- Per-bodypart visibility *per layer* — bodypart visibility stays global across all layers.
- Skeleton lines on comparison layers (kept primary-only to avoid visual noise).
- Editing on comparison layers (drag-to-adjust applies to primary only, and only in single-variant mode).
- Saving a "comparison preset" (which variants are selected) across sessions — the picker resets to companion-only when a new video is loaded.
- Changing the primary's drawing shape (always filled circle).
- Pixel-diff regression tests of canvas output (manual visual smoke in the Playwright e2e is enough).

## UI

### Where

The existing **Kinematic overlay** panel inside `card_viewer.html`. The single h5 path field + Browse becomes a layered picker; the rest of the panel (overlay toggle, threshold slider, Save Adjustments) stays.

### Layout (top-to-bottom in the overlay panel)

```
☑ Kinematic overlay
─────────────────────────────────
Primary  [▼ Raw — videoDLC_HrnetW48_…h5  (auto-detected) ]   [Browse]
Compare  [+ add comparison ▼]
  · ☑  filtered @ 11:36:42                      [ shape: ○ ]  [×]
  · ☑  refine_pipeline @ 12:04:00               [ shape: □ ]  [×]
  · ☑  filtered @ 14:22:09                      [ shape: △ ]  [×]
─────────────────────────────────
Threshold  ●━━━━━━━━━━ 0.60         ☐ Customize per layer
─────────────────────────────────
[Save Adjustments]   ⚠ Edit disabled (compare mode)
```

### Behaviour

- **Auto-discovery on video load:** `GET /dlc/viewer/h5-variants?video=…`. Populate the **Primary** dropdown with the auto-detected companion h5 first, then every variant under `<video_parent>/postproc/<ts>_*/` whose stem matches the video. Default selection = companion (matches today).
- **Comparison adder:** `+ add comparison` opens a dropdown of variants not yet in primary or compare. Each selection becomes a row with: visibility checkbox, label, auto-assigned shape, remove button.
- **Threshold UI:**
  - Default: one global slider applies to every visible layer.
  - Tick **Customize per layer** → each row (primary + each compare) gains its own slider, defaulting to the global value at toggle time. Untick → all revert to global; per-layer values are not stashed (documented in tooltip).
- **Edit mode gate:** drag-to-edit + Save Adjustments enabled only when `compareLayers.length === 0`. Otherwise drag handlers no-op and a banner reads "Edit disabled while comparing layers — remove comparisons to edit."
- **Variant labels:** `<type> @ <HH:MM:SS>` where `type ∈ {filtered, refine_pipeline, refine_lh, refine_outliers, refine_interp, refine_smooth}` (matches the post-process tool tags). The companion h5 is labeled `Raw — <basename>`. If two variants collide on `HH:MM:SS`, the label expands to `… @ YYYY-MM-DD HH:MM:SS`.
- **Browse fallback:** the existing manual Browse stays — point it at any `.h5` anywhere on disk; that becomes the primary, labeled `Custom — <basename>`. For comparisons, only the auto-discovered list is selectable (keeps the multi-select bounded and shape-assignment deterministic).

### Shape assignment

```
primary       → circle-filled       (matches today's marker shape)
comparison 1  → circle-open
comparison 2  → square
comparison 3  → triangle
≥ 4 comparisons → all share triangle (console warn; UI need not prevent)
```

### IDs (for unique-id assertions)

New: `va-overlay-primary-select`, `va-overlay-compare-list`, `va-overlay-add-compare`, `va-overlay-customize-thresholds`, plus per-row dynamic IDs `va-layer-row-<n>`, `va-layer-threshold-<n>`, `va-layer-remove-<n>`.

Retained (so the manual Browse fallback keeps working): `va-overlay-h5-path`, `va-overlay-h5-browse`, `va-overlay-h5-browser`.

## Backend

### One new route

`GET /dlc/viewer/h5-variants?video=<abs-path>`

Pure filesystem scan. No DLC import. Reuses `_dlc_project_security_check` for the user-data allowlist.

**Algorithm:**

1. `video_dir = Path(video).parent`, `video_stem = Path(video).stem`.
2. **Companion** detection: same rules as the existing `_h5_find` in `dlc/viewer.py` — match `<video_stem>{DLC_*}.h5` patterns, exclude any name containing `_filtered` or `_refined`. Up to one entry, type=`raw`.
3. Walk `video_dir / "postproc" / *` (one level under `postproc/`). For each subfolder:
   - Parse `run_id = dirname`. Parse `tool_tag` from the dirname's `<YYYYMMDD-HHMMSS>_<tool_tag>` shape; `ts` from the timestamp prefix.
   - List every `*.h5` whose stem starts with `<video_stem>` (so we don't surface other videos' outputs that share the parent).
   - Emit one entry per matching h5; derive `type` from `tool_tag`.
4. Read each entry's sidecar `run.json` (when present) and surface `status` so the UI can grey out failed runs.
5. Sort: companion first, then variants by `ts` descending.

### Response shape

```json
{
  "video": "/abs/path/to/video.mp4",
  "variants": [
    {
      "path":     "/abs/.../videoDLC_HrnetW48_….h5",
      "label":    "Raw — videoDLC_HrnetW48_…h5",
      "type":     "raw",
      "run_id":   null,
      "tool_tag": null,
      "ts":       null,
      "status":   null,
      "disabled": false
    },
    {
      "path":     "/abs/.../postproc/20260502-113642_filterpredictions/videoDLC_HrnetW48_…_filtered.h5",
      "label":    "filtered @ 11:36:42",
      "type":     "filtered",
      "run_id":   "20260502-113642_filterpredictions",
      "tool_tag": "filterpredictions",
      "ts":       "2026-05-02T11:36:42Z",
      "status":   "success",
      "disabled": false
    },
    {
      "path":     "/abs/.../postproc/20260502-120400_refine_pipeline/videoDLC_HrnetW48_…_refined.h5",
      "label":    "refine_pipeline @ 12:04:00",
      "type":     "refine_pipeline",
      "run_id":   "20260502-120400_refine_pipeline",
      "tool_tag": "refine_pipeline",
      "ts":       "2026-05-02T12:04:00Z",
      "status":   "success",
      "disabled": false
    }
  ]
}
```

`disabled: true` when `status == "failed"`; the UI greys those out in the dropdown.

### Tool-tag → label table (server-side)

```python
_LABEL_BY_TYPE = {
    "raw":              "Raw",
    "filtered":         "filtered",          # filterpredictions output
    "refine_pipeline":  "refine_pipeline",
    "refine_lh":        "refine_lh",
    "refine_outliers":  "refine_outliers",
    "refine_interp":    "refine_interp",
    "refine_smooth":    "refine_smooth",
}
```

The `_filtered` / `_refined` suffix in the filename is the source of truth for `type` when no sidecar is present (defensive: handle older runs without `run.json`).

### What stays untouched

- `/dlc/viewer/h5-info` — already keyed by `h5` path.
- `/dlc/viewer/frame-poses/<frame>?h5=…` and `/dlc/viewer/frame-poses-batch` — already keyed by `h5`. Each layer fetches independently.
- `/dlc/viewer/marker-edit`, `/dlc/viewer/save-marker-edits`, `/dlc/viewer/edit-cache` — unchanged. Frontend gates editing to single-variant mode; backend doesn't need to know.
- The server-side `_viewer_h5_cache` LRU (currently max 5) gets bumped to **12** so a multi-variant session with 4 active layers + a couple of recently-closed entries doesn't constantly evict.

## JS architecture

Refactor `viewer.js` in place to a layered model.

### Layer object

```js
// Element 0 = primary; rest = comparisons.
const _vaLayers = [];

// {
//   id:           "layer_0" | "layer_1" | …          unique key for DOM nodes
//   path:         "/abs/.../foo.h5"                  request key
//   label:        "Raw — foo.h5"                     UI text
//   type:         "raw" | "filtered" | "refine_…"    drives shape assignment
//   shape:        "circle-filled" | "circle-open" | "square" | "triangle"
//   visible:      true                                checkbox state
//   threshold:    null | number                       null = use global
//   posesCache:   Map<frame, Array<bodyparts>>       per-layer
//   bodyparts:    string[]                            from /h5-info
//   editsCache:   Map (only present on layer 0)      from /edit-cache
//   errored:      false                              true if path 404'd mid-session
// }

let _vaGlobalThreshold = 0.6;
let _vaPerLayerThresholds = false;   // Customize per layer toggle
```

### Helpers

```js
function _vaPrimary()      { return _vaLayers[0]; }
function _vaCompare()      { return _vaLayers.slice(1); }
function _vaIsEditable()   { return _vaLayers.length === 1; }
function _vaLayerThreshold(layer) {
  return _vaPerLayerThresholds && layer.threshold != null
    ? layer.threshold
    : _vaGlobalThreshold;
}
```

### Refactor map

| Old (single-state) | New (layered) |
|---|---|
| `_vaH5Path` (string) | `_vaPrimary().path` |
| `_vaPoseCache` (Map) | `layer.posesCache` per layer |
| `_vaThreshold` (num) | `_vaLayerThreshold(layer)` per draw |
| `_vaLoadH5Info(path)` | `_vaLoadLayerInfo(layer)` |
| `_vaFetchPosesForFrame(frame)` | `_vaFetchPosesForFrame(layer, frame)` |
| `_vaFetchPosesBatch(fromFrame)` | called per-layer |
| `_vaDrawOverlay(frame)` | iterates `_vaLayers.filter(l => l.visible && !l.errored)` |
| `_vaLoadEditCacheFromServer` | only called for primary |
| marker-edit POST (drag-end) | gated by `_vaIsEditable()`; targets `_vaPrimary().path` |

### Drawing primitives (new helpers)

```js
const _SHAPE_ORDER = ["circle-filled", "circle-open", "square", "triangle"];
const _SHAPE_FN    = {
  "circle-filled": _drawCircleFilled,
  "circle-open":   _drawCircleOpen,
  "square":        _drawSquare,
  "triangle":      _drawTriangle,
};
function _vaAssignShapes() {
  _vaLayers.forEach((l, i) => { l.shape = _SHAPE_ORDER[Math.min(i, _SHAPE_ORDER.length - 1)]; });
}
```

Color palette stays keyed by **bodypart name** so the same name renders the same color across all layers.

### Variant discovery flow (replaces `_vaAutoDetectH5`)

1. Video opened → `GET /dlc/viewer/h5-variants?video=…`.
2. Populate Primary `<select>` from `response.variants`. Default = first `type === "raw"`, else first variant overall.
3. Comparisons start empty.
4. On primary change OR compare add/remove → rebuild `_vaLayers`, clear stale caches, reissue `_vaLoadFrame(_vaCurrentFrame)`.

### Cache invalidation triggers

Clear `posesCache` for the affected layer when:

- Layer's effective threshold changes (global slider OR its own slider).
- `Customize per layer` toggle flips.
- Save-adjustments completes (primary only).
- Layer added or removed (only the affected layer; others retained).

### Migration of single-variant globals

`_vaH5Path`, `_vaPoseCache`, `_vaThreshold` are removed. Anywhere reading them now reads from a layer reference. `va-overlay-h5-path` is kept and re-roled as the **primary's** path display (and is still the target of the manual Browse fallback).

## Error handling

- **`/h5-variants` returns no variants** (no companion, no postproc): hide the Primary dropdown; show a hint "No analyzed h5 found near this video — use Browse." The manual Browse stays usable. Same as today's empty state.
- **Layer's path 404s mid-session** (e.g., sidecar deleted): per-layer `_vaFetchPoses*` flips `layer.errored = true`; the row gains a red badge; that layer is skipped in the draw loop until removed/reselected.
- **Multiple variants share `HH:MM:SS`** (re-runs within the minute on different days): label expands to include the date.
- **Failed runs in the response** (`status: "failed"`): emitted with `disabled: true`; greyed out and not selectable in the comparison dropdown.
- **Layer's bodyparts differ from primary's** (refine on a different scorer): each layer renders what it has; one console warning. Bodypart-color-palette stays keyed by name so identical names match across layers.
- **Edit attempt while compare-mode active**: drag handler returns early; no `marker-edit` POST sent; banner explains.
- **Primary changed with unsaved drag adjustments**: prompt "Switching primary will discard N unsaved edits. [Cancel] [Discard]".
- **Customize per layer toggled OFF when layers had different overrides**: revert to global; per-layer values not stashed (tooltip documents this).

## Testing

| Test file | Coverage |
|---|---|
| `tests/test_dlc_viewer_routes.py` (existing — extend) | `test_h5_variants_finds_companion_and_postproc`: build a tmp tree with a companion h5 + two postproc subfolders + sidecar JSON, hit `/dlc/viewer/h5-variants?video=…`, assert variant list shape and ordering. Also: empty (no h5), failed-status surfacing, video-stem filtering (only matching stems returned), allowlist enforcement (out-of-tree path → 400). |
| `tests/test_postprocess_real_project.py` (existing — extend) | One real-data integration test against the **OM-2 RatBox folder** at `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10/` (host: `/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/tdcs/042426/…`). Steps: (1) run the post-process median filter on every analyzable file there (re-uses the existing dlc_postprocess_run task); (2) call `/dlc/viewer/h5-variants?video=<one of the .avi files>`; (3) assert the response includes a `raw` companion entry AND a `filtered` entry whose `path` lives under that video's `postproc/<ts>_filterpredictions/`. Skipped when the synology mount is absent on this host. |
| `tests/test_viewer_layers_ui_isolation.py` (new) | Static-template assertions: new IDs (`va-overlay-primary-select`, `va-overlay-compare-list`, `va-overlay-add-compare`, `va-overlay-customize-thresholds`) are unique across all partials; the existing `va-overlay-h5-path` / `va-overlay-h5-browse` / `va-overlay-h5-browser` IDs are retained (Browse fallback). Asserts `viewer.js` references the new IDs and still references the legacy IDs. |
| `tests/e2e_viewer_layers_smoke.py` (new — Playwright, manual run) | Drives the live app: open a video from the OM-2 RatBox folder that already has a companion h5 + at least one postproc variant on disk; verify the Primary dropdown is populated; add a comparison; verify the "Edit disabled (compare mode)" banner appears and Save Adjustments is disabled; remove the comparison; verify edit re-enabled; toggle Customize per layer + drag a per-layer slider; verify only that layer's poses re-cache (network panel shows one fetch, not two). |
| Frontend regression sweep | After each commit run `python -m pytest tests/test_frontend_assets.py` and the existing post-process tests; confirm no JS asset checks regress. |

The real-data integration test is **required** before declaring this feature done, per CLAUDE.md's testing convention. The OM-2 RatBox folder is the same one we used for the post-process card e2e (commit `bd900d1`), so the test infrastructure already knows the path conventions.

## File summary

**New files:**

- `tests/test_viewer_layers_ui_isolation.py`
- `tests/e2e_viewer_layers_smoke.py`

**Modified files:**

- `src/dlc/viewer.py` — add `/dlc/viewer/h5-variants` route + helpers; bump `_viewer_h5_cache` capacity 5 → 12.
- `src/templates/partials/card_viewer.html` — replace the single-path overlay UI with the Primary dropdown + Compare list + Customize-per-layer toggle. Keep manual Browse fallback.
- `src/static/js/viewer.js` — refactor scalar overlay state to `_vaLayers` array; add discovery/fetch/draw helpers per layer; add shape primitives; gate edit on single-variant mode.
- `tests/test_dlc_viewer_routes.py` — add the variant-discovery route tests.
- `tests/test_postprocess_real_project.py` — add the OM-2 RatBox real-data integration test.

## Open items for the implementation plan

- Confirm the exact existing color-palette function in `viewer.js` so the layered code re-uses it as-is (don't fork).
- Confirm whether `_vaLoadH5Info` parses bodyparts from the same place across all DLC scorer outputs (raw vs HrnetW48 etc.) — verify by running `/dlc/viewer/h5-info` against one of the OM-2 `.h5` files before refactoring.
- The "discard unsaved edits when primary changes" prompt may need a custom modal; check if `viewer.js` already has a confirm helper or whether we use `window.confirm`.
- LRU bump from 5 → 12 in `_viewer_h5_cache` is a guess; if memory becomes an issue with large projects, lower it back and add a Redis-backed pose cache later (out of scope here).
