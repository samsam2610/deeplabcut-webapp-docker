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
    Dispatch one Celery task per target path for DLC analysis.

    Body (JSON) fields:
      target_paths    : list of absolute paths (files or directories)  ← preferred
      target_path     : single absolute path (backward-compat alias)
      shuffle         : int  (default 1)
      trainingsetindex: int  (default 0)
      gputouse        : int  (optional)
      save_as_csv     : bool (default false)
      create_labeled  : bool (default false)
      snapshot_path   : str  (optional; None = latest from config)

    Returns:
      { "task_ids": [...], "operation": "analyze" }   (one id per path)
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body = request.get_json(force=True) or {}

    # Resolve target_paths: new array field takes priority; fall back to legacy scalar.
    raw_paths = body.get("target_paths")
    if raw_paths is None:
        # Legacy single-path support
        single = (body.get("target_path") or "").strip()
        raw_paths = [single] if single else []

    target_paths = [p.strip() for p in raw_paths if isinstance(p, str) and p.strip()]
    if not target_paths:
        return jsonify({"error": "target_paths must be a non-empty list of paths."}), 400

    # Validate each path exists
    for tp in target_paths:
        if not Path(tp).exists():
            return jsonify({"error": f"Target not found: {tp}"}), 400

    def _int_or_none(key):
        v = body.get(key)
        try:
            return int(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    def _float_or_none(key):
        v = body.get(key)
        try:
            return float(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    clv_destfolder = (body.get("destfolder") or "").strip() or None
    if clv_destfolder and not Path(clv_destfolder).is_dir():
        return jsonify({"error": f"destfolder not found: {clv_destfolder}"}), 400

    params = {
        "shuffle":           _int_or_none("shuffle") or 1,
        "trainingsetindex":  _int_or_none("trainingsetindex") if _int_or_none("trainingsetindex") is not None else 0,
        "gputouse":          _int_or_none("gputouse"),
        "batch_size":        _int_or_none("batch_size"),
        "save_as_csv":       bool(body.get("save_as_csv", False)),
        "create_labeled":    bool(body.get("create_labeled", False)),
        "snapshot_path":     (body.get("snapshot_path") or "").strip() or None,
        "destfolder":        clv_destfolder,
        # labeled video params (used when create_labeled=True)
        "clv_pcutoff":       _float_or_none("pcutoff"),
        "clv_dotsize":       _int_or_none("dotsize") or 8,
        "clv_colormap":      (body.get("colormap") or "rainbow").strip(),
        "clv_modelprefix":   (body.get("modelprefix") or "").strip(),
        "clv_filtered":      bool(body.get("filtered", False)),
        "clv_draw_skeleton": bool(body.get("draw_skeleton", False)),
        "clv_overwrite":     bool(body.get("overwrite", False)),
    }

    task_ids = []
    for target_path in target_paths:
        task = _ctx.celery().send_task(
            "tasks.dlc_analyze",
            kwargs={"config_path": config_path, "target_path": target_path, "params": params},
            queue=_get_engine_queue(engine),
        )
        task_ids.append(task.id)

    return jsonify({"task_ids": task_ids, "task_id": task_ids[0], "operation": "analyze"}), 202


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


@bp.route("/dlc/project/create-labeled-video", methods=["POST"])
def dlc_create_labeled_video():
    """
    Dispatch a Celery task to run deeplabcut.create_labeled_video on an
    already-analyzed video.  Checks that at least one .h5 analysis file
    exists next to the video before dispatching.

    Body (JSON) fields:
      video_path      : absolute path to the video file
      shuffle         : int (default 1)
      trainingsetindex: int (default 0)
      snapshot_index  : int (optional)
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body       = request.get_json(force=True) or {}
    video_path = (body.get("video_path") or "").strip()
    if not video_path:
        return jsonify({"error": "video_path is required."}), 400
    video_p = Path(video_path)
    if not video_p.is_file():
        return jsonify({"error": f"Video not found: {video_path}"}), 400

    # Check analyzed h5 exists in same folder
    h5_files = list(video_p.parent.glob("*.h5"))
    if not h5_files:
        return jsonify({"error": "No analysis data (.h5) found next to the video. Run analysis first."}), 400

    def _int_or_none(key):
        v = body.get(key)
        try:
            return int(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    def _float_or_none(key):
        v = body.get(key)
        try:
            return float(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    destfolder = (body.get("destfolder") or "").strip() or None
    if destfolder and not Path(destfolder).is_dir():
        return jsonify({"error": f"destfolder not found: {destfolder}"}), 400

    params = {
        "shuffle":          _int_or_none("shuffle") or 1,
        "trainingsetindex": _int_or_none("trainingsetindex") if _int_or_none("trainingsetindex") is not None else 0,
        "snapshot_index":   _int_or_none("snapshot_index"),
        "pcutoff":          _float_or_none("pcutoff"),
        "dotsize":          _int_or_none("dotsize") or 8,
        "colormap":         (body.get("colormap") or "rainbow").strip(),
        "modelprefix":      (body.get("modelprefix") or "").strip(),
        "filtered":         bool(body.get("filtered", False)),
        "draw_skeleton":    bool(body.get("draw_skeleton", False)),
        "overwrite":        bool(body.get("overwrite", False)),
        "destfolder":       destfolder,
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_create_labeled_video",
        kwargs={"config_path": config_path, "video_path": video_path, "params": params},
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "create_labeled_video"}), 202


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
