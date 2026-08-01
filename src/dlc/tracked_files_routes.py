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
from flask import session as flask_session

from . import ctx as _ctx
from . import progress_bar as _progress
from . import tracked_db as _db
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


def _actor():
    """The Flask session uid. Identifies a browser session, not a person."""
    return flask_session.get("uid")


def _probe(path: str):
    """(size_bytes, frame_count), best effort — (None, None) on any failure.

    Reads only the container header, never the 12-16 GB payload.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return None, None
        size = p.stat().st_size
        import cv2
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return None, None
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        return (size, frames) if frames > 0 else (None, None)
    except Exception:
        return None, None


def _resolve_video(pp, body):
    """video_id from {video_id} (preferred) or {path}. None when unresolvable."""
    vid = body.get("video_id")
    if isinstance(vid, str) and vid.strip():
        return _store.resolve(pp, video_id=vid.strip())
    path = body.get("path")
    if isinstance(path, str) and path.strip():
        return _store.resolve(pp, path=path.strip())
    return None


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
    ids = [r["video_id"] for r in rows]
    try:
        values = _progress.get_values(pp, ids)
    except sqlite3.Error:
        values = {}          # progress is decorative here; never fail the listing
    files = [
        {
            "video_id": r["video_id"],
            "path": r["path"],
            "name": Path(r["path"]).name,
            "dir": str(Path(r["path"]).parent),
            "tracked_at": r["tracked_at"],
            "last_opened_at": r["last_opened_at"],
            "progress": values.get(r["video_id"], {}),
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
    size_bytes, frame_count = _probe(path)
    try:
        video_id = _store.track(pp, path, actor=_actor(),
                                size_bytes=size_bytes, frame_count=frame_count)
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    return jsonify({"ok": True, "tracked": True, "video_id": video_id})


@bp.route(_ROUTE, methods=["DELETE"])
def untrack_file():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    body = request.get_json(silent=True) or {}
    video_id = _resolve_video(pp, body)
    if not video_id:
        return jsonify({"error": "unknown video"}), 400
    try:
        _store.untrack(pp, video_id, actor=_actor())
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    return jsonify({"ok": True, "tracked": False})


@bp.route(_ROUTE + "/opened", methods=["POST"])
def mark_opened():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    body = request.get_json(silent=True) or {}
    video_id = _resolve_video(pp, body)
    if not video_id:
        return jsonify({"error": "unknown video"}), 400
    try:
        with _db.connect(pp) as conn:
            row = _db.video_row(conn, video_id)
        # The client only sends this after a successful open, so the file is
        # known to exist — a good moment to backfill metrics we lacked earlier.
        size_bytes, frame_count = _probe(row["path"]) if row else (None, None)
        _store.touch_opened(pp, video_id, actor=_actor(),
                            size_bytes=size_bytes, frame_count=frame_count)
    except sqlite3.Error as exc:
        return jsonify({"error": f"tracked-files DB error: {exc}"}), 500
    return jsonify({"ok": True})
