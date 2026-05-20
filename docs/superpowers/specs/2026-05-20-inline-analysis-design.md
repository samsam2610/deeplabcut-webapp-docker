# Inline Analysis — Design Spec

**Date:** 2026-05-20
**Status:** Approved for implementation planning
**Scope:** Main webapp (`/home/sam/docker-images/deeplabcut-webapp-docker/`)

## Goal

Add a new dashboard card — **Inline Analysis** — that lets the user scrub a video to a frame, run DLC pose inference on N frames forward with a warm-in-memory PyTorch model, and have the results merged into the canonical DLC outputs (`.h5`, `.csv`, `_meta.pickle`). The card embeds a frame-by-frame player with marker overlay, marker adjustment, and dataset curation — all behaviours already proven in the existing View-Analyzed card.

The motivating workflow: iterate quickly on troublesome sections of long videos. Today, running DLC on a 500-frame segment costs ~8–15 s of model-load + ~5 s of inference. With a warm worker, the same segment costs ~5 s total — and successive ranges add only their own inference time.

## Architecture overview

```
Browser (src/static/js/inline_analysis.js)
    │  POST /dlc/project/inline-analysis/session/start
    │  POST /dlc/project/inline-analysis/range
    │  GET  /dlc/project/inline-analysis/{session,range}/status
    │  POST /dlc/project/inline-analysis/session/stop
    ▼
Flask (src/dlc/inline_analysis.py)
    │  Redis list  inline:queue:{user_id}:{snap_key}
    │  Redis hash  inline:session:{user_id}:{snap_key}
    │  Redis hash  inline:result:{req_id}
    │  Redis key   inline:control:{user_id}:{snap_key}
    ▼
Celery worker (long-lived task: tasks.dlc_inline_session)
    Boot: utils.get_pose_inference_runner(model_config, snapshot_path, batch_size, device)
    Loop: BLPOP queue → video_inference(VideoIterator(range), runner) → merge into .h5/.csv → publish result
    Exit: TTL elapsed | control:stop | snapshot change
```

`user_id` is the Flask session uid (same `_user_id()` helper used by `src/dlc/inference.py`).
`snap_key = sha1(config_path, shuffle, snapshot_path)` — the three inputs that uniquely identify a `(project, model, snapshot)` triple.

Key shape decisions (settled during brainstorm):

- **Scope per warm worker** = one `(user, project, snapshot)` triple. Snapshot change tears down and rewarms.
- **Engine** = PyTorch only in v1. `/session/start` returns 409 for TF projects.
- **Project type** = single-animal only in v1. `/session/start` returns 409 for multi-animal projects.
- **Source media** = videos only.
- **Live progress** = no streaming markers; player refreshes on completion.
- **Merge policy** = overwrite-by-snapshot is automatic (different snapshot → different file); within the same h5, frames already analyzed are silently skipped and the user is told the count.
- **Code reuse for player** = copy-then-deferred-migration (Option B from §4 below); no `viewer.js` changes in this PR.

## §1 — UI / Card Layout

### Entry point

A new button in `src/templates/partials/card_dlc_project.html`, placed **between** `btn-open-analyze` (Analyze Video/Frames) and `btn-open-view-analyzed` (View Analyzed Videos/Frames). The button id is `btn-open-inline-analysis`. Icon: a stylised play-on-rectangle or similar that distinguishes it from the existing eye icon.

### Card structure — `src/templates/partials/card_inline_analysis.html`

```
┌─ INLINE ANALYSIS ──────────────────────────── [✕ Close]─┐
│ ┌─ file picker (REUSED file_browser pattern) ─────────┐ │
│ │  [Project Videos] [Browse Folders]                   │ │
│ │  □ Hide videos without h5     ← UNCHECKED by default │ │
│ │  ▸ DREADD-Ali / videos / m3-day7-cam1.mp4  [no h5]   │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Analysis Parameters [NEW] ─────────────────────────┐ │
│ │  Snapshot          [shuffle1/snapshot-200000 ▾] [↺]  │ │
│ │  Batch size        [  64 ]                           │ │
│ │  Frames per click  [ 500 ]                           │ │
│ │  Keep worker warm  [ 300 ] seconds   ● warm · 4:42   │ │
│ │  ☑ Save as CSV                                       │ │
│ │  [▶ Analyze 500 frames from frame 1240]              │ │
│ │  Last run: 487 analyzed, 13 skipped                  │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Frame player ──────────────────────────────────────┐ │
│ │  [frame image + canvas marker overlay]               │ │
│ │  ▬▬▬▬●▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬   │ │
│ │  [▶][◀][▶]  fps[5] step[1]  Frame 1240/18432         │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Kinematic markers ─────────────────────────────────┐ │
│ │  ☑ Show markers · threshold 0.60 · marker size 6     │ │
│ │  Primary [shuffle1.h5 ▾]  + add comparison…          │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Marker adjustment ─────────────────────────────────┐ │
│ │  ⚠ 3 frames edited — unsaved   [Save] [Discard]      │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Dataset Curation ──────────────────────────────────┐ │
│ │  ☐ Dataset Curation (extract / batch add / annotate) │ │
│ └──────────────────────────────────────────────────────┘ │
│  (no "Create labeled video / frame" controls)            │
└──────────────────────────────────────────────────────────┘
```

UI specifics:

- **Hide videos without h5** defaults to **unchecked** (unlike View-Analyzed which defaults to checked). The point of the card is to *produce* h5 data, so we want to see videos that don't have one yet.
- **Warm indicator** shows `● warm · MM:SS` (time until idle eviction) or `○ cold` when the worker hasn't been started. Clicking it manually stops the worker.
- **Button label** updates live as the user scrubs and changes the frames-per-click field: `▶ Analyze {N} frames from frame {K}`.
- **No "Create labeled video / frame"** button or checkbox. Explicitly omitted.

### Project-type gating — server-side only

No client-side preflight, no banner UI, no new field on `/dlc/project`. The new `POST /session/start` route reads the active project's `config.yaml` directly:

```python
cfg = yaml.safe_load((Path(project["project_path"]) / "config.yaml").read_text())
if cfg.get("multianimalproject"):
    return jsonify({"error": "Inline Analysis is single-animal only in v1. "
                              "Use the Analyze Video/Frames card."}), 409
if (cfg.get("engine") or "pytorch").lower() != "pytorch":
    return jsonify({"error": "Inline Analysis requires the PyTorch engine."}), 409
```

(409 Conflict — the request is well-formed; the server's project state is what makes it impossible. The implementation and tests both use 409.)

The browser shows the response error in the existing "Last run" status line. No separate banner element.

## §2 — Data Flow & State

Three actors: **browser** (`inline_analysis.js`), **Flask** (`src/dlc/inline_analysis.py`), **warm worker** (long-lived Celery task `tasks.dlc_inline_session`).

### Sequence — happy path

```
TIME ▼   BROWSER                FLASK                          REDIS                       WARM WORKER
────────────────────────────────────────────────────────────────────────────────────────────────────────
T0       open card
T1       pick video + snapshot
T2       POST /session/start ─▶ snap_key = sha1(config_path, shuffle, snapshot_path)
                                If session not present:
                                  celery.send_task(dlc_inline_session) ──▶  load DLC + model
                                                                            HSET inline:session
                                                                              status=warming → ready
T3       ◀── 202 {session_id,
              snap_key, status}
T4       poll /session/status ─▶ HGET inline:session
         ◀── {status:ready}
T5       scrub to frame 1240
T6       click Analyze 500 ───▶ req_id = uuid
                                LPUSH inline:queue:{snap_key}
                                  {req_id, video_path, start, n, batch_size, save_csv}
                                                            ───────▶  BLPOP wakes up
                                                                       decode + infer
                                                                       merge into .h5/.csv (atomic)
                                                                       HSET inline:result:{req_id}
                                                                         status=done, n_analyzed, n_skipped
T7       poll /range/status ──▶ HGET inline:result
         ◀── {status:done, ...}
T8       player.reloadH5()       (re-fetch poses for visible window via existing routes)
T9       (user scrubs / repeats)
…
Tn       idle > TTL                                                       BLPOP times out → exit
                                                                          HSET inline:session
                                                                            status=expired
Tn+1     poll /session/status ─▶ status=expired → grey out Analyze
         ◀── {status:expired}    until user clicks "rewarm"
```

### Lifecycle events

- **Snapshot change while warm.** Browser POSTs `/session/start` with the new `snap_key`. Flask sets `inline:control:{old_snap_key} = stop` (60 s TTL) and dispatches the new task. Old worker drains in-flight range, exits cleanly. New worker boots.
- **Card close mid-run.** Browser fires `POST /session/stop` on `beforeunload`. Worker finishes any in-flight range (don't lose computed frames), writes results, sets `status=stopped`. No mid-batch abort.
- **Worker crash.** Result hash gets `status=error` with truncated traceback. Browser surfaces this in the "Last run" status and offers a manual rewarm.
- **Two tabs, same snap_key.** Both share the same worker. Range requests serialise via the queue. Both tabs see the same warmth indicator.
- **Two tabs, different snap_keys.** Two workers, two model copies in VRAM. Indicator surfaces this so the user can see it.

### Redis keys

| Key | Type | TTL | Contents |
|---|---|---|---|
| `inline:session:{user_id}:{snap_key}` | hash | TTL + 30 s | `status` (`warming`/`ready`/`expired`/`error`/`stopped`), `snapshot_path`, `project`, `started_at`, `last_activity` |
| `inline:queue:{user_id}:{snap_key}` | list | — | range-request JSON |
| `inline:result:{req_id}` | hash | 300 s | `status` (`done`/`error`), `n_analyzed`, `n_skipped`, `error` |
| `inline:control:{user_id}:{snap_key}` | string | 60 s | `stop` (one-shot signal) |

No persistent state outside the canonical DLC files on disk. Redis flush → workers die → next request rewarms from scratch.

## §3 — Backend API & Worker Internals

### HTTP endpoints — `src/dlc/inline_analysis.py`

| Method | Path | Body / Query | Response | Purpose |
|---|---|---|---|---|
| POST | `/dlc/project/inline-analysis/session/start` | `{snapshot_path, shuffle, ttl_seconds}` | `202 {session_id, snap_key, status}` | Dispatch warm worker (or return existing). |
| GET | `/dlc/project/inline-analysis/session/status` | `?snap_key=…` | `{status, idle_remaining_s, last_error?}` | Polled while card open. |
| POST | `/dlc/project/inline-analysis/session/stop` | `{snap_key}` | `204` | Manual stop or `beforeunload`. |
| POST | `/dlc/project/inline-analysis/range` | `{snap_key, video_path, start_frame, n_frames, batch_size, save_as_csv}` | `202 {req_id}` | Push to worker queue. |
| GET | `/dlc/project/inline-analysis/range/status` | `?req_id=…` | `{status, n_analyzed?, n_skipped?, error?}` | Polled every 500 ms during run. |
| GET | `/dlc/project/inline-analysis/video-info` | `?path=…` | `{nframes, fps, width, height, has_h5_at_snapshot}` | One-shot probe when video picked. |

All endpoints reuse `_dlc_project_security_check` from `dlc/utils.py` so they cannot touch paths outside the data root.

### Worker — `tasks.dlc_inline_session` in `src/dlc/tasks.py`

Lives in the existing `worker-1` container (PyTorch). One subprocess per session, model held in memory the entire session. Uses DLC's own public primitives — no custom inference loop, no custom model loader.

```python
@celery.task(bind=True, name="tasks.dlc_inline_session", acks_late=False)
def dlc_inline_session(self, user_id, config_path, snap_key, snapshot_path,
                       shuffle, trainingsetindex, batch_size, ttl):
    from deeplabcut.pose_estimation_pytorch.apis import (
        utils, VideoIterator, video_inference,
    )
    from deeplabcut.pose_estimation_pytorch.apis.videos import create_df_from_prediction
    from deeplabcut.pose_estimation_pytorch.data import DLCLoader

    queue_key   = f"inline:queue:{user_id}:{snap_key}"
    control_key = f"inline:control:{user_id}:{snap_key}"
    session_key = f"inline:session:{user_id}:{snap_key}"

    # Boot — model weights load ONCE.
    # DLCLoader is the canonical project-metadata accessor that
    # analyze_videos itself uses; it owns scorer/model_cfg/multi_animal.
    # (PoseInferenceRunner's public surface is `inference / load_snapshot /
    #  predict` — it does NOT expose `scorer_name` or `bodyparts`, despite
    #  what an earlier spec draft assumed.)
    loader = DLCLoader(
        config=config_path,
        trainset_index=trainingsetindex,
        shuffle=shuffle,
    )
    scorer       = loader.scorer(snapshot_path)
    model_cfg    = loader.model_cfg
    multi_animal = bool(loader.project_cfg.get("multianimalproject", False))
    # multi_animal is always False here — the route gate already rejected
    # multi-animal projects. We still pass the flag through so
    # create_df_from_prediction picks the right column shape.

    runner = utils.get_pose_inference_runner(
        model_config=model_cfg, snapshot_path=snapshot_path,
        batch_size=batch_size,
        device=None,                # honors CUDA_VISIBLE_DEVICES
    )
    _publish_status(snap_key, "ready")

    cached_batch_size = batch_size
    while True:
        item = _blpop(queue_key, timeout=_idle_budget(snap_key, ttl))
        if item is None: break                          # TTL elapsed
        if _control_says_stop(snap_key): break

        req = json.loads(item)

        # Rebuild runner if batch_size changed — model weights stay in GPU.
        if req["batch_size"] != cached_batch_size:
            runner = utils.get_pose_inference_runner(
                model_config=model_cfg, snapshot_path=snapshot_path,
                batch_size=req["batch_size"], device=None,
            )
            cached_batch_size = req["batch_size"]

        try:
            n_analyzed, n_skipped = _run_range(
                runner, scorer=scorer, model_cfg=model_cfg,
                multi_animal=multi_animal, req=req,
            )
            _publish_result(req["req_id"], "done",
                            n_analyzed=n_analyzed, n_skipped=n_skipped)
        except Exception as e:
            _publish_result(req["req_id"], "error", error=str(e)[:500])
        _bump_activity(snap_key)

    _publish_status(snap_key, "expired")
```

`_run_range` builds the result DataFrame via DLC's own `create_df_from_prediction` (the helper `analyze_videos` itself uses), reindexes from `0..len-1` to the actual frame numbers, then merges into the canonical h5:

```python
def _run_range(runner, *, scorer, model_cfg, multi_animal, req):
    h5_path  = _resolve_h5_path(req["video_path"], scorer)
    existing = pd.read_hdf(h5_path) if h5_path.exists() else None

    target     = list(range(req["start_frame"], req["start_frame"] + req["n_frames"]))
    to_analyze = _filter_skip_already_done(target, existing)
    n_skipped  = len(target) - len(to_analyze)
    if not to_analyze:
        return 0, n_skipped

    vit = _RangeVideoIterator(req["video_path"], indices=to_analyze)
    predictions = video_inference(vit, pose_runner=runner)
    # predictions: list[dict[str, np.ndarray]] — one entry per analyzed
    # frame, in the same order as `to_analyze`.

    with _scratch_dir() as scratch:
        df_range = create_df_from_prediction(
            predictions=predictions,
            dlc_scorer=scorer,
            multi_animal=multi_animal,
            model_cfg=model_cfg,
            output_path=scratch,                   # short-lived tmp; we use the returned df
            output_prefix=f"range_{req['req_id']}",
            save_as_csv=False,
        )
    # create_df_from_prediction indexes rows 0..N-1. Re-key to absolute frames.
    df_range.index = pd.Index(to_analyze, name=df_range.index.name)

    df_merge = df_range if existing is None else df_range.combine_first(existing)
    _atomic_write_h5(h5_path, df_merge)
    if req["save_as_csv"]:
        _atomic_write_csv(h5_path.with_suffix(".csv"), df_merge)
    _update_meta_pickle(h5_path, df_merge, snapshot=req["snapshot_path"])
    return len(to_analyze), n_skipped
```

`_RangeVideoIterator` is a ~15-line subclass of `deeplabcut.pose_estimation_pytorch.apis.VideoIterator` that yields only the requested indices (handles non-contiguous skip lists).

`_scratch_dir()` is a `tempfile.TemporaryDirectory` context manager. The scratch h5 that `create_df_from_prediction` writes is short-lived and never persisted; we only need the in-memory DataFrame it returns.

### Why DLC primitives, not a custom inference loop

DLC exposes the right warm-worker primitives directly. We use them — not just analogues. Verified against DLC 3.0.0rc14 in the worker container on 2026-05-20:

- `DLCLoader(config, shuffle, trainset_index)` → project-metadata accessor. Owns `model_cfg`, `scorer(snapshot)`, and `project_cfg`. This is what `analyze_videos` uses internally to look up scorer and multi-animal flags.
- `utils.get_pose_inference_runner(model_config, snapshot_path, batch_size, device)` → `PoseInferenceRunner`. Public surface: `inference / load_snapshot / predict` — does **not** carry metadata; use `DLCLoader` for that.
- `VideoIterator(video_path)` provides `set_to_frame`, `read_frame`, `reset` — DLC's native video abstraction.
- `video_inference(video, pose_runner)` → `list[dict[str, np.ndarray]]` — same call site as `analyze_videos`.
- `create_df_from_prediction(predictions, dlc_scorer, multi_animal, model_cfg, output_path, output_prefix, save_as_csv)` → `pd.DataFrame` with the canonical MultiIndex columns. Same helper `analyze_videos` uses to build its DataFrame.

Using these means we don't duplicate DLC's batching, padding, device-management, or DataFrame-column logic, and we adapt at the same call sites that `analyze_videos` does if DLC's internals change.

## §4 — Player Code Reuse (Option B)

### Decision

`src/static/js/viewer.js` is **not** modified in this PR. We **copy** the player / overlay / marker-adjustment / dataset-curation core into a new module:

```
src/static/js/
  viewer.js                              ← unchanged
  inline_analysis.js                     ← new card
  components/
    file_browser.js                      ← existing
    analyzed_frame_player.js             ← new; copy-and-parameterise of viewer.js core
```

`analyzed_frame_player.js` exports `makeAnalyzedFramePlayer({...})`. The new card consumes it. `viewer.js` keeps its in-place implementation.

This decision intentionally accepts temporary duplication to avoid regression risk on the proven View-Analyzed card. See **Known tech debt** below for the deferred migration.

### Factory API

```javascript
makeAnalyzedFramePlayer({
  prefix,         // "ia" — used to look up DOM IDs (ia-frame-img, ia-overlay-canvas, …)
  frameUrlFn,     // (frameNumber) => url
  poseUrlFn,      // (layer, frameNumber) => url
  onCsvSaved,     // optional callback after dataset-curation save
}) → {
  loadVideo(videoPath, fps, nFrames),
  reloadH5(),                          // called after each completed inline range
  getCurrentFrame(), setCurrentFrame(n),
  destroy(),                           // removes listeners (required for card close)
}
```

The factory owns: frame loading + image preload pipeline, seek/play/skip controls, pose-window prefetch, marker rendering, hover + drag-to-edit, marker-adjustment banner, body-part chips, threshold + marker-size sliders, primary + comparison overlay logic, dataset curation panel.

The card owns: file picker (with the unchecked-by-default toggle), tab switching, analysis params block, snapshot picker, worker session bookkeeping.

### Header comments on both files

At the top of `viewer.js` and `analyzed_frame_player.js`:

```
// ⚠ DUPLICATION NOTICE
//   This file and ./components/analyzed_frame_player.js currently maintain
//   duplicate player/overlay/curation logic. Bug fixes in one must be
//   manually mirrored to the other until viewer.js is migrated.
//   See docs/superpowers/specs/2026-05-20-inline-analysis-design.md
//   (§4 and "Known tech debt") for the planned migration.
```

### Static-analysis check

`tests/test_analyzed_frame_player_factory.py` (new) asserts:

- The factory file exists at the canonical path.
- It exports `makeAnalyzedFramePlayer`.
- `inline_analysis.js` imports it.
- (Soft) At least one consumer uses the factory. Hardens to "all cards" once the deferred migration lands.

## §5 — File Handling & Disk Hygiene

### Canonical DLC outputs

We never invent new file names. Every inline run touches the same files DLC's `analyze_videos` would write for the given `(video, model, shuffle, snapshot)`:

```
videos/m3-day7-cam1.mp4
videos/m3-day7-cam1DLC_resnet50_DREADD-AlishuffleN_snapshot-200000.h5      ← we update
videos/m3-day7-cam1DLC_resnet50_DREADD-AlishuffleN_snapshot-200000_meta.pickle  ← we update
videos/m3-day7-cam1DLC_resnet50_DREADD-AlishuffleN_snapshot-200000.csv     ← if save_as_csv
```

Different snapshot → different filename → no cross-contamination.

### Atomic write

```python
def _atomic_write_h5(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_hdf(tmp, "df_with_missing", format="table", mode="w")
    os.replace(tmp, path)
```

Same pattern for `.csv` and `_meta.pickle`. Worker death mid-write leaves the canonical file intact (old or new, never half-written).

`_meta.pickle` gets a forward-compatible new field `inline_analysis_snapshots: set[str]` recording which snapshots contributed to the file. Unknown fields are ignored by older DLC tools.

### Skip-already-done

```python
def _filter_skip_already_done(target_frames, existing_df):
    if existing_df is None:
        return list(target_frames)
    have = existing_df.index
    return [f for f in target_frames
            if f not in have or existing_df.loc[f].isna().all()]
```

NaN-all rows (e.g. dynamic-cropping skips) are treated as missing and re-analyzed — matches DLC's own semantics.

### Disk hygiene guards

1. **No frame-decode files on disk.** Worker decodes via `VideoIterator` → numpy arrays → model. No JPEG/PNG dumps.
2. **Temp files** (`.tmp` suffix) live next to canonical files. Worker boot sweeps orphan `.tmp`s older than 1 hour.
3. **Test fixtures** follow the existing `dlc_sandbox_project` pattern; `pytest_sessionfinish` rmtrees basetemp; `pytest.ini` retention=1, policy=failed. GPU smoke caps `n_frames=50`.

### Failure modes

| Failure | Effect | Recovery |
|---|---|---|
| Worker OOM mid-inference | `.tmp` left; canonical file untouched | Next run cleans; user retries |
| Power loss between h5 and csv rename | h5 fresh, csv stale | User regenerates csv via DLC |
| Missing `pytorch_config.yaml` for snapshot | Worker fails at boot; no .h5 touched | Status = error, surfaced in card |
| Two browser tabs, same snap_key | One worker serialises requests | Safe |
| Two tabs, different snap_keys | Two workers, 2× VRAM | Safe; indicator surfaces both |

## §6 — Tests

### New test files

| File | Marker | Scope |
|---|---|---|
| `tests/test_inline_analysis_routes.py` | — | Each HTTP endpoint: 4xx on bad input, dispatches to correct Celery key, polls return correct shape. Mocks Celery + FakeRedis. |
| `tests/test_inline_analysis_worker.py` | — | Worker logic with DLC+GPU mocked: `_filter_skip_already_done`, `_RangeVideoIterator`, `_atomic_write_h5`, `_run_range` with stubbed `video_inference`, session/control/result transitions, TTL exit. |
| `tests/test_inline_analysis_session_lifecycle.py` | — | Snapshot change tear-down, idle TTL, control-key stop, concurrent-request serialisation. |
| `tests/test_analyzed_frame_player_factory.py` | — | Static-analysis: factory exists, exports `makeAnalyzedFramePlayer`, `inline_analysis.js` imports it. Soft consumer check. |
| `tests/e2e_inline_analysis_smoke.py` | `e2e` | Frontend smoke with stubbed worker: file picker shows videos w/o h5, analyze label updates with scrub, result status renders, marker overlay re-fetches. |
| `tests/test_inline_analysis_gpu_smoke.py` | `gpu` | Real warm-worker round-trip against `dlc_sandbox_project`. Caps: `n_frames=50`, `batch_size=8`, `TTL=10s`. Asserts h5 has 50 new rows, csv updated, meta records snapshot, worker exits within TTL+5s. Disk delta < 10 MB (assertion). |

### Existing tests that must stay green

- `tests/test_dlc_celery_tasks.py` — existing analyze task; verify no shared-state collision.
- `tests/test_analyzed_marker_adjustment.py` — viewer card untouched per §4.
- `tests/e2e_viewer_layers_smoke.py` — same.
- `tests/test_file_browser_policy.py` — extended to register the new factory (soft check initially).

### Mocking layers

```
Browser           ← Playwright / Flask test_client
   ↓ HTTP
Flask routes      ← real (test_client)
   ↓
Redis             ← FakeRedis (existing fixture)
   ↓
Celery dispatch   ← MagicMock — captured for assertions, never runs
   ↓
Worker code path  ← called as plain function in test_inline_analysis_worker.py;
                    utils.get_pose_inference_runner returns a stub runner
                    (fixed scorer_name, bodyparts)
   ↓
DLC primitives    ← mocked except in gpu test
   ↓
GPU + model       ← only test_inline_analysis_gpu_smoke.py
```

### GPU smoke specifics

```python
@pytest.mark.gpu
def test_inline_analysis_gpu_smoke(dlc_sandbox_project, ...):
    initial_du = _du_bytes(dlc_sandbox_project)
    # Start session against latest snapshot
    # Range: start=0, n_frames=50, batch_size=8
    # Assert response within 60s
    # Assert h5 has 50 frames, csv parses, meta has inline_analysis_snapshots
    # Wait TTL+5s, assert worker exited
    final_du = _du_bytes(dlc_sandbox_project)
    assert final_du - initial_du < 10 * 1024 * 1024
```

Decorator skips automatically when `CUDA_VISIBLE_DEVICES` is unset or `nvidia-smi` returns no devices.

## §7 — Out of Scope

Explicit non-goals for v1:

- Multi-animal projects (`/session/start` refuses with 409).
- TensorFlow engine (`/session/start` refuses with 409).
- Image folders as analysis target (videos only).
- Live streaming markers during a run (wait until done).
- Shared frame decode cache between worker and Flask.
- Detector batch_size field (no detector pass under single-animal scope).
- "Create labeled video / frame" controls (explicitly omitted from card).
- Tracking / filtering / smoothing (use Post-Process card after).
- Cross-session warm-worker admin controls.
- Autoscaling or queueing of warm workers (one per `(user, project, snapshot)`).

## Known tech debt

These are deliberate corner-cuts. Each item should remain visible until paid off:

1. **`viewer.js` migration to `analyzed_frame_player.js`.** Per §4, we copy rather than refactor. Until the migration, every player/overlay/curation fix must be hand-mirrored in both files. Both files carry a header comment pointing at the other and at this spec. Follow-up PR title prefix: `refactor(viewer): migrate to analyzed_frame_player factory`.
1a. **Dataset Curation handlers duplicated.** The Curation IIFE in `viewer.js` (lines 1106-1110 + 1875-2348) was copied verbatim into `inline_analysis.js` with `va-` → `ia-` ID renames per the polish spec §1.5 (2026-05-20). The duplication is tracked here and slated for migration to the shared factory in the SAME follow-up PR that migrates the player. Until then, every curation bugfix must be hand-mirrored in both files.
2. **`docs/policies/file-browser-component.md` → `shared-components.md`.** The policy doc must broaden to cover the new factory. Either rename or add a sibling. The static-analysis tests follow.
3. **`_meta.pickle.inline_analysis_snapshots` is unversioned.** Forward-compatible today (unknown fields ignored). If strict versioning becomes important, introduce `meta_version: int`.
4. **DLC internal API dependency.** Worker depends on `utils.get_pose_inference_runner`, `VideoIterator`, `video_inference`. These are public-ish but unversioned. A monthly smoke against the installed DLC version catches drift; we adapt at the same call sites `analyze_videos` itself uses.

## Future enhancements (separate specs)

- Multi-animal support.
- Resume / continue from first unanalyzed frame.
- Region-of-interest cropping per range.
- FP16 inference toggle.
- `torch.compile`'d runner option.
- Admin dashboard for warm-worker introspection.

## Files touched / created

Created:

- `src/templates/partials/card_inline_analysis.html`
- `src/static/js/inline_analysis.js`
- `src/static/js/components/analyzed_frame_player.js`
- `src/dlc/inline_analysis.py`
- `tests/test_inline_analysis_routes.py`
- `tests/test_inline_analysis_worker.py`
- `tests/test_inline_analysis_session_lifecycle.py`
- `tests/test_analyzed_frame_player_factory.py`
- `tests/e2e_inline_analysis_smoke.py`
- `tests/test_inline_analysis_gpu_smoke.py`

Modified:

- `src/templates/partials/card_dlc_project.html` — add `btn-open-inline-analysis` between Analyze and View-Analyzed buttons.
- `src/static/main.js` — wire open/close for the new card; register task-name → display-name mapping for the warm-worker session task.
- `src/dlc/tasks.py` — add `dlc_inline_session` Celery task and helpers.
- `src/app.py` — register `dlc_inline_analysis` blueprint alongside the existing `_dlc_inference_bp` at line ~197.
- `docs/policies/file-browser-component.md` — note the new factory; broaden scope.
- `tests/test_file_browser_policy.py` — register the factory in the policy check (soft consumer count).

Not modified:

- `src/static/js/viewer.js` — unchanged per §4 Option B.
- `src/templates/partials/card_viewer.html` — unchanged.
- Any TF-engine code paths.
