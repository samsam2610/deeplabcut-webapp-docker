"""
Blueprint: anipose_session
Handles Anipose session lifecycle (create, read, delete, config).
"""
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from celery.result import AsyncResult
from flask import Blueprint, current_app, jsonify, request, session as flask_session
from werkzeug.utils import secure_filename

bp = Blueprint("anipose_session", __name__)

ALLOWED_CONFIG_EXT = {".toml"}


# ── Shared-state accessors ─────────────────────────────────────────
def _data_dir() -> Path:
    return current_app.config["APP_DATA_DIR"]

def _redis():
    return current_app.config["APP_REDIS"]

def _celery():
    return current_app.config["APP_CELERY"]

def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]

def _session_key() -> str:
    return f"webapp:session:{_user_id()}"


def _resolve_project_dir_local(project_id: str, root: str = "") -> Path:
    base = Path(root) if root else _data_dir()
    project_dir = (base / project_id).resolve()
    if not project_dir.is_relative_to(base.resolve()):
        raise ValueError("Invalid project path.")
    return project_dir


# ── Session helper ────────────────────────────────────────────────
def _clear_session_data():
    """Helper: revoke pending init task, delete config dir, remove Redis key."""
    r = _redis()
    raw = r.get(_session_key())
    if not raw:
        return
    session_data = json.loads(raw)
    _celery().control.revoke(session_data.get("task_id", ""), terminate=True)
    config_path = Path(session_data.get("config_path", ""))
    if config_path.parent.exists() and config_path.parent.name.startswith("session_"):
        shutil.rmtree(str(config_path.parent), ignore_errors=True)
    r.delete(_session_key())


# ── Routes ────────────────────────────────────────────────────────
@bp.route("/session", methods=["POST"])
def create_anipose_session():
    """
    Upload a config.toml to start a persistent anipose session.
    Saves the config to the shared volume and dispatches an init task
    on the worker that imports Anipose and verifies the file is readable.
    """
    config_file = request.files.get("config")
    if not config_file or not config_file.filename:
        return jsonify({"error": "A config.toml file is required."}), 400
    if Path(config_file.filename).suffix.lower() not in ALLOWED_CONFIG_EXT:
        return jsonify({"error": "Config must be a .toml file."}), 400

    try:
        _clear_session_data()

        session_id = uuid.uuid4().hex[:12]
        session_dir = _data_dir() / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        config_path = session_dir / "config.toml"
        config_file.save(str(config_path))

        task = _celery().send_task(
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
        _redis().set(_session_key(), json.dumps(session_data))
        return jsonify(session_data), 201

    except Exception as exc:
        current_app.logger.exception("Session creation failed")
        return jsonify({"error": str(exc)}), 500


@bp.route("/session", methods=["GET"])
def get_session():
    """Return current session state, refreshing status from the Celery backend."""
    raw = _redis().get(_session_key())
    if not raw:
        return jsonify({"status": "none"}), 200

    session_data = json.loads(raw)

    result = AsyncResult(session_data["task_id"], app=_celery())
    if result.state == "SUCCESS":
        session_data["status"] = "ready"
        session_data["anipose_version"] = (result.result or {}).get("anipose_version", "")
    elif result.state == "FAILURE":
        session_data["status"] = "error"
        session_data["error"] = str(result.info)
    elif result.state in ("STARTED", "PROGRESS", "PENDING"):
        session_data["status"] = "initializing"

    _redis().set(_session_key(), json.dumps(session_data))
    return jsonify(session_data), 200


@bp.route("/session", methods=["DELETE"])
def clear_session():
    """Kill the init task, remove stored config, and wipe the session."""
    _clear_session_data()
    return jsonify({"status": "cleared"}), 200


@bp.route("/session/config", methods=["GET"])
def get_session_config():
    """Return the raw text of the active session's config.toml."""
    raw = _redis().get(_session_key())
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))
    if not config_path.is_file():
        return jsonify({"error": "Config file not found on disk."}), 404
    return jsonify({"content": config_path.read_text(), "config_path": str(config_path)})


@bp.route("/session/config", methods=["POST"])
def save_session_config():
    """Overwrite the active session's config.toml with new content."""
    raw = _redis().get(_session_key())
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


@bp.route("/fs/list-configs")
def fs_list_configs():
    """
    List config files and immediate subdirectories at a server-side path.
    Only accepts paths within USER_DATA_DIR or DATA_DIR.
    Query params:
      path=<absolute_path>
      ext=<.toml|.yaml|.yml>  (default .toml; .yaml also matches .yml)
    """
    user_data_dir = current_app.config.get("APP_USER_DATA_DIR",
                                            Path("/user-data"))
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
    allowed_roots = [_data_dir().resolve(), Path(user_data_dir).resolve()]
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


@bp.route("/session/from-path", methods=["POST"])
def create_session_from_server_path():
    """
    Create a new session from a server-side config.toml.
    Body: { "config_path": "<absolute_server_path_to_config.toml>" }
    """
    user_data_dir = current_app.config.get("APP_USER_DATA_DIR",
                                            Path("/user-data"))
    body = request.get_json(force=True) or {}
    config_path_str = body.get("config_path", "").strip()
    if not config_path_str:
        return jsonify({"error": "config_path is required."}), 400

    config_path = Path(config_path_str).resolve()

    if config_path.suffix.lower() != ".toml":
        return jsonify({"error": "config_path must point to a .toml file."}), 400
    if not config_path.is_file():
        return jsonify({"error": f"File not found: {config_path_str}"}), 404

    allowed_roots = [_data_dir().resolve(), Path(user_data_dir).resolve()]
    if not any(str(config_path).startswith(str(r) + "/") or config_path == r
               for r in allowed_roots):
        return jsonify({"error": "Access denied: path is outside allowed directories."}), 403

    try:
        _clear_session_data()

        session_id  = uuid.uuid4().hex[:12]
        session_dir = _data_dir() / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        dest_config = session_dir / "config.toml"
        shutil.copy2(str(config_path), str(dest_config))

        task = _celery().send_task(
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
        _redis().set(_session_key(), json.dumps(session_data))
        return jsonify(session_data), 201

    except Exception as exc:
        current_app.logger.exception("Session from-path creation failed")
        return jsonify({"error": str(exc)}), 500
