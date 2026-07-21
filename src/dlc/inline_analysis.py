"""DLC Inline Analysis blueprint.

Routes (all under /dlc/project/inline-analysis/):
  POST /session/start
  GET  /session/status   (read-only; does not bump activity)
  POST /session/stop
  POST /range            (bumps activity)
  GET  /range/status
  GET  /video-info

Activity (idle TTL) is bumped ONLY on /range submit. The worker
times out after `ttl_seconds` of no range submission, regardless
of whether the card is open. No client-side heartbeat — that's
the Jobs-page pattern and isn't needed here.

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
    if existing.get("status") in {"warming", "ready"}:
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
    out = {
        "status": h.get("status", "unknown"),
        "idle_remaining_s": idle_remaining,
    }
    if h.get("last_error"):
        out["last_error"] = h["last_error"]
    return jsonify(out)


@bp.route("/dlc/project/inline-analysis/session/stop", methods=["POST"])
def session_stop():
    body = request.get_json(silent=True) or {}
    snap_key = (body.get("snap_key") or "").strip()
    if not snap_key:
        return jsonify({"error": "snap_key required"}), 400
    redis = _ctx.redis_client()
    redis.set(f"inline:control:{_user_id()}:{snap_key}", "stop", ex=60)
    return ("", 204)


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


_UI_SETTING_KEYS = {"finalize_window", "clip_window", "postfix_tags", "status_tags", "note_tags"}


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
    res = _triangulate_async_result(req_id)
    state = res.state
    out = {"state": state, "progress": 0, "stage": "", "error": None, "result": None}
    if state == "PENDING":
        out["stage"] = "Queued — waiting for a worker…"
    elif state == "STARTED":
        out["progress"] = 5
        out["stage"] = "Worker picked up the task…"
    elif state == "PROGRESS":
        meta = res.info if isinstance(res.info, dict) else {}
        out["progress"] = int(meta.get("progress", 0) or 0)
        out["stage"] = meta.get("stage", "Processing…")
    elif state == "SUCCESS":
        out["progress"] = 100
        out["stage"] = "Complete"
        out["result"] = res.result
    elif state == "FAILURE":
        out["stage"] = "Failed"
        out["error"] = str(res.info)
    else:
        out["stage"] = state
    return jsonify(out)


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
