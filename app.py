"""
Flask API — Anipose / DLC Processing Gateway
Handles multi-file uploads and dispatches long-running tasks to Celery.
"""

import os
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
def create_session():
    """
    Upload a config.toml to start a persistent DLC session.
    Saves the config to the shared volume and dispatches an init task
    on the worker that imports DeepLabCut and verifies the file is readable.
    """
    config_file = request.files.get("config")
    if not config_file or not config_file.filename:
        return jsonify({"error": "A config.toml file is required."}), 400
    if not _valid_ext(config_file.filename, ALLOWED_CONFIG_EXT):
        return jsonify({"error": "Config must be a .toml file."}), 400

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
        "tasks.init_session",
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
        session_data["dlc_version"] = (result.result or {}).get("dlc_version", "")
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
