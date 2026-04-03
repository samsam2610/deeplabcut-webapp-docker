"""
Blueprint: anipose_visualization
Handles visualization-oriented routes: get-sessions, metadata, pose3d, trials, videos, etc.
"""
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory, session as flask_session

bp = Blueprint("anipose_visualization", __name__)


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


# ── Visualization helpers ─────────────────────────────────────────
def _natural_keys(text: str) -> list:
    """Sort helper — splits text into int and str chunks for natural ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _get_video_name(config: dict, fname: str) -> str:
    """Extract the trial name from a video filename by stripping the camera part."""
    cam_regex = config.get("triangulation", {}).get("cam_regex", r"cam[0-9]")
    basename  = Path(fname).stem
    vname     = re.sub(cam_regex, "", basename)
    return re.sub(r"^[_\-]+|[_\-]+$", "", vname).strip()


def _get_cam_name(config: dict, fname: str) -> str:
    """Extract the camera name from a filename using cam_regex."""
    cam_regex = config.get("triangulation", {}).get("cam_regex", r"cam[0-9]")
    m = re.search(cam_regex, Path(fname).stem)
    return m.group(0) if m else "unknown"


def _inspector_load_config(project_dir: Path) -> dict:
    cfg_path = project_dir / "config.toml"
    if not cfg_path.is_file():
        return {}
    import toml as _toml
    return _toml.load(str(cfg_path))


def _get_anipose_root(project_dir: Path, config: dict) -> Path:
    """Return the Anipose project root from config['path'], falling back to project_dir."""
    p = config.get("path", "").strip()
    if p:
        candidate = Path(p)
        if candidate.is_dir():
            return candidate
    return project_dir


def _get_config_for_project(project_id: str, root: str = "") -> dict:
    """
    Load config.toml for a project for visualization routes.
    Priority:
      1. <project_dir>/config.toml  (placed there by _ensure_config during pipeline runs)
      2. Redis active session config_path (fallback)
    """
    import toml

    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        raise ValueError(str(exc))

    local_config = project_dir / "config.toml"
    if local_config.is_file():
        return toml.load(str(local_config))

    raw = _redis().get(_session_key())
    if raw:
        config_path = Path(json.loads(raw).get("config_path", ""))
        if config_path.is_file():
            return toml.load(str(config_path))

    raise FileNotFoundError(f"No config.toml found for project '{project_id}'.")


# ── Routes ────────────────────────────────────────────────────────
@bp.route("/get-sessions")
def get_sessions():
    """List Anipose sessions (subdirs of the anipose root found via config['path']).

    Searches DATA_DIR for any project whose config.toml has a 'path' field pointing
    to an existing directory, then returns that directory's subdirectories as sessions.
    Falls back to listing DLC project folders if no anipose root is found.
    """
    data_dir = _data_dir()
    sessions: list = []
    anipose_root_found: Path | None = None

    if data_dir.is_dir():
        for project_dir in sorted(data_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            config = _inspector_load_config(project_dir)
            root = _get_anipose_root(project_dir, config)
            if root != project_dir and root.is_dir():
                anipose_root_found = root
                break

    if anipose_root_found:
        sessions = sorted(
            [d.name for d in anipose_root_found.iterdir()
             if d.is_dir() and not d.name.startswith(".")],
            key=_natural_keys,
        )
    else:
        sessions = sorted(
            [d.name for d in data_dir.iterdir()
             if d.is_dir() and (d / "config.toml").is_file()],
            key=_natural_keys,
        )

    return jsonify({"sessions": sessions})


@bp.route("/projects/<project_id>/metadata")
def get_project_metadata(project_id: str):
    """Return bodyparts, labeling scheme and video speed from the project config."""
    root = request.args.get("root", "").strip()
    try:
        config = _get_config_for_project(project_id, root)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({
        "bodyparts":   config.get("bodyparts", []),
        "scheme":      config.get("labeling", {}).get("scheme", []),
        "video_speed": config.get("converted_video_speed", 1),
    })


@bp.route("/projects/<project_id>/pose3d/<path:subpath>")
def get_pose3d(project_id: str, subpath: str):
    """Read a 3D-pose CSV and return normalised per-bodypart trajectories."""
    import numpy as np
    import pandas as pd

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    try:
        data = pd.read_csv(str(target))
    except Exception as exc:
        return jsonify({"error": f"Could not read CSV: {exc}"}), 400

    x_cols    = [c for c in data.columns if c.endswith("_x")]
    bodyparts = [c[:-2] for c in x_cols]
    if not bodyparts:
        return jsonify({"error": "No bodypart columns found in CSV."}), 400

    all_finite = []
    for bp in bodyparts:
        for ax in ("x", "y", "z"):
            col = f"{bp}_{ax}"
            if col in data.columns:
                vals = data[col].to_numpy(dtype=float)
                all_finite.append(vals[np.isfinite(vals)])

    if not any(len(a) for a in all_finite):
        return jsonify({"error": "No finite values found in CSV."}), 400

    flat    = np.concatenate(all_finite)
    v_min   = float(np.nanmin(flat))
    v_max   = float(np.nanmax(flat))
    v_range = v_max - v_min if v_max != v_min else 1.0

    result = {}
    for bp in bodyparts:
        coords = {}
        for ax in ("x", "y", "z"):
            col = f"{bp}_{ax}"
            if col in data.columns:
                raw  = data[col].to_numpy(dtype=float)
                norm = (raw - v_min) / v_range
                coords[ax] = [None if not np.isfinite(v) else round(float(v), 6) for v in norm]
        result[bp] = coords

    return jsonify({
        "bodyparts": bodyparts,
        "data":      result,
        "n_frames":  len(data),
        "norm_min":  v_min,
        "norm_max":  v_max,
    })


@bp.route("/projects/<project_id>/pose2dproj/<path:subpath>")
def get_pose2dproj(project_id: str, subpath: str):
    """Serve a pre-computed 2D-projected pose CSV."""
    import pandas as pd

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    try:
        data = pd.read_csv(str(target))
    except Exception as exc:
        return jsonify({"error": f"Could not read CSV: {exc}"}), 400

    return jsonify({
        "columns":  list(data.columns),
        "data":     data.where(data.notnull(), None).to_dict(orient="list"),
        "n_frames": len(data),
    })


@bp.route("/projects/<project_id>/framerate/<path:subpath>")
def get_project_framerate(project_id: str, subpath: str):
    """Return the framerate of a video file using OpenCV."""
    import cv2

    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    cap = cv2.VideoCapture(str(target))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video file."}), 400
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if fps <= 0:
        return jsonify({"error": "Could not determine framerate."}), 400
    return jsonify({"fps": fps})


@bp.route("/projects/<project_id>/video/<path:subpath>")
def stream_project_video(project_id: str, subpath: str):
    """Stream a video file with Range-request support for HTML5 playback."""
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "File not found."}), 404

    mime_map = {
        ".mp4": "video/mp4", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    }
    mimetype = mime_map.get(target.suffix.lower(), "application/octet-stream")
    return send_from_directory(str(target.parent), target.name,
                               mimetype=mimetype, conditional=True)


@bp.route("/projects/<project_id>/get-trials")
def get_project_trials(project_id: str):
    """List trial videos grouped by trial name using cam_regex from config."""
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    try:
        config = _get_config_for_project(project_id, root)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 404

    pipeline     = config.get("pipeline", {})
    videos_raw   = pipeline.get("videos_raw", "videos-raw")
    video_ext    = config.get("video_extension", "avi")
    video_folder = project_dir / videos_raw

    if not video_folder.is_dir():
        return jsonify({"trials": {}, "video_folder": str(video_folder), "project_id": project_id})

    video_exts = {f".{video_ext.lstrip('.')}"} | {".mp4", ".avi", ".mov", ".mkv"}
    videos = sorted(
        [f for f in video_folder.iterdir() if f.is_file() and f.suffix.lower() in video_exts],
        key=lambda p: _natural_keys(p.name),
    )

    trials: dict = defaultdict(list)
    for vid in videos:
        trial_name = _get_video_name(config, vid.name)
        cam_name   = _get_cam_name(config, vid.name)
        trials[trial_name].append({
            "filename": vid.name,
            "cam_name": cam_name,
            "rel_path": f"{videos_raw}/{vid.name}",
        })

    return jsonify({
        "trials":       dict(sorted(trials.items(), key=lambda kv: _natural_keys(kv[0]))),
        "video_folder": str(video_folder),
        "project_id":   project_id,
    })


@bp.route("/projects/<project_id>/behavior/<path:subpath>")
def get_project_behavior(project_id: str, subpath: str):
    """Return behavior annotation JSON for a given file path within a project."""
    root = request.args.get("root", "").strip()
    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / subpath).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"behaviors": {}, "exists": False})

    try:
        with open(target, "r") as f:
            data = json.load(f)
        return jsonify({"behaviors": data, "exists": True})
    except (json.JSONDecodeError, OSError) as exc:
        return jsonify({"error": f"Could not read behaviors file: {exc}"}), 400


@bp.route("/projects/<project_id>/update-behavior", methods=["POST"])
def update_project_behavior(project_id: str):
    """Write behavior annotation changes back to a JSON file."""
    body      = request.get_json(force=True) or {}
    root      = body.get("root", "").strip()
    rel_path  = body.get("path", "").strip()
    behaviors = body.get("behaviors")

    if not rel_path:
        return jsonify({"error": "path is required."}), 400
    if behaviors is None:
        return jsonify({"error": "behaviors is required."}), 400

    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / rel_path).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "w") as f:
            json.dump(behaviors, f, indent=2)
        return jsonify({"status": "saved", "path": str(target)})
    except OSError as exc:
        return jsonify({"error": f"Could not write file: {exc}"}), 500


@bp.route("/projects/<project_id>/download-behavior")
def download_project_behavior(project_id: str):
    """Download a behaviors JSON file as an attachment."""
    from flask import send_file
    root     = request.args.get("root", "").strip()
    rel_path = request.args.get("path", "behaviors.json").strip()

    try:
        project_dir = _resolve_project_dir_local(project_id, root)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not project_dir.is_dir():
        return jsonify({"error": f"Project not found: '{project_id}'"}), 404

    target = (project_dir / rel_path).resolve()
    if not target.is_relative_to(project_dir.resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not target.is_file():
        return jsonify({"error": "Behaviors file not found."}), 404

    return send_file(str(target), as_attachment=True,
                     download_name=target.name, mimetype="application/json")
