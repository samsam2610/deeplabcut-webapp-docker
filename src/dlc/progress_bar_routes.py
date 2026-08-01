"""
Progress arrow bar — Flask Blueprint.

Routes
------
GET /dlc/project/progress-bar         The project's segment/option definition.
PUT /dlc/project/progress-bar         Replace the definition (ids preserved).
PUT /dlc/project/progress-bar/value   Set/clear one segment for one file.

Per-file values are READ through GET /dlc/project/tracked-files, which carries
a `progress` object per file — one batched query instead of an N+1.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request
from flask import session as flask_session

from . import ctx as _ctx
from . import progress_bar as _store
from . import tracked_files as _tracked
from .labeling import _dlc_key, _sec_check


def _actor():
    """The Flask session uid. Identifies a browser session, not a person."""
    return flask_session.get("uid")

bp = Blueprint("dlc_progress_bar", __name__)

_ROUTE = "/dlc/project/progress-bar"


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


@bp.route(_ROUTE, methods=["GET"])
def get_progress_bar():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    try:
        return jsonify(_store.get_definition(pp))
    except sqlite3.Error as exc:
        return jsonify({"error": f"progress-bar DB error: {exc}"}), 500


@bp.route(_ROUTE, methods=["PUT"])
def put_progress_bar():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    body = request.get_json(silent=True) or {}
    segments = body.get("segments")
    if not isinstance(segments, list):
        return jsonify({"error": "segments must be a list"}), 400
    try:
        definition = _store.save_definition(pp, segments, actor=_actor())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.Error as exc:
        return jsonify({"error": f"progress-bar DB error: {exc}"}), 500
    return jsonify({"ok": True, **definition})


@bp.route(_ROUTE + "/value", methods=["PUT"])
def put_progress_value():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    body = request.get_json(silent=True) or {}
    segment_id = body.get("segment_id", "")
    if not isinstance(segment_id, str) or not segment_id.strip():
        return jsonify({"error": "segment_id required"}), 400
    option_id = body.get("option_id")
    if option_id is not None and not isinstance(option_id, str):
        return jsonify({"error": "option_id must be a string or null"}), 400

    # video_id wins; path is accepted so older clients keep working.
    vid = body.get("video_id")
    video_id = (_tracked.resolve(pp, video_id=vid.strip())
                if isinstance(vid, str) and vid.strip() else None)
    if not video_id:
        path = body.get("path")
        video_id = (_tracked.resolve(pp, path=path.strip())
                    if isinstance(path, str) and path.strip() else None)
    if not video_id:
        return jsonify({"error": "unknown video"}), 400

    try:
        _store.set_value(pp, video_id, segment_id.strip(), option_id, actor=_actor())
    except sqlite3.Error as exc:
        return jsonify({"error": f"progress-bar DB error: {exc}"}), 500
    return jsonify({"ok": True})
