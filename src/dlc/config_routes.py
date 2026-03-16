"""
DLC Config Routes Blueprint.

Routes:
  POST/GET/PATCH/DELETE /session/dlc-config
  POST /session/dlc-config/from-path
  GET /dlc/project/engine
  GET/PATCH /dlc/project/pytorch-config(s)
"""
from __future__ import annotations
import json, shutil
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, session as flask_session
from werkzeug.utils import secure_filename
import dlc_ctx as _ctx
from dlc.utils import (
    _engine_info,
    _dlc_project_security_check,
)

bp = Blueprint("dlc_config", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _session_key() -> str:
    return f"webapp:session:{_user_id()}"


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


ALLOWED_YAML_EXT = {".yaml", ".yml"}


@bp.route("/session/dlc-config", methods=["POST"])
def upload_dlc_config():
    """
    Upload a DeepLabCut config.yaml and attach its path to the active session.
    Stores the file alongside config.toml in the session directory.
    """
    raw = _ctx.redis_client().get(_session_key())
    if not raw:
        return jsonify({"error": "No active session."}), 400

    config_file = request.files.get("config")
    if not config_file or not config_file.filename:
        return jsonify({"error": "A config.yaml file is required."}), 400
    if Path(config_file.filename).suffix.lower() not in ALLOWED_YAML_EXT:
        return jsonify({"error": "DLC config must be a .yaml or .yml file."}), 400

    session_data = json.loads(raw)
    session_dir  = Path(session_data.get("config_path", "")).parent
    if not session_dir.is_dir():
        return jsonify({"error": "Session directory not found."}), 400

    dlc_config_path = session_dir / "config.yaml"
    config_file.save(str(dlc_config_path))

    session_data["dlc_config_path"] = str(dlc_config_path)
    session_data["dlc_config_name"] = secure_filename(config_file.filename)
    _ctx.redis_client().set(_session_key(), json.dumps(session_data))

    return jsonify({
        "dlc_config_path": str(dlc_config_path),
        "dlc_config_name": session_data["dlc_config_name"],
    }), 201


@bp.route("/session/dlc-config", methods=["GET"])
def get_dlc_config():
    """Return the raw text of the active session's DLC config.yaml."""
    raw = _ctx.redis_client().get(_session_key())
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


@bp.route("/session/dlc-config", methods=["PATCH"])
def save_dlc_config():
    """Save edited DLC config.yaml content back to disk."""
    raw = _ctx.redis_client().get(_session_key())
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


@bp.route("/session/dlc-config", methods=["DELETE"])
def clear_dlc_config():
    """Remove DLC config association from the active session (file stays on disk)."""
    raw = _ctx.redis_client().get(_session_key())
    if not raw:
        return jsonify({"error": "No active session."}), 400
    session_data = json.loads(raw)
    session_data.pop("dlc_config_path", None)
    session_data.pop("dlc_config_name", None)
    _ctx.redis_client().set(_session_key(), json.dumps(session_data))
    return jsonify({"status": "cleared"})


@bp.route("/session/dlc-config/from-path", methods=["POST"])
def load_dlc_config_from_path():
    """
    Attach a server-side config.yaml to the active session without re-uploading.
    The file is copied into the session directory.
    Body: { "config_path": "<absolute_server_path>" }
    """
    raw = _ctx.redis_client().get(_session_key())
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
    allowed_roots = [_ctx.data_dir().resolve(), _ctx.user_data_dir().resolve()]
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
    _ctx.redis_client().set(_session_key(), json.dumps(session_data))

    return jsonify({
        "dlc_config_path": str(dlc_config_path),
        "dlc_config_name": session_data["dlc_config_name"],
    }), 201


@bp.route("/dlc/project/engine", methods=["GET"])
def get_dlc_project_engine():
    """
    Read the 'engine' field from the active DLC project's config.yaml.
    Returns { "engine": "pytorch" | "tensorflow" }.
    Defaults to "pytorch" when the field is absent.
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    _yaml = _ctx.yaml_lib()
    try:
        if _yaml is None:
            return jsonify({"error": "PyYAML not installed."}), 500
        with open(config_path) as f:
            cfg = _yaml.safe_load(f)
        engine = (cfg.get("engine") or "pytorch").lower()
    except Exception as exc:
        return jsonify({"error": f"Failed to parse config.yaml: {exc}"}), 500

    return jsonify({"engine": engine})


@bp.route("/dlc/project/pytorch-configs", methods=["GET"])
def list_dlc_pytorch_configs():
    """List all pytorch_config.yaml files found in the active DLC project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    models_folder, model_cfg_file, _ = _engine_info(project_data.get("engine", "pytorch"))
    matches = sorted(
        project_path.glob(f"{models_folder}/**/train/{model_cfg_file}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    configs = [
        {"rel_path": str(m.relative_to(project_path)), "config_path": str(m)}
        for m in matches
    ]
    return jsonify({"configs": configs})


@bp.route("/dlc/project/pytorch-config", methods=["GET"])
def get_dlc_pytorch_config():
    """Return the content of a pytorch_config.yaml. Query param: rel_path (optional)."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    rel_path = request.args.get("rel_path", "").strip()
    if rel_path:
        target = (project_path / rel_path).resolve()
        if not target.is_relative_to(project_path.resolve()):
            return jsonify({"error": "Invalid path."}), 400
        if not target.is_file():
            return jsonify({"error": "File not found."}), 404
    else:
        models_folder, model_cfg_file, _ = _engine_info(project_data.get("engine", "pytorch"))
        matches = sorted(
            project_path.glob(f"{models_folder}/**/train/{model_cfg_file}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            return jsonify({"error": f"{model_cfg_file} not found. Run Create Training Dataset first."}), 404
        target = matches[0]

    return jsonify({
        "content":     target.read_text(),
        "config_path": str(target),
        "rel_path":    str(target.relative_to(project_path)),
    })


@bp.route("/dlc/project/pytorch-config", methods=["PATCH"])
def save_dlc_pytorch_config():
    """Save edited pytorch_config.yaml. Body: { content, rel_path (optional) }."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
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
        models_folder, model_cfg_file, _ = _engine_info(project_data.get("engine", "pytorch"))
        matches = sorted(
            project_path.glob(f"{models_folder}/**/train/{model_cfg_file}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            return jsonify({"error": f"{model_cfg_file} not found."}), 404
        target = matches[0]

    target.write_text(content)
    return jsonify({"status": "saved", "rel_path": str(target.relative_to(project_path))})
