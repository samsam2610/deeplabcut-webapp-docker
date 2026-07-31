"""
Tracked video files — Flask Blueprint.

Routes
------
GET    /dlc/project/tracked-files          List tracked videos for the active project.
POST   /dlc/project/tracked-files          Track one absolute video path.
DELETE /dlc/project/tracked-files          Untrack one absolute video path.
POST   /dlc/project/tracked-files/opened   Stamp last_opened_at on an existing row.

Lives in its own module rather than in inline_analysis.py (already 1000+ lines).
No route stats a tracked path: a vanished file is discovered when the user
tries to open it, not while listing. A bulk "verify these still exist" sweep
belongs to the planned file-management module.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request

from . import ctx as _ctx
from . import tracked_files as _store
from .labeling import _dlc_key, _sec_check

bp = Blueprint("dlc_tracked_files", __name__)

_ROUTE = "/dlc/project/tracked-files"

# Same set as _IA_VIDEO_EXTS in dlc-3D's inline_analysis_3d.js, so the server
# never rejects something the browse list offered.
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}


def _project_path_checked() -> tuple:
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, "No active DLC project."
    try:
        project = json.loads(raw)
    except (TypeError, ValueError):
        return None, "Active project state is unreadable."
    pp = Path(project.get("project_path", ""))
    if not pp.is_dir():
        return None, "Project directory not found."
    if not _sec_check(pp):
        return None, "Access denied."
    return pp, None


def _video_path_checked() -> tuple:
    body = request.get_json(silent=True) or {}
    raw = body.get("path", "")
    if not isinstance(raw, str) or not raw.strip():
        return None, "path required"
    path = raw.strip()
    if not path.startswith("/"):
        return None, "path must be absolute"
    if Path(path).suffix.lower() not in VIDEO_EXTS:
        return None, "not a video file"
    return path, None


@bp.route(_ROUTE, methods=["GET"])
def list_tracked_files():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    try:
        rows = _store.list_tracked(pp)
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    files = [
        {
            "path": r["path"],
            "name": Path(r["path"]).name,
            "dir": str(Path(r["path"]).parent),
            "tracked_at": r["tracked_at"],
            "last_opened_at": r["last_opened_at"],
        }
        for r in rows
    ]
    return jsonify({"files": files})


@bp.route(_ROUTE, methods=["POST"])
def track_file():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    path, perr = _video_path_checked()
    if perr:
        return jsonify({"error": perr}), 400
    try:
        _store.track(pp, path)
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    return jsonify({"ok": True, "tracked": True})


@bp.route(_ROUTE, methods=["DELETE"])
def untrack_file():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    path, perr = _video_path_checked()
    if perr:
        return jsonify({"error": perr}), 400
    try:
        _store.untrack(pp, path)
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    return jsonify({"ok": True, "tracked": False})


@bp.route(_ROUTE + "/opened", methods=["POST"])
def mark_opened():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    path, perr = _video_path_checked()
    if perr:
        return jsonify({"error": perr}), 400
    try:
        _store.touch_opened(pp, path)
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    return jsonify({"ok": True})
