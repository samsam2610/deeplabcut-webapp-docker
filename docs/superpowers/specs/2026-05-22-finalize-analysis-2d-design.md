# Finalize Analysis (2D inline) — Design

**Date:** 2026-05-22
**Status:** Approved (pending implementation plan)
**Repo:** `deeplabcut-webapp-docker` (main webapp), branch `feat/3d-inline-analysis`

## Context

Inline analysis now writes results to DeepLabCut's default file `<stem><scorer>.h5`
(the "working layer"), while the canonical `<stem>_analyzed.h5` (`src/dlc/canonical.py`)
is reserved for *curated/finalized* output. This spec defines how a user pushes a
reviewed frame range from the working layer into `_analyzed`.

This is **Spec 1 of a 3-part decomposition**:

1. **(this spec) Finalize-analysis for 2D** — the minicard, edit-gating, relocated
   marker-edit controls, and a shared backend endpoint that copies a layer-h5 range
   into `_analyzed`.
2. *(deferred)* Inline **3D focused-tile marker editing** — generalize the inline 3D
   overlay's drag-editing to whichever camera tile is focused, modeled on
   `src/static/frame_labeler_3d.js`'s focused-cam tile pattern.
3. *(deferred)* **Finalize-analysis for 3D** — the minicard + both-cameras copy,
   reusing this spec's backend endpoint and Spec 2's editing.

## Affected files

| File | Change |
|------|--------|
| `src/templates/partials/card_inline_analysis.html` | New Finalize minicard; relocate marker-edit controls below the marker list; remove the top banner |
| `src/static/js/inline_analysis_player.js` | Edit-gating flag; finalize toggle + button wiring; relocated control wiring; last-run capture + autopopulate |
| `src/dlc/inline_analysis.py` | New `POST /dlc/project/inline-analysis/finalize-range` endpoint |
| `tests/test_inline_analysis_ui_isolation.py` | Static markup/wiring assertions |
| `tests/test_inline_analysis_finalize.py` (new) | Backend `finalize-range` behavior |

## Feature 1 — Finalize minicard

A new panel in `card_inline_analysis.html`, placed **directly below** the Dataset
Curation panel (`#ia-curation-panel`), mirroring its toggle+controls structure:

- Toggle checkbox **"Finalize analysis"** (`#ia-finalize-toggle`), **unchecked by default**.
- Hidden controls div `#ia-finalize-controls` (class `hidden`), revealed when checked.
- Inside the controls:
  - **Start frame** number input `#ia-finalize-start`.
  - **Number of frames** number input `#ia-finalize-count`.
  - **"Add range to `_analyzed`"** button `#ia-finalize-add-btn` next to the fields.
  - Status line `#ia-finalize-status` (class `fe-extract-status`).

## Feature 2 — Edit-gating

A module flag `_iaFinalizeEnabled`, set from `#ia-finalize-toggle`'s `change` event.

- The existing drag / marker-edit handlers (mousedown/drag/click that mutate
  `_iaLocalEdits` and call `_iaFlushMarkerEdit`) gain one extra guard: they act only
  when `_iaFinalizeEnabled` is true. So **finalize off → overlay is view-only;
  finalize on → drag-to-edit enabled** on the selected (primary) layer.
- Checking the toggle **auto-enables the Kinematic overlay** if it is off (so markers
  exist to edit): set `iaOverlayToggle.checked = true` and run its enable path.
- Unchecking finalize stops *new* drags only; it does **not** discard pending edits.

## Feature 3 — Relocate marker-edit controls

The current top "Marker Adjustment Banner" (`#ia-marker-edit-banner`) is **removed**.
Its full control group moves to a new row **directly below the marker list**
(`#ia-bp-list-wrap`):

- the "N frames edited" count (`#ia-marker-edit-count`),
- **Save Adjustments** (`#ia-save-adjustments-btn`),
- **Discard** (`#ia-discard-adjustments-btn`),
- **Clear Frame** (`#ia-clear-frame-btn`).

The relocated row is **shown/enabled only when "Finalize analysis" is checked**
(hidden otherwise). All existing button IDs and their JS handlers are preserved —
only their DOM location and the show/hide gate change. The "N frames edited" count
keeps updating from `_iaLocalEdits` so unsaved state stays visible.

## Feature 4 — "Add range to `_analyzed`" button flow

On `#ia-finalize-add-btn` click:

1. **Commit edits to the layer.** Flush any pending marker edits to the current
   primary layer's h5/csv via the existing `/dlc/viewer/save-marker-edits` path
   (same call the relocated Save Adjustments button makes). This is the "save to the
   current viewing layer" step.
2. **Copy range → `_analyzed`.** `POST /dlc/project/inline-analysis/finalize-range`
   with `{ video_path, source_h5: <primary layer path>, start_frame, n_frames }`,
   read from `#ia-finalize-start` / `#ia-finalize-count` and `_iaPrimary().path`.
3. Write the result/counts to `#ia-finalize-status`.

If there is no primary layer selected, the button reports an error to the status line
and does not POST.

## Feature 5 — Backend `POST /dlc/project/inline-analysis/finalize-range`

Added to `src/dlc/inline_analysis.py` (it already imports `canonical as _canonical`,
`_active_project`, `_sec_check`). Request body:

```json
{ "video_path": "...", "source_h5": "...", "start_frame": 0, "n_frames": 500 }
```

Logic:
1. Validate: active project present; `video_path` and `source_h5` exist and pass
   `_sec_check`; `start_frame >= 0`, `1 <= n_frames <= 10000`.
2. Read `source_h5` with pandas. Determine `source_scorer` = the df's column level-0
   value (`df.columns.get_level_values(0)[0]`).
3. Slice rows whose index is in `range(start_frame, start_frame + n_frames)`.
4. `canonical_scorer = _canonical.canonical_scorer(config_path)`.
5. `_canonical.write_to_canonical(video_path, sliced_df, source_scorer=source_scorer,
   canonical_scorer=canonical_scorer, save_as_csv=True)`.
6. Return `{ "h5_path", "csv_path", "n_frames_written" }` (200), or a JSON error.

Because `write_to_canonical` does `new.combine_first(existing)` + column-order pin +
dense reindex, this yields **"curated range wins, out-of-range frames preserved,
`_analyzed` created dense if missing"** with no new merge logic. NaN rows in the slice
(frames not yet analyzed in the layer) do not clobber existing `_analyzed` values.

No change to `canonical.py` — it is reused as-is.

## Feature 6 — Autopopulate from the last run

The range-submit handler stores the submitted values into module vars
`_iaLastRunStart` and `_iaLastRunN` at submit time. The finalize fields populate from
those:

- when a range run completes, and
- when the finalize minicard is opened (toggle checked).

Fields remain user-editable. Before any run in the session, defaults are
`start = _iaCurrentFrame` and `count = ` the "frames per click" value
(`#ia-frames-per-click`).

## Testing

**Static (`tests/test_inline_analysis_ui_isolation.py`):**
- `#ia-finalize-toggle`, `#ia-finalize-controls`, `#ia-finalize-start`,
  `#ia-finalize-count`, `#ia-finalize-add-btn`, `#ia-finalize-status` exist, and the
  finalize panel appears after `#ia-curation-panel`.
- The marker-edit controls (`#ia-save-adjustments-btn`, `#ia-discard-adjustments-btn`,
  `#ia-clear-frame-btn`, `#ia-marker-edit-count`) now sit after `#ia-bp-list-wrap`,
  and `#ia-marker-edit-banner` (top banner) is gone.
- JS references `_iaFinalizeEnabled` and `/dlc/project/inline-analysis/finalize-range`.

**Backend (`tests/test_inline_analysis_finalize.py`, new):**
- Seed a dense layer h5 (`<stem><scorer>.h5`) with known values across a frame range.
- Call the endpoint logic to finalize a sub-range.
- Assert `<stem>_analyzed.h5` and `.csv` exist, contain the layer's values for the
  sub-range under the **canonical** scorer, preserve any pre-existing out-of-range
  `_analyzed` rows, and are dense (RangeIndex 0..max).

## Out of scope

- All 3D work (Specs 2 and 3): 3D focused-tile editing and 3D finalize.
- Any change to `canonical.py` or to the `analysis-file/status` + `initialize` routes
  and the Init button (those remain the separate `_analyzed` init mechanism).
- Auto-creation of `_analyzed` via the Init button as part of finalize (finalize
  creates `_analyzed` itself when missing, via `write_to_canonical`).
