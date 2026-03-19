"""
DLC Viewer Blueprint — kinematic pose overlay on original videos.

Routes:
  GET /dlc/viewer/h5-find              ?dir=<abs-path>&stem=<video-stem>[&prefix=<scorer-prefix>]
                                       OR ?h5=<abs-path>  (direct path validation)
  GET /dlc/viewer/h5-info              ?h5=<abs-path>
  GET /dlc/viewer/frame-poses/<frame>  ?h5=&threshold=&parts=
  GET /dlc/viewer/frame-poses-batch    ?h5=&start=&count=&threshold=&parts=
  GET /dlc/viewer/frame-annotated/<frame>
                                         ?video=<abs-path>&h5=<abs-path>
                                         [&threshold=<0.0-1.0>]
                                         [&parts=<comma-separated body-part names>]
                                         [&marker_size=<int px>]
                                         [&scale=<float 0.1-4.0>]

Performance notes:
  - viewer_load_h5 loads the full DataFrame once and caches it (LRU, max 5 files).
    It also pre-computes a compact NumPy array (n_frames × n_bodyparts × 3) so that
    per-frame pose lookups avoid repeated pandas MultiIndex navigation.
  - frame-poses-batch lets the client prefetch pose data for a window of upcoming
    frames in a single HTTP round trip, eliminating per-frame pose requests during
    video playback.

Rendering uses raw cv2.VideoCapture + cv2.circle — no matplotlib.
All caches use the `viewer_` prefix to avoid collisions with dlc/videos.py.
"""
from __future__ import annotations

import collections as _collections
import colorsys as _colorsys
import threading as _threading
import uuid

import numpy as _np

from pathlib import Path
from flask import Blueprint, request, jsonify, Response, session as flask_session

from . import ctx as _ctx
from dlc.utils import _dlc_project_security_check

bp = Blueprint("dlc_viewer", __name__)


# ── Per-path h5 DataFrame cache (LRU, max 5 files in memory) ─────────────────
_VIEWER_H5_CACHE_MAX = 5
_viewer_h5_cache: _collections.OrderedDict = _collections.OrderedDict()
_viewer_h5_lock = _threading.Lock()

# ── Per-session VideoCapture cache (separate from dlc/videos.py) ─────────────
_VIEWER_VCAP_MAX = 10
_viewer_vcap_cache: _collections.OrderedDict = _collections.OrderedDict()
_viewer_vcap_lock = _threading.Lock()


# ── Colour palette ────────────────────────────────────────────────────────────

def viewer_palette(n: int) -> list[tuple[int, int, int]]:
    """Return N evenly-spaced BGR colours from an HSV rainbow palette."""
    colors: list[tuple[int, int, int]] = []
    for i in range(max(n, 1)):
        h = i / max(n, 1)
        r, g, b = _colorsys.hsv_to_rgb(h, 0.9, 0.95)
        colors.append((int(b * 255), int(g * 255), int(r * 255)))
    return colors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _viewer_user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _viewer_sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def viewer_load_h5(h5_path: str) -> dict:
    """
    Load (or retrieve cached) h5 DataFrame.
    Returns dict with keys: df, scorer, bodyparts.
    Thread-safe: loads outside the lock to avoid blocking other requests.
    """
    import pandas as pd

    with _viewer_h5_lock:
        if h5_path in _viewer_h5_cache:
            _viewer_h5_cache.move_to_end(h5_path)
            return _viewer_h5_cache[h5_path]

    # Load outside lock — can take a few seconds for large files
    df        = pd.read_hdf(h5_path)
    scorer    = df.columns.get_level_values("scorer")[0]
    bodyparts = df[scorer].columns.get_level_values("bodyparts").unique().tolist()

    # Pre-compute a compact NumPy array: shape (n_frames, n_bodyparts, 3)
    # Dim-2 encoding: 0 = x, 1 = y, 2 = likelihood
    # This avoids repeated pandas MultiIndex navigation on every frame request.
    n_frames = len(df)
    n_bps    = len(bodyparts)
    poses_np = _np.empty((n_frames, n_bps, 3), dtype=_np.float32)
    for i, bp in enumerate(bodyparts):
        poses_np[:, i, 0] = df[scorer][bp]["x"].values
        poses_np[:, i, 1] = df[scorer][bp]["y"].values
        poses_np[:, i, 2] = df[scorer][bp]["likelihood"].values

    entry = {"df": df, "scorer": scorer, "bodyparts": bodyparts, "poses_np": poses_np}

    with _viewer_h5_lock:
        if len(_viewer_h5_cache) >= _VIEWER_H5_CACHE_MAX:
            _viewer_h5_cache.popitem(last=False)
        _viewer_h5_cache[h5_path] = entry
        _viewer_h5_cache.move_to_end(h5_path)

    return entry


def _viewer_get_vcap_entry(uid: str) -> dict:
    """Return (creating if needed) the per-session vcap cache entry."""
    with _viewer_vcap_lock:
        if uid not in _viewer_vcap_cache:
            if len(_viewer_vcap_cache) >= _VIEWER_VCAP_MAX:
                _, ev = _viewer_vcap_cache.popitem(last=False)
                try:
                    ev["vcap"].release()
                except Exception:
                    pass
            _viewer_vcap_cache[uid] = {
                "vcap": None, "path": None, "pos": -1,
                "lock": _threading.Lock(),
            }
        _viewer_vcap_cache.move_to_end(uid)
        return _viewer_vcap_cache[uid]


def viewer_render_frame(
    video_path: str,
    h5_path: str,
    frame_number: int,
    *,
    threshold: float = 0.6,
    selected_parts: list[str] | None = None,
    marker_size: int = 6,
    scale: float = 1.0,
    uid: str = "",
) -> bytes | None:
    """
    Read one frame from *video_path* with cv2.VideoCapture, draw DLC pose
    markers from *h5_path* using cv2.circle, return JPEG bytes.

    Returns None when the frame cannot be read.
    This function is importable by tests independently of Flask.
    """
    import cv2

    # ── Load pose data (cached) ───────────────────────────────────
    h5_data   = viewer_load_h5(h5_path)
    bodyparts = h5_data["bodyparts"]
    poses_np  = h5_data["poses_np"]   # (n_frames, n_bps, 3): x, y, likelihood

    selected  = set(selected_parts) if selected_parts else set(bodyparts)
    palette   = viewer_palette(len(bodyparts))
    bp_color  = {bp: palette[i] for i, bp in enumerate(bodyparts)}

    # ── Get / open VideoCapture (per-session cache) ───────────────
    entry = _viewer_get_vcap_entry(uid)
    with entry["lock"]:
        if (entry["vcap"] is None
                or entry["path"] != video_path
                or not entry["vcap"].isOpened()):
            if entry["vcap"] is not None:
                entry["vcap"].release()
            entry["vcap"] = cv2.VideoCapture(video_path)
            entry["path"] = video_path
            entry["pos"]  = -1
            if not entry["vcap"].isOpened():
                return None

        if frame_number != entry["pos"] + 1:
            entry["vcap"].set(cv2.CAP_PROP_POS_FRAMES, frame_number)

        ret, frame = entry["vcap"].read()
        entry["pos"] = frame_number if ret else -1

    if not ret:
        return None

    # ── Resize canvas if scale ≠ 1.0 ─────────────────────────────
    scale = max(0.1, min(float(scale), 4.0))
    if abs(scale - 1.0) > 0.01:
        h0, w0 = frame.shape[:2]
        frame = cv2.resize(
            frame,
            (int(w0 * scale), int(h0 * scale)),
            interpolation=cv2.INTER_LINEAR,
        )

    h_fr, w_fr = frame.shape[:2]
    # Coordinate scale factors (video coords → canvas coords)
    orig_h = int(h_fr / scale)
    orig_w = int(w_fr / scale)
    sx = w_fr / orig_w
    sy = h_fr / orig_h

    # ── Draw markers (NumPy array lookup — faster than pandas iloc) ───
    if frame_number < len(poses_np):
        frame_poses = poses_np[frame_number]       # shape (n_bps, 3)
        for i, bp in enumerate(bodyparts):
            if bp not in selected:
                continue
            x  = float(frame_poses[i, 0])
            y  = float(frame_poses[i, 1])
            lh = float(frame_poses[i, 2])
            if lh < threshold:
                continue
            cx    = int(x * sx)
            cy    = int(y * sy)
            color = bp_color[bp]
            r     = max(1, int(marker_size))
            cv2.circle(frame, (cx, cy), r,     color,     -1,  lineType=cv2.LINE_AA)
            cv2.circle(frame, (cx, cy), r + 1, (0, 0, 0),  1,  lineType=cv2.LINE_AA)

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else None


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/dlc/viewer/h5-find")
def viewer_h5_find():
    """
    Locate an h5 analysis file for a video.

    Method B (direct):  ?h5=<abs-path>
    Method A (scan):    ?dir=<abs-dir>&stem=<video-stem>[&prefix=<scorer-prefix>]
    """
    # Method B — direct path validation
    h5_direct = request.args.get("h5", "").strip()
    if h5_direct:
        p = Path(h5_direct)
        if p.suffix != ".h5" or not p.is_file():
            return jsonify({"error": f"h5 file not found: {h5_direct}"}), 404
        if not _viewer_sec_check(p.parent):
            return jsonify({"error": "Access denied."}), 403
        return jsonify({"h5_path": str(p), "method": "direct"})

    # Method A — scan directory
    search_dir = request.args.get("dir",    "").strip()
    stem       = request.args.get("stem",   "").strip()
    prefix     = request.args.get("prefix", "").strip().lower()

    if not search_dir or not stem:
        return jsonify({"error": "dir and stem are required for Method A."}), 400

    d = Path(search_dir)
    if not d.is_dir():
        return jsonify({"error": f"Directory not found: {search_dir}"}), 404
    if not _viewer_sec_check(d):
        return jsonify({"error": "Access denied."}), 403

    candidates = sorted(d.glob(f"{stem}*.h5"))
    if prefix:
        candidates = [p for p in candidates if prefix in p.name.lower()]

    if not candidates:
        return jsonify({"error": f"No .h5 file found for '{stem}' in {search_dir}."}), 404

    return jsonify({
        "h5_path":     str(candidates[0]),
        "method":      "prefix",
        "all_matches": [str(c) for c in candidates],
    })


@bp.route("/dlc/viewer/h5-info")
def viewer_h5_info():
    """
    Return scorer, bodyparts, frame_count for an h5 file.
    Reads only the first row to determine schema (fast).
    """
    import pandas as pd

    h5_path = request.args.get("h5", "").strip()
    if not h5_path:
        return jsonify({"error": "h5 parameter required."}), 400

    p = Path(h5_path)
    if not p.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(p.parent):
        return jsonify({"error": "Access denied."}), 403

    try:
        with pd.HDFStore(h5_path, mode="r") as store:
            key     = store.keys()[0]
            storer  = store.get_storer(key)
            nrows   = storer.nrows
            # Read a single row to inspect columns
            df_head = store.select(key, start=0, stop=1)

        scorer    = df_head.columns.get_level_values("scorer")[0]
        bodyparts = df_head[scorer].columns.get_level_values("bodyparts").unique().tolist()
        return jsonify({
            "scorer":      scorer,
            "bodyparts":   bodyparts,
            "frame_count": int(nrows),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/dlc/viewer/frame-poses/<int:frame_number>")
def viewer_frame_poses(frame_number: int):
    """
    Return visible marker positions for a single frame as compact JSON.
    Used by the client canvas overlay for hover hit-testing.

    Query params:
      h5        : absolute path to the .h5 analysis file
      threshold : likelihood cutoff (default 0.6)
      parts     : comma-separated body-part names (default: all)

    Response: { "poses": [{"bp": "Snout", "x": 712.1, "y": 308.9, "lh": 0.98, "color_idx": 0}, ...],
                "bodyparts": [...] }
    """
    h5_path = request.args.get("h5", "").strip()
    if not h5_path:
        return jsonify({"error": "h5 param required."}), 400

    hp = Path(h5_path)
    if not hp.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(hp.parent):
        return jsonify({"error": "Access denied."}), 403

    try:
        threshold = float(request.args.get("threshold", "0.6"))
    except ValueError:
        threshold = 0.6

    parts_raw      = request.args.get("parts", "").strip()
    selected_parts = {s.strip() for s in parts_raw.split(",") if s.strip()} or None

    h5_data   = viewer_load_h5(h5_path)
    bodyparts = h5_data["bodyparts"]
    poses_np  = h5_data["poses_np"]   # (n_frames, n_bps, 3)

    selected = selected_parts if selected_parts else set(bodyparts)

    poses = []
    if frame_number < len(poses_np):
        frame_poses = poses_np[frame_number]   # (n_bps, 3)
        for i, bp in enumerate(bodyparts):
            if bp not in selected:
                continue
            x  = float(frame_poses[i, 0])
            y  = float(frame_poses[i, 1])
            lh = float(frame_poses[i, 2])
            if lh < threshold:
                continue
            poses.append({
                "bp":        bp,
                "x":         round(x, 2),
                "y":         round(y, 2),
                "lh":        round(lh, 4),
                "color_idx": i,
            })

    return jsonify({"poses": poses, "bodyparts": bodyparts, "n_bodyparts": len(bodyparts)})


@bp.route("/dlc/viewer/frame-poses-batch")
def viewer_frame_poses_batch():
    """
    Return visible marker positions for a contiguous window of frames as JSON.

    Designed for client-side pose caching: fetch a batch up-front so the browser
    can display hover labels without a per-frame HTTP round trip during playback.

    Query params:
      h5        : absolute path to the .h5 analysis file
      start     : first frame index (default 0)
      count     : number of frames to return (default 30, capped at 300)
      threshold : likelihood cutoff (default 0.6)
      parts     : comma-separated body-part names (default: all)

    Response:
      {
        "frames": {
          "0": {"poses": [{"bp":…,"x":…,"y":…,"lh":…,"color_idx":…}, …],
                "n_bodyparts": N},
          "1": { … },
          …
        },
        "bodyparts": […]
      }
    """
    h5_path = request.args.get("h5", "").strip()
    if not h5_path:
        return jsonify({"error": "h5 param required."}), 400

    hp = Path(h5_path)
    if not hp.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(hp.parent):
        return jsonify({"error": "Access denied."}), 403

    try:
        start = max(0, int(request.args.get("start", 0)))
        count = min(max(1, int(request.args.get("count", 30))), 300)
    except ValueError:
        start, count = 0, 30

    try:
        threshold = float(request.args.get("threshold", "0.6"))
    except ValueError:
        threshold = 0.6

    parts_raw      = request.args.get("parts", "").strip()
    selected_parts = {s.strip() for s in parts_raw.split(",") if s.strip()} or None

    h5_data   = viewer_load_h5(h5_path)
    bodyparts = h5_data["bodyparts"]
    poses_np  = h5_data["poses_np"]   # (n_frames, n_bps, 3)
    n_frames  = len(poses_np)
    n_bps     = len(bodyparts)
    selected  = selected_parts if selected_parts else set(bodyparts)

    frames_out: dict = {}
    end = min(start + count, n_frames)
    for fn in range(start, end):
        frame_poses = poses_np[fn]   # (n_bps, 3)
        poses = []
        for i, bp in enumerate(bodyparts):
            if bp not in selected:
                continue
            x  = float(frame_poses[i, 0])
            y  = float(frame_poses[i, 1])
            lh = float(frame_poses[i, 2])
            if lh < threshold:
                continue
            poses.append({
                "bp":        bp,
                "x":         round(x, 2),
                "y":         round(y, 2),
                "lh":        round(lh, 4),
                "color_idx": i,
            })
        frames_out[str(fn)] = {"poses": poses, "n_bodyparts": n_bps}

    return jsonify({"frames": frames_out, "bodyparts": bodyparts})


@bp.route("/dlc/viewer/frame-annotated/<int:frame_number>")
def viewer_frame_annotated(frame_number: int):
    """
    Return a single JPEG frame from *video* with DLC pose overlay rendered
    using cv2.circle / cv2.VideoCapture — no matplotlib.

    Query params:
      video       : absolute path to the original video file
      h5          : absolute path to the .h5 analysis file
      threshold   : likelihood cutoff (default 0.6)
      parts       : comma-separated body-part names to show (default: all)
      marker_size : circle radius in pixels (default 6)
      scale       : canvas scale factor, 0.1–4.0 (default 1.0)
    """
    video_path = request.args.get("video", "").strip()
    h5_path    = request.args.get("h5",    "").strip()

    if not video_path or not h5_path:
        return jsonify({"error": "video and h5 params are required."}), 400

    vp = Path(video_path)
    hp = Path(h5_path)

    if not vp.is_file():
        return jsonify({"error": "Video not found."}), 404
    if not hp.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(vp.parent) or not _viewer_sec_check(hp.parent):
        return jsonify({"error": "Access denied."}), 403

    try:
        threshold = float(request.args.get("threshold", "0.6"))
    except ValueError:
        threshold = 0.6

    try:
        marker_size = max(1, int(request.args.get("marker_size", "6")))
    except ValueError:
        marker_size = 6

    try:
        scale = float(request.args.get("scale", "1.0"))
    except ValueError:
        scale = 1.0

    parts_raw      = request.args.get("parts", "").strip()
    selected_parts = [s.strip() for s in parts_raw.split(",") if s.strip()] or None

    uid        = _viewer_user_id()
    jpeg_bytes = viewer_render_frame(
        video_path    = str(vp),
        h5_path       = str(hp),
        frame_number  = frame_number,
        threshold     = threshold,
        selected_parts= selected_parts,
        marker_size   = marker_size,
        scale         = scale,
        uid           = uid,
    )

    if jpeg_bytes is None:
        return jsonify({"error": "Could not read or render frame."}), 404

    etag = f"va-ann-{video_path}-{h5_path}-{frame_number}-{threshold}-{marker_size}-{parts_raw}-{scale}"
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    resp = Response(jpeg_bytes, mimetype="image/jpeg")
    resp.headers["ETag"]          = etag
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp
