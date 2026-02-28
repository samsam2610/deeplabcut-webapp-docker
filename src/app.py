"""
Flask API — Anipose / DLC Processing Gateway
Handles multi-file uploads and dispatches long-running tasks to Celery.
"""

import csv
import io
import os
import re
import uuid
import json
import shutil
import zipfile
from pathlib import Path
from datetime import datetime, timezone

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

import redis as _redis
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from celery import Celery
from celery.result import AsyncResult
from werkzeug.utils import secure_filename

# ── Configuration ─────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

USER_DATA_DIR = Path(os.environ.get("USER_DATA_DIR", "/user-data"))

ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}
ALLOWED_CONFIG_EXT = {".toml"}
ALLOWED_YAML_EXT   = {".yaml", ".yml"}

app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates",
)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB max upload

# ── Redis (direct client for session storage) ─────────────────────
_REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
_redis_client = _redis.Redis.from_url(_REDIS_URL, decode_responses=True)
_SESSION_KEY     = "webapp:session"
_DLC_PROJECT_KEY = "webapp:dlc_project"

# Standard DLC project pipeline folders
DLC_PIPELINE_FOLDERS = [
    ("Models",             "dlc-models"),
    ("Labeled Data",       "labeled-data"),
    ("Training Datasets",  "training-datasets"),
    ("Videos",             "videos"),
]

# ── Celery (client-side only — worker is in tasks.py) ─────────────
celery = Celery(
    "tasks",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)
celery.conf.update(
    task_track_started=True,
    result_expires=86400,  # 24 h
)


# ── Helpers ───────────────────────────────────────────────────────
def _valid_ext(filename: str, allowed: set) -> bool:
    return Path(filename).suffix.lower() in allowed


def _resolve_project_dir(project_id: str, root: str = "") -> Path:
    """
    Return the resolved project directory.
    If root is given, resolves to Path(root)/project_id.
    Otherwise defaults to DATA_DIR/project_id.
    Raises ValueError on path-traversal attempts.
    """
    base = Path(root) if root else DATA_DIR
    project_dir = (base / project_id).resolve()
    if not project_dir.is_relative_to(base.resolve()):
        raise ValueError("Invalid project path.")
    return project_dir


# ── Global error handler — always return JSON, never HTML ─────────
@app.errorhandler(Exception)
def handle_exception(exc):
    """Catch-all so Flask never returns an HTML traceback to the client."""
    import traceback as _tb
    app.logger.error("Unhandled exception: %s", _tb.format_exc())
    return jsonify({"error": str(exc)}), 500


# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """
    Expects multipart/form-data with:
      - config    : a single .toml file
      - videos[]  : one or more video files
      - task_type : 'anipose' (default) | 'deeplabcut'
    Organises files into the Anipose-expected folder layout, then dispatches
    a Celery task and returns the task id immediately.
    """
    # ── Validate inputs ───────────────────────────────────────────
    config_file = request.files.get("config")
    video_files = request.files.getlist("videos[]")
    task_type = request.form.get("task_type", "anipose").lower()

    if not config_file or not config_file.filename:
        return jsonify({"error": "A config.toml file is required."}), 400
    if not _valid_ext(config_file.filename, ALLOWED_CONFIG_EXT):
        return jsonify({"error": "Config must be a .toml file."}), 400
    if not video_files or not video_files[0].filename:
        return jsonify({"error": "At least one video file is required."}), 400

    for vf in video_files:
        if not _valid_ext(vf.filename, ALLOWED_VIDEO_EXT):
            return jsonify({
                "error": f"Unsupported video format: {vf.filename}"
            }), 400

    # ── Build project directory ───────────────────────────────────
    project_id = uuid.uuid4().hex[:12]
    project_dir = DATA_DIR / project_id
    videos_dir = project_dir / "videos-raw"
    videos_dir.mkdir(parents=True, exist_ok=True)

    # Save config.toml at project root
    config_dest = project_dir / "config.toml"
    config_file.save(str(config_dest))

    # Save each video into videos-raw/
    saved_videos = []
    for vf in video_files:
        safe_name = secure_filename(vf.filename)
        vf.save(str(videos_dir / safe_name))
        saved_videos.append(safe_name)

    # ── Dispatch Celery task ──────────────────────────────────────
    task = celery.send_task(
        "tasks.run_processing",
        kwargs={
            "project_id": project_id,
            "task_type": task_type,
        },
    )

    return jsonify({
        "task_id": task.id,
        "project_id": project_id,
        "task_type": task_type,
        "videos": saved_videos,
        "message": "Upload successful. Processing started.",
    }), 202


@app.route("/status/<task_id>")
def status(task_id: str):
    """
    Returns the current state of a Celery task.
    The worker updates `meta` with progress info (percent, stage, logs).
    """
    result = AsyncResult(task_id, app=celery)

    response = {
        "task_id": task_id,
        "state": result.state,
    }

    if result.state == "PENDING":
        response["progress"] = 0
        response["stage"] = "Queued — waiting for a worker…"
    elif result.state == "STARTED":
        response["progress"] = 5
        response["stage"] = "Worker picked up the task…"
    elif result.state == "PROGRESS":
        meta = result.info or {}
        response["progress"] = meta.get("progress", 0)
        response["stage"] = meta.get("stage", "Processing…")
        response["log"] = meta.get("log", "")
    elif result.state == "SUCCESS":
        response["progress"] = 100
        response["stage"] = "Complete"
        response["result"] = result.result
    elif result.state == "FAILURE":
        response["progress"] = 0
        response["stage"] = "Failed"
        response["error"] = str(result.info)
    else:
        response["progress"] = 0
        response["stage"] = result.state

    return jsonify(response)


@app.route("/admin/flush-task-cache", methods=["POST"])
def flush_task_cache():
    """
    Delete all Celery task-result keys from Redis and purge the broker queue.
    Useful for clearing stale/malformed task results that cause worker crashes.
    Does NOT affect the active session key.
    """
    task_meta_keys = list(_redis_client.scan_iter("celery-task-meta-*"))
    if task_meta_keys:
        _redis_client.delete(*task_meta_keys)
    _redis_client.delete("celery")          # purge the default broker queue
    return jsonify({"deleted": len(task_meta_keys)})


@app.route("/config")
def get_config():
    """
    Return client-facing configuration values.
    user_data_dir is null when the volume is not mounted / not a directory.
    """
    return jsonify({
        "data_dir":      str(DATA_DIR),
        "user_data_dir": str(USER_DATA_DIR) if USER_DATA_DIR.is_dir() else None,
    })


@app.route("/fs/list")
def fs_list():
    """
    List immediate subdirectories of a server-side path as candidate project folders.
    Query param: path=<absolute_path>
    """
    path_str = request.args.get("path", "").strip()
    if not path_str:
        return jsonify({"error": "path parameter is required."}), 400
    p = Path(path_str)
    if not p.is_absolute():
        return jsonify({"error": "path must be absolute."}), 400
    if not p.is_dir():
        return jsonify({"error": f"Directory not found: {path_str}"}), 404
    projects = sorted([d.name for d in p.iterdir() if d.is_dir()], reverse=True)
    return jsonify({"projects": projects, "path": path_str})


@app.route("/session", methods=["POST"])
def create_anipose_session():
    """
    Upload a config.toml to start a persistent anipose session.
    Saves the config to the shared volume and dispatches an init task
    on the worker that imports Anipose and verifies the file is readable.
    """
    config_file = request.files.get("config")
    if not config_file or not config_file.filename:
        return jsonify({"error": "A config.toml file is required."}), 400
    if not _valid_ext(config_file.filename, ALLOWED_CONFIG_EXT):
        return jsonify({"error": "Config must be a .toml file."}), 400

    try:
        # Tear down any existing session first
        _clear_session_data()

        # Persist config on the shared volume
        session_id = uuid.uuid4().hex[:12]
        session_dir = DATA_DIR / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        config_path = session_dir / "config.toml"
        config_file.save(str(config_path))

        # Dispatch init task to the GPU worker
        task = celery.send_task(
            "tasks.init_anipose_session",
            kwargs={"config_path": str(config_path)},
        )

        session_data = {
            "session_id": session_id,
            "config_path": str(config_path),
            "config_name": secure_filename(config_file.filename),
            "task_id": task.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "initializing",
        }
        _redis_client.set(_SESSION_KEY, json.dumps(session_data))
        return jsonify(session_data), 201

    except Exception as exc:
        app.logger.exception("Session creation failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/session", methods=["GET"])
def get_session():
    """Return current session state, refreshing status from the Celery backend."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"status": "none"}), 200

    session_data = json.loads(raw)

    # Sync status from the init task
    result = AsyncResult(session_data["task_id"], app=celery)
    if result.state == "SUCCESS":
        session_data["status"] = "ready"
        session_data["anipose_version"] = (result.result or {}).get("anipose_version", "")
    elif result.state == "FAILURE":
        session_data["status"] = "error"
        session_data["error"] = str(result.info)
    elif result.state in ("STARTED", "PROGRESS", "PENDING"):
        session_data["status"] = "initializing"

    # Persist the refreshed status
    _redis_client.set(_SESSION_KEY, json.dumps(session_data))
    return jsonify(session_data), 200


@app.route("/session", methods=["DELETE"])
def clear_session():
    """Kill the init task, remove stored config, and wipe the session."""
    _clear_session_data()
    return jsonify({"status": "cleared"}), 200


@app.route("/session/config", methods=["GET"])
def get_session_config():
    """Return the raw text of the active session's config.toml."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))
    if not config_path.is_file():
        return jsonify({"error": "Config file not found on disk."}), 404
    return jsonify({"content": config_path.read_text(), "config_path": str(config_path)})


@app.route("/session/config", methods=["POST"])
def save_session_config():
    """Overwrite the active session's config.toml with new content."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))
    if not config_path.parent.exists():
        return jsonify({"error": "Session directory no longer exists."}), 400
    body = request.get_json(force=True) or {}
    content = body.get("content", "")
    if not content.strip():
        return jsonify({"error": "Content cannot be empty."}), 400
    config_path.write_text(content)
    return jsonify({"status": "saved", "config_path": str(config_path)})


@app.route("/session/dlc-config", methods=["POST"])
def upload_dlc_config():
    """
    Upload a DeepLabCut config.yaml and attach its path to the active session.
    Stores the file alongside config.toml in the session directory.
    """
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400

    config_file = request.files.get("config")
    if not config_file or not config_file.filename:
        return jsonify({"error": "A config.yaml file is required."}), 400
    if not _valid_ext(config_file.filename, ALLOWED_YAML_EXT):
        return jsonify({"error": "DLC config must be a .yaml or .yml file."}), 400

    session_data = json.loads(raw)
    session_dir  = Path(session_data.get("config_path", "")).parent
    if not session_dir.is_dir():
        return jsonify({"error": "Session directory not found."}), 400

    dlc_config_path = session_dir / "config.yaml"
    config_file.save(str(dlc_config_path))

    session_data["dlc_config_path"] = str(dlc_config_path)
    session_data["dlc_config_name"] = secure_filename(config_file.filename)
    _redis_client.set(_SESSION_KEY, json.dumps(session_data))

    return jsonify({
        "dlc_config_path": str(dlc_config_path),
        "dlc_config_name": session_data["dlc_config_name"],
    }), 201


@app.route("/session/dlc-config", methods=["GET"])
def get_dlc_config():
    """Return the raw text of the active session's DLC config.yaml."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400

    session_data    = json.loads(raw)
    dlc_config_path = session_data.get("dlc_config_path", "")
    if not dlc_config_path:
        return jsonify({"error": "No DLC config loaded."}), 404

    p = Path(dlc_config_path)
    if not p.is_file():
        return jsonify({"error": "DLC config file not found on disk."}), 404

    return jsonify({
        "content":         p.read_text(),
        "dlc_config_path": str(dlc_config_path),
        "dlc_config_name": session_data.get("dlc_config_name", "config.yaml"),
    })


@app.route("/session/dlc-config", methods=["PATCH"])
def save_dlc_config():
    """Save edited DLC config.yaml content back to disk."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400

    session_data    = json.loads(raw)
    dlc_config_path = session_data.get("dlc_config_path", "")
    if not dlc_config_path:
        return jsonify({"error": "No DLC config loaded."}), 404

    p = Path(dlc_config_path)
    if not p.is_file():
        return jsonify({"error": "DLC config file not found on disk."}), 404

    body    = request.get_json(force=True) or {}
    content = body.get("content", "")
    if not content.strip():
        return jsonify({"error": "Content cannot be empty."}), 400

    p.write_text(content)
    return jsonify({"status": "saved", "dlc_config_path": str(dlc_config_path)})


@app.route("/session/dlc-config", methods=["DELETE"])
def clear_dlc_config():
    """Remove DLC config association from the active session (file stays on disk)."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400
    session_data = json.loads(raw)
    session_data.pop("dlc_config_path", None)
    session_data.pop("dlc_config_name", None)
    _redis_client.set(_SESSION_KEY, json.dumps(session_data))
    return jsonify({"status": "cleared"})


@app.route("/session/dlc-config/from-path", methods=["POST"])
def load_dlc_config_from_path():
    """
    Attach a server-side config.yaml to the active session without re-uploading.
    The file is copied into the session directory.
    Body: { "config_path": "<absolute_server_path>" }
    """
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400

    body = request.get_json(force=True) or {}
    config_path_str = body.get("config_path", "").strip()
    if not config_path_str:
        return jsonify({"error": "config_path is required."}), 400

    config_path = Path(config_path_str).resolve()
    if config_path.suffix.lower() not in {".yaml", ".yml"}:
        return jsonify({"error": "config_path must point to a .yaml or .yml file."}), 400
    if not config_path.is_file():
        return jsonify({"error": f"File not found: {config_path_str}"}), 404

    # Security: only allow files within known roots
    allowed_roots = [DATA_DIR.resolve(), USER_DATA_DIR.resolve()]
    if not any(str(config_path).startswith(str(r) + "/") or config_path == r
               for r in allowed_roots):
        return jsonify({"error": "Access denied: path is outside allowed directories."}), 403

    session_data = json.loads(raw)
    session_dir  = Path(session_data.get("config_path", "")).parent
    if not session_dir.is_dir():
        return jsonify({"error": "Session directory not found."}), 400

    dlc_config_path = session_dir / "config.yaml"
    shutil.copy2(str(config_path), str(dlc_config_path))

    session_data["dlc_config_path"] = str(dlc_config_path)
    session_data["dlc_config_name"] = config_path.name
    _redis_client.set(_SESSION_KEY, json.dumps(session_data))

    return jsonify({
        "dlc_config_path": str(dlc_config_path),
        "dlc_config_name": session_data["dlc_config_name"],
    }), 201


@app.route("/fs/list-configs")
def fs_list_configs():
    """
    List config files and immediate subdirectories at a server-side path.
    Only accepts paths within USER_DATA_DIR or DATA_DIR.
    Query params:
      path=<absolute_path>
      ext=<.toml|.yaml|.yml>  (default .toml; .yaml also matches .yml)
    """
    path_str = request.args.get("path", "").strip()
    if not path_str:
        return jsonify({"error": "path parameter is required."}), 400
    ext = request.args.get("ext", ".toml").lower()
    allowed_exts_map = {
        ".toml": {".toml"},
        ".yaml": {".yaml", ".yml"},
        ".yml":  {".yaml", ".yml"},
    }
    if ext not in allowed_exts_map:
        return jsonify({"error": "Unsupported ext parameter."}), 400
    match_exts = allowed_exts_map[ext]

    p = Path(path_str).resolve()
    # Security: only allow paths within known roots
    allowed_roots = [DATA_DIR.resolve(), USER_DATA_DIR.resolve()]
    if not any(p == r or str(p).startswith(str(r) + "/") for r in allowed_roots):
        return jsonify({"error": "Access denied: path is outside allowed directories."}), 403
    if not p.is_dir():
        return jsonify({"error": f"Directory not found: {path_str}"}), 404
    configs = sorted([
        f.name for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in match_exts
    ])
    subdirs = sorted([
        d.name for d in p.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ])
    return jsonify({"configs": configs, "subdirs": subdirs, "path": str(p)})


@app.route("/session/from-path", methods=["POST"])
def create_session_from_server_path():
    """
    Create a new session from a server-side config.toml.
    The file is copied into a fresh session directory under DATA_DIR.
    Body: { "config_path": "<absolute_server_path_to_config.toml>" }
    """
    body = request.get_json(force=True) or {}
    config_path_str = body.get("config_path", "").strip()
    if not config_path_str:
        return jsonify({"error": "config_path is required."}), 400

    config_path = Path(config_path_str).resolve()

    if config_path.suffix.lower() != ".toml":
        return jsonify({"error": "config_path must point to a .toml file."}), 400
    if not config_path.is_file():
        return jsonify({"error": f"File not found: {config_path_str}"}), 404

    # Security: only allow files within known roots
    allowed_roots = [DATA_DIR.resolve(), USER_DATA_DIR.resolve()]
    if not any(str(config_path).startswith(str(r) + "/") or config_path == r
               for r in allowed_roots):
        return jsonify({"error": "Access denied: path is outside allowed directories."}), 403

    try:
        _clear_session_data()

        session_id  = uuid.uuid4().hex[:12]
        session_dir = DATA_DIR / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        dest_config = session_dir / "config.toml"
        shutil.copy2(str(config_path), str(dest_config))

        task = celery.send_task(
            "tasks.init_anipose_session",
            kwargs={"config_path": str(dest_config)},
        )

        session_data = {
            "session_id":  session_id,
            "config_path": str(dest_config),
            "config_name": config_path.name,
            "task_id":     task.id,
            "created_at":  datetime.now(timezone.utc).isoformat(),
            "status":      "initializing",
            "source_path": str(config_path),
        }
        _redis_client.set(_SESSION_KEY, json.dumps(session_data))
        return jsonify(session_data), 201

    except Exception as exc:
        app.logger.exception("Session from-path creation failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/projects/<project_id>/detect-frame-dims", methods=["POST"])
def detect_frame_dims(project_id: str):
    """
    Read the frame dimensions of a video file inside the project using OpenCV.
    Body: { "folder": "<folder_name>", "filename": "<video.ext>", "root": "<optional>" }
    Returns { "width": <int>, "height": <int> }.
    """
    import cv2

    body     = request.get_json(force=True) or {}
    root     = body.get("root",     "").strip()
    folder   = body.get("folder",   "").strip()
    filename = body.get("filename", "").strip()

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404
    if not folder or not filename:
        return jsonify({"error": "folder and filename are required."}), 400

    target = (project_dir / folder / filename).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    cap = cv2.VideoCapture(str(target))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video file."}), 400

    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if width == 0 or height == 0:
        return jsonify({"error": "Could not read frame dimensions from video."}), 400

    return jsonify({"width": width, "height": height})


def _clear_session_data():
    """Helper: revoke pending init task, delete config dir, remove Redis key."""
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return
    session_data = json.loads(raw)
    celery.control.revoke(session_data.get("task_id", ""), terminate=True)
    config_path = Path(session_data.get("config_path", ""))
    if config_path.parent.exists() and config_path.parent.name.startswith("session_"):
        shutil.rmtree(str(config_path.parent), ignore_errors=True)
    _redis_client.delete(_SESSION_KEY)


# ── Session pipeline operations ───────────────────────────────────
_OPERATION_TASKS = {
    # Anipose pipeline
    "calibrate":                      "tasks.process_calibrate",
    "filter_2d":                      "tasks.process_filter_2d",
    "triangulate":                    "tasks.process_triangulate",
    "filter_3d":                      "tasks.process_filter_3d",
    # MediaPipe preprocessing
    "organize_for_anipose":           "tasks.process_organize_for_anipose",
    "convert_mediapipe_csv_to_h5":    "tasks.process_convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv":   "tasks.process_convert_mediapipe_to_dlc_csv",
    "convert_3d_csv_to_mat":          "tasks.process_convert_3d_csv_to_mat",
}

# Operations that do NOT need a config.toml — only session_path + scorer
_MEDIAPIPE_OPS = {
    "organize_for_anipose",
    "convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv",
    "convert_3d_csv_to_mat",
}

# Operations that require frame_w / frame_h
_FRAME_DIMS_OPS = {"convert_mediapipe_to_dlc_csv", "convert_3d_csv_to_mat"}


@app.route("/run", methods=["POST"])
def run_operation():
    """
    Dispatch a pipeline operation against a project folder.

    Expects JSON body:
      { "operation": "calibrate|filter_2d|triangulate|filter_3d|organize_for_anipose|convert_mediapipe_csv_to_h5",
        "project_id": "<folder name under DATA_DIR>",
        "root": "<optional absolute path>",
        "scorer": "<scorer name, MediaPipe ops only, default 'User'>" }
    Returns { "task_id", "operation", "project_id" } immediately (202).
    """
    body = request.get_json(force=True) or {}
    operation  = body.get("operation", "").lower()
    project_id = body.get("project_id", "").strip()
    root       = body.get("root", "").strip()

    if operation not in _OPERATION_TASKS:
        return jsonify({"error": f"Unknown operation '{operation}'."}), 400
    if not project_id:
        return jsonify({"error": "project_id is required."}), 400

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project folder not found: '{project_id}'."}), 400

    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session. Create a session first."}), 400

    if operation in _MEDIAPIPE_OPS:
        task_kwargs = {"session_path": str(project_dir)}
        if operation != "convert_3d_csv_to_mat":
            scorer = (body.get("scorer", "") or "User").strip() or "User"
            task_kwargs["scorer"] = scorer
        if operation in _FRAME_DIMS_OPS:
            try:
                frame_w = int(body.get("frame_w", 0))
                frame_h = int(body.get("frame_h", 0))
            except (TypeError, ValueError):
                frame_w = frame_h = 0
            if frame_w <= 0 or frame_h <= 0:
                return jsonify({"error": "frame_w and frame_h (positive integers) are required."}), 400
            task_kwargs["frame_w"] = frame_w
            task_kwargs["frame_h"] = frame_h
    else:
        config_path = json.loads(raw).get("config_path", "")
        task_kwargs = {"session_path": str(project_dir), "config_path": config_path}

    task = celery.send_task(_OPERATION_TASKS[operation], kwargs=task_kwargs)
    return jsonify({
        "task_id":    task.id,
        "operation":  operation,
        "project_id": project_id,
    }), 202


@app.route("/session/pipeline")
def get_pipeline_structure():
    """
    Parse the [pipeline] section of the active session's config.toml and return
    a deduplicated, ordered list of {key, folder} objects.
    """
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))
    if not config_path.is_file():
        return jsonify({"error": "Config file not found."}), 404
    pipeline = _parse_pipeline_section(config_path.read_text())
    # Deduplicate by folder name while preserving order
    seen: set[str] = set()
    folders = []
    for key, folder in pipeline.items():
        if folder not in seen:
            seen.add(folder)
            folders.append({"key": key, "folder": folder})
    return jsonify({"pipeline": folders})


@app.route("/projects/<project_id>/browse")
def browse_project(project_id: str):
    """
    For each pipeline folder in the active session config, list the files that
    exist under <root>/<project_id>/<folder>/ (root defaults to DATA_DIR).
    Query param: root=<absolute_path>  (optional)
    """
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    pipeline = _parse_pipeline_section(config_path.read_text()) if config_path.is_file() else {}
    seen: set[str] = set()
    result = []
    for key, folder_name in pipeline.items():
        if folder_name in seen:
            continue
        seen.add(folder_name)
        folder_path = project_dir / folder_name
        files = []
        if folder_path.is_dir():
            for item in sorted(folder_path.iterdir()):
                files.append({
                    "name":   item.name,
                    "is_dir": item.is_dir(),
                    "size":   item.stat().st_size if item.is_file() else None,
                })
        result.append({
            "key":    key,
            "folder": folder_name,
            "exists": folder_path.is_dir(),
            "files":  files,
        })
    return jsonify({"project_id": project_id, "folders": result})


@app.route("/projects/<project_id>/upload", methods=["POST"])
def upload_to_project(project_id: str):
    """
    Upload files into <root>/<project_id>/<folder>/.
    Form fields: folder (str), files[] (one or more files), root (optional absolute path).
    """
    root = request.form.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    folder_name = request.form.get("folder", "").strip()
    if not folder_name:
        return jsonify({"error": "folder field is required."}), 400

    files = request.files.getlist("files[]")
    if not files or not files[0].filename:
        return jsonify({"error": "No files provided."}), 400

    target_dir = project_dir / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        safe_name = secure_filename(f.filename)
        f.save(str(target_dir / safe_name))
        saved.append(safe_name)

    return jsonify({"saved": saved, "folder": folder_name, "project_id": project_id}), 201


def _parse_pipeline_section(config_text: str) -> dict:
    """Extract [pipeline] key = "value" pairs from raw TOML text."""
    match = re.search(r'\[pipeline\](.*?)(?=\n\[|\Z)', config_text, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^(\w+)\s*=\s*"([^"]*)"', line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


# ── DLC Project Manager ───────────────────────────────────────────

def _walk_dir(path: Path, project_path: Path, depth: int = 0, max_depth: int = 6) -> list:
    """
    Recursively list a directory relative to project_path.
    Each item: { name, type, rel_path, size? (files), children? (dirs) }
    Dirs are sorted before files; hidden entries are skipped.
    """
    items = []
    try:
        entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        for item in entries:
            if item.name.startswith(".") or item.name.startswith("@"):
                continue
            rel = str(item.relative_to(project_path))
            if item.is_dir():
                children = _walk_dir(item, project_path, depth + 1, max_depth) if depth < max_depth else []
                items.append({"name": item.name, "type": "dir", "rel_path": rel, "children": children})
            else:
                items.append({"name": item.name, "type": "file", "size": item.stat().st_size, "rel_path": rel})
    except PermissionError:
        pass
    return items


def _dlc_project_security_check(p: Path) -> bool:
    """Return True if p is inside an allowed data root."""
    allowed_roots = [DATA_DIR.resolve(), USER_DATA_DIR.resolve()]
    pr = p.resolve()
    return any(pr == r or str(pr).startswith(str(r) + "/") for r in allowed_roots)


@app.route("/dlc/project", methods=["GET"])
def get_dlc_project():
    """Return the current DLC project state."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"status": "none"}), 200
    return jsonify(json.loads(raw)), 200


@app.route("/dlc/project", methods=["POST"])
def set_dlc_project():
    """
    Set the active DLC project by providing its server-side folder path.
    Checks for config.yaml and returns project metadata.
    Body: { "path": "<absolute_path_to_dlc_project_folder>" }
    """
    body = request.get_json(force=True) or {}
    path_str = body.get("path", "").strip()
    if not path_str:
        return jsonify({"error": "path is required."}), 400

    p = Path(path_str).resolve()
    if not _dlc_project_security_check(p):
        return jsonify({"error": "Access denied: path is outside allowed directories."}), 403
    if not p.is_dir():
        return jsonify({"error": f"Directory not found: {path_str}"}), 404

    has_config  = (p / "config.yaml").is_file()
    config_path = str(p / "config.yaml") if has_config else None

    project_data = {
        "project_path": str(p),
        "project_name": p.name,
        "has_config":   has_config,
        "config_path":  config_path,
    }
    _redis_client.set(_DLC_PROJECT_KEY, json.dumps(project_data))
    return jsonify(project_data), 200


@app.route("/dlc/project", methods=["DELETE"])
def clear_dlc_project():
    """Clear the active DLC project session."""
    _redis_client.delete(_DLC_PROJECT_KEY)
    return jsonify({"status": "cleared"}), 200


@app.route("/dlc/project/browse")
def browse_dlc_project():
    """List files in each DLC pipeline folder for the active DLC project."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    folders = []
    for key, folder_name in DLC_PIPELINE_FOLDERS:
        folder_path = project_path / folder_name
        children = _walk_dir(folder_path, project_path) if folder_path.is_dir() else []
        folders.append({
            "key":      key,
            "folder":   folder_name,
            "rel_path": folder_name,
            "children": children,
            "exists":   folder_path.is_dir(),
        })

    return jsonify({
        "project_path": str(project_path),
        "project_name": project_data.get("project_name", ""),
        "has_config":   project_data.get("has_config", False),
        "folders":      folders,
    })


@app.route("/dlc/project/config", methods=["GET"])
def get_dlc_project_config():
    """Return the raw text of the active DLC project's config.yaml."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404
    return jsonify({"content": config_path.read_text(), "config_path": str(config_path)})


@app.route("/dlc/project/config", methods=["PATCH"])
def save_dlc_project_config():
    """Overwrite the active DLC project's config.yaml."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    body    = request.get_json(force=True) or {}
    content = body.get("content", "")
    if not content.strip():
        return jsonify({"error": "Content cannot be empty."}), 400
    config_path.write_text(content)
    return jsonify({"status": "saved"})


@app.route("/dlc/project/upload", methods=["POST"])
def dlc_project_upload():
    """
    Upload files into a DLC pipeline folder of the active project.
    Form fields: folder (one of the DLC pipeline folders), files[]
    """
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    folder_name = request.form.get("folder", "").strip()
    dlc_folder_names = [f for _, f in DLC_PIPELINE_FOLDERS]
    if folder_name not in dlc_folder_names:
        return jsonify({"error": f"Invalid DLC folder: '{folder_name}'."}), 400

    files = request.files.getlist("files[]")
    if not files or not files[0].filename:
        return jsonify({"error": "No files provided."}), 400

    target_dir = project_path / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        safe_name = secure_filename(f.filename)
        f.save(str(target_dir / safe_name))
        saved.append(safe_name)

    return jsonify({"saved": saved, "folder": folder_name}), 201


@app.route("/dlc/project/file", methods=["DELETE"])
def dlc_project_delete_file():
    """Delete a file anywhere inside the active DLC project. Body: { rel_path }"""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    body     = request.get_json(force=True) or {}
    rel_path = body.get("rel_path", "").strip()
    if not rel_path:
        return jsonify({"error": "rel_path is required."}), 400

    # Must be inside a top-level pipeline folder
    dlc_folder_names = [f for _, f in DLC_PIPELINE_FOLDERS]
    top = Path(rel_path).parts[0] if Path(rel_path).parts else ""
    if top not in dlc_folder_names:
        return jsonify({"error": "Path must be inside a pipeline folder."}), 400

    target = (project_path / rel_path).resolve()
    if not target.is_relative_to(project_path.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    target.unlink()
    return jsonify({"status": "deleted", "rel_path": rel_path})


@app.route("/dlc/project/file", methods=["PATCH"])
def dlc_project_rename_file():
    """Rename a file anywhere inside the active DLC project. Body: { rel_path, new_name }"""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    body     = request.get_json(force=True) or {}
    rel_path = body.get("rel_path", "").strip()
    new_name = body.get("new_name", "").strip()

    if not rel_path or not new_name:
        return jsonify({"error": "rel_path and new_name are required."}), 400

    dlc_folder_names = [f for _, f in DLC_PIPELINE_FOLDERS]
    top = Path(rel_path).parts[0] if Path(rel_path).parts else ""
    if top not in dlc_folder_names:
        return jsonify({"error": "Path must be inside a pipeline folder."}), 400

    src = (project_path / rel_path).resolve()
    dst = (src.parent / secure_filename(new_name)).resolve()

    if not src.is_relative_to(project_path.resolve()) or \
       not dst.is_relative_to(project_path.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not src.is_file():
        return jsonify({"error": "File not found."}), 404
    if dst.exists():
        return jsonify({"error": "A file with that name already exists."}), 409

    src.rename(dst)
    return jsonify({"status": "renamed", "rel_path": rel_path, "new_name": dst.name})


@app.route("/dlc/project/download")
def dlc_project_download():
    """
    Download a DLC pipeline folder (or the whole project) as a ZIP.
    Query param: folder=<dlc_folder_name>  (optional; downloads all if omitted)
    """
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    project_name = project_data.get("project_name", "dlc-project")

    folder_name = request.args.get("folder", "").strip()
    if folder_name:
        dlc_folder_names = [f for _, f in DLC_PIPELINE_FOLDERS]
        if folder_name not in dlc_folder_names:
            return jsonify({"error": f"Invalid folder: '{folder_name}'."}), 400
        download_path = project_path / folder_name
        zip_name      = f"{project_name}_{folder_name}.zip"
    else:
        download_path = project_path
        zip_name      = f"{project_name}.zip"

    if not download_path.is_dir():
        return jsonify({"error": "Directory not found."}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(download_path.rglob("*")):
            if item.is_file() and not item.name.startswith("."):
                zf.write(item, item.relative_to(download_path.parent))
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=zip_name,
                     mimetype="application/zip")


# ── DLC Frame Extractor ───────────────────────────────────────────

@app.route("/dlc/project/videos")
def dlc_list_videos():
    """List video files in the active DLC project's videos folder."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    videos_dir = project_path / "videos"
    videos = []
    if videos_dir.is_dir():
        for f in sorted(videos_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_VIDEO_EXT:
                videos.append({"name": f.name, "size": f.stat().st_size})

    return jsonify({"videos": videos})


@app.route("/dlc/project/video-info/<path:filename>")
def dlc_video_info(filename: str):
    """Return FPS, frame count, width, height for a video in the videos folder."""
    import cv2

    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    video_path = (project_path / "videos" / filename).resolve()
    if not video_path.is_relative_to((project_path / "videos").resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video."}), 400

    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    return jsonify({"fps": fps, "frame_count": frame_count, "width": width, "height": height})


@app.route("/dlc/project/video-stream/<path:filename>")
def dlc_video_stream(filename: str):
    """Stream a video file from the active DLC project's videos folder."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    videos_dir = (project_path / "videos").resolve()
    video_path = (videos_dir / filename).resolve()
    if not video_path.is_relative_to(videos_dir):
        return jsonify({"error": "Invalid path."}), 400
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404
    if video_path.suffix.lower() not in ALLOWED_VIDEO_EXT:
        return jsonify({"error": "Unsupported video format."}), 400

    return send_file(str(video_path), conditional=True)


@app.route("/dlc/project/video-upload", methods=["POST"])
def dlc_video_upload():
    """Upload a video into the active DLC project's videos folder."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify({"error": "No video file provided."}), 400
    if not _valid_ext(video_file.filename, ALLOWED_VIDEO_EXT):
        return jsonify({"error": "Unsupported video format."}), 400

    videos_dir = project_path / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(video_file.filename)
    video_file.save(str(videos_dir / safe_name))

    return jsonify({"saved": safe_name}), 201


@app.route("/dlc/project/save-frame", methods=["POST"])
def dlc_save_frame():
    """
    Save an extracted video frame to labeled-data/<video_stem>/ as PNG.
    Body: { "video_name": "<str>", "frame_data": "<base64-encoded JPEG>" }
    The client sends JPEG (small payload); server converts to PNG via OpenCV.
    Returns the saved filename and running frame count.
    """
    import base64 as _base64
    import cv2
    import numpy as np

    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body         = request.get_json(force=True) or {}
    video_name   = body.get("video_name",   "").strip()
    frame_data   = body.get("frame_data",   "").strip()
    frame_number = body.get("frame_number")          # int video frame index, may be None
    if frame_number is not None:
        try:
            frame_number = int(frame_number)
        except (TypeError, ValueError):
            frame_number = None
    if not video_name:
        return jsonify({"error": "video_name is required."}), 400
    if not frame_data:
        return jsonify({"error": "frame_data is required."}), 400

    try:
        img_bytes = _base64.b64decode(frame_data)
    except Exception:
        return jsonify({"error": "Invalid frame_data (expected base64)."}), 400

    # Decode JPEG bytes → encode losslessly as PNG → write via Python (cv2.imwrite
    # can silently return False on some systems; imencode + write_bytes is reliable)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Could not decode image data."}), 400

    ok, png_buf = cv2.imencode(".png", img)
    if not ok:
        return jsonify({"error": "Could not encode frame as PNG."}), 500

    video_stem  = Path(secure_filename(video_name)).stem
    labeled_dir = project_path / "labeled-data" / video_stem
    labeled_dir.mkdir(parents=True, exist_ok=True)

    existing_pngs = [f for f in labeled_dir.iterdir() if f.suffix == ".png"]

    # Duplicate check: if frame_number already present as yyyyy in any img????-yyyyy.png, skip
    if frame_number is not None:
        dup_pat = re.compile(r"^img\d{4}-(\d+)\.png$")
        for f in existing_pngs:
            m = dup_pat.match(f.name)
            if m and int(m.group(1)) == frame_number:
                return jsonify({"skipped": True, "frame_number": frame_number}), 200

    order = len(existing_pngs)
    if frame_number is not None:
        frame_filename = f"img{order:04d}-{frame_number:05d}.png"
    else:
        frame_filename = f"img{order:04d}.png"
    (labeled_dir / frame_filename).write_bytes(png_buf.tobytes())

    return jsonify({
        "saved":        frame_filename,
        "folder":       f"labeled-data/{video_stem}",
        "abs_path":     str(labeled_dir / frame_filename),
        "frame_count":  order + 1,
    }), 201


# ── DLC Frame Labeler ─────────────────────────────────────────────

def _parse_dlc_yaml(config_path: Path) -> dict:
    """Parse a DLC config.yaml and return the relevant fields."""
    text = config_path.read_text()
    if _yaml is not None:
        return _yaml.safe_load(text) or {}
    # Fallback: naive regex parser for bodyparts + scorer
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
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return None, None, (jsonify({"error": "No active DLC project."}), 400)
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return project_data, {}, None
    try:
        cfg = _parse_dlc_yaml(config_path)
    except Exception as exc:
        return project_data, {}, (jsonify({"error": f"Could not parse config.yaml: {exc}"}), 500)
    return project_data, cfg, None


@app.route("/dlc/project/bodyparts")
def dlc_get_bodyparts():
    """Return bodyparts and scorer from the active DLC project's config.yaml."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err
    return jsonify({
        "bodyparts": cfg.get("bodyparts", []),
        "scorer":    cfg.get("scorer", "User"),
    })


@app.route("/dlc/project/labeled-frames")
def dlc_list_labeled_frames():
    """List video stems and their PNG frames inside labeled-data/."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    labeled_base = project_path / "labeled-data"
    if not labeled_base.is_dir():
        return jsonify({"video_stems": []})

    result = []
    for stem_dir in sorted(labeled_base.iterdir(), key=lambda p: _natural_keys(p.name)):
        if not stem_dir.is_dir():
            continue
        frames = sorted(
            [f.name for f in stem_dir.iterdir() if f.suffix.lower() == ".png"],
            key=_natural_keys,
        )
        if frames:
            result.append({"video_stem": stem_dir.name, "frames": frames})

    return jsonify({"video_stems": result})


@app.route("/dlc/project/frame-image/<path:video_stem>/<filename>")
def dlc_serve_frame_image(video_stem: str, filename: str):
    """Serve a PNG frame from labeled-data/<video_stem>/."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    safe_stem     = secure_filename(video_stem)
    safe_filename = secure_filename(filename)
    frame_dir  = (project_path / "labeled-data" / safe_stem).resolve()
    proj_root  = project_path.resolve()
    if not str(frame_dir).startswith(str(proj_root)):
        return jsonify({"error": "Access denied."}), 403

    frame_path = frame_dir / safe_filename
    if not frame_path.is_file():
        return jsonify({"error": "Frame not found."}), 404

    return send_file(str(frame_path), mimetype="image/png")


@app.route("/dlc/project/labels/<path:video_stem>", methods=["GET"])
def dlc_get_labels(video_stem: str):
    """Read CollectedData_<scorer>.csv and return labels as JSON."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err
    scorer       = cfg.get("scorer", "User")
    project_path = Path(project_data.get("project_path", ""))
    stem_dir     = project_path / "labeled-data" / secure_filename(video_stem)
    csv_path     = stem_dir / f"CollectedData_{scorer}.csv"

    if not csv_path.is_file():
        return jsonify({"labels": {}, "scorer": scorer})

    try:
        with open(str(csv_path), newline="") as f:
            rows = list(csv.reader(f))

        if len(rows) < 4:
            return jsonify({"labels": {}, "scorer": scorer})

        bodyparts_row = rows[1][1:]
        coords_row    = rows[2][1:]
        col_pairs     = list(zip(bodyparts_row, coords_row))

        labels = {}
        for row in rows[3:]:
            if not row:
                continue
            img_name    = Path(row[0]).name
            vals        = row[1:]
            bp_data: dict = {}
            for (bp, coord), val in zip(col_pairs, vals):
                bp_data.setdefault(bp, {})[coord] = val

            frame_labels = {}
            for bp, coords_dict in bp_data.items():
                x_str = coords_dict.get("x", "")
                y_str = coords_dict.get("y", "")
                try:
                    x = float(x_str) if x_str not in ("", "NaN", "nan") else None
                    y = float(y_str) if y_str not in ("", "NaN", "nan") else None
                except ValueError:
                    x = y = None
                frame_labels[bp] = [x, y] if x is not None and y is not None else None

            labels[img_name] = frame_labels

        return jsonify({"labels": labels, "scorer": scorer})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/dlc/project/labels/<path:video_stem>", methods=["POST"])
def dlc_save_labels(video_stem: str):
    """Write labels dict to CollectedData_<scorer>.csv in DLC MultiIndex CSV format."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err

    scorer     = cfg.get("scorer", "User")
    bodyparts  = cfg.get("bodyparts", [])
    project_path = Path(project_data.get("project_path", ""))
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body   = request.get_json(force=True) or {}
    labels = body.get("labels", {})   # {frame_name: {bp: [x, y] or null}}

    safe_stem = secure_filename(video_stem)
    stem_dir  = project_path / "labeled-data" / safe_stem
    stem_dir.mkdir(parents=True, exist_ok=True)
    csv_path  = stem_dir / f"CollectedData_{scorer}.csv"

    frame_names = sorted(labels.keys(), key=_natural_keys)

    header_scorer    = ["scorer"]    + [scorer] * (len(bodyparts) * 2)
    header_bodyparts = ["bodyparts"] + [bp for bp in bodyparts for _ in range(2)]
    header_coords    = ["coords"]    + ["x", "y"] * len(bodyparts)

    rows = [header_scorer, header_bodyparts, header_coords]
    for frame_name in frame_names:
        row_path    = f"labeled-data/{safe_stem}/{frame_name}"
        frame_lbls  = labels.get(frame_name, {})
        row         = [row_path]
        for bp in bodyparts:
            pt = frame_lbls.get(bp)
            if pt and len(pt) == 2 and pt[0] is not None and pt[1] is not None:
                row.extend([str(round(pt[0], 4)), str(round(pt[1], 4))])
            else:
                row.extend(["NaN", "NaN"])
        rows.append(row)

    try:
        with open(str(csv_path), "w", newline="") as f:
            csv.writer(f).writerows(rows)
        return jsonify({"status": "saved", "csv_path": str(csv_path), "scorer": scorer})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Visualization helpers ─────────────────────────────────────────

def _natural_keys(text: str) -> list:
    """Sort helper — splits text into int and str chunks for natural ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _get_video_name(config: dict, fname: str) -> str:
    """Extract the trial name from a video filename by stripping the camera part."""
    cam_regex = config.get("triangulation", {}).get("cam_regex", r"cam[0-9]")
    basename  = Path(fname).stem
    vname     = re.sub(cam_regex, "", basename)
    return re.sub(r"^[_\-]+|[_\-]+$", "", vname).strip()


def _get_cam_name(config: dict, fname: str) -> str:
    """Extract the camera name from a filename using cam_regex."""
    cam_regex = config.get("triangulation", {}).get("cam_regex", r"cam[0-9]")
    m = re.search(cam_regex, Path(fname).stem)
    return m.group(0) if m else "unknown"


def _get_config_for_project(project_id: str, root: str = "") -> dict:
    """
    Load config.toml for a project for visualization routes.
    Priority:
      1. <project_dir>/config.toml  (placed there by _ensure_config during pipeline runs)
      2. Redis active session config_path (fallback)
    """
    import toml

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        raise ValueError(str(exc))

    local_config = project_dir / "config.toml"
    if local_config.is_file():
        return toml.load(str(local_config))

    raw = _redis_client.get(_SESSION_KEY)
    if raw:
        config_path = Path(json.loads(raw).get("config_path", ""))
        if config_path.is_file():
            return toml.load(str(config_path))

    raise FileNotFoundError(f"No config.toml found for project '{project_id}'.")


@app.route("/projects", methods=["POST"])
def create_project():
    """
    Create a new project directory and auto-create every pipeline subfolder
    defined in the active session's config.toml.
    Body: { "name": "<project_name>", "root": "<optional_absolute_path>" }
    """
    body = request.get_json(force=True) or {}
    name = body.get("name", "").strip()
    root = body.get("root", "").strip()
    if not name:
        return jsonify({"error": "Project name is required."}), 400

    safe_name = re.sub(r"[^\w\-.]", "_", name)
    if not safe_name:
        return jsonify({"error": "Invalid project name."}), 400

    base = Path(root) if root else DATA_DIR
    project_dir = base / safe_name
    if project_dir.exists():
        return jsonify({"error": f"Project '{safe_name}' already exists."}), 409

    # Collect unique pipeline folder names from the active session config
    raw = _redis_client.get(_SESSION_KEY)
    pipeline_folders: list[str] = []
    if raw:
        config_path = Path(json.loads(raw).get("config_path", ""))
        if config_path.is_file():
            seen: set[str] = set()
            for folder in _parse_pipeline_section(config_path.read_text()).values():
                if folder not in seen:
                    seen.add(folder)
                    pipeline_folders.append(folder)

    project_dir.mkdir(parents=True, exist_ok=True)
    for folder in pipeline_folders:
        (project_dir / folder).mkdir(exist_ok=True)

    return jsonify({
        "project_id":      safe_name,
        "folders_created": pipeline_folders,
    }), 201


@app.route("/projects/<project_id>/file", methods=["PATCH"])
def rename_project_file(project_id: str):
    """
    Rename a file within a project pipeline folder.
    Body: { "folder": "<folder_name>", "old_name": "<current_name>", "new_name": "<new_name>", "root": "<optional_path>" }
    """
    body     = request.get_json(force=True) or {}
    root     = body.get("root",     "").strip()
    folder   = body.get("folder",   "").strip()
    old_name = body.get("old_name", "").strip()
    new_name = body.get("new_name", "").strip()

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    if not folder or not old_name or not new_name:
        return jsonify({"error": "folder, old_name, and new_name are required."}), 400

    base     = project_dir.resolve()
    src      = (project_dir / folder / old_name).resolve()
    dst      = (project_dir / folder / new_name).resolve()

    if not src.is_relative_to(base) or not dst.is_relative_to(base):
        return jsonify({"error": "Invalid path."}), 400
    if not src.is_file():
        return jsonify({"error": "File not found."}), 404
    if dst.exists():
        return jsonify({"error": f"'{new_name}' already exists."}), 409

    src.rename(dst)
    return jsonify({"old_name": old_name, "new_name": new_name, "folder": folder})


@app.route("/projects/<project_id>/file", methods=["DELETE"])
def delete_project_file(project_id: str):
    """
    Delete a single file from a project pipeline folder.
    Body: { "folder": "<folder_name>", "filename": "<file_name>", "root": "<optional_path>" }
    """
    body = request.get_json(force=True) or {}
    root     = body.get("root",     "").strip()
    folder   = body.get("folder",   "").strip()
    filename = body.get("filename", "").strip()

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    if not folder or not filename:
        return jsonify({"error": "folder and filename are required."}), 400

    # Resolve and guard against path traversal
    target = (project_dir / folder / filename).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    target.unlink()
    return jsonify({"deleted": filename, "folder": folder})


@app.route("/projects/<project_id>/download")
def download_project(project_id: str):
    """
    Stream project data as a ZIP archive.
    Optional query params:
      ?folder=<name>  limits the archive to that subfolder
      ?root=<path>    use a custom project root instead of DATA_DIR
    """
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    folder = request.args.get("folder", "").strip()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if folder:
            target = project_dir / folder
            if not target.is_dir():
                return jsonify({"error": f"Folder not found: '{folder}'"}), 404
            for item in sorted(target.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(project_dir))
            zip_name = f"{project_id}_{folder}.zip"
        else:
            for item in sorted(project_dir.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(project_dir))
            zip_name = f"{project_id}.zip"

    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=zip_name, mimetype="application/zip")


@app.route("/projects")
def list_projects():
    """List all project ids on the shared volume."""
    projects = sorted(
        [d.name for d in DATA_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )
    return jsonify({"projects": projects})


# ── Visualization routes ──────────────────────────────────────────

@app.route("/get-sessions")
def get_sessions():
    """List Anipose sessions (subdirs of the anipose root found via config['path']).

    Searches DATA_DIR for any project whose config.toml has a 'path' field pointing
    to an existing directory, then returns that directory's subdirectories as sessions.
    Falls back to listing DLC project folders if no anipose root is found.
    """
    sessions: list = []
    anipose_root_found: Path | None = None

    if DATA_DIR.is_dir():
        for project_dir in sorted(DATA_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            config = _inspector_load_config(project_dir)
            root = _get_anipose_root(project_dir, config)
            if root != project_dir and root.is_dir():
                anipose_root_found = root
                break  # use the first project that has a valid 'path'

    if anipose_root_found:
        sessions = sorted(
            [d.name for d in anipose_root_found.iterdir()
             if d.is_dir() and not d.name.startswith(".")],
            key=_natural_keys,
        )
    else:
        # Fall back: list DLC projects that have a config.toml
        sessions = sorted(
            [d.name for d in DATA_DIR.iterdir()
             if d.is_dir() and (d / "config.toml").is_file()],
            key=_natural_keys,
        )

    return jsonify({"sessions": sessions})


@app.route("/projects/<project_id>/metadata")
def get_project_metadata(project_id: str):
    """Return bodyparts, labeling scheme and video speed from the project config."""
    root = request.args.get("root", "").strip()
    try:
        config = _get_config_for_project(project_id, root)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({
        "bodyparts":   config.get("bodyparts", []),
        "scheme":      config.get("labeling", {}).get("scheme", []),
        "video_speed": config.get("converted_video_speed", 1),
    })


@app.route("/projects/<project_id>/pose3d/<path:subpath>")
def get_pose3d(project_id: str, subpath: str):
    """Read a 3D-pose CSV and return normalised per-bodypart trajectories."""
    import numpy as np
    import pandas as pd

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    try:
        data = pd.read_csv(str(target))
    except Exception as exc:
        return jsonify({"error": f"Could not read CSV: {exc}"}), 400

    x_cols    = [c for c in data.columns if c.endswith("_x")]
    bodyparts = [c[:-2] for c in x_cols]
    if not bodyparts:
        return jsonify({"error": "No bodypart columns found in CSV."}), 400

    all_finite = []
    for bp in bodyparts:
        for ax in ("x", "y", "z"):
            col = f"{bp}_{ax}"
            if col in data.columns:
                vals = data[col].to_numpy(dtype=float)
                all_finite.append(vals[np.isfinite(vals)])

    if not any(len(a) for a in all_finite):
        return jsonify({"error": "No finite values found in CSV."}), 400

    flat    = np.concatenate(all_finite)
    v_min   = float(np.nanmin(flat))
    v_max   = float(np.nanmax(flat))
    v_range = v_max - v_min if v_max != v_min else 1.0

    result = {}
    for bp in bodyparts:
        coords = {}
        for ax in ("x", "y", "z"):
            col = f"{bp}_{ax}"
            if col in data.columns:
                raw  = data[col].to_numpy(dtype=float)
                norm = (raw - v_min) / v_range
                coords[ax] = [None if not np.isfinite(v) else round(float(v), 6) for v in norm]
        result[bp] = coords

    return jsonify({
        "bodyparts": bodyparts,
        "data":      result,
        "n_frames":  len(data),
        "norm_min":  v_min,
        "norm_max":  v_max,
    })


@app.route("/projects/<project_id>/pose2dproj/<path:subpath>")
def get_pose2dproj(project_id: str, subpath: str):
    """Serve a pre-computed 2D-projected pose CSV."""
    import pandas as pd

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    try:
        data = pd.read_csv(str(target))
    except Exception as exc:
        return jsonify({"error": f"Could not read CSV: {exc}"}), 400

    return jsonify({
        "columns":  list(data.columns),
        "data":     data.where(data.notnull(), None).to_dict(orient="list"),
        "n_frames": len(data),
    })


@app.route("/projects/<project_id>/framerate/<path:subpath>")
def get_project_framerate(project_id: str, subpath: str):
    """Return the framerate of a video file using OpenCV."""
    import cv2

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    cap = cv2.VideoCapture(str(target))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video file."}), 400
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if fps <= 0:
        return jsonify({"error": "Could not determine framerate."}), 400
    return jsonify({"fps": fps})


@app.route("/projects/<project_id>/video/<path:subpath>")
def stream_project_video(project_id: str, subpath: str):
    """Stream a video file with Range-request support for HTML5 playback."""
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    mime_map = {
        ".mp4": "video/mp4", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    }
    mimetype = mime_map.get(target.suffix.lower(), "application/octet-stream")
    return send_from_directory(str(target.parent), target.name,
                               mimetype=mimetype, conditional=True)


@app.route("/projects/<project_id>/get-trials")
def get_project_trials(project_id: str):
    """List trial videos grouped by trial name using cam_regex from config."""
    from collections import defaultdict as _defaultdict

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    try:
        config = _get_config_for_project(project_id, root)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 404

    pipeline     = config.get("pipeline", {})
    videos_raw   = pipeline.get("videos_raw", "videos-raw")
    video_ext    = config.get("video_extension", "avi")
    video_folder = project_dir / videos_raw

    if not video_folder.is_dir():
        return jsonify({"trials": {}, "video_folder": str(video_folder), "project_id": project_id})

    video_exts = {f".{video_ext.lstrip('.')}"} | {".mp4", ".avi", ".mov", ".mkv"}
    videos = sorted(
        [f for f in video_folder.iterdir() if f.is_file() and f.suffix.lower() in video_exts],
        key=lambda p: _natural_keys(p.name),
    )

    trials: dict = _defaultdict(list)
    for vid in videos:
        trial_name = _get_video_name(config, vid.name)
        cam_name   = _get_cam_name(config, vid.name)
        trials[trial_name].append({
            "filename": vid.name,
            "cam_name": cam_name,
            "rel_path": f"{videos_raw}/{vid.name}",
        })

    return jsonify({
        "trials":       dict(sorted(trials.items(), key=lambda kv: _natural_keys(kv[0]))),
        "video_folder": str(video_folder),
        "project_id":   project_id,
    })


@app.route("/projects/<project_id>/behavior/<path:subpath>")
def get_project_behavior(project_id: str, subpath: str):
    """Return behavior annotation JSON for a given file path within a project."""
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"behaviors": {}, "exists": False})

    try:
        with open(target, "r") as f:
            data = json.load(f)
        return jsonify({"behaviors": data, "exists": True})
    except (json.JSONDecodeError, OSError) as exc:
        return jsonify({"error": f"Could not read behaviors file: {exc}"}), 400


@app.route("/projects/<project_id>/update-behavior", methods=["POST"])
def update_project_behavior(project_id: str):
    """Write behavior annotation changes back to a JSON file."""
    body      = request.get_json(force=True) or {}
    root      = body.get("root", "").strip()
    rel_path  = body.get("path", "").strip()
    behaviors = body.get("behaviors")

    if not rel_path:
        return jsonify({"error": "path is required."}), 400
    if behaviors is None:
        return jsonify({"error": "behaviors is required."}), 400

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / rel_path).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "w") as f:
            json.dump(behaviors, f, indent=2)
        return jsonify({"status": "saved", "path": str(target)})
    except OSError as exc:
        return jsonify({"error": f"Could not write file: {exc}"}), 500


@app.route("/projects/<project_id>/download-behavior")
def download_project_behavior(project_id: str):
    """Download a behaviors JSON file as an attachment."""
    root     = request.args.get("root", "").strip()
    rel_path = request.args.get("path", "behaviors.json").strip()

    try:
        project_dir = _resolve_project_dir(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / rel_path).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "Behaviors file not found."}), 404

    return send_file(str(target), as_attachment=True,
                     download_name=target.name, mimetype="application/json")


# ── Static files (CSS / JS) ──────────────────────────────────────
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


# ── Behavior Inspector page ───────────────────────────────────────
@app.route("/inspector")
def inspector_page():
    return render_template("inspector.html")


# ── Behavior Inspector adapter routes ────────────────────────────
# These match the URL patterns expected by inspector/script.js and
# map them to the existing project-based data in DATA_DIR.

_inspector_tokens: set = set()


def _inspector_split_subpath(subpath: str):
    """Split 'folderA|folderB/name' → (folder_parts, name).

    folder_parts is a list of path components (may be empty for top-level).
    """
    subpath = subpath.lstrip("/")
    if "/" in subpath:
        folder_key, name = subpath.rsplit("/", 1)
    else:
        folder_key, name = "", subpath
    parts = [p for p in folder_key.split("|") if p]
    return parts, name


def _inspector_load_config(project_dir: Path) -> dict:
    cfg_path = project_dir / "config.toml"
    if not cfg_path.is_file():
        return {}
    import toml as _toml
    return _toml.load(str(cfg_path))


def _get_anipose_root(project_dir: Path, config: dict) -> Path:
    """Return the Anipose project root from config['path'], falling back to project_dir."""
    p = config.get("path", "").strip()
    if p:
        candidate = Path(p)
        if candidate.is_dir():
            return candidate
    return project_dir


def _inspector_get_context(session: str):
    """Return (config, anipose_root, session_dir) for a session name.

    Searches DATA_DIR projects whose config['path'] (anipose_root) contains a
    subdirectory named `session`.  Falls back to treating `session` as a DLC
    project ID (for backwards compatibility).

    Raises ValueError for invalid / path-traversal inputs.
    """
    # Safety: reject any path separators in session name
    if "/" in session or "\\" in session or ".." in session:
        raise ValueError("Invalid session name.")

    # Search all DLC projects in DATA_DIR
    if DATA_DIR.is_dir():
        for project_dir in sorted(DATA_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            config = _inspector_load_config(project_dir)
            anipose_root = _get_anipose_root(project_dir, config)
            if anipose_root == project_dir:
                continue  # no 'path' field
            session_dir = anipose_root / session
            if session_dir.is_dir():
                return config, anipose_root, session_dir

    # Fall back: session is a DLC project ID
    project_dir = (DATA_DIR / session).resolve()
    if not project_dir.is_relative_to(DATA_DIR.resolve()):
        raise ValueError("Invalid session path.")
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Session not found: {session}")
    config = _inspector_load_config(project_dir)
    anipose_root = _get_anipose_root(project_dir, config)
    return config, anipose_root, anipose_root


@app.route("/metadata/<session>")
def inspector_metadata(session: str):
    """Return labeling scheme in index form for behavior inspector script."""
    try:
        config, _root, _sdir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    scheme = config.get("labeling", {}).get("scheme", [])
    video_speed = config.get("converted_video_speed", 1)

    bodyparts: list = []
    for bp_list in scheme:
        for bp in bp_list:
            if bp not in bodyparts:
                bodyparts.append(bp)
    kps = {bp: i for i, bp in enumerate(bodyparts)}
    idx_scheme = [[kps[bp] for bp in bp_list] for bp_list in scheme]

    return jsonify({"video_speed": video_speed, "scheme": idx_scheme})


@app.route("/get-trials/<session>")
def inspector_get_trials(session: str):
    """Return trial/folder structure for behavior inspector script."""
    import os as _os
    from collections import defaultdict as _dd

    try:
        config, anipose_root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    pipeline   = config.get("pipeline", {})
    videos_raw = pipeline.get("videos_raw_mp4", pipeline.get("videos_raw", "videos-raw-mp4"))
    vid_ext    = config.get("video_extension", "avi")
    vid_exts   = {f".{vid_ext.lstrip('.')}", ".mp4", ".avi", ".mov", ".mkv"}

    # behaviors.json lives at the anipose root level
    behaviors_path = anipose_root / "behaviors.json"
    behaviors: dict = {}
    if behaviors_path.is_file():
        try:
            with open(str(behaviors_path)) as _f:
                behaviors = json.load(_f)
        except (json.JSONDecodeError, OSError):
            pass

    session_behaviors_set: set = set()
    trial_behaviors: dict = {}

    folders_out = []
    for root_str, dirs, _ in _os.walk(str(session_dir)):
        dirs.sort()
        root_path  = Path(root_str)
        video_dir  = root_path / videos_raw
        if not video_dir.is_dir():
            continue

        video_files = sorted(
            [f for f in video_dir.iterdir()
             if f.is_file() and f.suffix.lower() in vid_exts],
            key=lambda p: _natural_keys(p.name),
        )
        if not video_files:
            continue

        rel = root_path.relative_to(session_dir)
        folder_key = "|".join(rel.parts) if str(rel) != "." else ""

        # Group by trial name
        trial_groups: dict = _dd(list)
        for vf in video_files:
            tname = _get_video_name(config, vf.name)
            cname = _get_cam_name(config, vf.name)
            trial_groups[tname].append({"file": vf.name, "cam": cname})

        files_out = []
        for tname in sorted(trial_groups.keys(), key=_natural_keys):
            cams = sorted(trial_groups[tname], key=lambda x: _natural_keys(x["cam"]))
            files_out.append({
                "vidname":  tname,
                "camnames": [c["cam"] for c in cams],
                "files":    [c["file"] for c in cams],
            })
            # behavior key uses session-relative folder path
            full_key = f"{session}|{folder_key}" if folder_key else session
            folder_behaviors = behaviors.get(full_key, {}).get(tname, {})
            if folder_behaviors:
                bnames = {b["behavior"] for b in folder_behaviors.values() if b.get("behavior")}
                session_behaviors_set.update(bnames)
                rel_path = f"{session}/{folder_key}/{tname}"
                trial_behaviors[rel_path] = {b: True for b in bnames}

        folders_out.append({"folder": folder_key, "files": files_out})

    folders_out.sort(key=lambda x: _natural_keys(x["folder"]))

    return jsonify({
        "session":          session,
        "folders":          folders_out,
        "sessionBehaviors": sorted(session_behaviors_set),
        "trialBehaviors":   trial_behaviors,
    })


@app.route("/pose3d/<session>/<path:subpath>")
def inspector_pose3d(session: str, subpath: str):
    """Return 3D pose as [n_frames, n_bodyparts, 3] for behavior inspector."""
    import numpy as _np
    import pandas as _pd

    try:
        config, _root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, vidname = _inspector_split_subpath(subpath)
    # Try filtered first, fall back to unfiltered
    csv_path = session_dir.joinpath(*folder_parts) / "pose-3d-filtered" / f"{vidname}.csv"
    if not csv_path.is_file():
        csv_path = session_dir.joinpath(*folder_parts) / "pose-3d" / f"{vidname}.csv"
    if not csv_path.is_file():
        return jsonify([])

    try:
        data = _pd.read_csv(str(csv_path))
    except Exception:
        return jsonify([])

    scheme     = config.get("labeling", {}).get("scheme", [])
    bodyparts  = []
    for bp_list in scheme:
        for bp in bp_list:
            if bp not in bodyparts:
                bodyparts.append(bp)

    if not bodyparts:
        bodyparts = [c[:-2] for c in data.columns if c.endswith("_x")]

    vecs = []
    for bp in bodyparts:
        cols = [f"{bp}_x", f"{bp}_y", f"{bp}_z"]
        if not all(c in data.columns for c in cols):
            continue
        vec = data[cols].to_numpy(dtype=float)
        err_col = f"{bp}_error"
        if err_col in data.columns:
            err = data[err_col].to_numpy(dtype=float)
            err[~_np.isfinite(err)] = 1000.0
            vec[err > 50] = _np.nan
        vecs.append(vec)

    if not vecs:
        return jsonify([])

    vecs = _np.array(vecs).swapaxes(0, 1)   # [n_frames, n_bps, 3]
    m    = _np.nanmean(vecs, axis=0)
    std  = float(_np.nanmedian(_np.diff(_np.nanpercentile(m, [25, 75], axis=0), axis=0)))
    if std == 0:
        std = 1.0
    vecs = 0.3 * vecs / std
    cm   = _np.nanmean(_np.nanmean(vecs, axis=1), axis=0)
    vecs = vecs - cm
    vecs[~_np.isfinite(vecs)] = 0.0

    return jsonify(vecs.tolist())


@app.route("/pose2dproj/<session>/<path:subpath>")
def inspector_pose2dproj(_session: str, _subpath: str):
    """Stub — 2D projections require aniposelib calibration (not yet integrated)."""
    return jsonify({})


@app.route("/video/<session>/<path:subpath>")
def inspector_video(session: str, subpath: str):
    """Serve a raw video file for the behavior inspector."""
    try:
        config, _root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, filename = _inspector_split_subpath(subpath)
    pipeline   = config.get("pipeline", {})
    videos_raw = pipeline.get("videos_raw_mp4", pipeline.get("videos_raw", "videos-raw-mp4"))

    video_path = session_dir.joinpath(*folder_parts) / videos_raw / filename
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    mime_map = {
        ".mp4": "video/mp4", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    }
    mimetype = mime_map.get(video_path.suffix.lower(), "application/octet-stream")
    return send_from_directory(str(video_path.parent), video_path.name,
                               mimetype=mimetype, conditional=True)


@app.route("/framerate/<session>/<path:subpath>")
def inspector_framerate(session: str, subpath: str):
    """Return video FPS as a bare number (required by inspector script)."""
    import cv2 as _cv2

    try:
        config, _root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, filename = _inspector_split_subpath(subpath)
    pipeline   = config.get("pipeline", {})
    videos_raw = pipeline.get("videos_raw_mp4", pipeline.get("videos_raw", "videos-raw-mp4"))

    video_path = session_dir.joinpath(*folder_parts) / videos_raw / filename
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    cap = _cv2.VideoCapture(str(video_path))
    fps = cap.get(_cv2.CAP_PROP_FPS)
    cap.release()
    return jsonify(fps if fps > 0 else 30.0)


@app.route("/behavior/<session>/<path:subpath>")
def inspector_behavior(session: str, subpath: str):
    """Return behavior bouts for a specific trial from behaviors.json."""
    try:
        _cfg, anipose_root, _sdir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, vidname = _inspector_split_subpath(subpath)
    full_key = f"{session}|{'|'.join(folder_parts)}" if folder_parts else session

    beh_path = anipose_root / "behaviors.json"
    if not beh_path.is_file():
        return jsonify({})

    try:
        with open(str(beh_path)) as _f:
            behaviors = json.load(_f)
    except (json.JSONDecodeError, OSError):
        return jsonify({})

    return jsonify(behaviors.get(full_key, {}).get(vidname, {}))


@app.route("/download-behavior/<session>")
def inspector_download_behavior(session: str):
    """Download the full behaviors.json for a session."""
    try:
        _cfg, anipose_root, _sdir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    beh_path = anipose_root / "behaviors.json"
    if not beh_path.is_file():
        return jsonify({})

    try:
        with open(str(beh_path)) as _f:
            behaviors = json.load(_f)
    except (json.JSONDecodeError, OSError):
        return jsonify({})

    return jsonify(behaviors)


@app.route("/unlock-editing", methods=["POST"])
def inspector_unlock_editing():
    """Validate password and issue a session token for behavior editing."""
    import string as _string
    import random as _random
    body = request.get_json(force=True) or {}
    password = body.get("password", "")
    server_pw = os.environ.get("INSPECTOR_PASSWORD", "password")
    token = -1
    if password == server_pw:
        token = "".join(_random.choices(_string.ascii_letters + "_", k=10))
        _inspector_tokens.add(token)
    valid = token in _inspector_tokens
    return jsonify({"token": token, "valid": valid})


@app.route("/get-token/<token>")
def inspector_get_token(token: str):
    """Check whether a token is still valid."""
    return jsonify({"valid": token in _inspector_tokens})


@app.route("/update-behavior", methods=["POST"])
def inspector_update_behavior():
    """Apply behavior change-log from inspector to behaviors.json."""
    from collections import defaultdict as _dd
    body   = request.get_json(force=True) or {}
    token  = body.get("token", "")
    if token not in _inspector_tokens:
        return "invalid token", 403

    changes_by_session: dict = _dd(list)
    for bout_changes in body.get("allBehaviorChanges", {}).values():
        for change in bout_changes:
            changes_by_session[change["session"]].append(change)

    for sess, changes in changes_by_session.items():
        try:
            _cfg, anipose_root, _sdir = _inspector_get_context(sess)
        except (ValueError, FileNotFoundError):
            continue

        beh_path = anipose_root / "behaviors.json"
        if beh_path.is_file():
            try:
                with open(str(beh_path)) as _f:
                    beh_dict = json.load(_f)
            except (json.JSONDecodeError, OSError):
                beh_dict = {}
        else:
            beh_dict = {}

        for change in changes:
            mod = change.get("modification")
            if mod == "added":
                bout = change["new"]
                fk, fn, bid = bout["folders"], bout["filename"], bout["bout_id"]
                beh_dict.setdefault(fk, {}).setdefault(fn, {})[bid] = bout
            elif mod == "removed":
                bout = change["old"]
                fk, fn, bid = bout["folders"], bout["filename"], bout["bout_id"]
                try:
                    del beh_dict[fk][fn][bid]
                except KeyError:
                    pass
            else:
                bout  = change["old"]
                edits = change.get("new", {})
                bout.update(edits)
                fk, fn, bid = bout["folders"], bout["filename"], bout["bout_id"]
                beh_dict.setdefault(fk, {}).setdefault(fn, {})[bid] = bout

        try:
            with open(str(beh_path), "w") as _f:
                json.dump(beh_dict, _f, indent=4)
        except OSError:
            pass

    return "behavior labels successfully updated"


# ── DLC Create Training Dataset ───────────────────────────────────

@app.route("/dlc/project/create-training-dataset", methods=["POST"])
def dlc_create_training_dataset():
    """Dispatch a Celery task to run deeplabcut.create_training_dataset()."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    body = request.get_json(force=True) or {}
    try:
        num_shuffles = int(body.get("num_shuffles", 1))
    except (TypeError, ValueError):
        num_shuffles = 1
    if num_shuffles < 1:
        num_shuffles = 1

    task = celery.send_task(
        "tasks.dlc_create_training_dataset",
        kwargs={"config_path": config_path, "num_shuffles": num_shuffles},
    )
    return jsonify({"task_id": task.id, "operation": "create_training_dataset"}), 202


@app.route("/dlc/project/pytorch-configs", methods=["GET"])
def list_dlc_pytorch_configs():
    """List all pytorch_config.yaml files found in the active DLC project."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    matches = sorted(
        project_path.glob("dlc-models/**/train/pytorch_config.yaml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    configs = [
        {"rel_path": str(m.relative_to(project_path)), "config_path": str(m)}
        for m in matches
    ]
    return jsonify({"configs": configs})


@app.route("/dlc/project/pytorch-config", methods=["GET"])
def get_dlc_pytorch_config():
    """Return the content of a pytorch_config.yaml. Query param: rel_path (optional)."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    rel_path = request.args.get("rel_path", "").strip()
    if rel_path:
        target = (project_path / rel_path).resolve()
        if not target.is_relative_to(project_path.resolve()):
            return jsonify({"error": "Invalid path."}), 400
        if not target.is_file():
            return jsonify({"error": "File not found."}), 404
    else:
        matches = sorted(
            project_path.glob("dlc-models/**/train/pytorch_config.yaml"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            return jsonify({"error": "pytorch_config.yaml not found. Run Create Training Dataset first."}), 404
        target = matches[0]

    return jsonify({
        "content":     target.read_text(),
        "config_path": str(target),
        "rel_path":    str(target.relative_to(project_path)),
    })


@app.route("/dlc/project/pytorch-config", methods=["PATCH"])
def save_dlc_pytorch_config():
    """Save edited pytorch_config.yaml. Body: { content, rel_path (optional) }."""
    raw = _redis_client.get(_DLC_PROJECT_KEY)
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _dlc_project_security_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body     = request.get_json(force=True) or {}
    content  = body.get("content", "")
    rel_path = body.get("rel_path", "").strip()

    if not content.strip():
        return jsonify({"error": "Content cannot be empty."}), 400

    if rel_path:
        target = (project_path / rel_path).resolve()
        if not target.is_relative_to(project_path.resolve()):
            return jsonify({"error": "Invalid path."}), 400
        if not target.is_file():
            return jsonify({"error": "File not found."}), 404
    else:
        matches = sorted(
            project_path.glob("dlc-models/**/train/pytorch_config.yaml"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            return jsonify({"error": "pytorch_config.yaml not found."}), 404
        target = matches[0]

    target.write_text(content)
    return jsonify({"status": "saved", "rel_path": str(target.relative_to(project_path))})


# ── Entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
