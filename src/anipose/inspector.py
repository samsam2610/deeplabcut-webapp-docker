"""
Blueprint: anipose_inspector
Behavior Inspector routes — serves the inspector page and its JSON API.
"""
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, render_template, send_from_directory, Response, session as flask_session

bp = Blueprint("anipose_inspector", __name__)


# ── Shared-state accessors ─────────────────────────────────────────
def _data_dir() -> Path:
    return current_app.config["APP_DATA_DIR"]


# ── Inspector state ───────────────────────────────────────────────
# Per-process in-memory token store (acceptable — inspector is a single-process
# Flask server).
_inspector_tokens: set = set()


# ── Inspector helpers ─────────────────────────────────────────────
def _natural_keys(text: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _get_video_name(config: dict, fname: str) -> str:
    cam_regex = config.get("triangulation", {}).get("cam_regex", r"cam[0-9]")
    basename  = Path(fname).stem
    vname     = re.sub(cam_regex, "", basename)
    return re.sub(r"^[_\-]+|[_\-]+$", "", vname).strip()


def _get_cam_name(config: dict, fname: str) -> str:
    cam_regex = config.get("triangulation", {}).get("cam_regex", r"cam[0-9]")
    m = re.search(cam_regex, Path(fname).stem)
    return m.group(0) if m else "unknown"


def _inspector_split_subpath(subpath: str):
    """Split 'folderA|folderB/name' → (folder_parts, name).

    folder_parts is a list of path components (may be empty for top-level).
    """
    subpath = subpath.lstrip("/")
    if "/" in subpath:
        folder_key, name = subpath.rsplit("/", 1)
    else:
        folder_key, name = "", subpath
    parts = [p for p in folder_key.split("|") if p]
    return parts, name


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


def _inspector_get_context(session: str):
    """Return (config, anipose_root, session_dir) for a session name.

    Searches DATA_DIR projects whose config['path'] (anipose_root) contains a
    subdirectory named `session`.  Falls back to treating `session` as a DLC
    project ID (for backwards compatibility).

    Raises ValueError for invalid / path-traversal inputs.
    """
    data_dir = _data_dir()

    if "/" in session or "\\" in session or ".." in session:
        raise ValueError("Invalid session name.")

    if data_dir.is_dir():
        for project_dir in sorted(data_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            config = _inspector_load_config(project_dir)
            anipose_root = _get_anipose_root(project_dir, config)
            if anipose_root == project_dir:
                continue
            session_dir = anipose_root / session
            if session_dir.is_dir():
                return config, anipose_root, session_dir

    project_dir = (data_dir / session).resolve()
    if not project_dir.is_relative_to(data_dir.resolve()):
        raise ValueError("Invalid session path.")
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Session not found: {session}")
    config = _inspector_load_config(project_dir)
    anipose_root = _get_anipose_root(project_dir, config)
    return config, anipose_root, anipose_root


# ── Routes ────────────────────────────────────────────────────────
@bp.route("/inspector")
def inspector_page():
    return render_template("inspector.html")


@bp.route("/metadata/<session>")
def inspector_metadata(session: str):
    """Return labeling scheme in index form for behavior inspector script."""
    try:
        config, _root, _sdir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    scheme = config.get("labeling", {}).get("scheme", [])
    video_speed = config.get("converted_video_speed", 1)

    bodyparts: list = []
    for bp_list in scheme:
        for bp in bp_list:
            if bp not in bodyparts:
                bodyparts.append(bp)
    kps = {bp: i for i, bp in enumerate(bodyparts)}
    idx_scheme = [[kps[bp] for bp in bp_list] for bp_list in scheme]

    return jsonify({"video_speed": video_speed, "scheme": idx_scheme})


@bp.route("/get-trials/<session>")
def inspector_get_trials(session: str):
    """Return trial/folder structure for behavior inspector script."""
    import os as _os

    try:
        config, anipose_root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    pipeline   = config.get("pipeline", {})
    videos_raw = pipeline.get("videos_raw_mp4", pipeline.get("videos_raw", "videos-raw-mp4"))
    vid_ext    = config.get("video_extension", "avi")
    vid_exts   = {f".{vid_ext.lstrip('.')}", ".mp4", ".avi", ".mov", ".mkv"}

    behaviors_path = anipose_root / "behaviors.json"
    behaviors: dict = {}
    if behaviors_path.is_file():
        try:
            with open(str(behaviors_path)) as _f:
                behaviors = json.load(_f)
        except (json.JSONDecodeError, OSError):
            pass

    session_behaviors_set: set = set()
    trial_behaviors: dict = {}

    folders_out = []
    for root_str, dirs, _ in _os.walk(str(session_dir)):
        dirs.sort()
        root_path  = Path(root_str)
        video_dir  = root_path / videos_raw
        if not video_dir.is_dir():
            continue

        video_files = sorted(
            [f for f in video_dir.iterdir()
             if f.is_file() and f.suffix.lower() in vid_exts],
            key=lambda p: _natural_keys(p.name),
        )
        if not video_files:
            continue

        rel = root_path.relative_to(session_dir)
        folder_key = "|".join(rel.parts) if str(rel) != "." else ""

        trial_groups: dict = defaultdict(list)
        for vf in video_files:
            tname = _get_video_name(config, vf.name)
            cname = _get_cam_name(config, vf.name)
            trial_groups[tname].append({"file": vf.name, "cam": cname})

        files_out = []
        for tname in sorted(trial_groups.keys(), key=_natural_keys):
            cams = sorted(trial_groups[tname], key=lambda x: _natural_keys(x["cam"]))
            files_out.append({
                "vidname":  tname,
                "camnames": [c["cam"] for c in cams],
                "files":    [c["file"] for c in cams],
            })
            full_key = f"{session}|{folder_key}" if folder_key else session
            folder_behaviors = behaviors.get(full_key, {}).get(tname, {})
            if folder_behaviors:
                bnames = {b["behavior"] for b in folder_behaviors.values() if b.get("behavior")}
                session_behaviors_set.update(bnames)
                rel_path = f"{session}/{folder_key}/{tname}"
                trial_behaviors[rel_path] = {b: True for b in bnames}

        folders_out.append({"folder": folder_key, "files": files_out})

    folders_out.sort(key=lambda x: _natural_keys(x["folder"]))

    return jsonify({
        "session":          session,
        "folders":          folders_out,
        "sessionBehaviors": sorted(session_behaviors_set),
        "trialBehaviors":   trial_behaviors,
    })


@bp.route("/pose3d/<session>/<path:subpath>")
def inspector_pose3d(session: str, subpath: str):
    """Return 3D pose as [n_frames, n_bodyparts, 3] for behavior inspector."""
    import numpy as _np
    import pandas as _pd

    try:
        config, _root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, vidname = _inspector_split_subpath(subpath)
    csv_path = session_dir.joinpath(*folder_parts) / "pose-3d-filtered" / f"{vidname}.csv"
    if not csv_path.is_file():
        csv_path = session_dir.joinpath(*folder_parts) / "pose-3d" / f"{vidname}.csv"
    if not csv_path.is_file():
        return jsonify([])

    try:
        data = _pd.read_csv(str(csv_path))
    except Exception:
        return jsonify([])

    scheme     = config.get("labeling", {}).get("scheme", [])
    bodyparts  = []
    for bp_list in scheme:
        for bp in bp_list:
            if bp not in bodyparts:
                bodyparts.append(bp)

    if not bodyparts:
        bodyparts = [c[:-2] for c in data.columns if c.endswith("_x")]

    vecs = []
    for bp in bodyparts:
        cols = [f"{bp}_x", f"{bp}_y", f"{bp}_z"]
        if not all(c in data.columns for c in cols):
            continue
        vec = data[cols].to_numpy(dtype=float)
        err_col = f"{bp}_error"
        if err_col in data.columns:
            err = data[err_col].to_numpy(dtype=float)
            err[~_np.isfinite(err)] = 1000.0
            vec[err > 50] = _np.nan
        vecs.append(vec)

    if not vecs:
        return jsonify([])

    vecs = _np.array(vecs).swapaxes(0, 1)   # [n_frames, n_bps, 3]
    m    = _np.nanmean(vecs, axis=0)
    std  = float(_np.nanmedian(_np.diff(_np.nanpercentile(m, [25, 75], axis=0), axis=0)))
    if std == 0:
        std = 1.0
    vecs = 0.3 * vecs / std
    cm   = _np.nanmean(_np.nanmean(vecs, axis=1), axis=0)
    vecs = vecs - cm
    vecs[~_np.isfinite(vecs)] = 0.0

    return jsonify(vecs.tolist())


@bp.route("/pose2dproj/<session>/<path:subpath>")
def inspector_pose2dproj(_session: str, _subpath: str):
    """Stub — 2D projections require aniposelib calibration (not yet integrated)."""
    return jsonify({})


@bp.route("/video/<session>/<path:subpath>")
def inspector_video(session: str, subpath: str):
    """Serve a raw video file for the behavior inspector."""
    try:
        config, _root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, filename = _inspector_split_subpath(subpath)
    pipeline   = config.get("pipeline", {})
    videos_raw = pipeline.get("videos_raw_mp4", pipeline.get("videos_raw", "videos-raw-mp4"))

    video_path = session_dir.joinpath(*folder_parts) / videos_raw / filename
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    mime_map = {
        ".mp4": "video/mp4", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    }
    mimetype = mime_map.get(video_path.suffix.lower(), "application/octet-stream")
    return send_from_directory(str(video_path.parent), video_path.name,
                               mimetype=mimetype, conditional=True)


@bp.route("/framerate/<session>/<path:subpath>")
def inspector_framerate(session: str, subpath: str):
    """Return video FPS as a bare number (required by inspector script)."""
    import cv2 as _cv2

    try:
        config, _root, session_dir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, filename = _inspector_split_subpath(subpath)
    pipeline   = config.get("pipeline", {})
    videos_raw = pipeline.get("videos_raw_mp4", pipeline.get("videos_raw", "videos-raw-mp4"))

    video_path = session_dir.joinpath(*folder_parts) / videos_raw / filename
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    cap = _cv2.VideoCapture(str(video_path))
    fps = cap.get(_cv2.CAP_PROP_FPS)
    cap.release()
    return jsonify(fps if fps > 0 else 30.0)


@bp.route("/behavior/<session>/<path:subpath>")
def inspector_behavior(session: str, subpath: str):
    """Return behavior bouts for a specific trial from behaviors.json."""
    try:
        _cfg, anipose_root, _sdir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    folder_parts, vidname = _inspector_split_subpath(subpath)
    full_key = f"{session}|{'|'.join(folder_parts)}" if folder_parts else session

    beh_path = anipose_root / "behaviors.json"
    if not beh_path.is_file():
        return jsonify({})

    try:
        with open(str(beh_path)) as _f:
            behaviors = json.load(_f)
    except (json.JSONDecodeError, OSError):
        return jsonify({})

    return jsonify(behaviors.get(full_key, {}).get(vidname, {}))


@bp.route("/download-behavior/<session>")
def inspector_download_behavior(session: str):
    """Download the full behaviors.json for a session."""
    try:
        _cfg, anipose_root, _sdir = _inspector_get_context(session)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    beh_path = anipose_root / "behaviors.json"
    if not beh_path.is_file():
        return jsonify({})

    try:
        with open(str(beh_path)) as _f:
            behaviors = json.load(_f)
    except (json.JSONDecodeError, OSError):
        return jsonify({})

    return jsonify(behaviors)


@bp.route("/unlock-editing", methods=["POST"])
def inspector_unlock_editing():
    """Validate password and issue a session token for behavior editing."""
    import string as _string
    import random as _random
    import os as _os
    body = request.get_json(force=True) or {}
    password = body.get("password", "")
    server_pw = _os.environ.get("INSPECTOR_PASSWORD", "password")
    token = -1
    if password == server_pw:
        token = "".join(_random.choices(_string.ascii_letters + "_", k=10))
        _inspector_tokens.add(token)
    valid = token in _inspector_tokens
    return jsonify({"token": token, "valid": valid})


@bp.route("/get-token/<token>")
def inspector_get_token(token: str):
    """Check whether a token is still valid."""
    return jsonify({"valid": token in _inspector_tokens})


@bp.route("/update-behavior", methods=["POST"])
def inspector_update_behavior():
    """Apply behavior change-log from inspector to behaviors.json."""
    body   = request.get_json(force=True) or {}
    token  = body.get("token", "")
    if token not in _inspector_tokens:
        return "invalid token", 403

    changes_by_session: dict = defaultdict(list)
    for bout_changes in body.get("allBehaviorChanges", {}).values():
        for change in bout_changes:
            changes_by_session[change["session"]].append(change)

    for sess, changes in changes_by_session.items():
        try:
            _cfg, anipose_root, _sdir = _inspector_get_context(sess)
        except (ValueError, FileNotFoundError):
            continue

        beh_path = anipose_root / "behaviors.json"
        if beh_path.is_file():
            try:
                with open(str(beh_path)) as _f:
                    beh_dict = json.load(_f)
            except (json.JSONDecodeError, OSError):
                beh_dict = {}
        else:
            beh_dict = {}

        for change in changes:
            mod = change.get("modification")
            if mod == "added":
                bout = change["new"]
                fk, fn, bid = bout["folders"], bout["filename"], bout["bout_id"]
                beh_dict.setdefault(fk, {}).setdefault(fn, {})[bid] = bout
            elif mod == "removed":
                bout = change["old"]
                fk, fn, bid = bout["folders"], bout["filename"], bout["bout_id"]
                try:
                    del beh_dict[fk][fn][bid]
                except KeyError:
                    pass
            else:
                bout  = change["old"]
                edits = change.get("new", {})
                bout.update(edits)
                fk, fn, bid = bout["folders"], bout["filename"], bout["bout_id"]
                beh_dict.setdefault(fk, {}).setdefault(fn, {})[bid] = bout

        try:
            with open(str(beh_path), "w") as _f:
                json.dump(beh_dict, _f, indent=4)
        except OSError:
            pass

    return "behavior labels successfully updated"
