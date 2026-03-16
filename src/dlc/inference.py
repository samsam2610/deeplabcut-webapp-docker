"""
DLC Inference/Analyze Blueprint.

Routes:
  POST /dlc/project/analyze
  POST /dlc/project/analyze/stop
  GET /dlc/project/labeled-content
"""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, session as flask_session
from celery.result import AsyncResult
from . import ctx as _ctx
from dlc.utils import (
    _get_engine_queue,
    _dlc_project_security_check,
    _FS_LS_VIDEO_EXTS,
)

bp = Blueprint("dlc_inference", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _natural_keys(text: str) -> list:
    """Sort helper — splits text into int and str chunks for natural ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


@bp.route("/dlc/project/analyze", methods=["POST"])
def dlc_project_analyze():
    """
    Dispatch a Celery task to run DLC analysis on a file or folder.
    Body (JSON) fields:
      target_path     : absolute path to a video file, image file, or directory
      shuffle         : int  (default 1)
      trainingsetindex: int  (default 0)
      gputouse        : int  (optional)
      save_as_csv     : bool (default false)
      create_labeled  : bool (default false)
      snapshot_index  : int  (optional; None = latest from config)
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body        = request.get_json(force=True) or {}
    target_path = (body.get("target_path") or "").strip()
    if not target_path:
        return jsonify({"error": "target_path is required."}), 400
    if not Path(target_path).exists():
        return jsonify({"error": f"Target not found: {target_path}"}), 400

    def _int_or_none(key):
        v = body.get(key)
        try:
            return int(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    params = {
        "shuffle":          _int_or_none("shuffle") or 1,
        "trainingsetindex": _int_or_none("trainingsetindex") if _int_or_none("trainingsetindex") is not None else 0,
        "gputouse":         _int_or_none("gputouse"),
        "save_as_csv":      bool(body.get("save_as_csv", False)),
        "create_labeled":   bool(body.get("create_labeled", False)),
        "snapshot_path":    (body.get("snapshot_path") or "").strip() or None,
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_analyze",
        kwargs={"config_path": config_path, "target_path": target_path, "params": params},
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "analyze"}), 202


@bp.route("/dlc/project/analyze/stop", methods=["POST"])
def dlc_project_analyze_stop():
    """
    Request a stop of a running dlc_analyze task.
    Body (JSON): { "task_id": "<celery task id>" }
    """
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id is required."}), 400

    _ctx.redis_client().setex("dlc_analyze_stop:" + task_id, 120, "1")
    _ctx.celery().control.revoke(task_id, terminate=False)
    try:
        AsyncResult(task_id, app=_ctx.celery()).forget()
    except Exception:
        pass

    # Optimistically mark as stopped so the monitor updates immediately
    _ctx.redis_client().zrem("dlc_analyze_jobs", task_id)
    _ctx.redis_client().hset("dlc_analyze_job:" + task_id, "status", "stopped")
    _ctx.redis_client().expire("dlc_analyze_job:" + task_id, 3600)

    return jsonify({"status": "stop_requested", "task_id": task_id}), 200


@bp.route("/dlc/project/labeled-content")
def dlc_labeled_content():
    """List labeled videos and frame folders produced by create_labeled_video."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    # Labeled videos: any video file whose stem contains "_labeled" in the videos/ dir
    videos = []
    videos_dir = project_path / "videos"
    if videos_dir.is_dir():
        for f in sorted(videos_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in _FS_LS_VIDEO_EXTS and "_labeled" in f.stem:
                videos.append({"name": f.name, "size": f.stat().st_size})

    # Labeled frame folders: labeled-data/<stem>/ dirs that contain *_labeled.png files
    frame_folders = []
    labeled_base = project_path / "labeled-data"
    if labeled_base.is_dir():
        for stem_dir in sorted(labeled_base.iterdir(), key=lambda p: _natural_keys(p.name)):
            if not stem_dir.is_dir():
                continue
            labeled_frames = sorted(
                [f.name for f in stem_dir.iterdir()
                 if f.is_file() and f.suffix.lower() == ".png" and "_labeled" in f.stem],
                key=_natural_keys,
            )
            if labeled_frames:
                frame_folders.append({
                    "stem": stem_dir.name,
                    "frames": labeled_frames,
                    "frame_count": len(labeled_frames),
                })

    return jsonify({
        "project_path": str(project_path),
        "videos": videos,
        "frame_folders": frame_folders,
    })
