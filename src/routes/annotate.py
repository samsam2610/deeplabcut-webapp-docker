"""
Blueprint: annotate
Video annotation routes (video-info, video-frame, csv operations).
"""
import csv as csv_module
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

bp = Blueprint("annotate", __name__)


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
    """Return a single frame as JPEG from any video at the given path (query param)."""
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

    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return jsonify({"error": "Could not open video."}), 400

    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return jsonify({"error": "Could not read frame."}), 400

    ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
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
