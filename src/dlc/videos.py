"""
DLC Video Routes Blueprint.

Routes:
  GET /dlc/project/videos
  GET /dlc/project/video-info/<filename>
  GET /dlc/project/video-csv/<filename>
  GET /dlc/project/video-csv-ext
  GET /dlc/project/video-stream/<filename>
  GET /dlc/project/video-frame/<filename>/<frame_number>
  POST /dlc/project/video-upload
  POST /dlc/project/add-video
  GET /dlc/project/video-info-ext
  GET /dlc/project/video-frame-ext/<frame_number>
  POST /dlc/project/save-frame
"""
from __future__ import annotations
import collections as _collections
import csv as csv_module
import json
import re
import threading as _threading
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, Response, session as flask_session
from werkzeug.utils import secure_filename
from . import ctx as _ctx
from dlc.utils import _dlc_project_security_check

bp = Blueprint("dlc_videos", __name__)

ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}

# Per-session VideoCapture cache
_FE_VCAP_MAX        = 20
_fe_vcap_cache: dict = _collections.OrderedDict()
_fe_vcap_cache_lock = _threading.Lock()


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _valid_ext(filename: str, allowed: set) -> bool:
    return Path(filename).suffix.lower() in allowed


@bp.route("/dlc/project/videos")
def dlc_list_videos():
    """List video files in the active DLC project's videos folder."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    videos_dir = project_path / "videos"
    videos = []
    if videos_dir.is_dir():
        for f in sorted(videos_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_VIDEO_EXT:
                videos.append({"name": f.name, "size": f.stat().st_size})

    return jsonify({"videos": videos})


@bp.route("/dlc/project/video-info/<path:filename>")
def dlc_video_info(filename: str):
    """Return FPS, frame count, width, height for a video in the videos folder."""
    import cv2

    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    video_path = (project_path / "videos" / filename).resolve()
    if not video_path.is_relative_to((project_path / "videos").resolve()):
        return jsonify({"error": "Invalid path."}), 400
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video."}), 400

    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    return jsonify({"fps": fps, "frame_count": frame_count, "width": width, "height": height})


@bp.route("/dlc/project/video-csv/<path:filename>")
def dlc_video_csv(filename: str):
    """Return CSV annotation rows for a video (same stem, .csv extension in videos/ folder)."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    videos_dir = (project_path / "videos").resolve()
    csv_path = (videos_dir / (Path(filename).stem + ".csv")).resolve()
    if not csv_path.is_relative_to(videos_dir):
        return jsonify({"error": "Invalid path."}), 400
    if not csv_path.is_file():
        return jsonify({"rows": []})

    rows = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv_module.DictReader(f, skipinitialspace=True)
            # Normalise field names in case header has extra whitespace
            if reader.fieldnames:
                reader.fieldnames = [n.strip() for n in reader.fieldnames]
            for row in reader:
                # Also strip any None-keyed remainder
                row = {k.strip() if k else k: v for k, v in row.items()}
                status = (row.get("frame_line_status") or "").strip()
                note   = (row.get("note") or "").strip()
                if status or note:
                    try:
                        fn = int(float(row.get("frame_number", 0)))
                    except (ValueError, TypeError):
                        fn = 0
                    rows.append({
                        "timestamp":         (row.get("timestamp") or "").strip(),
                        "frame_number":      fn,
                        "frame_line_status": status,
                        "note":              note,
                    })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    rows.sort(key=lambda r: r["frame_number"])
    return jsonify({"rows": rows})


@bp.route("/dlc/project/video-csv-ext")
def dlc_video_csv_ext():
    """Return CSV annotation rows for an external (absolute-path) video.
    The CSV is expected alongside the video file with the same stem.
    Query param: path (absolute path to the video file).
    """
    abs_path = request.args.get("path", "").strip()
    if not abs_path:
        return jsonify({"error": "path parameter required."}), 400

    video_path = Path(abs_path)
    csv_path   = video_path.with_suffix(".csv")
    if not csv_path.is_file():
        return jsonify({"rows": []})

    rows = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv_module.DictReader(f, skipinitialspace=True)
            if reader.fieldnames:
                reader.fieldnames = [n.strip() for n in reader.fieldnames]
            for row in reader:
                row = {k.strip() if k else k: v for k, v in row.items()}
                status = (row.get("frame_line_status") or "").strip()
                note   = (row.get("note") or "").strip()
                if status or note:
                    try:
                        fn = int(float(row.get("frame_number", 0)))
                    except (ValueError, TypeError):
                        fn = 0
                    rows.append({
                        "timestamp":         (row.get("timestamp") or "").strip(),
                        "frame_number":      fn,
                        "frame_line_status": status,
                        "note":              note,
                    })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    rows.sort(key=lambda r: r["frame_number"])
    return jsonify({"rows": rows})


@bp.route("/dlc/project/video-stream/<path:filename>")
def dlc_video_stream(filename: str):
    """Stream a video file from the active DLC project's videos folder."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    videos_dir = (project_path / "videos").resolve()
    video_path = (videos_dir / filename).resolve()
    if not video_path.is_relative_to(videos_dir):
        return jsonify({"error": "Invalid path."}), 400
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404
    if video_path.suffix.lower() not in ALLOWED_VIDEO_EXT:
        return jsonify({"error": "Unsupported video format."}), 400

    # Manual byte-range streaming – works reliably for multi-GB files
    file_size = video_path.stat().st_size
    range_header = request.headers.get("Range")
    ext = video_path.suffix.lower()
    mime_map = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
                ".avi": "video/x-msvideo", ".mpg": "video/mpeg", ".mpeg": "video/mpeg"}
    mime = mime_map.get(ext, "video/mp4")

    chunk = 2 * 1024 * 1024  # 2 MB chunks

    if range_header:
        try:
            rng = range_header.strip().replace("bytes=", "")
            start_s, end_s = rng.split("-")
            start = int(start_s)
            end   = int(end_s) if end_s else min(start + chunk - 1, file_size - 1)
        except (ValueError, AttributeError):
            start, end = 0, min(chunk - 1, file_size - 1)
        end = min(end, file_size - 1)
        length = end - start + 1

        def _stream_range(path, s, l):
            with open(path, "rb") as fh:
                fh.seek(s)
                remaining = l
                while remaining > 0:
                    data = fh.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        resp = Response(
            _stream_range(video_path, start, length),
            status=206,
            mimetype=mime,
            direct_passthrough=True,
        )
        resp.headers["Content-Range"]  = f"bytes {start}-{end}/{file_size}"
        resp.headers["Content-Length"] = str(length)
        resp.headers["Accept-Ranges"]  = "bytes"
        return resp

    # No Range header – stream full file
    def _stream_full(path):
        with open(path, "rb") as fh:
            while True:
                data = fh.read(65536)
                if not data:
                    break
                yield data

    resp = Response(_stream_full(video_path), status=200, mimetype=mime, direct_passthrough=True)
    resp.headers["Content-Length"] = str(file_size)
    resp.headers["Accept-Ranges"]  = "bytes"
    return resp


@bp.route("/dlc/project/video-frame/<path:filename>/<int:frame_number>")
def dlc_video_frame(filename: str, frame_number: int):
    """Decode and return a single frame as JPEG using OpenCV (for AVI / fallback mode)."""
    import cv2

    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    videos_dir = (project_path / "videos").resolve()
    video_path = (videos_dir / filename).resolve()
    if not video_path.is_relative_to(videos_dir):
        return jsonify({"error": "Invalid path."}), 400
    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    uid   = _user_id()
    vpath = str(video_path)

    # Get or create a per-session cache entry (LRU, capped at _FE_VCAP_MAX)
    with _fe_vcap_cache_lock:
        if uid not in _fe_vcap_cache:
            if len(_fe_vcap_cache) >= _FE_VCAP_MAX:
                _, evicted = _fe_vcap_cache.popitem(last=False)
                evicted["vcap"].release()
            _fe_vcap_cache[uid] = {"vcap": None, "path": None, "pos": -1,
                                   "lock": _threading.Lock()}
        _fe_vcap_cache.move_to_end(uid)
        entry = _fe_vcap_cache[uid]

    with entry["lock"]:
        # Re-open only when the video file changes
        if entry["vcap"] is None or entry["path"] != vpath or not entry["vcap"].isOpened():
            if entry["vcap"] is not None:
                entry["vcap"].release()
            entry["vcap"] = cv2.VideoCapture(vpath)
            entry["path"] = vpath
            entry["pos"]  = -1
            if not entry["vcap"].isOpened():
                return jsonify({"error": "Could not open video."}), 400

        # Sequential read: if this is exactly the next frame, skip the seek
        if frame_number != entry["pos"] + 1:
            entry["vcap"].set(cv2.CAP_PROP_POS_FRAMES, frame_number)

        ret, frame = entry["vcap"].read()
        entry["pos"] = frame_number if ret else -1

    if not ret:
        return jsonify({"error": "Could not read frame."}), 404

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return jsonify({"error": "Encoding failed."}), 500

    # Frames are immutable – let the browser cache them for the session
    etag = f"{vpath}-{frame_number}"
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    resp = Response(buf.tobytes(), mimetype="image/jpeg")
    resp.headers["ETag"]          = etag
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@bp.route("/dlc/project/video-upload", methods=["POST"])
def dlc_video_upload():
    """Upload a video into the active DLC project's videos folder."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify({"error": "No video file provided."}), 400
    if not _valid_ext(video_file.filename, ALLOWED_VIDEO_EXT):
        return jsonify({"error": "Unsupported video format."}), 400

    videos_dir = project_path / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(video_file.filename)
    video_file.save(str(videos_dir / safe_name))

    return jsonify({"saved": safe_name}), 201


@bp.route("/dlc/project/add-video", methods=["POST"])
def dlc_add_video():
    """Register an external video with the active DLC project (mirrors add_new_videos)."""
    import cv2

    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body = request.get_json(force=True) or {}
    video_path_str = (body.get("video_path") or "").strip()
    if not video_path_str:
        return jsonify({"error": "video_path required."}), 400

    p = Path(video_path_str).resolve()
    if not _sec_check(p.parent):
        return jsonify({"error": "Access denied: path not in an allowed location."}), 403
    if not p.is_file():
        return jsonify({"error": "Video file not found."}), 404
    if p.suffix.lower() not in ALLOWED_VIDEO_EXT:
        return jsonify({"error": "Unsupported video format."}), 400
    if "_labeled." in p.name:
        return jsonify({"error": "Labeled videos (DLC output overlays) cannot be added to video_sets."}), 400

    # Read video dimensions for the crop entry DLC expects in video_sets
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video to read dimensions."}), 400
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    config_file = project_path / "config.yaml"
    if not config_file.is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    _ruamel_yaml_instance = _ctx.ruamel()
    _yaml = _ctx.yaml_lib()

    if _ruamel_yaml_instance is None and _yaml is None:
        return jsonify({"error": "No YAML library available on this server."}), 500

    try:
        if _ruamel_yaml_instance is not None:
            import ruamel.yaml as _ruamel_yaml
            # Use ruamel.yaml to preserve key order and comments
            cfg = _ruamel_yaml_instance.load(config_file) or {}
            if "video_sets" not in cfg or cfg["video_sets"] is None:
                cfg["video_sets"] = _ruamel_yaml.comments.CommentedMap()
            cfg["video_sets"][str(p)] = {"crop": f"0, {width}, 0, {height}"}
            with open(config_file, "w") as _f:
                _ruamel_yaml_instance.dump(cfg, _f)
        else:
            cfg = _yaml.safe_load(config_file.read_text()) or {}
            if "video_sets" not in cfg or cfg["video_sets"] is None:
                cfg["video_sets"] = {}
            cfg["video_sets"][str(p)] = {"crop": f"0, {width}, 0, {height}"}
            config_file.write_text(_yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
    except Exception as exc:
        return jsonify({"error": f"Failed to update config.yaml: {exc}"}), 500

    return jsonify({"abs_path": str(p), "name": p.name}), 200


@bp.route("/dlc/project/video-info-ext")
def dlc_video_info_ext():
    """Return FPS / frame count for an external (absolute-path) video."""
    import cv2

    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    video_path_str = request.args.get("path", "").strip()
    if not video_path_str:
        return jsonify({"error": "path required."}), 400

    p = Path(video_path_str).resolve()
    if not _sec_check(p.parent):
        return jsonify({"error": "Access denied."}), 403
    if not p.is_file():
        return jsonify({"error": "Video not found."}), 404

    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video."}), 400

    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    return jsonify({"fps": fps, "frame_count": frame_count, "width": width, "height": height})


@bp.route("/dlc/project/video-frame-ext/<int:frame_number>")
def dlc_video_frame_ext(frame_number: int):
    """Decode and return a single frame as JPEG for an external (absolute-path) video."""
    import cv2

    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    video_path_str = request.args.get("path", "").strip()
    if not video_path_str:
        return jsonify({"error": "path required."}), 400

    p = Path(video_path_str).resolve()
    if not _sec_check(p.parent):
        return jsonify({"error": "Access denied."}), 403
    if not p.is_file():
        return jsonify({"error": "Video not found."}), 404

    uid   = _user_id()
    vpath = str(p)

    with _fe_vcap_cache_lock:
        if uid not in _fe_vcap_cache:
            if len(_fe_vcap_cache) >= _FE_VCAP_MAX:
                _, evicted = _fe_vcap_cache.popitem(last=False)
                evicted["vcap"].release()
            _fe_vcap_cache[uid] = {"vcap": None, "path": None, "pos": -1,
                                   "lock": _threading.Lock()}
        _fe_vcap_cache.move_to_end(uid)
        entry = _fe_vcap_cache[uid]

    with entry["lock"]:
        if entry["vcap"] is None or entry["path"] != vpath or not entry["vcap"].isOpened():
            if entry["vcap"] is not None:
                entry["vcap"].release()
            entry["vcap"] = cv2.VideoCapture(vpath)
            entry["path"] = vpath
            entry["pos"]  = -1
            if not entry["vcap"].isOpened():
                return jsonify({"error": "Could not open video."}), 400

        if frame_number != entry["pos"] + 1:
            entry["vcap"].set(cv2.CAP_PROP_POS_FRAMES, frame_number)

        ret, frame = entry["vcap"].read()
        entry["pos"] = frame_number if ret else -1

    if not ret:
        return jsonify({"error": "Could not read frame."}), 404

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return jsonify({"error": "Encoding failed."}), 500

    etag = f"ext-{vpath}-{frame_number}"
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    resp = Response(buf.tobytes(), mimetype="image/jpeg")
    resp.headers["ETag"]          = etag
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@bp.route("/dlc/project/save-frame", methods=["POST"])
def dlc_save_frame():
    """
    Save an extracted video frame to labeled-data/<video_stem>/ as PNG.
    Body: { "video_name": "<str>", "frame_data": "<base64-encoded JPEG>" }
    The client sends JPEG (small payload); server converts to PNG via OpenCV.
    Returns the saved filename and running frame count.
    """
    import base64 as _base64
    import cv2
    import numpy as np

    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404
    if not _sec_check(project_path):
        return jsonify({"error": "Access denied."}), 403

    body         = request.get_json(force=True) or {}
    video_name   = body.get("video_name",   "").strip()
    frame_data   = body.get("frame_data",   "").strip()
    frame_number = body.get("frame_number")          # int video frame index, may be None
    if frame_number is not None:
        try:
            frame_number = int(frame_number)
        except (TypeError, ValueError):
            frame_number = None
    if not video_name:
        return jsonify({"error": "video_name is required."}), 400
    if not frame_data:
        return jsonify({"error": "frame_data is required."}), 400

    try:
        img_bytes = _base64.b64decode(frame_data)
    except Exception:
        return jsonify({"error": "Invalid frame_data (expected base64)."}), 400

    # Decode JPEG bytes → encode losslessly as PNG → write via Python (cv2.imwrite
    # can silently return False on some systems; imencode + write_bytes is reliable)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Could not decode image data."}), 400

    ok, png_buf = cv2.imencode(".png", img)
    if not ok:
        return jsonify({"error": "Could not encode frame as PNG."}), 500

    video_stem  = Path(secure_filename(Path(video_name).name)).stem
    labeled_dir = project_path / "labeled-data" / video_stem
    labeled_dir.mkdir(parents=True, exist_ok=True)

    existing_pngs = [f for f in labeled_dir.iterdir() if f.suffix == ".png"]

    # Duplicate check: if frame_number already present as yyyyy in any img????-yyyyy.png, skip
    if frame_number is not None:
        dup_pat = re.compile(r"^img\d{4}-(\d+)\.png$")
        for f in existing_pngs:
            m = dup_pat.match(f.name)
            if m and int(m.group(1)) == frame_number:
                return jsonify({"skipped": True, "frame_number": frame_number}), 200

    order = len(existing_pngs)
    if frame_number is not None:
        frame_filename = f"img{order:04d}-{frame_number:05d}.png"
    else:
        frame_filename = f"img{order:04d}.png"
    (labeled_dir / frame_filename).write_bytes(png_buf.tobytes())

    return jsonify({
        "saved":        frame_filename,
        "folder":       f"labeled-data/{video_stem}",
        "abs_path":     str(labeled_dir / frame_filename),
        "frame_count":  order + 1,
    }), 201
