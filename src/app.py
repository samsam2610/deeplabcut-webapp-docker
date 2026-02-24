"""
Flask API — Anipose / DLC Processing Gateway
Handles multi-file uploads and dispatches long-running tasks to Celery.
"""

import os
import re
import uuid
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

import redis as _redis
from flask import Flask, request, jsonify, render_template, send_from_directory
from celery import Celery
from celery.result import AsyncResult
from werkzeug.utils import secure_filename

# ── Configuration ─────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}
ALLOWED_CONFIG_EXT = {".toml"}

app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates",
)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB max upload

# ── Redis (direct client for session storage) ─────────────────────
_REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
_redis_client = _redis.Redis.from_url(_REDIS_URL, decode_responses=True)
_SESSION_KEY = "webapp:session"

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
    "calibrate":   "tasks.process_calibrate",
    "filter_2d":   "tasks.process_filter_2d",
    "triangulate": "tasks.process_triangulate",
    "filter_3d":   "tasks.process_filter_3d",
}


@app.route("/run", methods=["POST"])
def run_operation():
    """
    Dispatch one of the four single-step Anipose operations against a project
    folder, using the config.toml stored in the active session.

    Expects JSON body:
      { "operation": "calibrate|filter_2d|triangulate|filter_3d",
        "project_id": "<folder name under DATA_DIR>" }
    Returns { "task_id", "operation", "project_id" } immediately (202).
    """
    body = request.get_json(force=True) or {}
    operation  = body.get("operation", "").lower()
    project_id = body.get("project_id", "").strip()

    if operation not in _OPERATION_TASKS:
        return jsonify({"error": f"Unknown operation '{operation}'."}), 400
    if not project_id:
        return jsonify({"error": "project_id is required."}), 400

    project_dir = DATA_DIR / project_id
    if not project_dir.is_dir():
        return jsonify({"error": f"Project folder not found: '{project_id}'."}), 400

    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session. Create a session first."}), 400
    config_path = json.loads(raw).get("config_path", "")

    task = celery.send_task(
        _OPERATION_TASKS[operation],
        kwargs={"session_path": str(project_dir), "config_path": config_path},
    )
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
    exist under DATA_DIR/<project_id>/<folder>/.
    """
    raw = _redis_client.get(_SESSION_KEY)
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))

    project_dir = DATA_DIR / project_id
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
    Upload files into DATA_DIR/<project_id>/<folder>/.
    Form fields: folder (str), files[] (one or more files).
    """
    project_dir = DATA_DIR / project_id
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


@app.route("/projects", methods=["POST"])
def create_project():
    """
    Create a new project directory and auto-create every pipeline subfolder
    defined in the active session's config.toml.
    Body: { "name": "<project_name>" }
    """
    body = request.get_json(force=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "Project name is required."}), 400

    safe_name = re.sub(r"[^\w\-.]", "_", name)
    if not safe_name:
        return jsonify({"error": "Invalid project name."}), 400

    project_dir = DATA_DIR / safe_name
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


@app.route("/projects")
def list_projects():
    """List all project ids on the shared volume."""
    projects = sorted(
        [d.name for d in DATA_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )
    return jsonify({"projects": projects})


# ── Static files (CSS / JS) ──────────────────────────────────────
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


# ── Entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
