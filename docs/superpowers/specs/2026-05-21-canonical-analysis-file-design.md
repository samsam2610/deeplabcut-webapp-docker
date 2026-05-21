# Canonical Analysis File (`<video>_analyzed.h5/.csv`) — Design Spec

**Date:** 2026-05-21
**Status:** Approved for implementation planning
**Branch:** `feat/3d-inline-analysis` (both repos — this builds on the inline-analysis cards that live there)
**Repos:** main webapp `deeplabcut-webapp-docker` (backend + analyze card + inline 2D), dlc-3D module `deeplabcut-webapp-docker-supports/dlc-3D` (inline 3D UI only)

## Goal & motivation

Today every analysis run writes a new scorer-named h5 next to the video
(`<videostem>DLC_<net>_<project>shuffleN_snapshot_<snap>.h5`), so a video
accumulates many analysis files (one per snapshot/shuffle). This clutters the
folder and makes "which file is the analysis?" ambiguous.

This feature gives each video **one canonical analysis file**,
`<videostem>_analyzed.h5` + `<videostem>_analyzed.csv` (DeepLabCut format), that
**all** analysis writes into. The user creates it once via an **"Initialize
analysis file"** button; once it exists it is **locked** — it cannot be
re-initialized unless the user deletes it manually elsewhere.

## Approved decisions

1. **All analysis → the canonical file.** Full "Analyze Video/Frames" runs AND
   inline analysis (2D + 3D) write into `<video>_analyzed.h5` — no more
   scorer-named files going forward.
2. **Fixed scorer set at init.** The canonical file's column `scorer` level is
   the project's configured `scorer` (from `config.yaml`), fallback constant
   `DLC_analyzed`. All analysis output is re-labeled to this scorer on write.
3. **Dense, all-NaN empty file.** Initialize probes the video's frame count `N`
   and writes rows `0..N-1`, all bodyparts NaN (satisfies the dense-h5 viewer
   invariant; analysis fills rows in as it runs).
4. **Optional, auto-create on first analysis.** Initialize is a manual
   convenience that pre-stamps the empty file + claims the name. Analyzing a
   video whose canonical file does not exist auto-creates it on first write.
   The lock still prevents *re-initializing* an existing file.

## Approach: A — consolidate-on-write helper

A single backend helper funnels every analysis write through one place:

```
_write_to_canonical(video_path, df, *, source_scorer, canonical_scorer) -> (h5_path, csv_path)
    1. Re-label df's top column level (source_scorer → canonical_scorer).
    2. Merge into <videostem>_analyzed.h5:
         existing = read if present
         merged   = df.combine_first(existing)          # new data wins
         dense    = merged.reindex(RangeIndex(0..max+1)) # dense-h5 invariant
    3. Atomic write h5 + sibling .csv.
```

- **Inline 2D/3D** (`tasks.py::_run_range`): replace `_resolve_h5_path(video, scorer)`
  + direct write with `_write_to_canonical(...)`.
- **Full Analyze Video** (`tasks.py::dlc_analyze_videos`, Phase 2): after DLC
  produces its scorer-named output, read it → `_write_to_canonical(...)` →
  delete the scorer-named original.

Rejected: (B) making DLC's `analyze_videos` write the canonical name directly —
DLC controls output naming internally, not reliably overridable. (C) rename-only
— loses scorer-stability and the merge of multiple runs into one file.

## §1 — Canonical file mechanics

| Item | Value |
|---|---|
| h5 path | `<video_parent>/<video_stem>_analyzed.h5` |
| csv path | `<video_parent>/<video_stem>_analyzed.csv` |
| schema | DLC MultiIndex columns `(scorer, bodypart, [x,y,likelihood])`, index = frame number |
| scorer | `config.yaml` `scorer:` (fallback `DLC_analyzed`), fixed at init |
| empty content | dense rows `0..N-1`, all NaN |

New helpers in `src/dlc/tasks.py` (alongside the existing `_resolve_h5_path`,
`_atomic_write_h5`, `_atomic_write_csv`, `_dlc_create_df_from_prediction`):

- `_canonical_h5_path(video_path) -> Path` → `<stem>_analyzed.h5`.
- `_canonical_scorer(config_path) -> str` → config `scorer` or `DLC_analyzed`.
- `_build_empty_dense_df(scorer, bodyparts, nframes) -> DataFrame`.
- `_write_to_canonical(video_path, df, *, source_scorer, canonical_scorer)`.

`N` (frame count) is probed with the existing video-info path used by
`/dlc/project/inline-analysis/video-info` (OpenCV `CAP_PROP_FRAME_COUNT`).
`bodyparts` come from `config.yaml` `bodyparts:` (existing read at tasks.py ~1627).

## §2 — Initialize + status routes + lock

New routes (project-level; add to the dlc project blueprint or a small new
`analysis_file.py` blueprint):

- **POST `/dlc/project/analysis-file/initialize`** body `{video_path}`
  - If `<stem>_analyzed.h5` already exists → **409 Conflict** `{error: "already initialized", h5_path}` (the file's existence IS the lock).
  - Else: probe `N`, read bodyparts + canonical scorer, build dense all-NaN df, atomic-write h5 + csv → **201** `{h5_path, csv_path, nframes}`.
  - Security: `video_path` must pass the existing path-allow check (under `/user-data/...`).
- **GET `/dlc/project/analysis-file/status?video_path=…`**
  - → `{initialized: bool, h5_path, csv_path, nframes?}` so the UI can render button state without trying to create anything.

The **lock** is purely "the file exists." Re-initialization requires the user to
delete the file manually elsewhere (the app never deletes it via this feature).

## §3 — Redirecting analysis to the canonical file

**Phase 1 (this spec's core):**
- Inline 2D + 3D both dispatch through `tasks.py::_run_range`. Change `_run_range`
  to compute `canonical_scorer = _canonical_scorer(config)`, run inference under
  the snapshot's `source_scorer` as today, then write via `_write_to_canonical`
  instead of `_resolve_h5_path(scorer)` + direct write. The existing
  merge/dense-ify logic moves into the helper.
- 3D: cam0 and cam1 each dispatch their own `_run_range` (already the case), so
  each gets `<camNstem>_analyzed.h5`. The sibling resolution
  (`_resolve_sibling_h5`, `_cam0_`→`_cam1_` substitution) still works because
  `_cam{N}_` precedes `_analyzed` in the name.

**Phase 2 (sequenced after Phase 1 is verified):**
- Full `dlc_analyze_videos` task: after DLC writes its scorer-named h5 (and any
  labeled-video / filtering steps that read it complete), consolidate into the
  canonical file via `_write_to_canonical`, then delete the scorer-named
  original. **Risk:** labeled-video creation + post-filtering reference the
  scorer-named path; the plan must keep those steps reading the scorer file
  *before* consolidation, or adapt them to the canonical name. This is the
  riskiest piece, hence deferred to Phase 2.

## §4 — UI: "Initialize analysis file" button

All buttons call the status endpoint to render state: **`○ Initialize analysis
file`** (available) vs **`✓ Analysis file ready`** (disabled, file exists).

- **Analyze Video/Frames card** (`card_analyze.html` / `analyze.js`): per queued
  video in `av-batch-list`, add a small init button + status chip in the row.
  Clicking initializes that video's canonical file.
- **Inline Analysis 2D** (`card_inline_analysis.html` / `inline_analysis_player.js`):
  a button in the params area, acting on the currently selected/opened video;
  disabled when already initialized.
- **Inline Analysis 3D** (`card_inline_analysis_3d.html` / `inline_analysis_3d.js`):
  a single **"Initialize analysis files (both cameras)"** button that initializes
  cam0 + its resolved cam1 sibling (3D always analyzes both); shows per-camera
  result/status.

All three call `POST /dlc/project/analysis-file/initialize` (the dlc-3D page is
same-origin, so it reaches the main-webapp route directly).

## §5 — Discovery / viewer

The viewer's discovery glob (`viewer.py` `_h5_variants_for_video` →
`parent.glob(f"{stem}*.h5")`, excluding `_filtered`/`_refined`) already matches
`<stem>_analyzed.h5` (the `*` matches `_analyzed`). **Implementation must verify
this glob actually matches `_analyzed`** and that `_analyzed` is not
accidentally excluded; if excluded, broaden the include. No new discovery code
expected.

## §6 — Edge cases

- **Video frame count unprobeable:** initialize returns 422 with a clear message;
  no file written.
- **Bodyparts missing from config:** 422 "project has no bodyparts".
- **Concurrent initialize / race:** the atomic write + the pre-existing-file 409
  check make double-init a no-op (second caller gets 409).
- **Legacy scorer-named files already on disk:** left as-is (backward compatible);
  discovery still finds them. New analysis writes only the canonical file.
- **`combine_first` scorer mismatch:** since we re-label to the canonical scorer
  before merge, existing canonical data and new data share the scorer level —
  no column-key mismatch.
- **3D sibling has no analysis file yet:** "Initialize both" creates whichever of
  cam0/cam1 is missing; if a camera's file already exists it is reported
  "already initialized" (not an error for the batch).

## §7 — Phasing

- **Phase 1:** §1 helpers, §2 routes + lock, §3 inline 2D/3D redirect, §4 UI in
  all three cards, §5 discovery verification. Delivers the full
  "one file per video" behavior for the inline paths + manual init everywhere.
- **Phase 2:** §3 full Analyze Video redirect (consolidate + delete scorer-named).

The implementation plan sequences Phase 2 strictly after Phase 1 is verified.

## §8 — Files touched

| Path | Phase | Change |
|---|---|---|
| `src/dlc/tasks.py` | 1 | add `_canonical_h5_path`, `_canonical_scorer`, `_build_empty_dense_df`, `_write_to_canonical`; route `_run_range` through it |
| `src/dlc/analysis_file.py` (or dlc project blueprint) | 1 | new initialize + status routes |
| `src/templates/partials/card_analyze.html` + `src/static/js/analyze.js` | 1 | per-row init button + status |
| `src/templates/partials/card_inline_analysis.html` + `src/static/js/inline_analysis_player.js` | 1 | init button in params |
| `dlc-3D/src/templates/partials/card_inline_analysis_3d.html` + `dlc-3D/src/static/inline_analysis_3d.js` | 1 | "Initialize both cameras" button |
| `src/dlc/viewer.py` | 1 | verify/adjust discovery glob for `_analyzed` |
| `src/dlc/tasks.py::dlc_analyze_videos` | 2 | consolidate scorer-named → canonical + delete |

## §9 — Testing

- **Backend unit (pytest):** `_build_empty_dense_df` produces correct DLC schema +
  dense NaN rows; `_write_to_canonical` re-labels scorer + merges + dense-ifies;
  initialize route 201 then 409 on re-init; status route reports correctly.
  (Use small synthetic h5s; respect the disk-cleanup conftest hooks — no large
  pytest leakage.)
- **Live (Playwright):**
  - Analyze card: queue a video, click Initialize → status flips to ✓; click
    again → no-op/disabled.
  - Inline 2D: select a video, Initialize, run a small analysis → markers paint
    from `<stem>_analyzed.h5` (verify the file exists + is the only new h5).
  - Inline 3D: "Initialize both" creates cam0 + cam1 `_analyzed.h5`; run analysis
    → both tiles paint from the canonical files.
- **Static guards:** init button present + wired in all three cards; `_run_range`
  writes via `_write_to_canonical` (no `_resolve_h5_path(scorer)` write in the
  inline path).

## §10 — Acceptance criteria

- Clicking "Initialize analysis file" creates `<stem>_analyzed.h5` + `.csv`
  (dense, all-NaN, DLC schema, canonical scorer) once; a second click is a
  no-op/409 while the file exists.
- After init (or auto-create), inline 2D + 3D analysis writes only the canonical
  file; the viewer discovers + renders markers from it.
- No new scorer-named files are created by the inline paths.
- Phase 2: full Analyze Video runs also consolidate into the canonical file.
- Tests + live smokes pass; no console errors.
