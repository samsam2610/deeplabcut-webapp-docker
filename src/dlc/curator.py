"""
DLC Dataset Curator Blueprint — active-learning helpers for the video viewer.

Routes:
  POST /dlc/curator/extract-frame
      Extract the current video frame as a lossless PNG into labeled-data/.
      Body: { "video_path": "<abs>", "frame_number": <int>
              [, "video_name": "<project-relative filename>"] }
      Returns: { saved, folder, abs_path, frame_count, duplicate }

  POST /dlc/curator/add-to-dataset
      Extract frame + create/update a CollectedData CSV/H5 entry (NaN coords).
      Body: same as extract-frame, plus optional "coords": {bp: [x, y]}
      Returns: { saved, frame_count, csv_path, h5_path, csv_updated, h5_updated }

  POST /dlc/curator/save-annotation
      Write corrected marker coordinates for an already-extracted frame.
      Extracts the frame first if the PNG is not yet in labeled-data/.
      Body: { "video_stem": "<str>", "frame_name": "<img????-?????.png>",
              "coords": { "bp_name": [x, y], ... }
              [, "video_path": "<abs>", "frame_number": <int>] }
      Returns: { csv_path, h5_path, csv_updated, h5_updated }
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, session as flask_session
from werkzeug.utils import secure_filename

from . import ctx as _ctx
from dlc.utils import _dlc_project_security_check

bp = Blueprint("dlc_curator", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _get_project() -> tuple[dict | None, str | None]:
    """Return (project_data, error_message) for the active DLC project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, "No active DLC project."
    return json.loads(raw), None


def _parse_config(project_data: dict) -> dict:
    """Parse config.yaml; returns dict with at least 'scorer' and 'bodyparts'."""
    import re
    config_path = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return {"scorer": "User", "bodyparts": []}
    _yaml = _ctx.yaml_lib()
    text  = config_path.read_text()
    if _yaml is not None:
        try:
            return _yaml.safe_load(text) or {}
        except Exception:
            pass
    # Regex fallback
    result = {}
    m = re.search(r'^scorer\s*:\s*(.+)$', text, re.MULTILINE)
    if m:
        result["scorer"] = m.group(1).strip().strip("\"'")
    m = re.search(r'^bodyparts\s*:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)', text, re.MULTILINE)
    if m:
        result["bodyparts"] = [
            item.strip().strip("\"'")
            for item in re.findall(r'^[ \t]*-[ \t]*(.+)$', m.group(1), re.MULTILINE)
        ]
    return result


def _resolve_video_path(body: dict, project_data: dict) -> Path | None:
    """
    Return the absolute video path from request body.

    Priority:
      1. body["video_path"] — absolute path (browse-video mode)
      2. body["video_name"] — project-relative name (project-video mode)
    """
    abs_path = (body.get("video_path") or "").strip()
    if abs_path:
        p = Path(abs_path)
        return p if p.is_file() else None

    rel_name = (body.get("video_name") or "").strip()
    if rel_name:
        project_path = Path(project_data.get("project_path", ""))
        safe_name    = secure_filename(rel_name)
        p = project_path / "videos" / safe_name
        return p if p.is_file() else None

    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/dlc/curator/extract-frame", methods=["POST"])
def curator_extract_frame():
    """
    Extract the current frame from the viewer's video as a lossless PNG.

    The PNG is saved to labeled-data/<video_stem>/ inside the active project.
    """
    from dlc_dataset_curator import extract_frame_as_png

    project_data, err = _get_project()
    if err:
        return jsonify({"error": err}), 400

    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body         = request.get_json(force=True) or {}
    frame_number = body.get("frame_number")
    if frame_number is None:
        return jsonify({"error": "frame_number is required."}), 400
    try:
        frame_number = int(frame_number)
    except (TypeError, ValueError):
        return jsonify({"error": "frame_number must be an integer."}), 400

    video_path = _resolve_video_path(body, project_data)
    if video_path is None:
        return jsonify({"error": "video_path or video_name is required (and file must exist)."}), 400
    if not _sec_check(video_path.parent):
        return jsonify({"error": "Access denied to video path."}), 403

    video_stem  = video_path.stem
    labeled_dir = project_path / "labeled-data" / video_stem
    labeled_dir.mkdir(parents=True, exist_ok=True)

    try:
        saved_path, is_dup = extract_frame_as_png(
            video_path   = str(video_path),
            frame_number = frame_number,
            output_dir   = labeled_dir,
        )
    except Exception as exc:
        return jsonify({"error": f"Frame extraction failed: {exc}"}), 500

    frame_count = len([f for f in labeled_dir.iterdir() if f.suffix == ".png"])

    return jsonify({
        "saved":       saved_path.name,
        "folder":      f"labeled-data/{video_stem}",
        "abs_path":    str(saved_path),
        "frame_count": frame_count,
        "duplicate":   is_dup,
        "video_stem":  video_stem,
    }), (200 if is_dup else 201)


@bp.route("/dlc/curator/add-to-dataset", methods=["POST"])
def curator_add_to_dataset():
    """
    Extract the current frame and register it in CollectedData_<scorer>.csv/.h5.

    Optional "coords" dict maps bodypart names to [x, y] pairs.  Omitted
    bodyparts are stored as NaN (ready for manual labeling in the label tool).
    """
    from dlc_dataset_curator import extract_frame_as_png, append_frame_to_dataset

    project_data, err = _get_project()
    if err:
        return jsonify({"error": err}), 400

    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    cfg        = _parse_config(project_data)
    scorer     = cfg.get("scorer", "User")
    bodyparts  = cfg.get("bodyparts", [])

    body         = request.get_json(force=True) or {}
    frame_number = body.get("frame_number")
    if frame_number is None:
        return jsonify({"error": "frame_number is required."}), 400
    try:
        frame_number = int(frame_number)
    except (TypeError, ValueError):
        return jsonify({"error": "frame_number must be an integer."}), 400

    video_path = _resolve_video_path(body, project_data)
    if video_path is None:
        return jsonify({"error": "video_path or video_name is required (and file must exist)."}), 400
    if not _sec_check(video_path.parent):
        return jsonify({"error": "Access denied to video path."}), 403

    # Optional caller-supplied coords (e.g. from the kinematic overlay)
    coords_raw = body.get("coords")   # {bp: [x, y]} or None
    coords: dict | None = None
    if isinstance(coords_raw, dict):
        coords = {
            bp: pt
            for bp, pt in coords_raw.items()
            if isinstance(pt, list) and len(pt) == 2
            and pt[0] is not None and pt[1] is not None
        }

    video_stem  = video_path.stem
    labeled_dir = project_path / "labeled-data" / video_stem
    labeled_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Extract PNG ────────────────────────────────────────────
    try:
        saved_path, is_dup = extract_frame_as_png(
            video_path   = str(video_path),
            frame_number = frame_number,
            output_dir   = labeled_dir,
        )
    except Exception as exc:
        return jsonify({"error": f"Frame extraction failed: {exc}"}), 500

    frame_name  = saved_path.name
    frame_count = len([f for f in labeled_dir.iterdir() if f.suffix == ".png"])

    # ── Step 2: Append to CollectedData CSV/H5 ────────────────────────
    if not bodyparts:
        return jsonify({
            "saved":        frame_name,
            "frame_count":  frame_count,
            "duplicate":    is_dup,
            "video_stem":   video_stem,
            "csv_updated":  False,
            "h5_updated":   False,
            "warning":      "No bodyparts in project config — CSV/H5 not updated.",
        }), 201

    try:
        csv_path, h5_path = append_frame_to_dataset(
            stem_dir   = labeled_dir,
            video_stem = video_stem,
            frame_name = frame_name,
            scorer     = scorer,
            bodyparts  = bodyparts,
            coords     = coords,
        )
    except Exception as exc:
        return jsonify({"error": f"Dataset append failed: {exc}"}), 500

    return jsonify({
        "saved":       frame_name,
        "frame_count": frame_count,
        "duplicate":   is_dup,
        "video_stem":  video_stem,
        "csv_path":    str(csv_path),
        "h5_path":     str(h5_path) if h5_path else None,
        "csv_updated": True,
        "h5_updated":  h5_path is not None,
    }), (200 if is_dup else 201)


@bp.route("/dlc/curator/save-annotation", methods=["POST"])
def curator_save_annotation():
    """
    Write corrected (x, y) coordinates into CollectedData_<scorer>.csv/.h5.

    If the PNG for this frame does not yet exist in labeled-data/, it is
    extracted first (requires video_path/video_name + frame_number).

    Body:
      { "video_stem":   "<str>",
        "frame_name":   "<img????-?????.png>",
        "coords":       { "bp_name": [x, y], ... },
        "video_path":   "<abs-path-to-video>",   // optional — needed if PNG not yet extracted
        "frame_number": <int>                     // optional — needed if PNG not yet extracted
      }
    """
    from dlc_dataset_curator import (
        extract_frame_as_png,
        update_frame_annotation,
    )

    project_data, err = _get_project()
    if err:
        return jsonify({"error": err}), 400

    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    cfg        = _parse_config(project_data)
    scorer     = cfg.get("scorer", "User")
    bodyparts  = cfg.get("bodyparts", [])
    if not bodyparts:
        return jsonify({"error": "No bodyparts in project config.yaml — cannot save annotation."}), 400

    body        = request.get_json(force=True) or {}
    video_stem  = (body.get("video_stem") or "").strip()
    frame_name  = (body.get("frame_name") or "").strip()
    coords_raw  = body.get("coords") or {}

    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400
    if not frame_name:
        return jsonify({"error": "frame_name is required."}), 400
    if not isinstance(coords_raw, dict) or not coords_raw:
        return jsonify({"error": "coords must be a non-empty dict {bodypart: [x, y]}."}), 400

    # Validate coords
    coords = {}
    for bp, pt in coords_raw.items():
        if not isinstance(pt, list) or len(pt) != 2:
            return jsonify({"error": f"coords['{bp}'] must be [x, y]."}), 400
        try:
            coords[bp] = [float(pt[0]), float(pt[1])]
        except (TypeError, ValueError):
            return jsonify({"error": f"coords['{bp}'] values must be numbers."}), 400

    safe_stem   = secure_filename(video_stem)
    labeled_dir = project_path / "labeled-data" / safe_stem
    labeled_dir.mkdir(parents=True, exist_ok=True)

    png_path = labeled_dir / secure_filename(frame_name)

    # Auto-extract PNG if not yet present
    if not png_path.is_file():
        frame_number = body.get("frame_number")
        video_path   = _resolve_video_path(body, project_data)

        if video_path is None or frame_number is None:
            return jsonify({
                "error": (
                    f"PNG '{frame_name}' not found in labeled-data/{safe_stem}/. "
                    "Provide video_path and frame_number to auto-extract it."
                )
            }), 404

        try:
            frame_number = int(frame_number)
        except (TypeError, ValueError):
            return jsonify({"error": "frame_number must be an integer."}), 400

        if not _sec_check(video_path.parent):
            return jsonify({"error": "Access denied to video path."}), 403

        try:
            extract_frame_as_png(str(video_path), frame_number, labeled_dir)
        except Exception as exc:
            return jsonify({"error": f"Auto-extraction failed: {exc}"}), 500

        # Re-check that the expected PNG name was created (duplicate detection
        # may have picked a different existing file name)
        if not png_path.is_file():
            return jsonify({
                "error": (
                    f"Auto-extraction ran but '{frame_name}' still not found. "
                    "The frame may already be stored under a different filename."
                )
            }), 409

    # ── Write annotation ──────────────────────────────────────────────────────
    try:
        csv_path, h5_path = update_frame_annotation(
            stem_dir   = labeled_dir,
            video_stem = safe_stem,
            frame_name = frame_name,
            scorer     = scorer,
            bodyparts  = bodyparts,
            coords     = coords,
        )
    except Exception as exc:
        return jsonify({"error": f"Annotation write failed: {exc}"}), 500

    return jsonify({
        "frame_name":  frame_name,
        "video_stem":  safe_stem,
        "csv_path":    str(csv_path),
        "h5_path":     str(h5_path) if h5_path else None,
        "csv_updated": True,
        "h5_updated":  h5_path is not None,
        "coords":      {bp: pt for bp, pt in coords.items()},
    })
