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
