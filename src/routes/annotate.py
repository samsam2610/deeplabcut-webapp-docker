"""
Blueprint: annotate
Video annotation routes (video-info, video-frame, csv operations, video cropping).
"""
import collections
import csv as csv_module
import json
import subprocess
import threading
import uuid
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, session as flask_session

bp = Blueprint("annotate", __name__)


# ── Per-session VideoCapture cache (same pattern as dlc/videos.py) ────────────
# Keeps cv2.VideoCapture objects alive across requests so that:
#   1. The codec is not re-opened on every frame request (expensive for AVI).
#   2. Sequential playback avoids cv2.CAP_PROP_POS_FRAMES seeks entirely.
_ANV_VCAP_MAX  = 10
_anv_vcap_cache: collections.OrderedDict = collections.OrderedDict()
_anv_vcap_lock  = threading.Lock()


def _anv_user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


# ── Routes ────────────────────────────────────────────────────────
@bp.route("/annotate/video-info")
def annotate_video_info():
    """Return FPS and frame count for any video at the given absolute path."""
    import cv2

    video_path = request.args.get("path", "").strip()
    if not video_path:
        return jsonify({"error": "path required"}), 400
    p = Path(video_path)
    if not p.is_file():
        return jsonify({"error": "File not found."}), 404

    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video."}), 400

    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    return jsonify({"fps": fps, "frame_count": frame_count, "width": width, "height": height})


@bp.route("/annotate/video-frame/<int:frame_number>")
def annotate_video_frame(frame_number: int):
    """Return a single frame as JPEG from any video at the given path (query param).

    Uses a per-session VideoCapture cache so the codec stays open across requests
    and sequential playback avoids repeated CAP_PROP_POS_FRAMES seeks.
    """
    import cv2

    video_path = request.args.get("path", "").strip()
    if not video_path:
        return jsonify({"error": "path required"}), 400
    p = Path(video_path)
    if not p.is_file():
        return jsonify({"error": "File not found."}), 404

    etag = f"anv-{video_path}-{frame_number}"
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    uid   = _anv_user_id()
    vpath = str(p)

    # Get or create a per-session cache entry (LRU, capped at _ANV_VCAP_MAX)
    with _anv_vcap_lock:
        if uid not in _anv_vcap_cache:
            if len(_anv_vcap_cache) >= _ANV_VCAP_MAX:
                _, evicted = _anv_vcap_cache.popitem(last=False)
                evicted["vcap"].release()
            _anv_vcap_cache[uid] = {"vcap": None, "path": None, "pos": -1,
                                    "lock": threading.Lock()}
        _anv_vcap_cache.move_to_end(uid)
        entry = _anv_vcap_cache[uid]

    with entry["lock"]:
        # Re-open only when the video path changes
        if entry["vcap"] is None or entry["path"] != vpath or not entry["vcap"].isOpened():
            if entry["vcap"] is not None:
                entry["vcap"].release()
            entry["vcap"] = cv2.VideoCapture(vpath)
            entry["path"] = vpath
            entry["pos"]  = -1
            if not entry["vcap"].isOpened():
                return jsonify({"error": "Could not open video."}), 400

        # Sequential read: skip the seek when requesting exactly the next frame
        if frame_number != entry["pos"] + 1:
            entry["vcap"].set(cv2.CAP_PROP_POS_FRAMES, frame_number)

        ok, frame = entry["vcap"].read()
        entry["pos"] = frame_number if ok else -1

    if not ok:
        return jsonify({"error": "Could not read frame."}), 400

    ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok2:
        return jsonify({"error": "Encoding failed."}), 500

    resp = Response(buf.tobytes(), mimetype="image/jpeg")
    resp.headers["ETag"]          = etag
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@bp.route("/annotate/csv")
def annotate_csv():
    """Return CSV annotation rows for any video path (looks for same-stem .csv)."""
    video_path = request.args.get("path", "").strip()
    if not video_path:
        return jsonify({"error": "path required"}), 400

    p        = Path(video_path)
    csv_path = p.with_suffix(".csv")

    if not csv_path.is_file():
        return jsonify({"rows": [], "csv_path": str(csv_path), "csv_exists": False})

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
                if not note and (not status or status == "0"):
                    continue
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
    return jsonify({"rows": rows, "csv_path": str(csv_path), "csv_exists": True})


@bp.route("/annotate/create-csv", methods=["POST"])
def annotate_create_csv():
    """Create a CSV pre-populated with every frame (1…frame_count), status=0, note=''."""
    body        = request.get_json(force=True) or {}
    video_path  = body.get("video_path", "").strip()
    fps         = float(body.get("fps", 30.0))
    frame_count = int(body.get("frame_count", 0))

    if not video_path:
        return jsonify({"error": "video_path required"}), 400

    p = Path(video_path)
    if not p.is_file():
        return jsonify({"error": "Video not found."}), 404

    csv_path = p.with_suffix(".csv")
    if csv_path.exists():
        return jsonify({"error": "CSV already exists."}), 400

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(
                f, fieldnames=["timestamp", "frame_number", "frame_line_status", "note"]
            )
            writer.writeheader()
            for fn in range(1, frame_count + 1):
                timestamp = f"{fn / fps:.3f}"
                writer.writerow({"frame_number": fn, "timestamp": timestamp,
                                  "frame_line_status": "0", "note": ""})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"csv_path": str(csv_path), "rows": []})


@bp.route("/annotate/save-row", methods=["POST"])
def annotate_save_row():
    """Create or update an annotation row in the companion CSV."""
    body         = request.get_json(force=True) or {}
    csv_path_str = body.get("csv_path", "").strip()
    frame_number = body.get("frame_number")
    note         = body.get("note", "")
    status       = body.get("frame_line_status", "0")
    fps          = float(body.get("fps", 30.0))

    if not csv_path_str or frame_number is None:
        return jsonify({"error": "csv_path and frame_number required"}), 400

    csv_path = Path(csv_path_str)

    rows: dict = {}
    if csv_path.is_file():
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv_module.DictReader(f, skipinitialspace=True)
                if reader.fieldnames:
                    reader.fieldnames = [n.strip() for n in reader.fieldnames]
                for row in reader:
                    row = {k.strip() if k else k: v for k, v in row.items()}
                    try:
                        fn = int(float(row.get("frame_number", 0)))
                    except (ValueError, TypeError):
                        fn = 0
                    rows[fn] = {
                        "frame_number":      fn,
                        "timestamp":         (row.get("timestamp") or "").strip(),
                        "frame_line_status": (row.get("frame_line_status") or "").strip(),
                        "note":              (row.get("note") or "").strip(),
                    }
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    timestamp = f"{int(frame_number) / fps:.3f}"
    rows[int(frame_number)] = {
        "frame_number":      int(frame_number),
        "timestamp":         timestamp,
        "frame_line_status": str(status),
        "note":              str(note),
    }

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(
                f, fieldnames=["timestamp", "frame_number", "frame_line_status", "note"]
            )
            writer.writeheader()
            for fn in sorted(rows.keys()):
                writer.writerow(rows[fn])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    saved_row = rows[int(frame_number)]
    return jsonify({"row": saved_row, "csv_path": str(csv_path)})


# ── ffprobe codec name → ffmpeg encoder name ──────────────────────
_CODEC_MAP = {
    "h264":        "libx264",
    "hevc":        "libx265",
    "h265":        "libx265",
    "vp8":         "libvpx",
    "vp9":         "libvpx-vp9",
    "av1":         "libaom-av1",
    "mpeg4":       "mpeg4",
    "mjpeg":       "mjpeg",
    "mpeg2video":  "mpeg2video",
    "mpeg1video":  "mpeg1video",
    "wmv2":        "wmv2",
    "wmv1":        "wmv1",
    "flv1":        "flv",
    "theora":      "libtheora",
}


@bp.route("/annotate/crop-video", methods=["POST"])
def annotate_crop_video():
    """Crop a video clip from start_frame for num_frames frames.

    Re-encodes using the same codec as the original (detected via ffprobe).
    If a companion CSV exists, crops it to the same frame range (frame numbers
    are remapped to be 1-indexed relative to the clip start).

    Output filename: {original_stem}_{start_frame}_{end_frame}[_{postfix}]{ext}
    Output dir: output_dir if given, else {video_parent}/{video_stem}/
    """
    import cv2

    body        = request.get_json(force=True) or {}
    video_path  = body.get("video_path", "").strip()
    start_frame = body.get("start_frame")
    num_frames  = body.get("num_frames")
    output_dir  = body.get("output_dir", "").strip()
    postfix     = body.get("postfix", "").strip()

    if not video_path:
        return jsonify({"error": "video_path required"}), 400
    if start_frame is None or num_frames is None:
        return jsonify({"error": "start_frame and num_frames required"}), 400
    try:
        start_frame = int(start_frame)
        num_frames  = int(num_frames)
    except (ValueError, TypeError):
        return jsonify({"error": "start_frame and num_frames must be integers"}), 400
    if num_frames <= 0:
        return jsonify({"error": "num_frames must be > 0"}), 400

    p = Path(video_path)
    if not p.is_file():
        return jsonify({"error": "Video not found."}), 404

    # ── Read video metadata ───────────────────────────────────────
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video."}), 400
    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    # Clamp to valid range
    start_frame      = max(0, min(start_frame, max(total_frames - 1, 0)))
    end_frame        = min(start_frame + num_frames - 1, max(total_frames - 1, 0))
    actual_frames    = end_frame - start_frame + 1

    # ── Determine output directory ────────────────────────────────
    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = p.parent / p.stem
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Cannot create output dir: {exc}"}), 500

    # ── Build output filename ─────────────────────────────────────
    name_parts = [p.stem, str(start_frame), str(end_frame)]
    if postfix:
        name_parts.append(postfix)
    out_name = "_".join(name_parts) + p.suffix
    out_path = out_dir / out_name

    # ── Detect source codec via ffprobe ───────────────────────────
    codec_name = "h264"
    pix_fmt    = "yuv420p"
    bit_rate   = None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,bit_rate,pix_fmt",
             "-of", "json", str(p)],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            streams = json.loads(probe.stdout).get("streams", [{}])
            if streams:
                codec_name = streams[0].get("codec_name", codec_name)
                pix_fmt    = streams[0].get("pix_fmt", pix_fmt)
                br         = streams[0].get("bit_rate")
                if br and int(br) > 0:
                    bit_rate = br
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, KeyError):
        pass  # fall back to h264 defaults

    encoder = _CODEC_MAP.get(codec_name, codec_name)

    # ── Build ffmpeg command ──────────────────────────────────────
    start_time = start_frame / fps
    duration   = actual_frames / fps

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_time:.6f}",
        "-i", str(p),
        "-t", f"{duration:.6f}",
        "-c:v", encoder,
        "-pix_fmt", pix_fmt,
        "-an",        # drop audio (annotation clips are typically silent)
        str(out_path),
    ]
    if bit_rate:
        # Insert -b:v before -an
        idx = cmd.index("-an")
        cmd[idx:idx] = ["-b:v", bit_rate]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return jsonify({"error": f"ffmpeg failed: {result.stderr[-800:]}"}), 500
    except FileNotFoundError:
        return jsonify({"error": "ffmpeg not found on this server."}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ffmpeg timed out."}), 500

    # ── Crop companion CSV (if present) ──────────────────────────
    csv_path     = p.with_suffix(".csv")
    out_csv_path = None
    if csv_path.is_file():
        out_csv_path = out_path.with_suffix(".csv")
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader     = csv_module.DictReader(f, skipinitialspace=True)
                fieldnames = [n.strip() for n in (reader.fieldnames or [])]
                cropped    = []
                for row in reader:
                    row = {k.strip() if k else k: v for k, v in row.items()}
                    try:
                        orig_fn = int(float(row.get("frame_number", -1)))
                    except (ValueError, TypeError):
                        continue
                    if start_frame <= orig_fn <= end_frame:
                        new_fn  = orig_fn - start_frame + 1   # 1-indexed in clip
                        new_ts  = f"{new_fn / fps:.3f}"
                        new_row = dict(row)
                        new_row["frame_number"] = str(new_fn)
                        new_row["timestamp"]    = new_ts
                        cropped.append(new_row)

            with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(
                    f,
                    fieldnames=fieldnames or ["timestamp", "frame_number",
                                              "frame_line_status", "note"],
                )
                writer.writeheader()
                writer.writerows(cropped)
        except Exception as exc:
            out_csv_path = None   # CSV crop failed; video succeeded — not fatal

    return jsonify({
        "output_path": str(out_path),
        "csv_path":    str(out_csv_path) if out_csv_path else None,
        "start_frame": start_frame,
        "end_frame":   end_frame,
        "num_frames":  actual_frames,
    })
