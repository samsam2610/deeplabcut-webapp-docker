# Policy: Inline Analysis card + pose-overlay invariants

**TL;DR:** The Inline Analysis player (`src/static/js/inline_analysis_player.js`)
is a **verbatim clone** of the View-Analyzed player (`src/static/js/viewer.js`)
with `va-*` → `ia-*` renamed. Any fix to one MUST be mirrored to the other.
Several non-obvious invariants below, if violated, cause **silent marker
failures** (poses fetch fine, but nothing paints). Each is guarded by a
regression test — don't remove the guard without removing the invariant.

This doc exists because the Inline Analysis card cost a long debugging session
(2026-05-20) where the same class of bug — "markers don't show" — recurred for
five distinct root causes. The point of this file is that the next person never
has to rediscover them.

## The two cards are clones

| | View-Analyzed | Inline Analysis |
|---|---|---|
| Card markup | `src/templates/partials/card_viewer.html` | `src/templates/partials/card_inline_analysis.html` |
| Player JS | `src/static/js/viewer.js` (`va-*`) | `src/static/js/inline_analysis_player.js` (`ia-*`) |
| Loaded by | `main.js` → `import './viewer.js'` | `main.js` → `import './inline_analysis_player.js'` |

`inline_analysis_player.js` was produced by `sed`-renaming `va` → `ia` in
`viewer.js`, then appending an **analysis-dispatch IIFE** at the bottom (snapshot
picker, Analyze button, `/session/*` + `/range*` polling, the done-handler).

**Rule:** a bug fix in the shared player logic (frame loading, overlay drawing,
pose fetching, marker editing, curation) must land in BOTH files. They are kept
deliberately identical except for (a) the `ia`/`va` prefix and (b) the
analysis-dispatch IIFE that only `inline_analysis_player.js` carries.

Guard: `tests/test_inline_analysis_ui_isolation.py::test_cloned_player_is_viewer_js_with_prefix_renamed`
asserts no `va*` identifier leaks into the clone.

> **Known tech debt:** the clone is a copy, not a shared module. This was a
> deliberate choice (the factory-extraction attempt diverged from viewer.js and
> reintroduced bugs). Migrating both cards onto one parameterised module is a
> future cleanup; until then, mirror fixes by hand.

## Invariant 1 — pose layers must clear `errored` on success

**Symptom if violated:** view-only flow (open a video, tick "Show markers",
scrub — without running Analyze) shows nothing, even though the pose fetch
returns valid data.

Each overlay layer has an `errored` flag. A failed/aborted fetch sets it true.
Every draw path skips a layer with `errored === true`
(`_*DrawCurrentFrame`'s `visibleLayers` filter + the primary `_*DrawPoseMarkers`
guard). The flag is **sticky** unless explicitly cleared.

During the rapid video-pick burst (`_*OpenBrowseVideo` fires `_*LoadFrame(0)` +
`_*DiscoverVariants` concurrently) a transient abort/race can set `errored`. If
nothing clears it, the layer is dead forever — later successful fetches cache
poses that never paint.

**Invariant:** a *successful* fetch must set `layer.errored = false`. Implemented
in `_*FetchPosesForFrame` and `_*LoadLayerInfo`. A genuinely broken h5 keeps
failing and stays errored; transient hiccups self-heal.

Guard: `tests/test_view_analyzed_no_compare.py::test_layer_errored_flag_is_cleared_on_successful_fetch`.

## Invariant 2 — analyze writes a DENSE h5

**Symptom if violated:** you analyze frames K..K+N from a non-zero start frame,
the `.h5` lands on disk, but the player shows no markers for those frames.

`/dlc/viewer/frame-poses-batch` (and `frame-poses/<n>`) index rows
**positionally** — `poses_np[frame_number]`, with `n_frames = len(poses_np)`. A
sparse h5 (rows only at the analyzed frame indices, e.g. 24015..24514) makes
`range(24015, min(24015+count, 500))` empty → no frames returned → no markers.

**Invariant:** `tasks.py::_run_range` reindexes the merged DataFrame to a
contiguous `RangeIndex(0..max+1)` with NaN for unanalyzed frames before writing
(DLC's canonical `analyze_videos` h5 is always dense). The full-skip branch
self-heals pre-existing sparse h5s the same way.

Guard: `tests/test_inline_analysis_ui_isolation.py::test_worker_dense_ifies_h5_for_positional_consumers`.

## Invariant 3 — canonical server endpoints only

**Symptom if violated:** 404s that the error handler wraps as 500s; blank player
or blank overlay with no obvious error.

| Purpose | Canonical endpoint | Do NOT invent |
|---|---|---|
| Browse-mode frame image | `/annotate/video-frame/<n>?path=…` | ~~`/annotate/frame?path=…&frame=N`~~ |
| Pose window (prefetch) | `/dlc/viewer/frame-poses-batch?h5=…&start=…&count=…` | ~~`/dlc/viewer/h5-pose-window?…&n=…`~~ |
| Single-frame poses | `/dlc/viewer/frame-poses/<n>?h5=…&threshold=…` | — |
| h5 variant discovery | `/dlc/viewer/h5-variants?video=…` | — |

The batch endpoint returns `{frames: {"<n>": {poses: [{bp,x,y,lh,color_idx}]}}, bodyparts: […]}`.
Note poses use `lh`, not `likelihood`.

Guard: `tests/test_inline_analysis_ui_isolation.py::test_player_uses_canonical_server_endpoints`.

## Invariant 4 — overlay canvas must not eat clicks

**Symptom if violated:** the seek slider and transport buttons are unresponsive;
"the timeline doesn't work."

The `*-overlay-canvas` overlays the frame image. It MUST declare
`pointer-events:none` and `width:100%;height:100%`. Without `width/height`, the
canvas grows to its intrinsic pixel size (e.g. 800×600) and overflows the video
wrap, covering the slider + buttons beneath; with `pointer-events:auto`, it
intercepts their clicks.

Guard: `tests/test_inline_analysis_ui_isolation.py::test_overlay_canvas_does_not_intercept_pointer_events`.

## Invariant 5 — after analyze, force a full frame load

**Symptom if violated:** markers don't appear until you wiggle the threshold
slider.

Two draw paths exist: `_*LoadFrame` (image preload + pose prefetch + `requestAnimationFrame`
paint barrier) and `_*FetchPoses` (poses only, fire-and-forget). The lighter path
can race a layout/sync event that wipes the canvas. The Inline Analysis
done-handler must, after re-discovering variants and ticking the overlay toggle,
`await _iaLoadFrame(_iaCurrentFrame)` — the robust path the threshold slider also
uses.

Guard: `tests/test_inline_analysis_ui_isolation.py::test_player_has_analyze_dispatch_block`
(asserts the discover + toggle wiring; the `_iaLoadFrame` force is part of the
same block).

## Invariant 6 — seek slider is normalised 0..1000

The seek slider's value is a 0..1000 fraction, not an absolute frame index.
`change` maps it back: `frame = round(value/1000 * (frameCount-1))`. Any test or
code that sets `seek.value` must use the normalised form, e.g.
`round(targetFrame / (total-1) * 1000)`. Setting it to a raw frame index clamps
to 1000 → wrong frame.

## Things that are intentionally NOT in these cards

Removed 2026-05-20 (see `docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md`):

- Comparison-layer overlay (`*-overlay-add-compare`, `*-overlay-compare-list`,
  `*-overlay-edit-disabled-banner`) — gone from both cards.
- "Customize threshold per layer" (`*-overlay-customize-thresholds`) — gone;
  the overlay uses a single global threshold (`_*GlobalThreshold`).

Guards (so they can't sneak back via copy-paste):
`tests/test_view_analyzed_no_compare.py` +
`tests/test_inline_analysis_ui_isolation.py::test_card_partial_has_no_compare_layer_markup`
/ `::test_player_js_has_no_compare_layer_symbols`.

The `_*Layers` array is retained but only ever holds a single primary layer; the
primary-select dropdown (which h5 to view) and global threshold/marker-size
sliders all stay.

## How to verify a change to either card

1. `node --input-type=module --check < src/static/js/inline_analysis_player.js`
   (and `viewer.js`) — catches syntax, not runtime.
2. `python -m pytest tests/test_inline_analysis_ui_isolation.py tests/test_view_analyzed_no_compare.py -v`
   — the static invariant guards.
3. **Live marker paint** (the only check that catches the silent failures).
   Drive the card in a headless browser, navigate to a frame that HAS pose data
   (not frame 0, which is usually all-NaN), and count non-transparent pixels on
   `*-overlay-canvas` via `getImageData`. > 100 px ⇒ markers painted. Test BOTH:
   - **view-only** (pick video, tick Show markers, scrub) — exercises invariant 1
   - **analyze** (scrub, click Analyze, wait for "Last run:") — exercises invariant 5
4. Server side is untouched by frontend changes — `/dlc/viewer/*` and
   `/dlc/project/inline-analysis/*` routes own the data contract.

## Worker-side notes (Inline Analysis only)

The warm worker (`tasks.py::dlc_inline_session`) uses DLC's own primitives —
`DLCLoader` for metadata (scorer, bodyparts, multianimal flag),
`utils.get_pose_inference_runner` + `video_inference` for inference,
`create_df_from_prediction` for the DataFrame. It does NOT hand-roll model
loading or DataFrame construction. See
`docs/superpowers/specs/2026-05-20-inline-analysis-design.md` §3.

One warm worker per `(user_id, project, snapshot)` keyed by
`snap_key = sha1(config_path, shuffle, snapshot_path)`. Idle TTL is bumped only
on `/range` submit. `PoseInferenceRunner` does NOT expose `scorer_name` /
`bodyparts` — use `DLCLoader`.
