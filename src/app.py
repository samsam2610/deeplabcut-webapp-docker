"""
Flask API — Anipose / DLC Processing Gateway
Bootstrap: config, init, helpers, before_request, blueprint registrations.
"""

import os
import re
import sys as _sys
import uuid
import json
import secrets
import threading as _threading
import collections as _collections
from pathlib import Path

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

try:
    import ruamel.yaml as _ruamel_yaml
    _ruamel_yaml_instance = _ruamel_yaml.YAML()
    _ruamel_yaml_instance.preserve_quotes = True
except ImportError:
    _ruamel_yaml = None
    _ruamel_yaml_instance = None

import redis as _redis
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file, Response, session as flask_session
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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# ── Redis (direct client for session storage) ─────────────────────
_REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
_redis_client = _redis.Redis.from_url(_REDIS_URL, decode_responses=True)

# ── Per-browser session identity ───────────────────────────────────
def _user_id() -> str:
    """Return a stable browser-session identifier, creating one if absent."""
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]

def _session_key() -> str:
    return f"webapp:session:{_user_id()}"

def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"

# ── Per-session VideoCapture cache ────────────────────────────────
_FE_VCAP_MAX        = 20   # max concurrent per-session captures
_fe_vcap_cache: dict = _collections.OrderedDict()  # uid → {vcap, path, pos, lock}
_fe_vcap_cache_lock = _threading.Lock()             # protects the dict itself

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

# ── DLC sub-modules ───────────────────────────────────────────────
from dlc import ctx as _dlc_ctx
from dlc import utils as _dlc_utils

# Re-export so existing tests can still access via app module
from dlc.utils import (
    _TF_ENGINE_ALIASES, _ENGINE_PYTORCH, _ENGINE_TF, _PIPELINE_BASE_FOLDERS,
    _engine_info, _get_pipeline_folders, _get_engine_queue,
    _FS_LS_MEDIA_EXTS, _FS_LS_VIDEO_EXTS, _FS_LS_IMAGE_EXTS,
    _walk_dir, _dir_has_media,
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
    return _dlc_utils._resolve_project_dir(project_id, DATA_DIR, root)


def _dlc_project_security_check(p: Path) -> bool:
    """Return True if p is inside an allowed data root."""
    return _dlc_utils._dlc_project_security_check(p, DATA_DIR, USER_DATA_DIR)


# ── Global error handler — always return JSON, never HTML ─────────
@app.errorhandler(Exception)
def handle_exception(exc):
    """Catch-all so Flask never returns an HTML traceback to the client."""
    import traceback as _tb
    app.logger.error("Unhandled exception: %s", _tb.format_exc())
    return jsonify({"error": str(exc)}), 500


@app.before_request
def _sync_dlc_ctx():
    """Keep DLC shared context in sync with app module globals."""
    _dlc_ctx.setup(DATA_DIR, USER_DATA_DIR, _redis_client, celery, _yaml, _ruamel_yaml_instance)
    # Also expose for anipose blueprints via current_app.config
    app.config["APP_DATA_DIR"]      = DATA_DIR
    app.config["APP_USER_DATA_DIR"] = USER_DATA_DIR
    app.config["APP_REDIS"]         = _redis_client
    app.config["APP_CELERY"]        = celery


# ── Register DLC Blueprints ───────────────────────────────────────
from dlc.project import bp as _dlc_project_bp
from dlc.config_routes import bp as _dlc_config_bp
from dlc.videos import bp as _dlc_videos_bp
from dlc.labeling import bp as _dlc_labeling_bp
from dlc.training import bp as _dlc_training_bp
from dlc.inference import bp as _dlc_inference_bp
from dlc.monitoring import bp as _dlc_monitoring_bp

app.register_blueprint(_dlc_project_bp)
app.register_blueprint(_dlc_config_bp)
app.register_blueprint(_dlc_videos_bp)
app.register_blueprint(_dlc_labeling_bp)
app.register_blueprint(_dlc_training_bp)
app.register_blueprint(_dlc_inference_bp)
app.register_blueprint(_dlc_monitoring_bp)

# ── Register Anipose Blueprints ───────────────────────────────────
from anipose.session import bp as _anipose_session_bp
from anipose.pipeline import bp as _anipose_pipeline_bp
from anipose.projects import bp as _anipose_projects_bp
from anipose.visualization import bp as _anipose_visualization_bp
from anipose.inspector import bp as _anipose_inspector_bp
from routes.annotate import bp as _annotate_bp
from routes.custom_script import bp as _custom_script_bp

app.register_blueprint(_anipose_session_bp)
app.register_blueprint(_anipose_pipeline_bp)
app.register_blueprint(_anipose_projects_bp)
app.register_blueprint(_anipose_visualization_bp)
app.register_blueprint(_anipose_inspector_bp)
app.register_blueprint(_annotate_bp)
app.register_blueprint(_custom_script_bp)


# ── Core Routes ───────────────────────────────────────────────────
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

    project_id = uuid.uuid4().hex[:12]
    project_dir = DATA_DIR / project_id
    videos_dir = project_dir / "videos-raw"
    videos_dir.mkdir(parents=True, exist_ok=True)

    config_dest = project_dir / "config.toml"
    config_file.save(str(config_dest))

    saved_videos = []
    for vf in video_files:
        safe_name = secure_filename(vf.filename)
        vf.save(str(videos_dir / safe_name))
        saved_videos.append(safe_name)

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
    """
    task_meta_keys = list(_redis_client.scan_iter("celery-task-meta-*"))
    if task_meta_keys:
        _redis_client.delete(*task_meta_keys)
    _redis_client.delete("celery")
    return jsonify({"deleted": len(task_meta_keys)})


@app.route("/config")
def get_config():
    """Return client-facing configuration values."""
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


@app.route("/fs/ls")
def fs_ls():
    """List a directory's immediate children for the file browser."""
    raw_path = request.args.get("path", "").strip()
    if not raw_path:
        return jsonify({"error": "path required"}), 400
    p = Path(raw_path)
    if not p.is_dir():
        return jsonify({"error": "Not a directory"}), 404
    try:
        entries = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                entries.append({"name": child.name, "type": "dir", "has_media": _dir_has_media(child)})
            else:
                entries.append({"name": child.name, "type": "file"})
        parent = str(p.parent) if str(p.parent) != str(p) else None
        return jsonify({"path": str(p), "parent": parent, "entries": entries})
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


# ── Entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
