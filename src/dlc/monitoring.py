"""
DLC Monitoring + Machine Labeling Blueprint.

Routes:
  POST /dlc/project/machine-label-frames
  POST /dlc/project/machine-label-frames/stop
  GET /dlc/project/machine-label-raw
  POST /dlc/project/machine-label-reapply
  GET /dlc/training/jobs
  POST /dlc/training/jobs/clear
  GET /dlc/jobs/all           — unified Jobs-page listing (train/analyze zset +
                                 Celery inspector + inline-analysis sessions)
  POST /dlc/jobs/cancel       — kill switch for any row from /dlc/jobs/all
  GET /dlc/gpu/status
"""
from __future__ import annotations
import json
import re
import time
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, session as flask_session
from celery.result import AsyncResult
from werkzeug.utils import secure_filename
from . import ctx as _ctx
from . import inline_analysis as _inline_analysis
from dlc.utils import _get_engine_queue, _dlc_project_security_check

bp = Blueprint("dlc_monitoring", __name__)

# A frontend-driven `triangulate` batch heartbeats every completed range (~8s). If
# no heartbeat lands in this long, the browser tab that drove it is gone (closed /
# reloaded mid-batch) → mark the aggregate row dead instead of "running" forever.
_BATCH_STALE_SECS = 180


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _natural_keys(text: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _parse_dlc_yaml_local(config_path: Path) -> dict:
    """Parse DLC config.yaml for scorer/bodyparts."""
    _yaml = _ctx.yaml_lib()
    text = config_path.read_text()
    if _yaml is not None:
        return _yaml.safe_load(text) or {}
    result = {}
    m = re.search(r'^scorer\s*:\s*(.+)$', text, re.MULTILINE)
    if m:
        result["scorer"] = m.group(1).strip().strip("\"'")
    m = re.search(r'^bodyparts\s*:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)', text, re.MULTILINE)
    if m:
        result["bodyparts"] = [
            item.strip().strip("\"'")
            for item in re.findall(r'^[ \t]*-[ \t]*(.+)$', m.group(1), re.MULTILINE)
        ]
    return result


def _get_dlc_project_and_config():
    """Return (project_data, config_dict, error_response) for the active DLC project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, None, (jsonify({"error": "No active DLC project."}), 400)
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return project_data, {}, None
    try:
        cfg = _parse_dlc_yaml_local(config_path)
    except Exception as exc:
        return project_data, {}, (jsonify({"error": f"Could not parse config.yaml: {exc}"}), 500)
    return project_data, cfg, None


@bp.route("/dlc/project/machine-label-frames", methods=["POST"])
def dlc_project_machine_label_frames():
    """
    Dispatch a Celery task to run model inference on a labeled-data frames folder
    and save predictions as CollectedData_<scorer>.csv.
    Body (JSON): { video_stem, shuffle, trainingsetindex, gputouse, snapshot_index }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    project_path = Path(project_data.get("project_path", ""))
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400

    labeled_data_path = project_path / "labeled-data" / secure_filename(video_stem)
    if not labeled_data_path.is_dir():
        return jsonify({"error": f"Frames folder not found: {labeled_data_path}"}), 400

    def _int_or_none(key):
        v = body.get(key)
        try:
            return int(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    def _float_or(key, default):
        v = body.get(key)
        try:
            return float(v) if v is not None and v != "" else default
        except (ValueError, TypeError):
            return default

    params = {
        "shuffle":              _int_or_none("shuffle") or 1,
        "trainingsetindex":     _int_or_none("trainingsetindex") if _int_or_none("trainingsetindex") is not None else 0,
        "gputouse":             _int_or_none("gputouse"),
        "save_as_csv":          True,
        "snapshot_path":        (body.get("snapshot_path") or "").strip() or None,
        "likelihood_threshold": _float_or("likelihood_threshold", 0.9),
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_machine_label_frames",
        kwargs={
            "config_path":       config_path,
            "labeled_data_path": str(labeled_data_path),
            "params":            params,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "machine_label_frames"}), 202


@bp.route("/dlc/project/machine-label-frames/stop", methods=["POST"])
def dlc_project_machine_label_frames_stop():
    """Stop a running dlc_machine_label_frames task."""
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id is required."}), 400

    _ctx.redis_client().setex("dlc_ml_stop:" + task_id, 120, "1")
    _ctx.celery().control.revoke(task_id, terminate=False)
    try:
        AsyncResult(task_id, app=_ctx.celery()).forget()
    except Exception:
        pass
    return jsonify({"status": "stop_requested", "task_id": task_id}), 200


@bp.route("/dlc/project/machine-label-raw")
def dlc_machine_label_raw_exists():
    """Return whether _machine_predictions_raw.h5 exists for a labeled-data stem."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    video_stem = request.args.get("video_stem", "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir   = project_path / "labeled-data" / secure_filename(video_stem)
    raw_h5     = stem_dir / "_machine_predictions_raw.h5"
    meta_file  = stem_dir / "_ml_frames.json"
    return jsonify({
        "exists":    raw_h5.is_file(),
        "has_meta":  meta_file.is_file(),
    })


@bp.route("/dlc/project/machine-label-reapply", methods=["POST"])
def dlc_machine_label_reapply():
    """
    Dispatch a Celery task to re-apply a new likelihood threshold to the saved
    raw machine predictions without re-running the model.
    Body: { video_stem, likelihood_threshold }
    Returns: { task_id, operation }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err

    project_path = Path(project_data.get("project_path", ""))
    scorer       = cfg.get("scorer", "User")
    bodyparts    = list(cfg.get("bodyparts", []))

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    threshold  = float(body.get("likelihood_threshold") or 0.9)

    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir = project_path / "labeled-data" / secure_filename(video_stem)
    raw_h5   = stem_dir / "_machine_predictions_raw.h5"
    if not raw_h5.is_file():
        return jsonify({"error": "No saved raw predictions found for this stem."}), 404

    engine = project_data.get("engine", "pytorch")
    task = _ctx.celery().send_task(
        "tasks.dlc_machine_label_reapply",
        kwargs={
            "stem_dir":   str(stem_dir),
            "video_stem": video_stem,
            "scorer":     scorer,
            "bodyparts":  bodyparts,
            "threshold":  threshold,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "machine_label_reapply"}), 202


def _celery_task_status(jid: str) -> str:
    """Task state via the result backend directly (a redis GET) — NOT Celery
    ``AsyncResult``, whose pubsub result-consumer leaks in gunicorn *sync* workers
    and deadlocks one under heavy polling (this route reconciles up to 100 jobs per
    refresh). See ``dlc.inline_analysis._triangulate_task_meta``. Patchable seam."""
    return _ctx.celery().backend.get_task_meta(jid).get("status")


_LIVE_CELERY_STATES = {"PENDING", "RECEIVED", "STARTED", "RETRY", "PROGRESS"}


def _reconcile_job(redis_key: str, jid: str) -> dict | None:
    """Read one train/analyze job hash and cross-check it against Celery.

    Moved out of `dlc_training_jobs` (unchanged below, still calls this) so
    `/dlc/jobs/all` can reuse the exact same reconciliation instead of
    duplicating it — same body, same behavior, just module-scoped so a
    second route can call it too.
    """
    job = _ctx.redis_client().hgetall(redis_key)
    if not job:
        # Orphan: zset still indexes this jid but the backing hash is
        # gone (partial hard-reset, manual cleanup, TTL surprise…).
        # Surface a stub so the UI can show + clear it instead of
        # silently hiding running-but-untracked work.
        return {"task_id": jid, "status": "orphaned"}
    # A `triangulate` batch is a FRONTEND-driven aggregate — its id is not a
    # Celery task, so the Celery reconcile below never applies (a synthetic id
    # reads as PENDING = "live" forever, which would pin it "running" even after
    # the browser tab that drove it closed/reloaded mid-batch). Instead, treat a
    # stale heartbeat (no progress update in _BATCH_STALE_SECS) as abandoned.
    if job.get("operation") == "triangulate":
        if job.get("status") == "running":
            try:
                last = float(job.get("updated_at") or job.get("started_at") or 0)
            except (TypeError, ValueError):
                last = 0.0
            if last and (time.time() - last) > _BATCH_STALE_SECS:
                _ctx.redis_client().hset(redis_key, "status", "dead")
                job["status"] = "dead"
        return job
    celery_state = _celery_task_status(jid)
    if job.get("status") == "running" and celery_state not in _LIVE_CELERY_STATES:
        _ctx.redis_client().hset(redis_key, "status", "dead")
        job["status"] = "dead"
    elif (
        job.get("status") in ("dead", "stopped")
        and celery_state in _LIVE_CELERY_STATES
    ):
        # Reaper false-positive: Celery still considers the task running.
        # Trust the live Celery state and flip the Redis flag back.
        _ctx.redis_client().hset(redis_key, "status", "running")
        job["status"] = "running"
    return job


@bp.route("/dlc/training/jobs")
def dlc_training_jobs():
    """Return all training and analyze jobs (running + recent) stored in Redis.

    Jobs marked 'running' are cross-checked against Celery. If the Celery task
    is no longer active (e.g. after a container restart), the Redis record is
    updated to 'dead' so the UI unblocks automatically.
    """
    jobs = []
    for jid in _ctx.redis_client().zrevrange("dlc_train_jobs", 0, 49):
        job = _reconcile_job("dlc_train_job:" + jid, jid)
        if job:
            job.setdefault("operation", "train")
            jobs.append(job)
    for jid in _ctx.redis_client().zrevrange("dlc_analyze_jobs", 0, 49):
        job = _reconcile_job("dlc_analyze_job:" + jid, jid)
        if job:
            jobs.append(job)

    # Sort combined list by started_at descending
    jobs.sort(key=lambda j: float(j.get("started_at", 0)), reverse=True)
    return jsonify({"jobs": jobs[:50]})


# ── Unified Jobs page (/dlc/jobs/all + /dlc/jobs/cancel) ──────────────────
#
# Every row on the wire has the same shape, regardless of which of the three
# sources it came from:
#
#   id           str   — task_id for a Celery-backed row; "<user_id>:<snap_key>"
#                         for an inline-analysis session row (there is no task_id
#                         for a warm session — see module docstring / f0f71be).
#                         This is exactly what the client echoes back to
#                         POST /dlc/jobs/cancel.
#   type         str   — "celery" | "inline" — selects the cancel dispatch.
#   kind         str   — finer operation label: "train", "analyze",
#                         "triangulate", a bare Celery task name (e.g.
#                         "tasks.dlc_emit_peaks") for inspector-only rows, or
#                         "inline_session".
#   label        str   — human-readable one-line summary for the row.
#   state        str   — "running" | "reserved" | "paused" | "complete" |
#                         "failed" | "dead" | "stopped" | "orphaned" |
#                         "warming" | "ready" | "expired" | "error" |
#                         "stranded" (inline queue with no session — see
#                         `_inline_session_rows`) | ...
#   started_at   float | None — epoch seconds, for sorting/runtime display.
#   detail       dict  — free-form extra fields (project, worker, pending, …).
#   cancellable  bool  — whether POST /dlc/jobs/cancel accepts this row.
#
# De-duplication: a train/analyze task dispatched via send_task can show up
# in BOTH the `dlc_train_jobs`/`dlc_analyze_jobs` zsets (written by the task
# itself) and the Celery inspector's `active`/`reserved` output (read straight
# from the worker). They're keyed on the same task_id, so the zset record
# (richer: project, stage, engine, gpu_id, …) wins over the bare inspector
# record when both are present.

_CELERY_INSPECT_TIMEOUT = 1.0  # seconds — page must never block on a sick worker

# inspect() is TWO blocking broadcasts (active + reserved), each waiting the full
# timeout for replies from every worker -- ~4 s while a training job keeps them
# busy. gunicorn runs 4 SYNC workers, so a /jobs page polling this endpoint from
# a couple of tabs saturates the whole pool and the entire site stops responding.
# That happened in production. Cache the snapshot so polling reuses one result,
# and cap concurrent inspects at one via an NX lock; everyone else gets the last
# known snapshot rather than queueing behind a broadcast.
_CELERY_SNAPSHOT_KEY  = "jobs:celery_snapshot"       # fresh
_CELERY_SNAPSHOT_LAST = "jobs:celery_snapshot:last"  # stale-but-serveable
_CELERY_REFRESH_LOCK  = "jobs:celery_refreshing"
_CELERY_SNAPSHOT_TTL  = 10   # seconds a SUCCESSFUL snapshot counts as fresh
_CELERY_FAIL_TTL      = 2    # a failure is cached only briefly -- see below
_CELERY_STALE_TTL     = 600  # how long a stale snapshot stays serveable


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row_from_zset_job(job: dict) -> dict:
    """Turn one reconciled `dlc_train_job:*`/`dlc_analyze_job:*` hash into a
    unified-jobs row. `job` is the dict `_reconcile_job` returns."""
    op = job.get("operation") or "train"
    status = job.get("status", "unknown")
    task_id = job.get("task_id", "")
    project = job.get("project") or job.get("target_path") or task_id
    detail = {k: v for k, v in job.items()
              if k not in ("task_id", "status", "operation", "started_at")}
    return {
        "id":          task_id,
        "type":        "celery",
        "kind":        op,
        "label":       f"{op} — {project}" if project else op,
        "state":       status,
        "started_at":  _to_float(job.get("started_at")),
        "detail":      detail,
        "cancellable": status in ("running", "paused"),
    }


def _zset_rows() -> list[dict]:
    """Rows sourced from `dlc_train_jobs` + `dlc_analyze_jobs`, reusing the
    exact reconciliation `/dlc/training/jobs` already performs (see
    `_reconcile_job`) — item (a) of the unified-jobs merge."""
    rows = []
    for jid in _ctx.redis_client().zrevrange("dlc_train_jobs", 0, 49):
        job = _reconcile_job("dlc_train_job:" + jid, jid)
        if job:
            job.setdefault("operation", "train")
            rows.append(_row_from_zset_job(job))
    for jid in _ctx.redis_client().zrevrange("dlc_analyze_jobs", 0, 49):
        job = _reconcile_job("dlc_analyze_job:" + jid, jid)
        if job:
            rows.append(_row_from_zset_job(job))
    return rows


def _row_from_inspect_task(task: dict, worker: str, state: str) -> dict:
    name = task.get("name") or ""
    tid = task.get("id") or ""
    return {
        "id":          tid,
        "type":        "celery",
        "kind":        name or "celery_task",
        "label":       f"{name or 'celery task'} ({worker})",
        "state":       state,
        "started_at":  _to_float(task.get("time_start")),
        "detail":      {"worker": worker, "args": task.get("args"),
                         "kwargs": task.get("kwargs")},
        "cancellable": bool(tid),
    }


def _celery_inspect_rows() -> tuple[list[dict], bool]:
    """Rows from Celery's live `inspect().active()` + `.reserved()` — item (b)
    of the unified-jobs merge. Catches `tasks.dlc_emit_peaks`, the LP tasks on
    the `lp_3d` queue, `tasks.process_triangulate_range`, and analyze-video
    with NO producer-side changes, because we read the worker's live state
    directly instead of relying on a Redis record any of those tasks would
    have had to opt into writing.

    Bounded to a short timeout (`_CELERY_INSPECT_TIMEOUT`) and never raises —
    on any error, OR if the inspector got no reply at all (both `active()`
    and `reserved()` come back `None`, which is what a timed-out/unreachable
    inspect looks like), returns `([], False)` so the caller can degrade
    gracefully instead of hanging the whole /jobs page on a sick worker.
    """
    # --- cache layer (see _CELERY_SNAPSHOT_KEY comment) ------------------
    r = None
    try:
        r = _ctx.redis_client()
        hit = r.get(_CELERY_SNAPSHOT_KEY)
        if hit:
            d = _json.loads(hit)
            return d["rows"], d["reachable"]
    except Exception:
        r = None  # no redis -> fall through and inspect directly

    if r is not None:
        try:
            # Only ONE request may run the broadcast; others serve the last
            # snapshot immediately rather than piling up on the worker pool.
            got_lock = r.set(_CELERY_REFRESH_LOCK, "1", nx=True,
                             ex=int(_CELERY_INSPECT_TIMEOUT * 2) + 2)
            if not got_lock:
                stale = r.get(_CELERY_SNAPSHOT_LAST)
                if stale:
                    d = _json.loads(stale)
                    return d["rows"], d["reachable"]
                return [], False
        except Exception:
            pass

    try:
        insp = _ctx.celery().control.inspect(timeout=_CELERY_INSPECT_TIMEOUT)
        active = insp.active()
        reserved = insp.reserved()
    except Exception:
        _store_celery_snapshot(r, [], False)
        prev = _last_good_snapshot(r)
        return (prev["rows"], prev["reachable"]) if prev else ([], False)
    if active is None and reserved is None:
        # A timed-out broadcast looks identical to "no workers". Serve the last
        # known-good rows rather than blanking the page on one slow reply.
        _store_celery_snapshot(r, [], False)
        prev = _last_good_snapshot(r)
        return (prev["rows"], prev["reachable"]) if prev else ([], False)
    rows = []
    for worker, tasks in (active or {}).items():
        for t in tasks:
            rows.append(_row_from_inspect_task(t, worker, "running"))
    for worker, tasks in (reserved or {}).items():
        for t in tasks:
            rows.append(_row_from_inspect_task(t, worker, "reserved"))
    _store_celery_snapshot(r, rows, True)
    return rows, True


def _store_celery_snapshot(r, rows, reachable) -> None:
    """Persist a snapshot. Never raises: caching is an optimisation, and a redis
    hiccup must not take the jobs page down.

    A FAILED inspect is cached only for `_CELERY_FAIL_TTL` and never overwrites
    `LAST`. Caching a failure for the full TTL made a single transient timeout
    hide every running task for 10s -- including the session draining the user's
    queue -- which is worse than showing slightly stale but real rows.
    """
    if r is None:
        return
    try:
        blob = _json.dumps({"rows": rows, "reachable": reachable})
        if not reachable:
            # NEVER cache a failure. A short-TTL failure entry overwrote the
            # fresh SUCCESS entry, collapsing the cache and sending every other
            # request back through the 4s broadcast. The NX refresh lock alone
            # is enough to stop a stampede; on failure we serve LAST instead.
            return
        r.set(_CELERY_SNAPSHOT_KEY, blob, ex=_CELERY_SNAPSHOT_TTL)
        r.set(_CELERY_SNAPSHOT_LAST, blob, ex=_CELERY_STALE_TTL)
    except Exception:
        pass


def _last_good_snapshot(r):
    """The most recent SUCCESSFUL snapshot, or None."""
    if r is None:
        return None
    try:
        blob = r.get(_CELERY_SNAPSHOT_LAST)
        if blob:
            return _json.loads(blob)
    except Exception:
        pass
    return None


def _parse_inline_suffix(key: str, prefix: str) -> tuple[str, str] | None:
    """Split `inline:<prefix>:<user_id>:<snap_key>` into (user_id, snap_key).

    Splits on the KNOWN prefix (not a naive `split(":")` count) so this holds
    up even if `snap_key` or `user_id` ever contained a colon. Returns None
    for anything that doesn't match cleanly — a malformed key must be
    skipped, never raise and take the whole `/dlc/jobs/all` endpoint down.
    """
    full_prefix = f"inline:{prefix}:"
    if not key.startswith(full_prefix):
        return None
    rest = key[len(full_prefix):]
    if ":" not in rest:
        return None
    user_id, snap_key = rest.split(":", 1)
    if not user_id or not snap_key:
        return None
    return user_id, snap_key


def _inline_session_rows() -> list[dict]:
    """Rows from inline-analysis sessions/queues — item (c) of the unified-
    jobs merge. One row per `<user_id>:<snap_key>` suffix seen across the
    UNION of `inline:session:*` and `inline:queue:*` keys (not just
    `inline:session:*` — a queue can outlive its session, see below).

    Each suffix lands in one of three states:
      - session + non-empty (or absent) queue: normal row, `state` is the
        session's own status, pending count from LLEN.
      - session, empty/absent queue: idle warm session, nothing pending.
      - queue, NO session: **STRANDED** — the session's Celery task died or
        lost its race with a stop signal (see f0f71be) and nothing is left
        to drain this queue. `inline:session:*`-only scanning made this case
        invisible, which is exactly the stranded-work state the Jobs page
        exists to surface (261 ranges stranded once, 522 across four users
        before that). Reported prominently: `state` is the literal
        "stranded", distinct from any real session status.

    No project/snapshot metadata exists for a stranded row (that lived only
    in the session hash, which is gone) — it is left empty rather than
    fabricated.

    All rows are cancellable: cancelling just sets the stop key and drops
    the queue (see `_inline_analysis._explicit_session_stop`), which works
    whether or not a session hash exists.
    """
    r = _ctx.redis_client()

    suffixes: set[tuple[str, str]] = set()
    for key in r.scan_iter("inline:session:*"):
        parsed = _parse_inline_suffix(key, "session")
        if parsed:
            suffixes.add(parsed)
    for key in r.scan_iter("inline:queue:*"):
        parsed = _parse_inline_suffix(key, "queue")
        if parsed:
            suffixes.add(parsed)

    rows = []
    for user_id, snap_key in sorted(suffixes):
        session_key = f"inline:session:{user_id}:{snap_key}"
        queue_key = f"inline:queue:{user_id}:{snap_key}"
        h = r.hgetall(session_key) or {}
        pending = r.llen(queue_key)
        stranded = not h
        if stranded and pending == 0:
            # Session hash and queue both gone/empty by the time we got
            # here (e.g. drained between the scan and this read) — nothing
            # left to show for this suffix.
            continue

        project = h.get("project", "")
        snapshot = Path(h.get("snapshot_path", "") or "").name
        plural = "" if pending == 1 else "s"
        if stranded:
            state = "stranded"
            label = (f"inline analysis — STRANDED (no active session) — "
                      f"{pending} pending range{plural}")
        else:
            state = h.get("status", "unknown")
            label = (f"inline analysis — {project or 'unknown project'} "
                      f"— {pending} pending range{plural}")

        rows.append({
            "id":          f"{user_id}:{snap_key}",
            "type":        "inline",
            "kind":        "inline_session",
            "label":       label,
            "state":       state,
            "started_at":  _to_float(h.get("started_at")),
            "detail": {
                "project":     project,
                "snapshot":    snapshot,
                "snap_key":    snap_key,
                "user_id":     user_id,
                "pending":     pending,
                "stranded":    stranded,
            },
            "cancellable": True,
        })
    return rows


@bp.route("/dlc/jobs/all")
def dlc_jobs_all():
    """Unified Jobs-page listing: merge (a) the train/analyze zset jobs,
    (b) Celery's live active+reserved inspector output, and (c) warm
    inline-analysis sessions into one flat, de-duplicated list. See the
    row-shape docstring above `_CELERY_INSPECT_TIMEOUT`.

    Never blocks on a sick/slow worker: the Celery inspector is bounded to
    `_CELERY_INSPECT_TIMEOUT` seconds and degrades to `celery_reachable:
    false` (with the other two sources still returned) instead of hanging
    or erroring the whole response.
    """
    by_id: dict[str, dict] = {}
    inspect_rows, celery_reachable = _celery_inspect_rows()
    for row in inspect_rows:
        if row["id"]:
            by_id[row["id"]] = row
    for row in _zset_rows():
        # Same task_id may already be present from the inspector — the zset
        # record is richer (project, stage, engine, gpu_id, …), so it wins.
        if row["id"]:
            by_id[row["id"]] = row

    jobs = list(by_id.values()) + _inline_session_rows()
    jobs.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return jsonify({"jobs": jobs, "celery_reachable": celery_reachable})


@bp.route("/dlc/jobs/cancel", methods=["POST"])
def dlc_jobs_cancel():
    """Kill switch for any row returned by GET /dlc/jobs/all. Body: {id, type}.

    type == "celery":
        `_ctx.celery().control.revoke(task_id, terminate=True)`. terminate=True
        is REQUIRED and deliberate — this is a kill switch that stops running
        work, not merely a "don't start this later" (that softer semantics is
        exactly what /dlc/training/queue/cancel — terminate=False — already
        provides for queued-but-not-started tasks; that endpoint is untouched
        and has different semantics on purpose).

        Safety notes — read before "helpfully" changing terminate=False:
          - h5 writes go through `_atomic_write_h5` (temp-then-rename), so a
            terminate mid-write cannot corrupt an existing file: only the
            in-flight range is lost, never the file.
          - terminate=True kills the prefork CHILD PROCESS. For the warm
            inline-analysis session task (`tasks.dlc_inline_session`) that
            also drops the loaded model and any sibling range mid-flight in
            that same worker process — not just the targeted task. That's an
            accepted, understood tradeoff for a kill switch, not a bug.

    type == "inline":
        `id` is "<user_id>:<snap_key>" (see `_inline_session_rows`). Same
        effect as an explicit (non-only_if_idle) POST .../session/stop: sets
        `inline:control:<user_id>:<snap_key>` = "stop" AND deletes
        `inline:queue:<user_id>:<snap_key>` via the shared
        `_inline_analysis._explicit_session_stop` helper (reused from
        f0f71be) — a cancel from the Jobs page must not orphan the queue
        either, that's the original 522-stranded-ranges bug.

    Never dispatches with an empty id (an empty revoke id can broadcast far
    more broadly than intended). Unknown/absent type or id -> 400.
    """
    body = request.get_json(silent=True) or {}
    job_id = (body.get("id") or "").strip()
    job_type = (body.get("type") or "").strip()

    if job_type not in ("celery", "inline"):
        return jsonify({"error": "type must be 'celery' or 'inline'"}), 400
    if not job_id:
        return jsonify({"error": "id is required"}), 400

    if job_type == "celery":
        _ctx.celery().control.revoke(job_id, terminate=True)
        return jsonify({"cancelled": True, "type": "celery", "cleared": None})

    # type == "inline"
    if ":" not in job_id:
        return jsonify({
            "error": "invalid inline id, expected '<user_id>:<snap_key>'"
        }), 400
    user_id, snap_key = job_id.split(":", 1)
    if not user_id or not snap_key:
        return jsonify({
            "error": "invalid inline id, expected '<user_id>:<snap_key>'"
        }), 400
    cleared = _inline_analysis._explicit_session_stop(
        _ctx.redis_client(), user_id, snap_key)
    return jsonify({"cancelled": True, "type": "inline", "cleared": cleared})


@bp.route("/dlc/training/jobs/clear", methods=["POST"])
def dlc_training_jobs_clear():
    """Delete all finished (non-running) train and analyze jobs from the monitor list."""
    removed = 0
    for jid in _ctx.redis_client().zrevrange("dlc_train_jobs", 0, 199):
        job = _ctx.redis_client().hgetall("dlc_train_job:" + jid)
        if job.get("status") != "running":
            _ctx.redis_client().zrem("dlc_train_jobs", jid)
            _ctx.redis_client().delete("dlc_train_job:" + jid)
            removed += 1
    for jid in _ctx.redis_client().zrevrange("dlc_analyze_jobs", 0, 199):
        job = _ctx.redis_client().hgetall("dlc_analyze_job:" + jid)
        if job.get("status") != "running":
            _ctx.redis_client().zrem("dlc_analyze_jobs", jid)
            _ctx.redis_client().delete("dlc_analyze_job:" + jid)
            removed += 1
    return jsonify({"removed": removed})


# ── Queue inspection helpers ──────────────────────────────────────────────

def _read_broker_queues() -> list[dict]:
    """Return a list of pending (queued) tasks across all Celery broker queues.

    Each entry has: task_id, task_name, queue, args, kwargs, eta.
    Tasks are read directly from the Redis broker lists so no worker is required.
    Tasks already tracked as running jobs are excluded to prevent duplicates
    that appear when acks_late requeues a task after a worker restart.
    """
    import json as _json
    r = _ctx.redis_client()

    # Collect all task IDs already tracked as active jobs
    running_ids: set[str] = set()
    for zset in ("dlc_train_jobs", "dlc_analyze_jobs"):
        for jid in r.zrevrange(zset, 0, 99):
            running_ids.add(jid)

    _INTERNAL = {"tasks.dlc_probe_gpu_stats"}

    queue_names = ("celery", "pytorch", "tensorflow")
    tasks = []
    for qname in queue_names:
        raw_items = r.lrange(qname, 0, -1)  # read without consuming
        for raw in raw_items:
            try:
                msg = _json.loads(raw)
                # Kombu message envelope: body is base64 JSON or raw JSON
                body = msg.get("body") or {}
                if isinstance(body, str):
                    import base64 as _b64
                    try:
                        body = _json.loads(_b64.b64decode(body).decode())
                    except Exception:
                        body = _json.loads(body)
                # Celery task message layout: [args, kwargs, embed]
                if isinstance(body, list) and len(body) >= 2:
                    task_kwargs = body[1] if isinstance(body[1], dict) else {}
                else:
                    task_kwargs = {}
                headers = msg.get("headers") or {}
                task_id   = headers.get("id") or msg.get("properties", {}).get("correlation_id", "")
                task_name = headers.get("task") or msg.get("headers", {}).get("task", "")
                if task_id in running_ids or task_name in _INTERNAL:
                    continue
                tasks.append({
                    "task_id":    task_id,
                    "task_name":  task_name,
                    "queue":      qname,
                    "config_path": task_kwargs.get("config_path", ""),
                    "eta":        headers.get("eta"),
                })
            except Exception:
                pass
    return tasks


@bp.route("/dlc/training/queue")
def dlc_training_queue():
    """Return all pending (queued but not yet running) Celery tasks."""
    tasks = _read_broker_queues()
    return jsonify({"tasks": tasks, "count": len(tasks)})


@bp.route("/dlc/training/queue/cancel", methods=["POST"])
def dlc_training_queue_cancel():
    """Revoke a queued task by task_id so it will not run when picked up."""
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id required"}), 400

    # Revoke via Celery control (broadcasts to all workers)
    _ctx.celery().control.revoke(task_id, terminate=False)

    # Also remove from broker queue directly so it doesn't linger
    import json as _json
    r = _ctx.redis_client()
    for qname in ("celery", "pytorch", "tensorflow"):
        raw_items = r.lrange(qname, 0, -1)
        for raw in raw_items:
            try:
                msg = _json.loads(raw)
                headers = msg.get("headers") or {}
                tid = headers.get("id") or msg.get("properties", {}).get("correlation_id", "")
                if tid == task_id:
                    r.lrem(qname, 0, raw)
                    break
            except Exception:
                pass

    return jsonify({"revoked": task_id})


@bp.route("/dlc/training/queue/cancel-all", methods=["POST"])
def dlc_training_queue_cancel_all():
    """Revoke and remove all pending queued tasks."""
    tasks   = _read_broker_queues()
    revoked = []
    import json as _json
    r = _ctx.redis_client()
    for t in tasks:
        tid = t["task_id"]
        if tid:
            _ctx.celery().control.revoke(tid, terminate=False)
            revoked.append(tid)
    # Flush entire queues (simpler than per-task removal when cancelling all)
    for qname in ("celery", "pytorch", "tensorflow"):
        r.delete(qname)
    return jsonify({"revoked": len(revoked)})


@bp.route("/dlc/project/tapnet-check")
def dlc_tapnet_check():
    """
    Scan a labeled-data folder and return which consecutive sequences have a
    labeled anchor frame (and are therefore eligible for TAPNet propagation).

    Query params: video_stem
    Returns: { sequences: [{frames, anchor, first_labeled, last_labeled}] }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    video_stem   = request.args.get("video_stem", "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir = project_path / "labeled-data" / secure_filename(video_stem)
    if not stem_dir.is_dir():
        return jsonify({"error": f"Folder not found: {stem_dir}"}), 404

    try:
        from dlc_tapnet_tracker import (
            find_consecutive_sequences,
            load_dlc_labels,
            get_labeled_frame_names,
            check_anchor_frames,
        )

        pngs = sorted(stem_dir.glob("*.png"))
        frame_names = [p.name for p in pngs]
        sequences   = find_consecutive_sequences(frame_names)

        csv_candidates = sorted(stem_dir.glob("CollectedData_*.csv"))
        labeled: set[str] = set()
        if csv_candidates:
            df = load_dlc_labels(csv_candidates[0])
            labeled = get_labeled_frame_names(df)

        result = []
        for seq in sequences:
            info = check_anchor_frames(seq, labeled)
            result.append({
                "frame_count":   len(seq),
                "first_frame":   seq[0],
                "last_frame":    seq[-1],
                "first_labeled": info["first_labeled"],
                "last_labeled":  info["last_labeled"],
                "anchor":        info["anchor"],
                "propagatable":  info["anchor"] is not None,
            })

        return jsonify({
            "video_stem":       video_stem,
            "total_frames":     len(frame_names),
            "sequences":        result,
            "propagatable_count": sum(1 for r in result if r["propagatable"]),
        })

    except Exception as exc:
        import traceback
        return jsonify({"error": str(exc), "detail": traceback.format_exc()}), 500


@bp.route("/dlc/project/tapnet-propagate", methods=["POST"])
def dlc_tapnet_propagate():
    """
    Dispatch a TAPNet label-propagation Celery task.

    Body (JSON):
        video_stem              (str, required)
        tapnet_checkpoint_path  (str, required) — absolute path to TAPIR .npy
        anchor                  (str) "auto" | "first" | "last"  default "auto"
        gpu_index               (int) default 0 (RTX 5090)
        overwrite               (bool) default false
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    project_path = Path(project_data.get("project_path", ""))

    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    ckpt_path  = (body.get("tapnet_checkpoint_path") or "").strip()

    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400
    if not ckpt_path:
        return jsonify({"error": "tapnet_checkpoint_path is required."}), 400

    labeled_data_path = project_path / "labeled-data" / secure_filename(video_stem)
    if not labeled_data_path.is_dir():
        return jsonify({"error": f"Frames folder not found: {labeled_data_path}"}), 400

    params = {
        "anchor":    (body.get("anchor") or "auto").strip(),
        "gpu_index": int(body.get("gpu_index") or 0),
        "overwrite": bool(body.get("overwrite", False)),
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_tapnet_propagate",
        kwargs={
            "config_path":             config_path,
            "labeled_data_path":       str(labeled_data_path),
            "tapnet_checkpoint_path":  ckpt_path,
            "params":                  params,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "tapnet_propagate"}), 202


@bp.route("/dlc/project/tapnet-confirmed")
def dlc_tapnet_confirmed():
    """
    Return confirmed anchor frames and TAPNet-labeled frames for a stem.

    Query params: video_stem
    Returns: { confirmed: [...], tapnet_frames: [...] }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    video_stem   = request.args.get("video_stem", "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir = project_path / "labeled-data" / secure_filename(video_stem)
    if not stem_dir.is_dir():
        return jsonify({"error": f"Folder not found: {stem_dir}"}), 404

    try:
        from dlc_tapnet_tracker import load_confirmed_anchors, load_tapnet_frames
        confirmed     = sorted(load_confirmed_anchors(stem_dir))
        tapnet_frames = sorted(load_tapnet_frames(stem_dir))
        return jsonify({"confirmed": confirmed, "tapnet_frames": tapnet_frames})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/dlc/project/tapnet-confirm-frame", methods=["POST"])
def dlc_tapnet_confirm_frame():
    """
    Toggle a frame as a confirmed TAPNet anchor.

    Body (JSON): { video_stem, frame_name }
    Returns: { frame, confirmed, total }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    frame_name = (body.get("frame_name") or "").strip()

    if not video_stem or not frame_name:
        return jsonify({"error": "video_stem and frame_name are required."}), 400

    stem_dir = project_path / "labeled-data" / secure_filename(video_stem)
    if not stem_dir.is_dir():
        return jsonify({"error": f"Folder not found: {stem_dir}"}), 404

    try:
        from dlc_tapnet_tracker import toggle_confirmed_anchor
        result = toggle_confirmed_anchor(stem_dir, frame_name)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/dlc/project/tapnet-propagate-multi", methods=["POST"])
def dlc_tapnet_propagate_multi():
    """
    Dispatch a multi-anchor TAPNet propagation task using confirmed anchors.

    Body (JSON):
        video_stem              (str, required)
        tapnet_checkpoint_path  (str, required)
        gpu_index               (int) default 0
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    project_path = Path(project_data.get("project_path", ""))

    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    ckpt_path  = (body.get("tapnet_checkpoint_path") or "").strip()

    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400
    if not ckpt_path:
        return jsonify({"error": "tapnet_checkpoint_path is required."}), 400

    labeled_data_path = project_path / "labeled-data" / secure_filename(video_stem)
    if not labeled_data_path.is_dir():
        return jsonify({"error": f"Frames folder not found: {labeled_data_path}"}), 400

    # Load confirmed anchors from sidecar
    try:
        from dlc_tapnet_tracker import load_confirmed_anchors
        anchor_frames = sorted(load_confirmed_anchors(labeled_data_path))
    except Exception as exc:
        return jsonify({"error": f"Could not read confirmed anchors: {exc}"}), 500

    if not anchor_frames:
        return jsonify({"error": "No confirmed anchor frames found. Confirm at least one frame first."}), 400

    params = {
        "anchor_frames": anchor_frames,
        "gpu_index":     int(body.get("gpu_index") or 0),
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_tapnet_propagate",
        kwargs={
            "config_path":            config_path,
            "labeled_data_path":      str(labeled_data_path),
            "tapnet_checkpoint_path": ckpt_path,
            "params":                 params,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({
        "task_id":       task.id,
        "operation":     "tapnet_propagate_multi",
        "anchor_count":  len(anchor_frames),
    }), 202


@bp.route("/dlc/project/tapnet-propagate/stop", methods=["POST"])
def dlc_tapnet_propagate_stop():
    """Stop a running tapnet_propagate task."""
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id is required."}), 400
    _ctx.celery().control.revoke(task_id, terminate=True)
    try:
        from celery.result import AsyncResult
        AsyncResult(task_id, app=_ctx.celery()).forget()
    except Exception:
        pass
    return jsonify({"status": "stop_requested", "task_id": task_id}), 200


@bp.route("/dlc/gpu/status")
def dlc_gpu_status():
    """
    Return GPU stats. Prefers the Redis cache written by the Celery worker
    during training; when no cache is present, dispatches a lightweight probe
    task to the GPU-enabled worker and waits up to 5 s for the result.
    """
    import time as _time

    raw = _ctx.redis_client().get("dlc_gpu_stats")
    ts  = _ctx.redis_client().get("dlc_gpu_stats_ts")

    # No cache — ask the GPU worker to run nvidia-smi for us
    if not raw:
        try:
            task = _ctx.celery().send_task("tasks.dlc_probe_gpu_stats")
            csv  = task.get(timeout=5, propagate=False)
            if csv:
                raw = csv
                ts  = str(_time.time())
        except Exception:
            pass

    if not raw:
        return jsonify({"gpus": [], "available": False})

    def _parse_csv(text):
        gpus = []
        for line in text.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                try:
                    gpus.append({
                        "index":        int(parts[0]),
                        "name":         parts[1],
                        "utilization":  int(parts[2]),
                        "memory_used":  int(parts[3]),
                        "memory_total": int(parts[4]),
                        "temperature":  int(parts[5]),
                    })
                except (ValueError, IndexError):
                    pass
        return gpus

    gpus = _parse_csv(raw)
    if not gpus:
        return jsonify({"gpus": [], "available": False})

    age = round(_time.time() - float(ts), 1) if ts else None
    return jsonify({"gpus": gpus, "available": True, "age_s": age})
