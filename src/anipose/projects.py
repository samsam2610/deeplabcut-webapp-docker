"""
Blueprint: anipose_projects
Handles project creation, listing, file management, download, browse, upload.
"""
import io
import json
import re
import uuid
import zipfile
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file, session as flask_session
from werkzeug.utils import secure_filename

bp = Blueprint("anipose_projects", __name__)


# ── Shared-state accessors ─────────────────────────────────────────
def _data_dir() -> Path:
    return current_app.config["APP_DATA_DIR"]

def _redis():
    return current_app.config["APP_REDIS"]

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
    import re as _re
    match = _re.search(r'\[pipeline\](.*?)(?=\n\[|\Z)', config_text, _re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = _re.match(r'^(\w+)\s*=\s*"([^"]*)"', line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


# ── Routes ────────────────────────────────────────────────────────
@bp.route("/projects", methods=["POST"])
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

    base = Path(root) if root else _data_dir()
    project_dir = base / safe_name
    if project_dir.exists():
        return jsonify({"error": f"Project '{safe_name}' already exists."}), 409

    raw = _redis().get(_session_key())
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


@bp.route("/projects")
def list_projects():
    """List all project ids on the shared volume."""
    projects = sorted(
        [d.name for d in _data_dir().iterdir() if d.is_dir()],
        reverse=True,
    )
    return jsonify({"projects": projects})


@bp.route("/projects/<project_id>/file", methods=["PATCH"])
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
        project_dir = _resolve_project_dir_local(project_id, root)
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


@bp.route("/projects/<project_id>/file", methods=["DELETE"])
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

    target.unlink()
    return jsonify({"deleted": filename, "folder": folder})


@bp.route("/projects/<project_id>/download")
def download_project(project_id: str):
    """
    Stream project data as a ZIP archive.
    Optional query params:
      ?folder=<name>  limits the archive to that subfolder
      ?root=<path>    use a custom project root instead of DATA_DIR
    """
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
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


@bp.route("/projects/<project_id>/browse")
def browse_project(project_id: str):
    """
    For each pipeline folder in the active session config, list the files that
    exist under <root>/<project_id>/<folder>/ (root defaults to DATA_DIR).
    Query param: root=<absolute_path>  (optional)
    """
    raw = _redis().get(_session_key())
    if not raw:
        return jsonify({"error": "No active session."}), 400
    config_path = Path(json.loads(raw).get("config_path", ""))

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
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


@bp.route("/projects/<project_id>/upload", methods=["POST"])
def upload_to_project(project_id: str):
    """
    Upload files into <root>/<project_id>/<folder>/.
    Form fields: folder (str), files[] (one or more files), root (optional absolute path).
    """
    root = request.form.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
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
