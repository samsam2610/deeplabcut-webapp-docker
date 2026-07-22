# Triangulate batch — aggregate job on both Jobs surfaces

**Date:** 2026-07-22
**Status:** Approved (user: "both main page + dlc-3D card", "one aggregate row per batch")

## Problem

There are TWO Jobs surfaces:
- **Main** `/jobs` (`src/static/js/jobs.js`) ← `GET /dlc/training/jobs` reads redis zsets
  `dlc_train_jobs` / `dlc_analyze_jobs` (+ hashes `dlc_*_job:<id>`), reconciled vs Celery.
- **dlc-3D card** (`lp_cards.js`) ← `GET /dlc-3d/lp/jobs` reads `dlc3d:lp:job:*`.

Triangulation registered per-range only in the dlc-3D registry. The user watches the
MAIN page, so it showed nothing. A 100+ range tag batch also must NOT flood either list.

## Design — one aggregate row per batch, on both surfaces

The tag-batch (and the single-range button = a batch of 1) is orchestrated in the
frontend (`_onTriangulateTagClick` / the range button in `inline_analysis_3d.js`). It
owns a **frontend-generated `batch_id`** (`crypto.randomUUID()`), registers ONE aggregate
job at start, updates its progress per range, and finalizes it. Both backends write their
own registry; the frontend calls both. Per-range `_registerTriangulateJob` is REMOVED.

### Contract A — MAIN webapp: `POST /dlc/project/triangulate/batch`

Body: `{ batch_id, action, total?, done?, skipped?, failed?, stage?, video?, error? }`
- `action:"start"` → `hset dlc_analyze_job:<batch_id>` `{task_id:batch_id,
  operation:"triangulate", project:<video parent dir name>, target_path:<video>,
  started_at:<now>, status:"running", total:<N>, done:0, stage:"0/<N>"}`, `expire 7200`,
  `zadd dlc_analyze_jobs {batch_id: now}`.
- `action:"progress"` → `hset` `done`, `skipped`, `stage` (a short string like
  `"7/119 · Merging into canonical 3D… 70%"`).
- `action:"done"` → `hset status = "complete"` (always terminal; the batch always
  finishes, skipped/failed are counts not states), final `stage`
  (`"119/119 done · K skipped"` or an error string), `expire 3600`.

Security: if `video` present, reject with 403 when `not _sec_check(Path(video))`.
Return `{ok:true}` (best-effort; 503 if redis down). Reuses the `dlc_analyze_jobs`
category so `/dlc/training/jobs` surfaces it with NO monitor-route change — a synthetic
`batch_id` reconciles as PENDING (∈ live states) so it stays "running" until `done` sets
"complete".

### Contract B — jobs.js progress render

In `_renderRail` (and optionally the detail header), if `j.stage` is present render it as
a dim one-line sub-label under the operation/status (mirrors how the dlc-3D card shows a
stage). No other jobs.js behavior changes.

### Contract C — dlc-3D `/register-inline` (aggregate fields)

Accept optional `batch` (bool), `total`, `done`, `stage` in the body (in addition to
`type`, `video`, `req_id`, `start_frame`, `n_frames`). When present, store them in the
job meta. `register()` upserts, so repeated calls update progress. `req_id` = the
`batch_id`.

### Contract D — dlc-3D `lp_cards.js` render

For `type:"triangulate"` rows: if the row is a batch (`job.batch` / has `stage` in meta),
render `job.stage` directly and DO NOT call `augmentTriangulate` (the batch_id is not a
Celery task, so the per-req status endpoint doesn't apply). Non-batch legacy rows keep the
existing augment. No Cancel (unchanged). Detail view shows the stored stage/done/total.

### Contract E — frontend orchestration (`inline_analysis_3d.js`)

`_onTriangulateTagClick`:
1. `const batchId = crypto.randomUUID();`
2. Before the loop: POST MAIN `batch {action:"start", batch_id, total:ranges.length,
   video:cam0}` AND dlc-3D `register-inline {type:"triangulate", req_id:batchId,
   video:cam0, batch:true, total:ranges.length, done:0, stage:"0/N"}`. Both best-effort.
3. Per range, after each completes: build `stage = "${done+skip}/${N} · ${d.stage||''} ${pct}"`
   and POST progress to BOTH (`batch {action:"progress", batch_id, done, skipped, stage}` +
   `register-inline` upsert with the same `stage`/`done`). (Coarse per-range granularity is
   fine — no need to stream the per-range % to the registries; update once per range.)
4. After the loop: POST `done` to both with the final stage/counts.

The single-range button does the same with `total:1` (start → the one range → done).
Remove the old per-range `_registerTriangulateJob` calls + its helper (superseded).

## Files & restarts

- **main:** `src/dlc/inline_analysis.py` (batch route), `src/static/js/jobs.js` (stage
  render) → **flask restart** (py). jobs.js is static/hot but restart is harmless.
- **dlc-3D:** `src/dlc_3d_bp/lp_routes.py` (register-inline fields),
  `src/static/lp_cards.js` (batch render), `src/static/inline_analysis_3d.js` (orchestration)
  → **dlc-3d restart**.

## Tests

- main: `POST /dlc/project/triangulate/batch` start/progress/done writes the hash + zset
  with operation "triangulate", status running→complete, stage updates; 403 on bad video.
- jobs.js source guard: `_renderRail` renders `j.stage`.
- dlc-3D: register-inline stores batch/total/done/stage; lp_cards renders batch stage
  without augmenting; inline_analysis_3d.js uses `crypto.randomUUID` + posts start/progress/
  done to both backends and no longer per-range-registers.
