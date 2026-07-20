# Inline 3D — Triangulate keyframe range (incremental anipose) — Phase 2

**Date:** 2026-07-20
**Repos:** main (`deeplabcut-webapp-docker`) + support module (`dlc-3D`)
**Depends on:** Phase 1 (`Triangulate` panel + `/dlc-3d/anipose/init` scaffolding `calibration/` + `pose-2d/`).

## Goal

Add a button to the inline-3D **Triangulate** panel that triangulates **only the current
keyframe range** of the selected stereo pair using the existing anipose code, applies
anipose's 3D median filter to that range, and stores the results in anipose format. Ranges
accumulate **incrementally** into a dense canonical 3D file (mirroring the 2D `_analyzed`
pattern) so triangulating a new range never re-runs previously triangulated ranges.

## Decisions (locked)

1. **Execution:** worker via Celery. A main-repo Flask route enqueues a range-triangulate
   Celery task; the module button dispatches to it and polls status. (The dlc-3d container
   can't import `anipose_src` — missing numba/tqdm — but the worker has a full anipose
   install and already runs the existing anipose tasks.)
2. **Config source:** READ the existing `config.toml` from **`parent(current_folder)`**
   (the anipose project root; `current_folder` = the video/session folder). Used only as
   the parameter source for `[triangulation]` and `[filter3d]`. Never generated/overwritten.
   Missing → clear error (user guarantees it exists).
3. **Median filter:** filter the **new range only**, splice into the filtered canonical.
   (Accepted minor seam caveat where independently-filtered ranges meet.)
4. **Range source / gating:** the button triangulates the **current finalize keyframe
   window** `[start, start+n)` — the same range the existing *"Start analysis for range"*
   button (`#ia3d-btn-analyze-range-confined`) uses — and is gated identically (enabled
   when the keyframe is **locked**).
5. **3D coverage bar:** included — mirrors the "Finalized frames" coverage bar, showing
   which frame regions currently have 3D data.

## Facts (verified)

- `anipose_src.triangulate_funcs.triangulate(config, calib_folder, video_folder,
  pose_folder, fname_dict, output_fname)` — loads `CameraGroup.load(calib_folder/
  calibration.toml)`, loads a `{cam_name: h5}` dict, triangulates (optim if
  `config['triangulation']['optim']`), writes an anipose pose-3d CSV to `output_fname`.
- 3D median filter: `anipose_src.filter_3d_funcs` — `medfilt_data(values, size)`,
  `filter_pose(config, fname, outname)` (uses `config['filter3d']['medfilt']` +
  `offset_threshold`).
- Worker bind-mounts `./src:/app` (compose) — new `.py` files load after a worker
  **restart** (no rebuild). Worker already imports `anipose_src.*` and `dlc/*`.
- Both `src/` trees are bind-mounted; dlc-3d HTML changes need a container restart
  (single-file bind mount), JS/CSS are hot.
- 2D incremental pattern to mirror: `src/dlc/canonical.py`
  (`write_to_canonical` = dense `combine_first` + reindex; `unfinalize_range` = set range
  to NaN). Route/validation patterns to mirror: `src/dlc/inline_analysis.py`
  (`_active_project()`, `_sec_check`, the range enqueue + `…/range/status?req_id=` poll).

## Data layout (added to Phase-1 output, inside `current_folder`)

```
parent/
  config.toml                       ← user-guaranteed (param source, read-only)
  current_folder/                   ← the video/session folder
    <cam0>.avi  <cam1>.avi
    calibration/  calibration.toml  detections.pickle          (Phase 1)
    pose-2d/      <cam0stem>_analyzed.h5  <cam1stem>_analyzed.h5 (Phase 1)
    pose-3d/          <pair>_3d.csv        ← canonical raw 3D  (dense, incremental)  [NEW]
    pose-3d-filtered/ <pair>_3d.csv        ← median-filtered 3D (range-spliced)      [NEW]
```

`<pair>` = a stable name derived from the cam0 stem with the cam token normalized
(e.g. `<stem-with-_cam0_→_cam_>`), so cam0/cam1 map to ONE 3D file.

## Main repo — components

### `src/dlc/canonical_3d.py` (new) — incremental 3D store (mirror of `canonical.py`)
- `canonical_3d_csv_path(session_dir, pair_name) -> Path` → `pose-3d/<pair>_3d.csv`
- `filtered_3d_csv_path(session_dir, pair_name) -> Path` → `pose-3d-filtered/<pair>_3d.csv`
- `write_range_to_canonical_3d(session_dir, pair_name, df_range) -> Path`:
  `df_range` is anipose pose-3d rows indexed by **global** frame number. Dense
  `combine_first` merge over existing canonical + reindex to `0..max`, atomic write.
  Preserve column order across runs (reindex to existing columns like the 2D helper).
- `medfilt_range_and_splice(session_dir, pair_name, start, n, config) -> Path`:
  read the `[start, start+n)` rows from the RAW canonical, median-filter per bodypart
  coordinate with `config['filter3d']['medfilt']` (respect `offset_threshold`), splice
  those rows into the FILTERED canonical (dense combine_first + reindex), atomic write.
- `unfinalize_3d_range(session_dir, pair_name, start, n) -> int`: set the range rows to
  NaN in both canonicals (inverse; for later use / tests).
- `read_3d_coverage(session_dir, pair_name, buckets) -> list` (or presence array): which
  frame regions have non-NaN 3D rows, for the coverage bar.

Atomic write + csv helpers may be shared with / mirror `canonical.py`.

### Celery task `process_triangulate_range` (in `src/anipose/tasks.py`)
Signature: `process_triangulate_range(self, cam0_video, start_frame, n_frames)` (bind=True,
name `tasks.process_triangulate_range`). Steps:
1. Resolve `cam0` path; `current_folder = cam0.parent`; resolve `cam1` sibling (reuse the
   cam-regex sibling logic). Load `config = load_config(current_folder.parent/config.toml)`
   → error if missing. Require `current_folder/calibration/calibration.toml` and both
   `pose-2d/<stem>_analyzed.h5` → error ("run Initialize into anipose format first").
2. In a temp dir, write sliced pose-2d h5s for cam0/cam1 covering `[start, start+n)`
   (rows re-indexed 0..n-1). Build `fname_dict = {cam0_name: slice0.h5, cam1_name: slice1.h5}`
   keyed by anipose cam name (`get_cam_name`/`cam_regex`).
3. `triangulate(config, calib_folder, video_folder=str(current_folder), pose_folder,
   fname_dict, tmp_out_csv)`.
4. Read `tmp_out_csv` with pandas; add `start_frame` to its frame index →
   `write_range_to_canonical_3d(current_folder, pair_name, df_range)`.
5. `medfilt_range_and_splice(current_folder, pair_name, start_frame, n_frames, config)`.
6. Report progress via `self.update_state(meta={progress, stage, log})` at each step, like
   the existing anipose tasks. Return `{pair_name, start_frame, n_frames, raw_csv,
   filtered_csv}`.

CPU-only (no CUDA needed); route to the default/`celery` queue.

### Flask routes (in `src/dlc/inline_analysis.py`)
- `POST /dlc/project/triangulate/range` — body `{cam0_video, start_frame, n_frames}`.
  Validate (`_sec_check`, `start>=0`, `1<=n<=10000`), enqueue
  `process_triangulate_range.delay(...)`, return `{"req_id": task.id}`, 202.
- `GET /dlc/project/triangulate/range/status?req_id=` — return
  `{"state": <PENDING|STARTED|SUCCESS|FAILURE>, "progress": int, "stage": str,
    "error": str|null, "result": {...}|null}` (mirror the existing range/status route).
- `GET /dlc/project/triangulate/coverage?cam0_video=&buckets=` — return
  `{"buckets": [...], "nframes": int}` from `canonical_3d.read_3d_coverage`. Empty/absent
  canonical → all-zero buckets (not an error).

## API contract (both repos build to THIS verbatim)

```
POST /dlc/project/triangulate/range
  req:  { "cam0_video": str, "start_frame": int, "n_frames": int }
  res 202: { "req_id": str }
  res 4xx: { "error": str }

GET  /dlc/project/triangulate/range/status?req_id=<id>
  res 200: { "state": str, "progress": int, "stage": str,
             "error": str|null, "result": object|null }

GET  /dlc/project/triangulate/coverage?cam0_video=<path>&buckets=<n>
  res 200: { "buckets": number[], "nframes": int }
```

## Support module (`dlc-3D`) — UI

### Template `src/templates/partials/card_inline_analysis_3d.html`
Extend the existing `#ia3d-triangulate-controls` block (Phase 1) with, below the init button:
- `#ia3d-triangulate-range-btn` — "▶ Triangulate keyframe range" (`btn-sm btn-create`),
  disabled by default (gated on keyframe lock, like `#ia3d-btn-analyze-range-confined`).
- `#ia3d-triangulate-range-status` — `fe-extract-status` span.
- A 3D coverage bar mirroring the "Finalized frames" bar: a labeled
  `#ia3d-triangulate-coverage-wrap` with `<canvas id="ia3d-triangulate-coverage" height="14">`.

### JS `src/static/inline_analysis_3d.js`
- In `_wireTriangulateChrome()`: wire `#ia3d-triangulate-range-btn`. On click, read the
  current keyframe window `[start, n]` from the SAME source `#ia3d-btn-analyze-range-confined`
  uses (find how that button computes its range and reuse it). POST to
  `/dlc/project/triangulate/range`, then poll `…/range/status?req_id=` (reuse the existing
  poll helper `_pollReq`/pattern) until terminal; render stage/progress then a summary.
- Gate the button's `disabled` on keyframe-lock exactly like `#ia3d-btn-analyze-range-confined`
  (mirror wherever that button is enabled/disabled).
- After success, refetch `/dlc/project/triangulate/coverage?cam0_video=…&buckets=<canvasW>`
  and draw the coverage bar (reuse the finalize-coverage drawing helper if one exists).

## Tests

**Main repo** (`tests/`):
- `canonical_3d` unit tests: dense merge; two non-overlapping ranges coexist; re-running a
  range overwrites only its rows; `medfilt_range_and_splice` writes only the range into the
  filtered canonical; `unfinalize_3d_range` clears only its rows; `read_3d_coverage` buckets.
- Route tests: enqueue returns `req_id` (mock `process_triangulate_range.delay`); status
  maps Celery states → contract; coverage on absent canonical → zero buckets; validation 4xx.
- Task test with `anipose_src.triangulate_funcs.triangulate` **mocked** to emit a small
  pose-3d CSV: verify the slice offset, canonical merge, and filter-splice wiring end to end
  (no real calibration data needed).

**Support repo** (`dlc-3D/tests/`):
- Markup guard: `#ia3d-triangulate-range-btn` + `#ia3d-triangulate-coverage` present inside
  the Triangulate controls; button disabled by default.
- JS wiring guard: handler posts to `/dlc/project/triangulate/range` and polls
  `/dlc/project/triangulate/range/status`; coverage fetch to `/dlc/project/triangulate/coverage`.

## Deployment notes

- Restart the **worker** to register the new Celery task + load `canonical_3d.py`
  (`docker restart <worker>`), and the **flask** container for the new routes.
- Restart `deeplabcut-webapp-docker-dlc-3d-1` for the template change; JS is hot.
- No image rebuild required (src bind-mounted).

## Out of scope (Phase 2)

- Coordinate-frame/constraint config authoring (user owns config.toml).
- 3D visualization/overlay of the triangulated points (separate phase).
- Un-triangulate button in the UI (helper exists for tests/future).
