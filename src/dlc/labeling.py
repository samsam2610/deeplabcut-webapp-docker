"""
DLC Frame Labeler Blueprint.

Routes:
  GET /dlc/project/bodyparts
  GET /dlc/project/labeled-frames
  GET /dlc/project/frame-image/<video_stem>/<filename>
  GET/POST /dlc/project/labels/<video_stem>
  POST /dlc/project/labels/convert-to-h5
"""
from __future__ import annotations
import csv
import json
import re
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, session as flask_session
from werkzeug.utils import secure_filename
from . import ctx as _ctx
from dlc.utils import (
    _get_engine_queue,
    _dlc_project_security_check,
)

bp = Blueprint("dlc_labeling", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _natural_keys(text: str) -> list:
    """Sort helper — splits text into int and str chunks for natural ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _parse_dlc_yaml(config_path: Path) -> dict:
    """Parse a DLC config.yaml and return the relevant fields.

    Falls back to a regex parser if yaml.safe_load fails (e.g. a video_sets
    entry whose file path contains a space wraps onto the next line, creating
    an invalid YAML multi-line key).  The regex is sufficient for the fields
    actually needed by the webapp (bodyparts, scorer).
    """
    _yaml = _ctx.yaml_lib()
    text = config_path.read_text()
    if _yaml is not None:
        try:
            return _yaml.safe_load(text) or {}
        except Exception:
            pass  # fall through to regex parser below
    # Regex fallback — extracts bodyparts + scorer without a full YAML parse
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


def _get_dlc_project_and_config():
    """Return (project_data, config_dict, error_response) for the active DLC project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, None, (jsonify({"error": "No active DLC project."}), 400)
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return project_data, {}, None
    try:
        cfg = _parse_dlc_yaml(config_path)
    except Exception as exc:
        return project_data, {}, (jsonify({"error": f"Could not parse config.yaml: {exc}"}), 500)
    return project_data, cfg, None


@bp.route("/dlc/project/bodyparts")
def dlc_get_bodyparts():
    """Return bodyparts and scorer from the active DLC project's config.yaml."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err
    return jsonify({
        "bodyparts": cfg.get("bodyparts", []),
        "scorer":    cfg.get("scorer", "User"),
    })


@bp.route("/dlc/project/labeled-frames")
def dlc_list_labeled_frames():
    """List video stems and their PNG frames inside labeled-data/."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    labeled_base = project_path / "labeled-data"
    if not labeled_base.is_dir():
        return jsonify({"video_stems": []})

    result = []
    for stem_dir in sorted(labeled_base.iterdir(), key=lambda p: _natural_keys(p.name)):
        if not stem_dir.is_dir():
            continue
        frames = sorted(
            [f.name for f in stem_dir.iterdir() if f.suffix.lower() == ".png"],
            key=_natural_keys,
        )
        if frames:
            result.append({"video_stem": stem_dir.name, "frames": frames})

    return jsonify({"video_stems": result})


@bp.route("/dlc/project/frame-image/<path:video_stem>/<filename>")
def dlc_serve_frame_image(video_stem: str, filename: str):
    """Serve a PNG frame from labeled-data/<video_stem>/."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400
    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    safe_stem     = secure_filename(video_stem)
    safe_filename = secure_filename(filename)
    frame_dir  = (project_path / "labeled-data" / safe_stem).resolve()
    proj_root  = project_path.resolve()
    if not str(frame_dir).startswith(str(proj_root)):
        return jsonify({"error": "Access denied."}), 403

    frame_path = frame_dir / safe_filename
    if not frame_path.is_file():
        return jsonify({"error": "Frame not found."}), 404

    return send_file(str(frame_path), mimetype="image/png")


@bp.route("/dlc/project/labels/<path:video_stem>", methods=["GET"])
def dlc_get_labels(video_stem: str):
    """Read CollectedData_<scorer>.csv and return labels as JSON."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err
    scorer       = cfg.get("scorer", "User")
    project_path = Path(project_data.get("project_path", ""))
    stem_dir     = project_path / "labeled-data" / secure_filename(video_stem)
    csv_path     = stem_dir / f"CollectedData_{scorer}.csv"

    if not csv_path.is_file():
        # Fall back to any CollectedData_*.csv present
        candidates = sorted(stem_dir.glob("CollectedData_*.csv"))
        if not candidates:
            return jsonify({"labels": {}, "scorer": scorer})
        csv_path = candidates[0]
        scorer = csv_path.stem[len("CollectedData_"):]

    try:
        with open(str(csv_path), newline="") as f:
            rows = list(csv.reader(f))

        if len(rows) < 4:
            return jsonify({"labels": {}, "scorer": scorer})

        # Napari/standard format: 3-column MultiIndex index (labeled-data | video_stem | img_name)
        # Header data starts at column 3; data rows have img_name at column 2.
        bodyparts_row = rows[1][3:]
        coords_row    = rows[2][3:]
        col_pairs     = list(zip(bodyparts_row, coords_row))

        labels = {}
        for row in rows[3:]:
            if not row:
                continue
            img_name = row[2]
            vals     = row[3:]
            bp_data: dict = {}
            for (bp, coord), val in zip(col_pairs, vals):
                bp_data.setdefault(bp, {})[coord] = val

            frame_labels = {}
            for bp, coords_dict in bp_data.items():
                x_str = coords_dict.get("x", "")
                y_str = coords_dict.get("y", "")
                try:
                    x = float(x_str) if x_str not in ("", "NaN", "nan") else None
                    y = float(y_str) if y_str not in ("", "NaN", "nan") else None
                except ValueError:
                    x = y = None
                frame_labels[bp] = [x, y] if x is not None and y is not None else None

            labels[img_name] = frame_labels

        return jsonify({"labels": labels, "scorer": scorer})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/dlc/project/labels/<path:video_stem>", methods=["POST"])
def dlc_save_labels(video_stem: str):
    """Write labels dict to CollectedData_<scorer>.csv in DLC MultiIndex CSV format."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err

    scorer     = cfg.get("scorer", "User")
    bodyparts  = cfg.get("bodyparts", [])
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body   = request.get_json(force=True) or {}
    labels = body.get("labels", {})   # {frame_name: {bp: [x, y] or null}}

    safe_stem = secure_filename(video_stem)
    stem_dir  = project_path / "labeled-data" / safe_stem
    stem_dir.mkdir(parents=True, exist_ok=True)
    csv_path  = stem_dir / f"CollectedData_{scorer}.csv"

    frame_names = sorted(labels.keys(), key=_natural_keys)

    # Napari/standard MultiIndex format: 3-column index (labeled-data | video_stem | img_name)
    header_scorer    = ["scorer",    "", ""] + [scorer] * (len(bodyparts) * 2)
    header_bodyparts = ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in range(2)]
    header_coords    = ["coords",    "", ""] + ["x", "y"] * len(bodyparts)

    rows = [header_scorer, header_bodyparts, header_coords]
    for frame_name in frame_names:
        frame_lbls  = labels.get(frame_name, {})
        row         = ["labeled-data", safe_stem, frame_name]
        for bp in bodyparts:
            pt = frame_lbls.get(bp)
            if pt and len(pt) == 2 and pt[0] is not None and pt[1] is not None:
                row.extend([str(round(pt[0], 4)), str(round(pt[1], 4))])
            else:
                row.extend(["NaN", "NaN"])
        rows.append(row)

    try:
        with open(str(csv_path), "w", newline="") as f:
            csv.writer(f).writerows(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "saved", "csv_path": str(csv_path), "scorer": scorer})


@bp.route("/dlc/project/labels/convert-to-h5", methods=["POST"])
def dlc_convert_labels_to_h5():
    """Dispatch a Celery task to run deeplabcut.convertcsv2h5 on the active project."""
    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err

    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    project_path = Path(project_data.get("project_path", ""))
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "config.yaml not found in project."}), 400
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    scorer = cfg.get("scorer", "User")
    task   = _ctx.celery().send_task(
        "tasks.dlc_convert_labels_to_h5",
        kwargs={"config_path": config_path, "scorer": scorer},
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "convert_labels_to_h5"}), 202
