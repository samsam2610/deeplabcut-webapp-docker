"""DLC Inline Analysis blueprint.

Routes (all under /dlc/project/inline-analysis/):
  POST /session/start
  GET  /session/status   (read-only; does not bump activity)
  POST /session/stop
  POST /range            (bumps activity)
  GET  /range/status
  GET  /video-info

Activity (idle TTL) is bumped ONLY on /range submit and on each range
the worker finishes. The worker times out after `ttl_seconds` of no
range submission, regardless of whether the card is open. No
client-side heartbeat — that's the Jobs-page pattern and isn't needed
here, and a run must survive the browser closing.

The WORKER does heartbeat, on `heartbeat` in the session hash (see
tasks._touch_session). That is a liveness signal, not a keepalive: it
is how /session/start tells a running session from one whose worker was
killed, so a dead session is replaced instead of silently swallowing
every subsequent submit.

See docs/superpowers/specs/2026-05-20-inline-analysis-design.md.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, session as flask_session

from . import ctx as _ctx
from . import canonical as _canonical
from . import canonical_3d as _canonical_3d
from . import project_settings as _project_settings
from . import anipose_config as _anipose_config
from .utils import _dlc_project_security_check

bp = Blueprint("dlc_inline_analysis", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────

def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _snap_key(config_path: str, shuffle: int, snapshot_path: str) -> str:
    raw = f"{config_path}|{int(shuffle)}|{snapshot_path}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _active_project():
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _disable_reason(project: dict):
    """Return (status_code, error) if the project can't run inline analysis.

    Reads config.yaml on disk directly — no separate route exposes
    multianimal/engine, so neither does the Redis-cached project state.
    """
    cfg_path = Path(project.get("config_path", ""))
    if not cfg_path.is_file():
        return 400, "Active project has no readable config.yaml."
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception as exc:
        return 400, f"Could not parse config.yaml: {exc}"
    if cfg.get("multianimalproject"):
        return 409, (
            "Inline Analysis is single-animal only in v1. "
            "Use the Analyze Video/Frames card for multi-animal projects."
        )
    if (cfg.get("engine") or "pytorch").lower() != "pytorch":
        return 409, "Inline Analysis requires the PyTorch engine."
    return None


def _celery_send_task(name, *, kwargs, queue):
    """Indirection so tests can patch this single function."""
    return _ctx.celery().send_task(name, kwargs=kwargs, queue=queue)


def _enqueue_triangulate_range(cam0_video: str, start_frame: int, n_frames: int):
    """Enqueue the range-triangulate Celery task and return the AsyncResult.

    Dispatched by name via ``send_task`` (the Flask container cannot import the
    worker task object — anipose is worker-only — so ``.delay`` isn't available
    here). CPU-only → default ``celery`` queue. Patchable seam for tests."""
    return _celery_send_task(
        "tasks.process_triangulate_range",
        kwargs={"cam0_video": cam0_video,
                "start_frame": int(start_frame),
                "n_frames": int(n_frames)},
        queue="celery",
    )


def _triangulate_async_result(req_id: str):
    """Look up a Celery AsyncResult for the range-triangulate task. Patchable
    seam so route tests can inject fake states without a live worker/backend."""
    from celery.result import AsyncResult
    return AsyncResult(req_id, app=_ctx.celery())


def _triangulate_task_meta(req_id: str) -> dict:
    """Read the range-triangulate task's stored meta straight from the result
    backend (a plain redis GET) — NOT via Celery ``AsyncResult``.

    ``AsyncResult``'s redis result-consumer registers a pubsub subscription that is
    torn down in ``__del__``; in gunicorn *sync* workers the heavy per-range polling
    of a triangulate batch (hundreds of status reads) accumulates until a worker
    deadlocks on the pubsub lock during GC → the 300s WORKER TIMEOUT → SIGKILL →
    a 502 on whatever request that worker held (aborting the batch). ``get_task_meta``
    never touches the ResultConsumer, so it can't leak. Returns
    ``{status, result, traceback, …}``. Patchable seam for tests."""
    return _ctx.celery().backend.get_task_meta(req_id)


def _probe_video(path: Path) -> dict:
    """Cheap video metadata probe (nframes, fps, width, height)."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    info = {
        "nframes": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        "fps":     float(cap.get(cv2.CAP_PROP_FPS) or 0),
        "width":   int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
        "height":  int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
    }
    cap.release()
    return info


def _hgetall(redis_, key):
    """hgetall with FakeRedis fallback."""
    h = None
    if hasattr(redis_, "hgetall"):
        try:
            h = redis_.hgetall(key)
        except Exception:
            h = None
    if not h:
        try:
            h = dict(redis_._hstore.get(key, {}))
        except AttributeError:
            h = {}
    return h or {}


def _finalize_range_to_canonical(video_path, source_h5, start_frame, n_frames, config_path):
    """Copy rows [start_frame, start_frame+n_frames) from a layer h5 into the
    canonical _analyzed file. Curated range wins; frames already in canonical
    outside the curated range are preserved;
    _analyzed created dense if missing. Returns (h5_path, csv_path, n_written)."""
    import pandas as pd
    df = pd.read_hdf(str(source_h5))
    source_scorer = df.columns.get_level_values(0)[0]
    wanted = set(range(int(start_frame), int(start_frame) + int(n_frames)))
    sliced = df[df.index.isin(wanted)]
    canon = _canonical.canonical_scorer(config_path)
    h5_path, csv_path = _canonical.write_to_canonical(
        video_path, sliced,
        source_scorer=source_scorer, canonical_scorer=canon, save_as_csv=True)
    return h5_path, csv_path, int(len(sliced))


# ── Routes ────────────────────────────────────────────────────────────────

#: A session worker stamps `heartbeat` every 30 s (tasks._SESSION_HEARTBEAT_S).
#: Anything older than this is a corpse — its worker was SIGKILLed by a task
#: time limit, OOM-killed, or lost to a container restart.
_SESSION_STALE_AFTER_S = 120


def _session_is_alive(existing: dict) -> bool:
    """True iff this session still has a worker that will drain its queue.

    A status of warming/ready is NOT sufficient on its own. A hard kill skips
    the worker's exit path, so the hash keeps saying "ready" forever while
    nothing drains the queue; /session/start used to believe it and return
    early, so every subsequent submit piled onto a queue with no consumer and
    the card sat there looking warm. Treating such a session as dead makes the
    next click re-dispatch a worker, which also drains whatever the dead one
    left behind.

    `warming` with NO heartbeat is alive: /session/start writes that hash at
    dispatch, and the task can sit in the broker queue for a long time before a
    worker picks it up — the worker log has a 2 h wait behind another job.
    Calling that dead would dispatch a duplicate session on every click.
    Once a worker has beaten even once, a stale beat means it died.
    """
    status = existing.get("status")
    if status not in {"warming", "ready"}:
        return False
    raw = existing.get("heartbeat")
    if not raw:
        # Never started. Queued sessions are alive; a "ready" hash without a
        # beat is a corpse from before heartbeats existed.
        return status == "warming"
    try:
        beat = float(raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - beat) < _SESSION_STALE_AFTER_S


@bp.route("/dlc/project/inline-analysis/session/start", methods=["POST"])
def session_start():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    block = _disable_reason(project)
    if block:
        return jsonify({"error": block[1]}), block[0]

    body = request.get_json(silent=True) or {}
    snapshot_path = (body.get("snapshot_path") or "").strip()
    shuffle = int(body.get("shuffle") or 1)
    ttl = int(body.get("ttl_seconds") or 300)
    if not snapshot_path:
        return jsonify({"error": "snapshot_path required"}), 400

    config_path = project["config_path"]

    # /dlc/project/snapshots returns project-relative paths (rel_path).
    # DLCLoader wants absolute. Resolve here so (a) we can validate before
    # dispatch and (b) the snap_key is canonical regardless of whether the
    # caller sent a relative or absolute path.
    project_root = Path(config_path).parent
    snap_abs = (project_root / snapshot_path).resolve()
    if not snap_abs.is_file():
        return jsonify({
            "error": f"snapshot not found: {snapshot_path}"
        }), 404
    snapshot_path = str(snap_abs)

    snap_key = _snap_key(config_path, shuffle, snapshot_path)
    user_id = _user_id()
    session_key = f"inline:session:{user_id}:{snap_key}"
    redis = _ctx.redis_client()

    existing = _hgetall(redis, session_key)
    if _session_is_alive(existing):
        return jsonify({
            "session_id": snap_key, "snap_key": snap_key,
            "status": existing.get("status", "warming"),
        }), 202

    # Mark warming up front so the poll sees a non-empty hash even if
    # dispatch is slow.
    redis.hset(session_key, mapping={
        "status": "warming",
        "snapshot_path": snapshot_path,
        "project": Path(config_path).parent.name,
        "started_at": str(time.time()),
        "last_activity": str(time.time()),
    })

    _celery_send_task(
        "tasks.dlc_inline_session",
        kwargs={
            "user_id":          user_id,
            "config_path":      config_path,
            "snap_key":         snap_key,
            "snapshot_path":    snapshot_path,
            "shuffle":          shuffle,
            "trainingsetindex": int(body.get("trainingsetindex") or 0),
            "batch_size":       int(body.get("batch_size") or 8),
            "ttl":              ttl,
        },
        queue="pytorch",
    )
    return jsonify({
        "session_id": snap_key, "snap_key": snap_key, "status": "warming",
    }), 202


@bp.route("/dlc/project/inline-analysis/session/status", methods=["GET"])
def session_status():
    snap_key = (request.args.get("snap_key") or "").strip()
    if not snap_key:
        return jsonify({"error": "snap_key required"}), 400
    redis = _ctx.redis_client()
    key = f"inline:session:{_user_id()}:{snap_key}"
    h = _hgetall(redis, key)
    if not h:
        return jsonify({"status": "absent", "idle_remaining_s": 0})
    last = float(h.get("last_activity") or 0)
    ttl = 300
    idle_remaining = max(0, int(ttl - (time.time() - last)))
    status = h.get("status", "unknown")
    # Don't report a hard-killed worker as warm just because its hash still
    # says so — see _session_is_alive. "dead" is what the card's warm
    # indicator shows, and it is the truth until the next start re-dispatches.
    if status in {"warming", "ready"} and not _session_is_alive(h):
        status = "dead"
    out = {
        "status": status,
        "idle_remaining_s": idle_remaining,
    }
    if h.get("last_error"):
        out["last_error"] = h["last_error"]
    return jsonify(out)


def _explicit_session_stop(redis_, user_id: str, snap_key: str) -> int:
    """Unconditionally stop a warm inline-analysis session and drop its
    pending queue. Returns the number of queued-but-undrained ranges that
    were discarded.

    Shared by two "deliberate cancel" call sites — POST .../session/stop
    (only_if_idle absent/false) and dlc.monitoring's `/dlc/jobs/cancel`
    (type="inline") — so both stop the session AND clear the queue rather
    than leaving it to be drained later, which is what stranded 522 ranges
    across four users before f0f71be.
    """
    control_key = f"inline:control:{user_id}:{snap_key}"
    queue_key = f"inline:queue:{user_id}:{snap_key}"
    cleared = redis_.llen(queue_key)
    redis_.set(control_key, "stop", ex=60)
    redis_.delete(queue_key)
    return cleared


@bp.route("/dlc/project/inline-analysis/session/stop", methods=["POST"])
def session_stop():
    """Stop a warm inline-analysis session.

    Two distinct intents share this route, disambiguated by `only_if_idle`
    in the JSON body:

    - `only_if_idle: true` (tab-close / card-close): the caller doesn't know
      whether OTHER tabs/cards are still feeding this (deterministic,
      cross-tab-shared) session's queue. Only stop if the queue is empty —
      otherwise leave the session running and report how many ranges are
      still pending so the caller can no-op instead of stranding them.
    - `only_if_idle` absent/false (explicit, deliberate cancel): stop
      unconditionally AND delete the queue, so a deliberate cancel doesn't
      orphan whatever was still queued either.
    """
    body = request.get_json(silent=True) or {}
    snap_key = (body.get("snap_key") or "").strip()
    if not snap_key:
        return jsonify({"error": "snap_key required"}), 400
    only_if_idle = bool(body.get("only_if_idle", False))
    redis = _ctx.redis_client()
    user_id = _user_id()
    control_key = f"inline:control:{user_id}:{snap_key}"
    queue_key = f"inline:queue:{user_id}:{snap_key}"

    if only_if_idle:
        pending = redis.llen(queue_key)
        if pending > 0:
            return jsonify({"stopped": False, "pending": pending})
        redis.set(control_key, "stop", ex=60)
        return jsonify({"stopped": True, "pending": 0})

    cleared = _explicit_session_stop(redis, user_id, snap_key)
    return jsonify({"stopped": True, "cleared": cleared})


@bp.route("/dlc/project/inline-analysis/range", methods=["POST"])
def range_submit():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    snap_key = (body.get("snap_key") or "").strip()
    video_path = (body.get("video_path") or "").strip()
    if not snap_key or not video_path:
        return jsonify({"error": "snap_key and video_path required"}), 400
    p = Path(video_path)
    if not p.is_file():
        return jsonify({"error": f"video not found: {video_path}"}), 400
    if not _sec_check(p):
        return jsonify({"error": "video path is outside the data root"}), 403

    try:
        start_frame = int(body.get("start_frame", 0))
        n_frames    = int(body.get("n_frames", 0))
        batch_size  = int(body.get("batch_size", 8))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame, n_frames, batch_size must be ints"}), 400
    if n_frames <= 0 or n_frames > 10_000:
        return jsonify({"error": "n_frames must be in 1..10000"}), 400

    req_id = uuid.uuid4().hex
    payload = {
        "req_id":        req_id,
        "video_path":    str(p),
        "start_frame":   start_frame,
        "n_frames":      n_frames,
        "batch_size":    batch_size,
        "save_as_csv":   bool(body.get("save_as_csv", False)),
        "snapshot_path": body.get("snapshot_path", ""),
    }
    redis = _ctx.redis_client()
    redis.lpush(f"inline:queue:{_user_id()}:{snap_key}", json.dumps(payload))
    # Bump activity on the session hash so the worker's idle budget resets.
    sess_key = f"inline:session:{_user_id()}:{snap_key}"
    try:
        redis.hset(sess_key, "last_activity", str(time.time()))
    except Exception:
        pass
    return jsonify({"req_id": req_id}), 202


@bp.route("/dlc/project/inline-analysis/range/status", methods=["GET"])
def range_status():
    req_id = (request.args.get("req_id") or "").strip()
    if not req_id:
        return jsonify({"error": "req_id required"}), 400
    redis = _ctx.redis_client()
    key = f"inline:result:{req_id}"
    h = _hgetall(redis, key)
    if not h:
        return jsonify({"status": "pending"})
    return jsonify({
        "status":     h.get("status", "pending"),
        "n_analyzed": int(h.get("n_analyzed") or 0),
        "n_skipped":  int(h.get("n_skipped") or 0),
        "error":      h.get("error", ""),
        "scorer":     h.get("scorer", ""),
    })


@bp.route("/dlc/project/inline-analysis/video-info", methods=["GET"])
def video_info():
    raw = (request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "path required"}), 400
    p = Path(raw)
    if not p.is_file():
        return jsonify({"error": "not a file"}), 404
    if not _sec_check(p):
        return jsonify({"error": "video path is outside the data root"}), 403
    info = _probe_video(p)
    # Cheap "has_h5_at_snapshot" probe — looks for any sibling .h5 with the video stem.
    sibling_h5s = list(p.parent.glob(p.stem + "*.h5"))
    info["has_h5_at_snapshot"] = bool(sibling_h5s)
    return jsonify(info)


@bp.route("/dlc/project/analysis-file/status", methods=["GET"])
def analysis_file_status():
    raw = (request.args.get("video_path") or "").strip()
    if not raw:
        return jsonify({"error": "video_path required"}), 400
    if not _sec_check(Path(raw)):
        return jsonify({"error": "path not allowed"}), 403
    h5 = _canonical.canonical_h5_path(raw)
    csv = _canonical.canonical_csv_path(raw)
    return jsonify({
        "initialized": h5.exists(),
        "h5_path": str(h5),
        "csv_path": str(csv),
    })


@bp.route("/dlc/project/analysis-file/initialize", methods=["POST"])
def analysis_file_initialize():
    body = request.get_json(silent=True) or {}
    raw = (body.get("video_path") or "").strip()
    if not raw:
        return jsonify({"error": "video_path required"}), 400
    if not _sec_check(Path(raw)):
        return jsonify({"error": "path not allowed"}), 403

    h5 = _canonical.canonical_h5_path(raw)
    if h5.exists():
        return jsonify({"error": "already initialized", "h5_path": str(h5)}), 409

    project = _active_project()
    if not project or not project.get("config_path"):
        return jsonify({"error": "no active project"}), 400
    config_path = project["config_path"]
    bodyparts = _canonical.read_bodyparts(config_path)
    if not bodyparts:
        return jsonify({"error": "project has no bodyparts"}), 422
    scorer = _canonical.canonical_scorer(config_path)

    import cv2
    cap = cv2.VideoCapture(raw)
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if nframes <= 0:
        return jsonify({"error": "could not read video frame count"}), 422

    h5_path, csv_path = _canonical.write_empty(
        raw, scorer=scorer, bodyparts=bodyparts, nframes=nframes, save_as_csv=True)
    return jsonify({
        "h5_path": str(h5_path), "csv_path": str(csv_path), "nframes": nframes,
    }), 201


_UI_SETTING_KEYS = {
    "finalize_window", "clip_window", "postfix_tags", "status_tags", "note_tags",
    "pose3d_bg_color", "pose3d_view_prefs", "pinned_snapshot", "note_tag_colors",
    "reproj_params", "clip_window_reproj", "finalize_window_reproj",
    "note_tags_reproj", "postfix_tags_reproj", "status_tags_reproj",
    "pose3d_bg_color_reproj", "pose3d_view_prefs_reproj",
    "tracked_sort",
    "batch_tags", "batch_window", "batch_prefs",
}


@bp.route("/dlc/project/ui-setting", methods=["GET"])
def get_ui_setting():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    key = request.args.get("key", "")
    if key not in _UI_SETTING_KEYS:
        return jsonify({"error": "unknown key"}), 400
    config_path = project.get("config_path", "")
    if not config_path:
        return jsonify({"error": "Active project has no path."}), 400
    project_path = str(Path(config_path).parent)
    return jsonify({"value": _project_settings.get_setting(project_path, key)})


@bp.route("/dlc/project/ui-setting", methods=["POST"])
def set_ui_setting():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    key = body.get("key", "")
    if key not in _UI_SETTING_KEYS:
        return jsonify({"error": "unknown key"}), 400
    if "value" not in body:
        return jsonify({"error": "value required"}), 400
    value = body["value"]
    config_path = project.get("config_path", "")
    if not config_path:
        return jsonify({"error": "Active project has no path."}), 400
    project_path = str(Path(config_path).parent)
    _project_settings.set_setting(project_path, key, str(value))
    return jsonify({"ok": True})


@bp.route("/dlc/project/inline-analysis/unfinalize-range", methods=["POST"])
def unfinalize_range():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    video_path = (body.get("video_path") or "").strip()
    if not video_path:
        return jsonify({"error": "video_path required"}), 400
    vp = Path(video_path)
    if not vp.is_file():
        return jsonify({"error": "video_path not found"}), 400
    if not _sec_check(vp):
        return jsonify({"error": "path outside the data root"}), 403
    try:
        start_frame = int(body.get("start_frame", 0))
        n_frames    = int(body.get("n_frames", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame and n_frames must be ints"}), 400
    if start_frame < 0 or n_frames <= 0 or n_frames > 10_000:
        return jsonify({"error": "start_frame >= 0 and n_frames in 1..10000"}), 400
    try:
        n = _canonical.unfinalize_range(video_path, start_frame, n_frames)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500
    return jsonify({"n_frames_cleared": n})


@bp.route("/dlc/project/inline-analysis/finalize-range", methods=["POST"])
def finalize_range():
    project = _active_project()
    if not project or not project.get("config_path"):
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    video_path = (body.get("video_path") or "").strip()
    source_h5  = (body.get("source_h5") or "").strip()
    if not video_path or not source_h5:
        return jsonify({"error": "video_path and source_h5 required"}), 400
    vp, sp = Path(video_path), Path(source_h5)
    if not vp.is_file() or not sp.is_file():
        return jsonify({"error": "video_path or source_h5 not found"}), 400
    if not _sec_check(vp) or not _sec_check(sp):
        return jsonify({"error": "path outside the data root"}), 403
    try:
        start_frame = int(body.get("start_frame", 0))
        n_frames    = int(body.get("n_frames", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame and n_frames must be ints"}), 400
    if start_frame < 0 or n_frames <= 0 or n_frames > 10_000:
        return jsonify({"error": "start_frame >= 0 and n_frames in 1..10000"}), 400
    try:
        h5_path, csv_path, n_written = _finalize_range_to_canonical(
            video_path, source_h5, start_frame, n_frames, project["config_path"])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"h5_path": str(h5_path), "csv_path": str(csv_path),
                    "n_frames_written": n_written}), 200


# ── Triangulate keyframe range (incremental anipose 3D) ────────────────────

@bp.route("/dlc/project/triangulate/range", methods=["POST"])
def triangulate_range_submit():
    """Enqueue a range-triangulate Celery task for the current keyframe window
    of the selected stereo pair. Body: {cam0_video, start_frame, n_frames}."""
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    body = request.get_json(silent=True) or {}
    cam0_video = (body.get("cam0_video") or "").strip()
    if not cam0_video:
        return jsonify({"error": "cam0_video required"}), 400
    p = Path(cam0_video)
    if not p.is_file():
        return jsonify({"error": f"cam0_video not found: {cam0_video}"}), 400
    if not _sec_check(p):
        return jsonify({"error": "cam0_video path is outside the data root"}), 403
    try:
        start_frame = int(body.get("start_frame", 0))
        n_frames    = int(body.get("n_frames", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame and n_frames must be ints"}), 400
    if start_frame < 0:
        return jsonify({"error": "start_frame must be >= 0"}), 400
    if n_frames < 1 or n_frames > 10_000:
        return jsonify({"error": "n_frames must be in 1..10000"}), 400

    task = _enqueue_triangulate_range(str(p), start_frame, n_frames)
    return jsonify({"req_id": task.id}), 202


@bp.route("/dlc/project/triangulate/range/status", methods=["GET"])
def triangulate_range_status():
    """Map the Celery task state → the API contract
    {state, progress, stage, error, result}."""
    req_id = (request.args.get("req_id") or "").strip()
    if not req_id:
        return jsonify({"error": "req_id required"}), 400
    # Read the task meta directly from the backend (no AsyncResult/pubsub — see
    # _triangulate_task_meta). meta = {status, result, traceback, …}; `result`
    # holds the custom PROGRESS dict, the SUCCESS return value, or the exception.
    meta = _triangulate_task_meta(req_id) or {}
    state = meta.get("status")
    info = meta.get("result")
    out = {"state": state, "progress": 0, "stage": "", "error": None, "result": None}
    if state == "PENDING":
        out["stage"] = "Queued — waiting for a worker…"
    elif state == "STARTED":
        out["progress"] = 5
        out["stage"] = "Worker picked up the task…"
    elif state == "PROGRESS":
        m = info if isinstance(info, dict) else {}
        out["progress"] = int(m.get("progress", 0) or 0)
        out["stage"] = m.get("stage", "Processing…")
    elif state == "SUCCESS":
        out["progress"] = 100
        out["stage"] = "Complete"
        out["result"] = info
    elif state == "FAILURE":
        out["stage"] = "Failed"
        out["error"] = str(info)
    else:
        out["stage"] = state
    return jsonify(out)


@bp.route("/dlc/project/triangulate/batch", methods=["POST"])
def triangulate_batch():
    """Register/update ONE aggregate triangulate-batch job in the main Jobs
    surface. The frontend owns a `batch_id` and drives this route with
    start/progress/done actions. Reuses the `dlc_analyze_jobs` category so
    `/dlc/training/jobs` surfaces it with no monitor-route change — a synthetic
    batch_id reconciles as PENDING (a live state) so it stays "running" until
    `done` sets "complete". Best-effort; 503 if redis is down.
    """
    body = request.get_json(silent=True) or {}
    batch_id = (body.get("batch_id") or "").strip()
    if not batch_id:
        return jsonify({"error": "batch_id required"}), 400
    action = (body.get("action") or "").strip()
    if action not in ("start", "progress", "done"):
        return jsonify({"error": "action must be one of start/progress/done"}), 400

    video = body.get("video")
    if video and not _sec_check(Path(video)):
        return jsonify({"error": "video path is outside the data root"}), 403

    redis = _ctx.redis_client()
    if redis is None:
        return jsonify({"error": "redis unavailable"}), 503

    job_key = "dlc_analyze_job:" + batch_id
    jobs_zset = "dlc_analyze_jobs"

    if action == "start":
        try:
            total = int(body.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        now = time.time()
        redis.hset(job_key, mapping={
            "task_id":     batch_id,
            "operation":   "triangulate",
            "project":     (Path(video).parent.name if video else ""),
            "target_path": video or "",
            "started_at":  str(now),
            "updated_at":  str(now),   # heartbeat — see monitoring._reconcile stale check
            "status":      "running",
            "total":       int(total),
            "done":        0,
            "stage":       f"0/{total}",
        })
        redis.expire(job_key, 7200)
        redis.zadd(jobs_zset, {batch_id: now})

    elif action == "progress":
        # Only update an existing hash; ignore if the batch is unknown/expired.
        if redis.exists(job_key):
            mapping = {"updated_at": str(time.time())}   # heartbeat
            if body.get("done") is not None:
                mapping["done"] = body.get("done")
            if body.get("skipped") is not None:
                mapping["skipped"] = body.get("skipped")
            if body.get("stage") is not None:
                mapping["stage"] = body.get("stage")
            redis.hset(job_key, mapping=mapping)

    else:  # action == "done"
        mapping = {"status": "complete"}
        stage = body.get("error") or body.get("stage")
        if stage is not None:
            mapping["stage"] = stage
        redis.hset(job_key, mapping=mapping)
        redis.expire(job_key, 3600)

    return jsonify({"ok": True}), 200


@bp.route("/dlc/project/triangulate/coverage", methods=["GET"])
def triangulate_coverage():
    """Return the 3D coverage bar buckets for the pair derived from cam0_video.
    Absent/empty canonical → all-zero buckets (not an error)."""
    cam0_video = (request.args.get("cam0_video") or "").strip()
    if not cam0_video:
        return jsonify({"error": "cam0_video required"}), 400
    if not _sec_check(Path(cam0_video)):
        return jsonify({"error": "cam0_video path is outside the data root"}), 403
    try:
        buckets = int(request.args.get("buckets", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "buckets must be an int"}), 400
    if buckets < 1 or buckets > 10_000:
        return jsonify({"error": "buckets must be in 1..10000"}), 400
    # Full video frame count (from the seek-bar's _frameCount) so the 3D bar
    # scales to the whole video, not the last-triangulated frame.
    try:
        total_frames = int(request.args.get("nframes", 0)) or None
    except (TypeError, ValueError):
        total_frames = None

    session_dir = Path(cam0_video).parent
    pair_name = _canonical_3d.pair_name_from_cam0(cam0_video)
    return jsonify({
        "buckets":  _canonical_3d.read_3d_coverage(
            session_dir, pair_name, buckets, total_frames),
        "nframes":  total_frames or _canonical_3d.canonical_3d_nframes(
            session_dir, pair_name),
    })


@bp.route("/dlc/project/triangulate/poses-3d", methods=["GET"])
def triangulate_poses_3d():
    """Return the populated 3D poses + derived skeleton for the pair derived
    from cam0_video, per the frozen viewer contract. ``source=filtered``
    (default) reads pose-3d-filtered/; ``source=raw`` reads pose-3d/. Absent /
    empty canonical → empty frames with bounds=null (200, not an error)."""
    cam0_video = (request.args.get("cam0_video") or "").strip()
    if not cam0_video:
        return jsonify({"error": "cam0_video required"}), 400
    if not _sec_check(Path(cam0_video)):
        return jsonify({"error": "cam0_video path is outside the data root"}), 403
    source = (request.args.get("source") or "filtered").strip().lower()
    if source not in ("filtered", "raw"):
        source = "filtered"

    session_dir = Path(cam0_video).parent
    pair_name = _canonical_3d.pair_name_from_cam0(cam0_video)
    return jsonify(_canonical_3d.read_poses_3d(session_dir, pair_name, source))


def _skeleton_constraints_suggestion(cam0_video) -> list:
    """Best-effort skeleton pairs (Wrist→MCP-k→PIP-k→DIP-k finger chains) derived
    from the analyzed CSV's bodypart names — powers the params editor's 'Fill from
    skeleton' button. Empty on any problem (missing CSV, odd header, etc.)."""
    try:
        import csv as _csv
        from itertools import islice
        from dlc.canonical_3d import derive_skeleton
        p = Path(cam0_video)
        # Prefer the fresh source next to the video; fall back to the pose-2d copy.
        for cand in (p.with_name(p.stem + "_analyzed.csv"),
                     p.parent / "pose-2d" / f"{p.stem}_analyzed.csv"):
            if not cand.is_file():
                continue
            with open(cand, newline="") as fh:
                rows = list(islice(_csv.reader(fh), 3))   # DLC header rows only
            bp_row = rows[1][1:] if len(rows) >= 2 else []   # row1 = bodyparts
            bodyparts = []
            for name in bp_row:
                name = (name or "").strip()
                if name and name not in bodyparts:
                    bodyparts.append(name)
            skel = derive_skeleton(bodyparts)
            if skel:
                return skel
        return []
    except Exception:
        return []


@bp.route("/dlc/project/triangulate/config", methods=["GET"])
def triangulate_config_get():
    """Return the editable anipose params for the config.toml governing the
    session of ``cam0_video``. config path = parent(session)/config.toml.
    Also includes ``constraints_suggestion`` (skeleton pairs from the bodypart
    names) for the params editor's 'Fill from skeleton' button."""
    cam0_video = (request.args.get("cam0_video") or "").strip()
    if not cam0_video:
        return jsonify({"error": "cam0_video required"}), 400
    p = Path(cam0_video)
    if not _sec_check(p):
        return jsonify({"error": "cam0_video path is outside the data root"}), 403
    config_path = p.parent.parent / "config.toml"
    if not config_path.is_file():
        return jsonify({"error": f"config.toml not found at {config_path}"}), 400
    out = _anipose_config.read_params(config_path)
    out["constraints_suggestion"] = _skeleton_constraints_suggestion(cam0_video)
    return jsonify(out), 200


@bp.route("/dlc/project/triangulate/config", methods=["POST"])
def triangulate_config_post():
    """Persist edited anipose params (targeted, formatting-preserving writes)
    and echo the re-read result. Body: {cam0_video, params}."""
    body = request.get_json(silent=True) or {}
    cam0_video = (body.get("cam0_video") or "").strip()
    if not cam0_video:
        return jsonify({"error": "cam0_video required"}), 400
    p = Path(cam0_video)
    if not _sec_check(p):
        return jsonify({"error": "cam0_video path is outside the data root"}), 403
    config_path = p.parent.parent / "config.toml"
    if not config_path.is_file():
        return jsonify({"error": f"config.toml not found at {config_path}"}), 400
    params = body.get("params") or {}
    errs = _anipose_config.validate_params(params)
    if errs:
        return jsonify({"error": "; ".join(errs)}), 400
    _anipose_config.write_params(config_path, params)
    return jsonify({"ok": True,
                    "params": _anipose_config.read_params(config_path)}), 200


@bp.route("/dlc/project/triangulate/refilter", methods=["POST"])
def triangulate_refilter():
    """Re-run the anipose 3D median filter over the populated raw-3d span with a
    new medfilt/offset_threshold, rewriting the filtered canonical. Runs
    synchronously in flask (scipy present); NOT dispatched to Celery.

    Body: {cam0_video, medfilt, offset_threshold}."""
    body = request.get_json(silent=True) or {}
    cam0_video = (body.get("cam0_video") or "").strip()
    if not cam0_video:
        return jsonify({"error": "cam0_video required"}), 400
    if not _sec_check(Path(cam0_video)):
        return jsonify({"error": "cam0_video path is outside the data root"}), 403

    try:
        medfilt = int(body.get("medfilt"))
    except (TypeError, ValueError):
        return jsonify({"error": "medfilt must be an odd int in 1..199"}), 400
    if medfilt < 1 or medfilt > 199 or medfilt % 2 == 0:
        return jsonify({"error": "medfilt must be an odd int in 1..199"}), 400

    try:
        offset_threshold = float(body.get("offset_threshold"))
    except (TypeError, ValueError):
        return jsonify({"error": "offset_threshold must be a number >= 0"}), 400
    if not (offset_threshold >= 0):
        return jsonify({"error": "offset_threshold must be a number >= 0"}), 400

    session_dir = Path(cam0_video).parent
    pair_name = _canonical_3d.pair_name_from_cam0(cam0_video)
    span = _canonical_3d.populated_span(session_dir, pair_name, "raw")
    if span is None:
        return jsonify({
            "error": "no triangulated 3D yet — triangulate a range first"}), 400
    start, n = span
    _canonical_3d.medfilt_range_and_splice(
        session_dir, pair_name, start, n,
        {"filter3d": {"medfilt": medfilt, "offset_threshold": offset_threshold}})
    return jsonify({"ok": True, "start": start, "n": n,
                    "medfilt": medfilt, "offset_threshold": offset_threshold}), 200


# ── Candidate-peak emission (additive second GPU pass) ──────────────────────
#
# Fired by the "3D Inline Analysis - Reprojection" card after analysis
# completes, to save top-K heatmap peaks alongside the pose h5. Deliberately
# separate from /range — a failure here must never roll back or touch
# analysis results, which is why it takes explicit h5_paths rather than
# deriving them (the scorer string is only known inside the worker, from the
# loaded model; see tasks.dlc_emit_peaks). See
# deeplabcut-webapp-docker-supports/dlc-3D/docs/superpowers/specs/
#     2026-07-30-heatmap-peak-screen-design.md

_PEAKS_FRAME_CAP = 50_000


def _peaks_int(body, name, default, lo, hi):
    """Parse a bounded int argument, raising ValueError (-> 400) rather than
    letting a string or dict reach int() and surface as a 500."""
    v = body.get(name, default)
    if v is None:
        return int(default)
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError("{} must be an integer, got {!r}".format(name, v))
    try:
        iv = int(v)
    except (TypeError, ValueError):
        raise ValueError("{} must be an integer, got {!r}".format(name, v))
    if not (lo <= iv <= hi):
        raise ValueError("{} must be in {}..{}, got {}".format(name, lo, hi, iv))
    return iv


def _frames_from_ranges(ranges):
    """Flatten [{start, n}, ...] into a sorted, deduped frame list."""
    out = set()
    for r in ranges:
        start, n = int(r["start"]), int(r["n"])
        if n <= 0:
            raise ValueError("each range needs n >= 1")
        out.update(range(start, start + n))
    return sorted(out)


def _dispatch_emit_peaks(**kw):
    """Seam for tests; production sends the task to the worker's GPU queue.

    Dispatched by name via send_task, same as _enqueue_triangulate_range
    above — the Flask container cannot import the worker task object."""
    return _celery_send_task("tasks.dlc_emit_peaks", kwargs=kw, queue="pytorch")


@bp.route("/dlc/project/inline-analysis/peaks", methods=["POST"])
def peaks_submit():
    """Queue a candidate-peak pass over frames already analysed.

    Additive: this never runs as part of an analysis and never touches the
    pose h5. A failure here leaves the analysis results exactly as they were.

    Two routes, chosen by whether the caller sends `snap_key`:

    - with `snap_key`: appended to that warm session's queue and run by its
      worker after every range already queued. The worker knows the scorer,
      so h5_paths is neither needed nor accepted. Preferred — the caller can
      queue this before any range has finished and then close the tab.
    - without: dispatched as a standalone tasks.dlc_emit_peaks. The scorer
      can't be derived in this request context, so h5_paths is REQUIRED,
      parallel to video_paths (same length, same order) — the caller has
      `scorer` from /range/status and builds video_stem + scorer + ".h5".
    """
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400

    body = request.get_json(silent=True) or {}
    video_paths = body.get("video_paths") or []
    ranges = body.get("ranges") or []
    snapshot_path = (body.get("snapshot_path") or "").strip()
    if not video_paths or not snapshot_path:
        return jsonify({"error": "video_paths and snapshot_path required"}), 400
    if not ranges:
        return jsonify({"error": "at least one range required"}), 400

    resolved_videos = []
    for raw in video_paths:
        p = Path(str(raw))
        if not p.is_file():
            return jsonify({"error": f"video not found: {raw}"}), 400
        if not _sec_check(p):
            return jsonify({"error": "video path is outside the data root"}), 403
        resolved_videos.append(p)

    try:
        frames = _frames_from_ranges(ranges)
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": f"bad ranges: {exc}"}), 400
    if len(frames) > _PEAKS_FRAME_CAP:
        return jsonify({
            "error": f"{len(frames)} frames exceeds the cap of {_PEAKS_FRAME_CAP}"
        }), 400

    # `snap_key` selects the in-session route: the pass is appended to that
    # warm session's own queue and run by its worker, which already knows the
    # scorer and so derives h5_paths itself. Without it we fall back to the
    # standalone Celery task, which cannot, and therefore still needs
    # h5_paths from the caller.
    snap_key = (body.get("snap_key") or "").strip()
    resolved_h5s = []
    if not snap_key:
        h5_paths = body.get("h5_paths") or []
        if not h5_paths or len(h5_paths) != len(video_paths):
            return jsonify({
                "error": "h5_paths required, parallel to video_paths"
            }), 400
        for raw in h5_paths:
            p = Path(str(raw))
            if not _sec_check(p):
                return jsonify({"error": "h5 path is outside the data root"}), 403
            resolved_h5s.append(p)

    # /dlc/project/snapshots returns project-relative paths (rel_path).
    # Resolve against the active project root before validating — see the
    # identical comment in session_start above.
    project_root = Path(project["config_path"]).parent
    snap_abs = (project_root / snapshot_path).resolve()
    if not snap_abs.is_file():
        return jsonify({
            "error": f"snapshot not found: {snapshot_path}"
        }), 404
    if not _sec_check(snap_abs):
        return jsonify({"error": "snapshot path is outside the data root"}), 403
    model_dir = str(snap_abs.parent.parent)
    snapshot_name = snap_abs.name

    # Parse the bounded ints BEFORE minting a req_id, so a bad value is a 400
    # with nothing dispatched rather than a 500 from int() blowing up.
    try:
        k = _peaks_int(body, "k", 5, 1, 50)
        min_distance = _peaks_int(body, "min_distance", 3, 1, 64)
        batch_size = _peaks_int(body, "batch_size", 1, 1, 64)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    req_id = uuid.uuid4().hex
    common = dict(
        req_id=req_id,
        video_paths=[str(p) for p in resolved_videos],
        frames=frames,
        model_dir=model_dir,
        snapshot_name=snapshot_name,
        k=k,
        min_distance=min_distance,
        batch_size=batch_size,
    )
    if snap_key:
        # RPUSH, not LPUSH: the session queue is drained from the head, so the
        # tail is the one position guaranteed to come after every range the
        # caller just submitted. This is what lets the card fire the peak pass
        # up front and then stop caring whether the browser is still open.
        redis = _ctx.redis_client()
        queue_key = f"inline:queue:{_user_id()}:{snap_key}"
        redis.rpush(queue_key, json.dumps({"kind": "peaks", **common}))
        return jsonify({"req_id": req_id, "queued_in_session": True}), 202

    _dispatch_emit_peaks(h5_paths=[str(p) for p in resolved_h5s], **common)
    return jsonify({"req_id": req_id}), 202


@bp.route("/dlc/project/inline-analysis/peaks/status", methods=["GET"])
def peaks_status():
    req_id = (request.args.get("req_id") or "").strip()
    if not req_id:
        return jsonify({"error": "req_id required"}), 400
    h = _hgetall(_ctx.redis_client(), f"inline:result:{req_id}")
    if not h:
        return jsonify({"status": "pending", "n_frames": 0, "error": ""})
    return jsonify({
        "status":   h.get("status", "pending"),
        # n_analyzed is _publish_result's generic counter field, reused here
        # to carry the peak-frame count (tasks.py has no n_frames field —
        # see _emit_peaks_inner).
        "n_frames": int(h.get("n_analyzed") or 0),
        "error":    h.get("error", ""),
    })
