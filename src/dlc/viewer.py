"""
DLC Viewer Blueprint — kinematic pose overlay on original videos.

Routes:
  GET  /dlc/viewer/h5-find              ?dir=<abs-path>&stem=<video-stem>[&prefix=<scorer-prefix>]
                                        OR ?h5=<abs-path>  (direct path validation)
  GET  /dlc/viewer/h5-info              ?h5=<abs-path>
  GET  /dlc/viewer/frame-poses/<frame>  ?h5=&threshold=&parts=
  GET  /dlc/viewer/frame-poses-batch    ?h5=&start=&count=&threshold=&parts=
  GET  /dlc/viewer/frame-annotated/<frame>
                                          ?video=<abs-path>&h5=<abs-path>
                                          [&threshold=<0.0-1.0>]
                                          [&parts=<comma-separated body-part names>]
                                          [&marker_size=<int px>]
                                          [&scale=<float 0.1-4.0>]
  GET  /dlc/viewer/edit-cache           ?h5=<abs-path>
  POST /dlc/viewer/marker-edit          body: {h5, frame, bp, x, y}
  POST /dlc/viewer/save-marker-edits    body: {h5}

JSON Delta Architecture:
  Interactive marker edits are stored in a lightweight hidden JSON file
  co-located with the H5:  .{h5_stem}_edits.json
  The cache filename is dynamically derived from the H5 stem to ensure
  namespace isolation when multiple video H5 files share the same directory.
  Pose routes (frame-poses, frame-poses-batch) check this cache first and
  override H5 values with cached edits before returning to the client.
  The H5/CSV files are NOT modified until the user explicitly clicks
  "Save Adjustments", which calls /dlc/viewer/save-marker-edits.

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
import json as _json
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


# ── JSON Delta Edit-Cache Utilities ───────────────────────────────────────────
#
# Naming convention:  .{h5_stem}_edits.json  (hidden, same directory as H5)
# This ties the cache to the specific analysis file, preventing collisions
# when multiple videos share the same folder.
#
# Cache format:
#   {
#     "frame_N": {"bodypart_name": {"x": float, "y": float}},
#     ...
#   }
#
# The H5 and CSV are never modified until the user clicks "Save Adjustments".

def _edit_cache_path(h5_path: str) -> Path:
    """Return the hidden JSON edit-cache path for the given H5 file."""
    p = Path(h5_path)
    return p.parent / f".{p.stem}_edits.json"


def load_edit_cache(h5_path: str) -> dict:
    """
    Load the JSON edit cache for *h5_path*.
    Returns an empty dict if no cache file exists.
    Thread-safe: reads are atomic on POSIX for files < PIPE_BUF bytes.
    """
    cache_p = _edit_cache_path(h5_path)
    if not cache_p.is_file():
        return {}
    try:
        return _json.loads(cache_p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_edit_cache(h5_path: str, cache: dict) -> None:
    """
    Atomically overwrite the JSON edit cache for *h5_path*.
    Uses a .tmp write + rename to prevent partial writes on crash.
    """
    cache_p = _edit_cache_path(h5_path)
    tmp_p   = cache_p.parent / (cache_p.name + ".tmp")
    tmp_p.write_text(_json.dumps(cache, indent=2), encoding="utf-8")
    tmp_p.replace(cache_p)


def clear_edit_cache(h5_path: str) -> None:
    """Delete the JSON edit cache for *h5_path*. No-op if it does not exist."""
    cache_p = _edit_cache_path(h5_path)
    try:
        cache_p.unlink(missing_ok=True)
    except Exception:
        pass


def _apply_marker_edits_to_h5(h5_path: str, cache: dict) -> dict:
    """
    Apply the JSON edit cache to the H5 DataFrame.

    For each edited (frame, bodypart):
      - Overwrite (scorer, bp, 'x') and (scorer, bp, 'y') in the DataFrame.
      - Set (scorer, bp, 'likelihood') = 1.0 (manual corrections are certain).

    After patching:
      - Write H5 atomically: .tmp → rename (key="df_with_missing", mode="w").
      - Regenerate companion .csv next to the H5.
      - Invalidate the in-memory H5 LRU cache for this file so the next
        frame-poses request re-reads from disk.

    Returns: {"frames_edited": int, "bodyparts_edited": int}
    """
    import pandas as _pd

    h5_p    = Path(h5_path)
    df      = _pd.read_hdf(str(h5_p), key="df_with_missing")
    scorer  = df.columns.get_level_values("scorer")[0]
    n_frames = len(df)

    frames_edited = 0
    bps_edited    = 0

    for frame_key, bp_edits in cache.items():
        # frame_key format: "frame_N"
        try:
            frame_num = int(frame_key.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        if frame_num < 0 or frame_num >= n_frames:
            continue

        frame_had_edit = False
        for bp, coords in bp_edits.items():
            try:
                x_col  = (scorer, bp, "x")
                y_col  = (scorer, bp, "y")
                lh_col = (scorer, bp, "likelihood")
                df.iloc[frame_num, df.columns.get_loc(x_col)]  = float(coords["x"])
                df.iloc[frame_num, df.columns.get_loc(y_col)]  = float(coords["y"])
                df.iloc[frame_num, df.columns.get_loc(lh_col)] = 1.0
                bps_edited    += 1
                frame_had_edit = True
            except (KeyError, ValueError):
                continue  # unknown bodypart or column — skip silently
        if frame_had_edit:
            frames_edited += 1

    # Atomic H5 write (constraint: write to .tmp, then rename)
    tmp_p = Path(str(h5_p) + ".tmp")
    df.to_hdf(str(tmp_p), key="df_with_missing", mode="w")
    tmp_p.replace(h5_p)

    # Regenerate companion CSV (same stem, .csv extension)
    csv_p = h5_p.with_suffix(".csv")
    df.to_csv(str(csv_p))

    # Invalidate the in-memory H5 LRU cache so next request re-reads from disk
    with _viewer_h5_lock:
        _viewer_h5_cache.pop(str(h5_p), None)

    return {"frames_edited": frames_edited, "bodyparts_edited": bps_edited}


def _get_effective_poses(
    h5_path: str,
    frame_number: int,
    *,
    threshold: float = 0.6,
    selected_parts: set[str] | None = None,
) -> list[dict]:
    """
    Return pose dicts for *frame_number*, applying any JSON edit-cache overrides.

    This is the single source-of-truth for pose data consumed by frame-poses routes
    and tests.  Edit-cache hits always win over H5 data; likelihood is forced to
    1.0 for edited keypoints regardless of the threshold.

    Returns list of: {"bp": str, "x": float, "y": float, "lh": float, "color_idx": int}
    """
    h5_data   = viewer_load_h5(h5_path)
    bodyparts = h5_data["bodyparts"]
    poses_np  = h5_data["poses_np"]   # (n_frames, n_bps, 3)

    selected = selected_parts if selected_parts else set(bodyparts)
    cache    = load_edit_cache(h5_path)
    frame_key = f"frame_{frame_number}"
    frame_cache: dict = cache.get(frame_key, {})

    poses: list[dict] = []
    if frame_number >= len(poses_np):
        return poses

    frame_poses = poses_np[frame_number]   # (n_bps, 3): x, y, likelihood

    for i, bp in enumerate(bodyparts):
        if bp not in selected:
            continue

        if bp in frame_cache:
            # Edit-cache override: always shown (likelihood forced to 1.0)
            x  = float(frame_cache[bp]["x"])
            y  = float(frame_cache[bp]["y"])
            lh = 1.0
        else:
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

    return poses


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

    poses = _get_effective_poses(
        h5_path,
        frame_number,
        threshold=threshold,
        selected_parts=selected_parts,
    )

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
    n_frames  = len(h5_data["poses_np"])
    n_bps     = len(bodyparts)
    selected  = selected_parts if selected_parts else set(bodyparts)

    frames_out: dict = {}
    end = min(start + count, n_frames)
    for fn in range(start, end):
        poses = _get_effective_poses(
            h5_path, fn, threshold=threshold, selected_parts=selected,
        )
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


# ── JSON Delta: Edit-cache routes ─────────────────────────────────────────────

@bp.route("/dlc/viewer/edit-cache")
def viewer_edit_cache_get():
    """
    Return the current JSON edit cache for an H5 file.

    Query params:
      h5 : absolute path to the .h5 analysis file

    Response: {"cache": {frame_key: {bp: {x, y}}, ...}, "h5": "..."}
    """
    h5_path = request.args.get("h5", "").strip()
    if not h5_path:
        return jsonify({"error": "h5 param required."}), 400

    hp = Path(h5_path)
    if not hp.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(hp.parent):
        return jsonify({"error": "Access denied."}), 403

    cache = load_edit_cache(h5_path)
    return jsonify({"cache": cache, "h5": h5_path, "pending_frames": len(cache)})


@bp.route("/dlc/viewer/marker-edit", methods=["POST"])
def viewer_marker_edit():
    """
    Persist a single marker adjustment to the JSON edit cache.
    Does NOT modify the H5 or CSV.

    Body (JSON): {
      "h5":    absolute path to the .h5 file,
      "frame": integer frame index,
      "bp":    body-part name,
      "x":     new x coordinate (video-native pixels),
      "y":     new y coordinate (video-native pixels)
    }

    Response: {"ok": true, "pending_frames": N}
    """
    data = request.get_json(silent=True) or {}
    h5_path = str(data.get("h5", "")).strip()
    if not h5_path:
        return jsonify({"error": "h5 field required."}), 400

    hp = Path(h5_path)
    if not hp.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(hp.parent):
        return jsonify({"error": "Access denied."}), 403

    try:
        frame = int(data["frame"])
        bp    = str(data["bp"]).strip()
        x     = float(data["x"])
        y     = float(data["y"])
    except (KeyError, ValueError, TypeError) as exc:
        return jsonify({"error": f"Invalid fields: {exc}"}), 400

    if not bp:
        return jsonify({"error": "bp must be a non-empty string."}), 400

    # Load existing cache, update, save atomically
    cache = load_edit_cache(h5_path)
    frame_key = f"frame_{frame}"
    if frame_key not in cache:
        cache[frame_key] = {}
    cache[frame_key][bp] = {"x": round(x, 4), "y": round(y, 4)}
    save_edit_cache(h5_path, cache)

    return jsonify({"ok": True, "pending_frames": len(cache)})


@bp.route("/dlc/viewer/save-marker-edits", methods=["POST"])
def viewer_save_marker_edits():
    """
    Apply the JSON edit cache to the H5 and regenerate the companion CSV.
    Deletes the JSON cache file on success.

    Body (JSON): {"h5": absolute path to the .h5 file}

    Response: {
      "ok": true,
      "frames_edited":    N,
      "bodyparts_edited": M,
      "csv_regenerated":  true,
      "cache_cleared":    true
    }
    """
    data = request.get_json(silent=True) or {}
    h5_path = str(data.get("h5", "")).strip()
    if not h5_path:
        return jsonify({"error": "h5 field required."}), 400

    hp = Path(h5_path)
    if not hp.is_file():
        return jsonify({"error": "h5 file not found."}), 404
    if not _viewer_sec_check(hp.parent):
        return jsonify({"error": "Access denied."}), 403

    cache = load_edit_cache(h5_path)
    if not cache:
        return jsonify({"ok": True, "frames_edited": 0, "bodyparts_edited": 0,
                        "csv_regenerated": False, "cache_cleared": False,
                        "message": "No pending edits."})

    try:
        result = _apply_marker_edits_to_h5(h5_path, cache)
    except Exception as exc:
        return jsonify({"error": f"Failed to apply edits: {exc}"}), 500

    clear_edit_cache(h5_path)

    return jsonify({
        "ok":               True,
        "frames_edited":    result["frames_edited"],
        "bodyparts_edited": result["bodyparts_edited"],
        "csv_regenerated":  True,
        "cache_cleared":    True,
    })
