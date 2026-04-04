"""
DLC Unified Viewer Engine — memory-safe, generator-based frame renderer.

Design constraints enforced here:
  1. GPU routing — cv2.VideoCapture and cv2.circle are CPU-only; no CUDA
     allocation occurs in this module.  Per project convention:
       GPU 0 = RTX 5090  →  DLC inference / training (Celery workers)
       GPU 1 = Blackwell →  Orchestrator / LLM
     The caller is responsible for setting CUDA_VISIBLE_DEVICES when
     dispatching GPU work.

  2. One frame in memory at a time.
     After encoding each raw frame to JPEG:
       - ``del frame`` frees the numpy BGR array.
       - ``del buf``   frees the cv2.imencode output buffer.
       - ``del _prev_jpeg`` at the top of the next iteration frees the
         JPEG bytes returned by the previous ``yield`` (the caller has
         already consumed them by then).
       - ``gc.collect()`` runs every ``_GC_INTERVAL`` frames to break
         any cyclic references the garbage collector has not yet collected.
     At any point in the generator loop the live heap contains:
       • The JPEG bytes currently being yielded (one per iteration).
       • The VideoCapture handle (persistent, closed in ``finally``).
       • The poses_np array when h5 is loaded (persistent, LRU-cached).
     No other frame-sized buffers accumulate.

  3. Strict streaming — no look-ahead, no pre-buffering of multiple frames.
"""
from __future__ import annotations

import csv as csv_module
import gc
from pathlib import Path
from typing import Generator

# How often to invoke the cyclic garbage collector.
# Every frame would be too slow; every 100 iterations is negligible overhead.
_GC_INTERVAL = 100


# ── Companion CSV helpers ─────────────────────────────────────────────────────

def find_companion_csv(video_path: str) -> "Path | None":
    """
    Return the Path of the companion .csv file (same base name as the video),
    or None if it does not exist.

    Example: /data/videos/video1.avi  →  /data/videos/video1.csv
    """
    p = Path(video_path)
    csv_path = p.with_suffix(".csv")
    return csv_path if csv_path.is_file() else None


def read_companion_csv(csv_path: str) -> list:
    """
    Parse a companion annotation CSV.  Returns a list of row dicts:
        [{"frame_number": int, "timestamp": str,
          "frame_line_status": str, "note": str}, ...]
    All rows are included (not filtered by note/status content).
    Sorted ascending by frame_number.
    """
    rows: list = []
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
                rows.append({
                    "frame_number":      fn,
                    "timestamp":         (row.get("timestamp") or "").strip(),
                    "frame_line_status": (row.get("frame_line_status") or "").strip(),
                    "note":              (row.get("note") or "").strip(),
                })
    except OSError:
        return []
    rows.sort(key=lambda r: r["frame_number"])
    return rows


def csv_row_for_frame(rows: list, frame_number: int) -> "dict | None":
    """
    Binary-search for the annotation row matching *frame_number*.
    *rows* must be sorted ascending by frame_number (as returned by
    ``read_companion_csv``).  Returns None if not found.
    """
    lo, hi = 0, len(rows) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        fn  = rows[mid]["frame_number"]
        if fn == frame_number:
            return rows[mid]
        elif fn < frame_number:
            lo = mid + 1
        else:
            hi = mid - 1
    return None


# ── DLC h5 file helpers ───────────────────────────────────────────────────────

def find_dlc_h5(video_path: str, *, prefix: str = "") -> list:
    """
    Search the same directory as *video_path* for DLC analysis .h5 files
    whose name starts with the video stem (i.e. ``<stem>*.h5``).

    Optionally filter by *prefix* (case-insensitive substring match against
    the full file name — useful for filtering by scorer name).

    Returns a sorted list of matching ``Path`` objects (may be empty).
    """
    p    = Path(video_path)
    stem = p.stem
    d    = p.parent
    if not d.is_dir():
        return []
    candidates = sorted(d.glob(f"{stem}*.h5"))
    if prefix:
        candidates = [c for c in candidates if prefix.lower() in c.name.lower()]
    return candidates


# ── Frame generator ───────────────────────────────────────────────────────────

def frame_generator(
    video_path: str,
    *,
    h5_path: "str | None" = None,
    start: int = 0,
    end: "int | None" = None,
    threshold: float = 0.6,
    selected_parts: "list | None" = None,
    marker_size: int = 6,
    scale: float = 1.0,
    jpeg_quality: int = 85,
) -> Generator:
    """
    Memory-safe generator that yields ``(frame_number, jpeg_bytes)`` tuples,
    one frame at a time.

    Memory contract
    ---------------
    After encoding each frame to JPEG:
      - ``del frame`` → the raw numpy BGR array is freed immediately.
      - ``del buf``   → the imencode scratch buffer is freed immediately.
      - At the start of the *next* iteration ``del _prev_jpeg`` frees the
        JPEG bytes the caller held during the previous ``yield``.
      - ``gc.collect()`` runs every ``_GC_INTERVAL`` frames.
      - ``cv2.VideoCapture.release()`` is called unconditionally in
        the ``finally`` block.

    GPU routing
    -----------
    All operations here are CPU-only.  The caller must set
    ``CUDA_VISIBLE_DEVICES`` before spawning any GPU workload.

    Parameters
    ----------
    video_path    : Absolute path to the source video file.
    h5_path       : Optional DLC analysis .h5 file.  When provided, pose
                    markers are drawn onto each frame via ``cv2.circle``.
    start         : First frame index to yield (0-based, default 0).
    end           : One-past-the-last frame index (None = until EOF).
    threshold     : Likelihood cutoff for marker rendering (default 0.6).
    selected_parts: Body-part names to render; None = all parts.
    marker_size   : Marker circle radius in pixels (default 6).
    scale         : Canvas scale factor clamped to [0.1, 4.0] (default 1.0).
    jpeg_quality  : JPEG encoding quality 1–100 (default 85).

    Yields
    ------
    (frame_number, jpeg_bytes)
        frame_number is the 0-based index in the original video timeline.
        jpeg_bytes is empty (b"") if cv2.imencode fails.
    """
    import cv2

    scale = max(0.1, min(float(scale), 4.0))

    # ── Load pose data (reuses existing LRU cache) ────────────────────────
    poses_np   = None
    bodyparts: list = []
    palette:   list = []
    bp_index:  dict = {}
    _n_h5: int = 0

    if h5_path:
        try:
            from dlc.viewer import viewer_load_h5, viewer_palette
            h5_data   = viewer_load_h5(h5_path)
            poses_np  = h5_data["poses_np"]   # shape (n_frames, n_bps, 3)
            bodyparts = h5_data["bodyparts"]
            palette   = viewer_palette(len(bodyparts))
            bp_index  = {bp: i for i, bp in enumerate(bodyparts)}
            _n_h5     = len(poses_np)
        except Exception:
            poses_np = None   # graceful degradation: render without markers

    _selected = set(selected_parts) if selected_parts else set(bodyparts)

    # ── Open VideoCapture ─────────────────────────────────────────────────
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return

    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        _end  = min(end, total) if end is not None else total

        if start > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(start))

        frame_number = start
        _prev_jpeg: "bytes | None" = None

        while True:
            if frame_number >= _end:
                break

            ret, frame = cap.read()
            if not ret:
                break

            # ── Optional resize ───────────────────────────────────────────
            if abs(scale - 1.0) > 0.01:
                h0, w0 = frame.shape[:2]
                frame = cv2.resize(
                    frame,
                    (int(w0 * scale), int(h0 * scale)),
                    interpolation=cv2.INTER_LINEAR,
                )

            # ── Draw pose markers ─────────────────────────────────────────
            if poses_np is not None and frame_number < _n_h5:
                h_fr, w_fr = frame.shape[:2]
                orig_h = int(h_fr / scale)
                orig_w = int(w_fr / scale)
                sx = w_fr / orig_w
                sy = h_fr / orig_h
                frame_poses = poses_np[frame_number]   # shape (n_bps, 3)
                r = max(1, int(marker_size))
                for bp, idx in bp_index.items():
                    if bp not in _selected:
                        continue
                    x  = float(frame_poses[idx, 0])
                    y  = float(frame_poses[idx, 1])
                    lh = float(frame_poses[idx, 2])
                    if lh < threshold:
                        continue
                    cx    = int(x * sx)
                    cy    = int(y * sy)
                    color = palette[idx]
                    cv2.circle(frame, (cx, cy), r,     color,     -1, lineType=cv2.LINE_AA)
                    cv2.circle(frame, (cx, cy), r + 1, (0, 0, 0),  1, lineType=cv2.LINE_AA)

            # ── Encode to JPEG ────────────────────────────────────────────
            ok, buf = cv2.imencode(
                ".jpg", frame,
                [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)],
            )
            jpeg_bytes: bytes = buf.tobytes() if ok else b""

            # Free the frame buffer and encode buffer BEFORE yielding.
            del frame, buf

            # Free the PREVIOUS yield's JPEG bytes (caller has consumed them).
            if _prev_jpeg is not None:
                del _prev_jpeg
            _prev_jpeg = jpeg_bytes

            # Periodic GC to collect any lingering cyclic references.
            if frame_number % _GC_INTERVAL == 0:
                gc.collect()

            yield frame_number, jpeg_bytes

            frame_number += 1

    finally:
        cap.release()
        gc.collect()
