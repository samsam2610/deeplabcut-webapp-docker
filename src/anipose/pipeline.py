"""
Blueprint: anipose_pipeline
Handles pipeline operations (run, session/pipeline, detect-frame-dims).
"""
import json
import re
import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session as flask_session

bp = Blueprint("anipose_pipeline", __name__)


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


# ── Operation dispatch maps ───────────────────────────────────────
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


# ── Routes ────────────────────────────────────────────────────────
@bp.route("/run", methods=["POST"])
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
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project folder not found: '{project_id}'."}), 400

    raw = _redis().get(_session_key())
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

    task = _celery().send_task(_OPERATION_TASKS[operation], kwargs=task_kwargs)
    return jsonify({
        "task_id":    task.id,
        "operation":  operation,
        "project_id": project_id,
    }), 202


@bp.route("/session/pipeline")
def get_pipeline_structure():
    """
    Parse the [pipeline] section of the active session's config.toml and return
    a deduplicated, ordered list of {key, folder} objects.
    """
    raw = _redis().get(_session_key())
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


@bp.route("/projects/<project_id>/detect-frame-dims", methods=["POST"])
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
        project_dir = _resolve_project_dir_local(project_id, root)
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
