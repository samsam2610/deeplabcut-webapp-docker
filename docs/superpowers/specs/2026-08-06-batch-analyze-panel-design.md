# Batch Analyze panel — design

**Status:** approved 2026-08-06

A checkbox-gated panel on the "Analyze Video / Frames" card that runs the
inline-analysis pipeline over many videos instead of one, with the model chosen
by policy and the run optionally deferred until training finishes.

## Why this belongs on the analyze card

`card_analyze.html` is a main-webapp partial that BOTH `index.html` and the
dlc-3D module's `dlc_3d.html` `{% include %}`, and `analyze.js` is loaded on
both pages through `main.js`. There is one card rendered twice, not two cards.
Everything here therefore lands in the main webapp; the dlc-3D module needs no
changes at all.

"Analyze for tag" exists today only in the 3D inline cards
(`inline_analysis_3d.js`, `inline_analysis_3d_reprojection.js`). The 2D inline
card has note chips but no tag-batch button, so the 2D half of this feature is
new behaviour rather than a port. The underlying data — the per-video companion
CSV's `note` column — exists for any video either way.

## Architecture

An inline-analysis **session is keyed by model, not by video**
(`snap_key = sha1(config_path|shuffle|snapshot_path)`). One warm session
therefore serves every video in a batch. Batching reduces to: resolve a model,
start one session, and push the right ranges for each video onto that session's
queue.

```
browser                    flask                     celery
───────                    ─────                     ──────
Batch Analyze panel
  │ POST batch-analyze/start
  ├──────────────────────────▶ validate, write
  │                            dlc:batch:<id>
  │                            send_task ──────────▶ dlc_batch_analyze
  │                                                   │ training gate
  │                                                   │   (re-arm w/ countdown)
  │                                                   │ resolve snapshot
  │                                                   │ start inline session ──▶ dlc_inline_session
  │                                                   │ per video: ranges       (drains queue,
  │                                                   │   RPUSH inline:queue     writes h5)
  │                                                   └ exit (seconds)
  │ GET batch-analyze/status
  └──────────────────────────▶ read dlc:batch:<id>
                               + inline:result:<req_id>…
```

The batch task **submits and exits**. It does not poll ranges to completion, so
it holds a worker slot for seconds rather than hours, and the run survives both
a browser close and a restart of the flask container.

### Queue ordering

The inline queue is `LPUSH` + `BLPOP`, i.e. LIFO. The batch task uses **`RPUSH`**
instead. Two consequences, both wanted:

- batch ranges drain in the order they were queued;
- an interactive click from the inline card (`LPUSH`) still jumps ahead of the
  entire batch, so scrubbing stays responsive while a batch runs.

### Tag windows come from ONE camera

Note tags are annotated on cam0 only. The cameras are hardware triggered
(`trig1`), so a tagged frame number on cam0 is the same instant on cam1 and
nobody annotates twice — measured on `banh-mi-1_*_20260704_104915_13`, cam0's
companion CSV carries 141 tagged frames and cam1's carries **none**.

So the windows are built from the **queued** video's CSV and the *same* ranges
go to both cameras, matching `inline_analysis_3d.js::_onAnalyzeTagClick`. They
are re-clamped per camera in case a sibling is a few frames shorter. Reading
each target's own notes reviews fine and unit-tests fine, but skips cam1 with
"no frames carry any of those tags" and silently analyses half the pair.

### The training gate

`wait_for_training` means: **wait until a training job has been observed running
and has then finished.** If one is running when the batch is queued it waits for
that one; if the user ticks the box and then starts training, it waits for that.
It does not fire immediately merely because nothing is running yet.

Liveness needs **two** signals, because each alone is wrong in production:

* The job hash alone is not enough. `dlc_train_network` writes no `updated_at`,
  so there is no field to age out. It DOES slide the hash's TTL forward on every
  progress poll (`tasks.py`, "Slide the TTL forward so long runs (>2 h) stay
  visible"), which makes the hash's existence a dead-man's switch: present while
  the process writes, gone 2 h after it stops. Present-and-`running` is therefore
  a real liveness signal, but it carries no timestamp of its own to check.
* Celery's state alone is not enough either. The `dlc_train_jobs` zset outlives
  the hashes (which expire after 2 h), and an id whose result-backend entry has
  been purged reads as `PENDING` — a live state.

So the hash must exist and claim a live status, AND Celery must not report a
terminal state. "Celery says PROGRESS but the hash has lapsed" is the
hard-killed case — a SIGKILL never publishes a terminal state, so Celery's view
goes stale while the dead-man's switch correctly trips. The switch's 2 h lag is
the price: the gate cannot distinguish "killed" from "briefly stalled" sooner.

No backend entry at all means dispatched-but-not-started, which counts as
running: waiting slightly too long is far cheaper than analysing with
the pre-training model. Celery state is read as a plain redis GET of
`celery-task-meta-<id>`, never `AsyncResult`.

Implemented by re-dispatching the task with `countdown=60`, so a waiting batch
occupies no concurrency slot (the worker runs `-Q celery,pytorch --concurrency=2`;
blocking one slot for hours would halve GPU throughput). Deadline 24 h, after
which the batch fails with a stated reason. Cancellable throughout.

Model resolution happens **after** the gate — that is the only way "use latest
model" can mean the model training just produced.

## Components

### `src/dlc/batch_analyze.py` (new)

Pure helpers, no Flask or redis, unit-testable:

| function | contract |
|---|---|
| `resolve_snapshot(snapshots, policy, pinned)` | `(rel_path, None)` or `(None, reason)` |
| `merge_windows(frames, before, after, frame_count)` | `[{start, end, n}]` — Python mirror of `tag_batch.mjs::mergeWindows` |
| `chunk_video(frame_count, max_n)` | `[{start, end, n}]` covering `0..frame_count-1` |
| `tagged_frames(rows, tags)` | sorted, deduped frame numbers whose `note` exactly equals any of `tags` |

Routes:

- `POST /dlc/project/batch-analyze/start` → `{batch_id}`; validates the queue is
  non-empty, the policy is known, and (tag mode) that at least one tag is given.
- `GET  /dlc/project/batch-analyze/status?batch_id=` → state, counts, per-range
  roll-up read from `inline:result:<req_id>`.
- `POST /dlc/project/batch-analyze/cancel` → sets `cancelled` on the record.

### `tasks.dlc_batch_analyze` (new, queue `celery`)

1. Load `dlc:batch:<id>`; honour `cancelled`.
2. Training gate (above).
3. `resolve_snapshot` → absolute path → `snap_key`.
4. Start `tasks.dlc_inline_session` unless one is already alive for that
   `snap_key`, with `ttl` 1800 s.
5. For each queued video (plus its `_resolve_cam1` sibling when `both_cams`):
   probe frame count, build ranges, `RPUSH` payloads, record `req_id`s.
6. Write the aggregate job row and exit.

### Model policies

Resolved server-side from the same data `/dlc/project/snapshots` returns:

| policy | rule |
|---|---|
| `pinned` | the `pinned_snapshot` ui-setting |
| `latest_iter_best` | highest `iteration` for the shuffle, then the `snapshot-best-*` within it |
| `latest` | last entry of the existing `(iteration, shuffle, mtime)` sort |

An unresolvable choice **fails the batch loudly** rather than silently
substituting a different model — the same principle `_ia3dApplyPinnedSnapshot`
already follows for the pin.

### `src/static/js/batch_analyze.js` (new) + `card_analyze.html`

Panel sits under the card's subtitle; collapsed it is one checkbox line and the
existing single-run controls below are untouched.

The three source tabs are existing components: `makeFileBrowser`
(`components/file_browser.js`), `makeTrackedFiles`
(`components/tracked_files_tab.js`), and one fetch of
`/dlc/project/labeled-content` for Project Content. Double-click queues a path,
mirroring the card's existing `+ Add to queue` behaviour.

Controls:

- **Both cameras** — always present, always honoured; default ON when the card
  is rendered under `/dlc-3d/`, OFF on the main page. Sibling resolution is
  `triangulate_range._resolve_cam1`, so there is no dependency on the dlc-3D
  module.
- **Model** — three radios, per the table above.
- **Queue the run after training finishes** — the gate.
- **Analyze all** — every frame of every queued video.
- **Analyze for tag** — a comma-separated list of tags, each matched **exactly**
  against the CSV `note` column (the user is responsible for the spelling), plus
  `before`/`after` window fields defaulting to 200/599 — i.e. 800 frames per
  tagged frame, matching the 3D inline card's defaults.
- **batch tags** — `_makeQuickTags`-style chips; click fills the field, `+ tag`
  adds. Persisted per project under a new `batch_tags` ui-setting, alongside
  `batch_window` for before/after.

## Two properties inherited from the existing code

- **"Analyze all" is already incremental.** `_run_range` calls
  `_filter_skip_already_done`, so whole-video submission re-analyses nothing
  already present in the h5. No coverage read is needed to get that. Ranges are
  chunked at the `/range` route's 10 000-frame cap.
- **No h5 initialization step.** `_run_range` creates the file when absent, so
  fresh videos work without the inline card's "Initialize analysis files".

## Deliberately excluded

`override existing labels` and `ignore frames in _analyzed`. They exist on the
3D inline card but are **inert on the inline path**: `range_submit` drops them
from the payload and `_run_range` never reads them. Surfacing them here would
add a working-looking control that does nothing. That is a separate bug, tracked
on its own.

## Error handling

Every failure sets `state=failed` with a human `reason` on `dlc:batch:<id>` and
on the aggregate job row, so it surfaces on the Jobs page as well as in the
panel. Failures that are worth distinguishing:

- policy unresolvable (nothing pinned / no `best` snapshot / no snapshots)
- a queued video missing from disk or outside the data root (skipped, counted,
  reported — does not abort the batch)
- `both_cams` on with no resolvable sibling (that video is skipped and reported)
- training gate deadline exceeded

## Testing

- Unit: `resolve_snapshot` across all three policies including "pinned but
  deleted" and "no `best` in the top iteration"; `merge_windows` against the
  same cases `tag_batch.mjs` covers, including window overlap and clamping at
  `frame_count`; `chunk_video` at and beyond the 10 000 cap; `tagged_frames`
  exact-match (a `note` of `start-failure-2` must not match `start-failure`).
- Routes: start rejects an empty queue, an unknown policy and tag mode with no
  tags; status shape; cancel.
- Task, against the fake redis already used by the inline tests: the training
  gate re-arms instead of submitting; `both_cams` produces two videos' worth of
  payloads; payloads land via `RPUSH` (ordering); a cancelled record submits
  nothing.
