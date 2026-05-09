"""
DLC Project Management Blueprint.

Routes:
  GET/POST/DELETE /dlc/project
  GET /dlc/project/browse
  GET/PATCH /dlc/project/config
  POST /dlc/project/upload
  DELETE/PATCH /dlc/project/file
  GET /dlc/project/download
"""
from __future__ import annotations
import io, json, re, shutil, zipfile
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, session as flask_session
from werkzeug.utils import secure_filename
from . import ctx as _ctx
from dlc.utils import (
    _engine_info, _get_pipeline_folders, _get_engine_queue,
    _walk_dir, _dir_has_media, _dlc_project_security_check,
    _resolve_project_dir,
)

bp = Blueprint("dlc_project", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


@bp.route("/dlc/project", methods=["GET"])
def get_dlc_project():
    """Return the current DLC project state."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"status": "none"}), 200
    return jsonify(json.loads(raw)), 200


@bp.route("/dlc/project", methods=["POST"])
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
    if not _sec_check(p):
        return jsonify({"error": "Access denied: path is outside allowed directories."}), 403
    if not p.is_dir():
        return jsonify({"error": f"Directory not found: {path_str}"}), 404

    has_config  = (p / "config.yaml").is_file()
    config_path = str(p / "config.yaml") if has_config else None

    # Read engine from config.yaml (default pytorch) and fix project_path if stale
    engine = "pytorch"
    _yaml = _ctx.yaml_lib()
    if has_config:
        try:
            text = (p / "config.yaml").read_text()
            if _yaml is not None:
                cfg = _yaml.safe_load(text) or {}
                engine = (cfg.get("engine") or "pytorch").lower()
                # Fix stale project_path so DLC resolves all paths to the actual location
                cfg_project_path = cfg.get("project_path", "")
                if cfg_project_path and Path(cfg_project_path).resolve() != p.resolve():
                    updated = re.sub(
                        r'^(project_path\s*:\s*).*$',
                        lambda m: m.group(1) + str(p),
                        text,
                        flags=re.MULTILINE,
                    )
                    (p / "config.yaml").write_text(updated)
            else:
                m = re.search(r'^engine\s*:\s*(\S+)', text, re.MULTILINE)
                if m:
                    engine = m.group(1).strip().strip("\"'").lower()
                # Fix stale project_path via regex fallback
                m2 = re.search(r'^(project_path\s*:\s*)(.+)$', text, re.MULTILINE)
                if m2 and m2.group(2).strip() != str(p):
                    updated = re.sub(
                        r'^(project_path\s*:\s*).*$',
                        lambda m: m.group(1) + str(p),
                        text,
                        flags=re.MULTILINE,
                    )
                    (p / "config.yaml").write_text(updated)
        except Exception:
            pass

    project_data = {
        "project_path": str(p),
        "project_name": p.name,
        "has_config":   has_config,
        "config_path":  config_path,
        "engine":       engine,
    }
    _ctx.redis_client().set(_dlc_key(), json.dumps(project_data))
    return jsonify(project_data), 200


@bp.route("/dlc/project", methods=["DELETE"])
def clear_dlc_project():
    """Clear the active DLC project session."""
    _ctx.redis_client().delete(_dlc_key())
    return jsonify({"status": "cleared"}), 200


@bp.route("/dlc/project/browse")
def browse_dlc_project():
    """List files in each DLC pipeline folder for the active DLC project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    folders = []
    for key, folder_name in _get_pipeline_folders(project_data.get("engine", "pytorch")):
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


@bp.route("/dlc/project/config", methods=["GET"])
def get_dlc_project_config():
    """Return the raw text of the active DLC project's config.yaml."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404
    return jsonify({"content": config_path.read_text(), "config_path": str(config_path)})


@bp.route("/dlc/project/config", methods=["PATCH"])
def save_dlc_project_config():
    """Overwrite the active DLC project's config.yaml."""
    raw = _ctx.redis_client().get(_dlc_key())
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


# ── Common-typo repair for DLC config.yaml ────────────────────────
# Pattern catches the most common DLC config corruption: a video path
# that got soft-wrapped across two lines under `video_sets:`. The artifact
# looks like:
#
#   video_sets:
#     /user-data/.../RatBox<SP>
#         Videos/.../foo.avi:
#       crop: 0, 1376, 0, 900
#
# The path key is split across two lines (a literal newline mid-path).
# YAML's scanner reads the continuation as a child mapping and dies with
# "mapping values are not allowed here" — every DLC operation that loads
# the config (train, analyze, machine-label) then fails.
#
# Fix: rejoin the path onto one line. Matches a line under video_sets
# (≥2-space indent, starts with `/`, ends with whitespace + newline)
# followed by a deeper-indented line that ends in `:` (so we don't
# accidentally swallow a `crop:` value line).
_BROKEN_PATH_RE = _re_compile_path_break = __import__("re").compile(
    r"^(  /\S[^\n]*?) +\n {4,}(\S[^\n]*?:\s*)$",
    __import__("re").MULTILINE,
)


def _repair_dlc_config_text(text: str) -> tuple[str, int]:
    """Apply known config.yaml repairs in-place. Returns (new_text, n_fixes)."""
    new_text, n = _BROKEN_PATH_RE.subn(r"\1 \2", text)
    return new_text, n


@bp.route("/dlc/project/config/repair", methods=["POST"])
def repair_dlc_project_config():
    """Detect and fix common DLC config.yaml corruption (multi-line video paths).

    Workflow:
      1. Read the active project's config.yaml from disk.
      2. Apply known repairs (currently: rejoin video paths broken across lines).
      3. If anything changed, back up the original to config.yaml.bak.<ts>
         and write the repaired content.
      4. Try to parse the (possibly repaired) YAML and report the result.

    Response:
      { repaired: bool, n_fixes: int, parse_ok: bool, parse_error?: str,
        backup_path?: str, content: <new file text> }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    original = config_path.read_text()
    repaired, n_fixes = _repair_dlc_config_text(original)

    backup_path = None
    if n_fixes > 0:
        from datetime import datetime as _dt
        backup_path = config_path.with_suffix(
            f".yaml.bak.{_dt.now().strftime('%Y%m%d-%H%M%S')}"
        )
        backup_path.write_text(original)
        config_path.write_text(repaired)

    # Probe the (possibly repaired) file with a strict YAML parse so the
    # caller knows whether the file is now loadable.
    try:
        import yaml as _yaml
        _yaml.safe_load(repaired)
        parse_ok, parse_error = True, None
    except Exception as e:
        parse_ok, parse_error = False, str(e)[:600]

    return jsonify({
        "repaired":    n_fixes > 0,
        "n_fixes":     n_fixes,
        "parse_ok":    parse_ok,
        "parse_error": parse_error,
        "backup_path": str(backup_path) if backup_path else None,
        "content":     repaired,
    })


@bp.route("/dlc/project/upload", methods=["POST"])
def dlc_project_upload():
    """
    Upload files into a DLC pipeline folder of the active project.
    Form fields: folder (one of the DLC pipeline folders), files[]
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    folder_name = request.form.get("folder", "").strip()
    dlc_folder_names = [f for _, f in _get_pipeline_folders(project_data.get("engine", "pytorch"))]
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


@bp.route("/dlc/project/file", methods=["DELETE"])
def dlc_project_delete_file():
    """Delete a file anywhere inside the active DLC project. Body: { rel_path }"""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    body     = request.get_json(force=True) or {}
    rel_path = body.get("rel_path", "").strip()
    if not rel_path:
        return jsonify({"error": "rel_path is required."}), 400

    # Must be inside a top-level pipeline folder
    dlc_folder_names = [f for _, f in _get_pipeline_folders(project_data.get("engine", "pytorch"))]
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


@bp.route("/dlc/project/file", methods=["PATCH"])
def dlc_project_rename_file():
    """Rename a file anywhere inside the active DLC project. Body: { rel_path, new_name }"""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    body     = request.get_json(force=True) or {}
    rel_path = body.get("rel_path", "").strip()
    new_name = body.get("new_name", "").strip()

    if not rel_path or not new_name:
        return jsonify({"error": "rel_path and new_name are required."}), 400

    dlc_folder_names = [f for _, f in _get_pipeline_folders(project_data.get("engine", "pytorch"))]
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


@bp.route("/dlc/project/download")
def dlc_project_download():
    """
    Download a DLC pipeline folder (or the whole project) as a ZIP.
    Query param: folder=<dlc_folder_name>  (optional; downloads all if omitted)
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    project_name = project_data.get("project_name", "dlc-project")

    folder_name = request.args.get("folder", "").strip()
    if folder_name:
        dlc_folder_names = [f for _, f in _get_pipeline_folders(project_data.get("engine", "pytorch"))]
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
