"""
VLM Visual Memory Indexer — Component A.

Scans a DLC project's labeled-data/, generates compact feature vectors for
every labeled PNG, and persists a searchable index alongside the project.

Feature strategy (no CLIP required):
  1. Pixel features  — resize to 32×32 grayscale, flatten → 1024-dim float.
                        Uses cv2 (flask container) or PIL (worker container).
  2. Ollama embed    — optional text embedding of a VLM description;
                        activated when OLLAMA_URL is reachable and
                        nomic-embed-text (or similar) is available.
     The two vectors are concatenated and re-normalised so either alone
     still yields sensible cosine distances.

Index format (JSON, stored as labeled-data/../vlm_index.json):
  {
    "frames": [
      {
        "video_stem":  str,
        "frame":       str,            # e.g. "img0000-00190.png"
        "frame_path":  str,            # absolute host path
        "labels":      {bp: [x, y]},   # from CSV (NaN → null)
        "vector":      [float, ...]    # L2-normalised feature vector
      }, ...
    ],
    "built_at": ISO-8601 timestamp,
    "project_path": str
  }
"""
from __future__ import annotations

import csv
import json
import math
import re
import time
from pathlib import Path
from typing import Any


# ── JSON extraction helper ─────────────────────────────────────────────────────

def _extract_first_json_obj(text: str) -> str | None:
    """
    Extract the first well-formed JSON object from text.

    Handles:
    - qwen3 <think>...</think> thinking tokens before the JSON
    - Nested braces inside the object
    - Brace / quote characters inside string literals

    Returns the JSON substring on success, None if no valid object found.
    """
    # Strip thinking-mode tags (qwen3 / qwen3-vl emit these)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    start = text.find("{")
    if start == -1:
        return None

    depth       = 0
    in_string   = False
    escape_next = False
    for i, c in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if c == "\\" and in_string:
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ── Image backends (cv2 preferred; PIL fallback) ──────────────────────────────
try:
    import cv2 as _cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

try:
    from PIL import Image as _PILImage
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

_IMG_OK = _CV2_OK or _PIL_OK   # at least one backend available


# ── Optional Ollama embedding ─────────────────────────────────────────────────
_OLLAMA_EMBED_MODEL = "nomic-embed-text:latest"
_OLLAMA_URL_DEFAULT = "http://localhost:11434"

import os as _os
_OLLAMA_URL = _os.environ.get("OLLAMA_URL", _OLLAMA_URL_DEFAULT)


def _pixel_vector(image_path: Path, size: int = 32) -> list[float] | None:
    """Return a normalised pixel feature vector, or None on failure.

    Tries cv2 first (available in the Flask container via opencv-python-headless),
    then PIL as a fallback.
    """
    if _CV2_OK:
        try:
            import numpy as _np
            img = _cv2.imread(str(image_path), _cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise ValueError("cv2.imread returned None")
            img = _cv2.resize(img, (size, size), interpolation=_cv2.INTER_AREA)
            raw = img.flatten().tolist()
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            return [v / norm for v in raw]
        except Exception:
            pass  # fall through to PIL

    if _PIL_OK:
        try:
            img = _PILImage.open(str(image_path)).convert("L").resize((size, size))
            raw = list(img.getdata())
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            return [v / norm for v in raw]
        except Exception:
            pass

    return None


def _ollama_embed(text: str, model: str = _OLLAMA_EMBED_MODEL) -> list[float] | None:
    """Request a text embedding from Ollama; return None on any failure."""
    try:
        import requests as _req
        resp = _req.post(
            f"{_OLLAMA_URL}/api/embed",
            json={"model": model, "input": text},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            vec = data.get("embeddings", [[]])[0] or data.get("embedding", [])
            if vec:
                norm = math.sqrt(sum(v * v for v in vec)) or 1.0
                return [v / norm for v in vec]
    except Exception:
        pass
    return None


def _combine_vectors(*vecs: list[float] | None) -> list[float]:
    """Concatenate non-None vectors and L2-normalise the result."""
    combined: list[float] = []
    for v in vecs:
        if v:
            combined.extend(v)
    if not combined:
        return []
    norm = math.sqrt(sum(x * x for x in combined)) or 1.0
    return [x / norm for x in combined]


def _natural_keys(text: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


# ── CSV reader ────────────────────────────────────────────────────────────────

def _read_labels_from_csv(csv_path: Path) -> dict[str, dict[str, list[float | None] | None]]:
    """Return {frame_name: {bp: [x, y] or null}} from a DLC MultiIndex CSV."""
    try:
        with open(str(csv_path), newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return {}

    if len(rows) < 4:
        return {}

    bodyparts_row = rows[1][3:]
    coords_row    = rows[2][3:]
    col_pairs     = list(zip(bodyparts_row, coords_row))

    labels: dict = {}
    for row in rows[3:]:
        if not row:
            continue
        img_name = row[2]
        vals     = row[3:]
        bp_data: dict = {}
        for (bp, coord), val in zip(col_pairs, vals):
            bp_data.setdefault(bp, {})[coord] = val
        frame_labels = {}
        for bp, cd in bp_data.items():
            xs = cd.get("x", "")
            ys = cd.get("y", "")
            try:
                x = float(xs) if xs not in ("", "NaN", "nan") else None
                y = float(ys) if ys not in ("", "NaN", "nan") else None
            except ValueError:
                x = y = None
            frame_labels[bp] = [x, y] if x is not None and y is not None else None
        labels[img_name] = frame_labels
    return labels


# ── Raw predictions CSV (written by machine-labeling task) ────────────────────

def read_raw_predictions(
    stem_dir: Path,
    min_lh: float = 0.0,
) -> dict[str, dict[str, list[float] | None]] | None:
    """
    Read _machine_predictions_raw.csv (frame, bodypart, x, y, likelihood).

    If the CSV is absent but _machine_predictions_raw.h5 exists it is generated
    on-demand (pandas + tables available in the Flask container).

    Returns {frame: {bp: [x, y] or None}} filtered to lk >= min_lh,
    or None if neither file exists (fall back to CollectedData CSV).
    """
    if not _ensure_raw_pred_csv(stem_dir):
        return None
    raw_csv = Path(stem_dir) / RAW_PRED_CSV
    if not raw_csv.is_file():
        return None
    result: dict = {}
    try:
        with open(str(raw_csv), newline="") as f:
            for row in csv.DictReader(f):
                frame = row.get("frame", "")
                bp    = row.get("bodypart", "")
                if not frame or not bp:
                    continue
                if frame not in result:
                    result[frame] = {}
                try:
                    lk = float(row.get("likelihood") or "1.0")
                    if lk >= min_lh:
                        result[frame][bp] = [float(row["x"]), float(row["y"])]
                    else:
                        result[frame][bp] = None
                except (ValueError, KeyError):
                    result[frame][bp] = None
    except OSError:
        return None
    return result or None


def frame_min_likelihoods(stem_dir: Path) -> dict[str, float]:
    """
    Return {frame: min_likelihood} from _machine_predictions_raw.csv.
    Generates the CSV from the h5 on-demand if needed.
    Frames absent from the file are not included.
    """
    if not _ensure_raw_pred_csv(stem_dir):
        return {}
    raw_csv = Path(stem_dir) / RAW_PRED_CSV
    if not raw_csv.is_file():
        return {}
    mins: dict[str, float] = {}
    try:
        with open(str(raw_csv), newline="") as f:
            for row in csv.DictReader(f):
                frame = row.get("frame", "")
                if not frame:
                    continue
                try:
                    lk = float(row.get("likelihood") or "1.0")
                    if frame not in mins or lk < mins[frame]:
                        mins[frame] = lk
                except ValueError:
                    pass
    except OSError:
        pass
    return mins

# ── VLM results persistence ───────────────────────────────────────────────────

RAW_PRED_CSV = "_machine_predictions_raw.csv"
RAW_PRED_H5  = "_machine_predictions_raw.h5"

VLM_RESULTS_FILENAME = "_vlm_results.json"


def _ensure_raw_pred_csv(stem_dir: Path) -> bool:
    """
    If _machine_predictions_raw.csv is absent but _machine_predictions_raw.h5
    exists, generate the CSV from the h5 now (pandas + tables are available in
    Flask).  Returns True if the CSV is (now) present, False otherwise.
    """
    csv_path = Path(stem_dir) / RAW_PRED_CSV
    if csv_path.is_file():
        return True

    h5_path = Path(stem_dir) / RAW_PRED_H5
    if not h5_path.is_file():
        return False

    try:
        import pandas as _pd

        df = _pd.read_hdf(str(h5_path))

        # Resolve (x_col, y_col, lk_col) for each bodypart from MultiIndex columns
        def _find_cols(bp: str):
            mx, my, ml = [], [], []
            for col in df.columns:
                if col[-2] == bp:
                    c = col[-1]
                    if c == "x":                             mx.append(col)
                    elif c == "y":                           my.append(col)
                    elif c in ("likelihood", "p"):           ml.append(col)
            if mx and my:
                return mx[0], my[0], ml[0] if ml else None
            return None

        # Collect all bodyparts from the h5
        bodyparts = list(dict.fromkeys(
            col[-2] for col in df.columns
            if hasattr(col, "__len__") and len(col) >= 2
        ))

        rows = [["frame", "bodypart", "x", "y", "likelihood"]]
        for idx, row in df.iterrows():
            frame_name = Path(str(idx[-1] if isinstance(idx, tuple) else idx)).name
            for bp in bodyparts:
                cols = _find_cols(bp)
                if cols is None:
                    continue
                xc, yc, lc = cols
                try:
                    x  = float(row[xc])
                    y  = float(row[yc])
                    lk = float(row[lc]) if lc is not None else 1.0
                    if not (_pd.isna(x) or _pd.isna(y)):
                        rows.append([frame_name, bp,
                                     round(x, 4), round(y, 4),
                                     round(lk, 4)])
                except Exception:
                    pass

        with open(str(csv_path), "w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        return True
    except Exception:
        return False


def _vlm_results_path(stem_dir: Path) -> Path:
    return Path(stem_dir) / VLM_RESULTS_FILENAME


def save_vlm_result(
    stem_dir: Path,
    frame: str,
    vlm_coords: dict,
    vlm_debug: dict,
) -> None:
    """Persist VLM result for one frame into _vlm_results.json (upsert)."""
    import datetime
    path = _vlm_results_path(stem_dir)
    try:
        data: dict = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[frame] = {
        "vlm_coords": vlm_coords,
        "vlm_debug":  vlm_debug,
        "saved_at":   datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(data, indent=2))


def load_vlm_result(stem_dir: Path, frame: str) -> tuple[dict, dict] | tuple[None, None]:
    """
    Return (vlm_coords, vlm_debug) for one frame, or (None, None) if absent.
    """
    path = _vlm_results_path(stem_dir)
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text())
        entry = data.get(frame)
        if entry:
            return entry.get("vlm_coords"), entry.get("vlm_debug")
    except (OSError, json.JSONDecodeError):
        pass
    return None, None


def list_vlm_frames(stem_dir: Path) -> list[str]:
    """Return the list of frame names that have a saved VLM result."""
    path = _vlm_results_path(stem_dir)
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return []


# ── Public API ────────────────────────────────────────────────────────────────

INDEX_FILENAME = "vlm_index.json"


def _index_path(project_path: Path) -> Path:
    return project_path / INDEX_FILENAME


def build_index(
    project_path: str | Path,
    use_ollama: bool = False,
    progress_cb: Any = None,
) -> dict:
    """
    Scan all labeled PNG frames that appear in a CollectedData CSV, generate
    feature vectors, and write vlm_index.json next to config.yaml.

    Args:
        project_path: Root folder of the DLC project (contains config.yaml).
        use_ollama:   If True, attempt to generate Ollama text embeddings too.
        progress_cb:  Optional callable(done: int, total: int) for status updates.

    Returns the index dict.
    """
    project_path = Path(project_path)
    labeled_base = project_path / "labeled-data"
    if not labeled_base.is_dir():
        raise FileNotFoundError(f"labeled-data not found: {labeled_base}")

    frames_data: list[dict] = []

    # Collect all (stem_dir, csv_path) pairs
    tasks: list[tuple[Path, Path]] = []
    for stem_dir in sorted(labeled_base.iterdir(), key=lambda p: _natural_keys(p.name)):
        if not stem_dir.is_dir():
            continue
        csvs = sorted(stem_dir.glob("CollectedData_*.csv"))
        for csv_path in csvs:
            tasks.append((stem_dir, csv_path))

    total_frames = 0
    # Pre-count for progress
    for stem_dir, csv_path in tasks:
        labels = _read_labels_from_csv(csv_path)
        total_frames += len(labels)

    done = 0
    for stem_dir, csv_path in tasks:
        labels = _read_labels_from_csv(csv_path)
        video_stem = stem_dir.name

        for frame_name in sorted(labels.keys(), key=_natural_keys):
            frame_path = stem_dir / frame_name
            if not frame_path.is_file():
                done += 1
                if progress_cb:
                    progress_cb(done, total_frames)
                continue

            pix_vec = _pixel_vector(frame_path)
            olm_vec = None
            if use_ollama:
                # Describe using qwen3-vl then embed the description
                desc = _describe_frame(frame_path)
                if desc:
                    olm_vec = _ollama_embed(desc)

            vector = _combine_vectors(pix_vec, olm_vec)

            frames_data.append({
                "video_stem": video_stem,
                "frame":      frame_name,
                "frame_path": str(frame_path.resolve()),
                "labels":     labels[frame_name],
                "vector":     vector,
            })

            done += 1
            if progress_cb:
                progress_cb(done, total_frames)

    from datetime import datetime, timezone
    index: dict = {
        "frames":       frames_data,
        "built_at":     datetime.now(timezone.utc).isoformat(),
        "project_path": str(project_path.resolve()),
        "total_frames": len(frames_data),
    }

    out = _index_path(project_path)
    out.write_text(json.dumps(index, indent=2))
    return index


def load_index(project_path: str | Path) -> dict | None:
    """Load and return the existing index, or None if it doesn't exist."""
    path = _index_path(Path(project_path))
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two already-normalised vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def find_similar(
    index: dict,
    query_vector: list[float],
    k: int = 5,
    exclude_video_stem: str | None = None,
    require_video_stem: str | None = None,
) -> list[dict]:
    """
    Return the top-k most similar frames from *index* for the given vector.

    exclude_video_stem: skip all frames from this stem (the active stem).
    require_video_stem: only consider frames from this specific stem.

    Each result is the frame entry dict augmented with a 'score' key (0–1).
    """
    if not query_vector:
        return []

    scored = []
    for entry in index.get("frames", []):
        stem = entry.get("video_stem")
        if exclude_video_stem and stem == exclude_video_stem:
            continue
        if require_video_stem and stem != require_video_stem:
            continue
        vec = entry.get("vector", [])
        if not vec:
            continue
        score = _cosine_sim(query_vector, vec)
        scored.append((score, entry))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [dict(entry, score=round(score, 4)) for score, entry in scored[:k]]


def index_stems(index: dict) -> list[str]:
    """Return sorted unique video_stem values present in the index."""
    stems: list[str] = sorted({e["video_stem"] for e in index.get("frames", []) if e.get("video_stem")}
    )
    return stems


def get_frame_vector(index: dict, video_stem: str, frame: str) -> list[float]:
    """Look up the pre-computed vector for a specific frame."""
    for entry in index.get("frames", []):
        if entry.get("video_stem") == video_stem and entry.get("frame") == frame:
            return entry.get("vector", [])
    return []


# ── VLM description helper (used during indexing with use_ollama=True) ────────

def _b64_image(path: Path) -> str:
    """Return raw base64 (no data-URI prefix) for an image file."""
    import base64
    with open(str(path), "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def _ollama_chat(
    messages: list,
    model: str,
    timeout: int = 120,
    fmt: str | None = None,
) -> tuple[str | None, str]:
    """
    POST to Ollama /api/chat using the native format.

    Returns (content, error_msg):
      content   — assistant message string on success, None on failure
      error_msg — empty string on success, human-readable error on failure

    Ollama image format: each message with images uses
      {"role": "user", "content": "<text>", "images": ["<base64_no_prefix>", ...]}
    NOT the OpenAI content-array format.
    """
    try:
        import requests as _req
        payload: dict = {"model": model, "messages": messages, "stream": False,
                         "options": {"temperature": 0.0}}
        if fmt:
            payload["format"] = fmt
        resp = _req.post(f"{_OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        if resp.status_code == 200:
            return resp.json().get("message", {}).get("content", ""), ""
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return None, str(exc)[:200]


def _describe_frame(image_path: Path, model: str = "qwen3-vl:32b") -> str | None:
    """Ask qwen3-vl to produce a 1-sentence description of a labeled frame."""
    messages = [{
        "role": "user",
        "content": (
            "Describe the animal posture and keypoint positions in this frame "
            "in one concise sentence. Focus on body orientation and limb positions."
        ),
        "images": [_b64_image(image_path)],
    }]
    content, _ = _ollama_chat(messages, model, timeout=60)
    return content


def _crop_patch(image_path: Path, cx: float, cy: float, size: int = 128) -> str | None:
    """
    Crop a *size*×*size* patch centred on (cx, cy) from the image at image_path.
    Returns raw base64 (no data-URI prefix), or None on failure.

    Uses cv2 if available, falls back to PIL.
    """
    half = size // 2

    if _CV2_OK:
        try:
            img = _cv2.imread(str(image_path))
            if img is None:
                raise ValueError("cv2.imread returned None")
            h, w = img.shape[:2]
            x0 = max(0, int(cx) - half)
            y0 = max(0, int(cy) - half)
            x1 = min(w, x0 + size)
            y1 = min(h, y0 + size)
            patch = img[y0:y1, x0:x1]
            # Pad to exactly size×size if near border
            if patch.shape[0] != size or patch.shape[1] != size:
                canvas = _cv2.copyMakeBorder(
                    patch,
                    top=max(0, half - int(cy)),
                    bottom=max(0, (size - patch.shape[0]) - max(0, half - int(cy))),
                    left=max(0, half - int(cx)),
                    right=max(0, (size - patch.shape[1]) - max(0, half - int(cx))),
                    borderType=_cv2.BORDER_CONSTANT,
                    value=0,
                )
                patch = canvas[:size, :size]
            ok, buf = _cv2.imencode(".png", patch)
            if not ok:
                raise ValueError("imencode failed")
            import base64
            return base64.b64encode(buf.tobytes()).decode()
        except Exception:
            pass  # fall through to PIL

    if _PIL_OK:
        try:
            import base64
            from io import BytesIO
            img = _PILImage.open(str(image_path)).convert("RGB")
            w, h = img.size
            x0 = max(0, int(cx) - half)
            y0 = max(0, int(cy) - half)
            x1 = min(w, x0 + size)
            y1 = min(h, y0 + size)
            patch = img.crop((x0, y0, x1, y1))
            canvas = _PILImage.new("RGB", (size, size), (0, 0, 0))
            canvas.paste(patch, (max(0, half - int(cx)), max(0, half - int(cy))))
            buf = BytesIO()
            canvas.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass

    return None


def refine_coords_with_vlm(
    active_frame_path: str | Path,
    reference_frame_path: str | Path,
    reference_labels: dict[str, list[float | None] | None],
    machine_coords: dict[str, list[float | None] | None],
    bodyparts: list[str],
    patch_size: int = 64,
    model: str = "qwen3-vl:32b",
) -> tuple[dict[str, list[float] | None], dict[str, dict]]:
    """
    Patch-based VLM refinement — single batched Ollama call.

    All bodyparts that have both a machine label and a reference label are
    processed in ONE model call.  The images list is interleaved:
      [ref_crop_bp0, active_crop_bp0, ref_crop_bp1, active_crop_bp1, ...]
    The prompt names each pair by index so the model can answer for all at once.

    Bodyparts without machine or reference coords are skipped and recorded in
    the debug dict.

    Returns (coords, debug):
      coords  — {bodypart: [x, y] | null}
      debug   — {bodypart: {reason, dx, dy, correct}}
        reason: "ok" | "no_machine_coord" | "no_ref_label" |
                "crop_failed" | "ollama_failed" | "parse_failed"
    """
    active_path = Path(active_frame_path)
    ref_path    = Path(reference_frame_path)
    result: dict[str, list[float] | None] = {}
    debug:  dict[str, dict] = {}

    # ── Pass 1: categorise every bodypart, collect crops for callable ones ──
    callable_bps: list[str] = []   # bodyparts that will be sent to VLM
    machine_xy_map: dict[str, tuple[float, float]] = {}
    images: list[str] = []         # interleaved [ref0, active0, ref1, active1, …]

    for bp in bodyparts:
        machine_xy = machine_coords.get(bp)
        ref_xy     = reference_labels.get(bp)

        if not machine_xy or machine_xy[0] is None or machine_xy[1] is None:
            result[bp] = None
            debug[bp]  = {"reason": "no_machine_coord"}
            continue

        if not ref_xy or ref_xy[0] is None or ref_xy[1] is None:
            result[bp] = list(machine_xy)
            debug[bp]  = {"reason": "no_ref_label"}
            continue

        mx, my = float(machine_xy[0]), float(machine_xy[1])
        rx, ry = float(ref_xy[0]),     float(ref_xy[1])

        ref_crop    = _crop_patch(ref_path,    rx, ry, patch_size)
        active_crop = _crop_patch(active_path, mx, my, patch_size)

        if not ref_crop or not active_crop:
            result[bp] = [mx, my]
            debug[bp]  = {"reason": "crop_failed"}
            continue

        callable_bps.append(bp)
        machine_xy_map[bp] = (mx, my)
        images.extend([ref_crop, active_crop])

    # ── Pass 2: batched VLM calls (≤ MAX_BATCH bodyparts per call) ────────────
    MAX_BATCH = 3   # 3 pairs = 6 images per call; keeps qwen3-vl within GPU budget

    # Pre-build crop maps so we can re-slice per chunk
    ref_crop_map:    dict[str, str] = {}
    active_crop_map: dict[str, str] = {}
    for i, bp in enumerate(callable_bps):
        ref_crop_map[bp]    = images[i * 2]
        active_crop_map[bp] = images[i * 2 + 1]

    def _call_chunk(chunk: list[str]) -> dict:
        """
        Call Ollama for one chunk of bodyparts.
        Returns a normalised {bp: entry_dict} — keys are the original bp names.
        """
        chunk_images = []
        for bp in chunk:
            chunk_images.extend([ref_crop_map[bp], active_crop_map[bp]])

        pair_desc = "\n".join(
            f"  Pair {i+1}: '{bp}'  —  image {i*2+1} = reference, image {i*2+2} = active"
            for i, bp in enumerate(chunk)
        )
        example_entries = ", ".join(
            '"' + bp + '": {"correct": true/false, "dx": integer, "dy": integer}'
            for bp in chunk
        )
        prompt = (
            "You are a precise pose-estimation assistant.\n"
            f"I am showing you {len(chunk)} pairs of 128\u00d7128 image crops:\n"
            f"{pair_desc}\n\n"
            "In each pair:\n"
            "  - The REFERENCE image has the named keypoint at the EXACT CENTRE.\n"
            "  - The ACTIVE image has the machine prediction at the EXACT CENTRE.\n\n"
            "For each pair, compare the two crops and estimate the pixel offset (dx, dy) "
            "needed to move the ACTIVE centre to the true keypoint location "
            "(positive dx = right, positive dy = down).\n\n"
            f"Reply ONLY with a JSON object (keys must match exactly):\n"
            "{" + example_entries + "}"
        )

        timeout = 90 + 45 * len(chunk)  # 64px crops are faster; generous for 3-bp chunks
        raw = err = None
        for _attempt in range(2):  # one retry on failure
            if _attempt:
                time.sleep(5)
            raw, err = _ollama_chat(
                [{"role": "user", "content": prompt, "images": chunk_images}],
                model, timeout=timeout, fmt="json",
            )
            if raw:
                break
        if not raw:
            return {"_failed": "ollama_failed", "_raw": err}

        # Extract the first well-formed JSON object (strips <think>…</think> first)
        json_str = _extract_first_json_obj(raw)
        if not json_str:
            return {"_failed": "parse_failed", "_raw": raw[:400]}
        try:
            parsed = json.loads(json_str)
        except (ValueError, TypeError):
            return {"_failed": "parse_failed", "_raw": raw[:400]}

        # Build a case-insensitive / normalised key lookup
        # (model may return "mcp-1" instead of "MCP-1" etc.)
        normalised: dict[str, dict] = {}
        for k, v in parsed.items():
            normalised[k.strip().lower().replace(" ", "-")] = v

        result_chunk: dict = {}
        for bp in chunk:
            bp_norm = bp.strip().lower().replace(" ", "-")
            entry = parsed.get(bp) or normalised.get(bp_norm)
            result_chunk[bp] = entry  # may be None → caller handles
        return result_chunk

    for chunk_start in range(0, len(callable_bps), MAX_BATCH):
        chunk = callable_bps[chunk_start:chunk_start + MAX_BATCH]
        chunk_result = _call_chunk(chunk)

        # Check for whole-chunk failure
        if "_failed" in chunk_result:
            reason = chunk_result["_failed"]
            raw_snippet = chunk_result.get("_raw", "")
            for bp in chunk:
                mx, my = machine_xy_map[bp]
                result[bp] = [mx, my]
                debug[bp]  = {"reason": reason, "raw": raw_snippet}
            continue

        for bp in chunk:
            mx, my = machine_xy_map[bp]
            entry = chunk_result.get(bp)
            if not isinstance(entry, dict):
                result[bp] = [mx, my]
                debug[bp]  = {"reason": "parse_failed", "raw": str(entry)[:200]}
                continue
            try:
                dx      = float(entry.get("dx", 0))
                dy      = float(entry.get("dy", 0))
                correct = bool(entry.get("correct", False))
                result[bp] = [mx + dx, my + dy]
                debug[bp]  = {"reason": "ok", "dx": dx, "dy": dy, "correct": correct}
            except (TypeError, ValueError):
                result[bp] = [mx, my]
                debug[bp]  = {"reason": "parse_failed", "raw": str(entry)[:200]}

    return result, debug


# ── Geometric Pose Signature (posture-centric matching) ───────────────────────

POSTURE_INDEX_FILENAME  = "posture_index.json"
POSTURE_RESULTS_FILENAME = "_posture_vlm_results.json"


def posture_signature(
    labels: dict[str, list[float | None] | None],
    bodyparts: list[str],
) -> list[float]:
    """
    Compute a Geometric Pose Signature for one frame.

    Algorithm:
      1. Collect all valid (non-None) (x, y) coords for bodyparts in order.
      2. Translate so the centroid is (0, 0).
      3. Scale so the maximum pairwise inter-keypoint distance is 1.0.
      4. Return a flattened [x0, y0, x1, y1, ...] vector of length
         2 * len(bodyparts).  Missing bodyparts are filled with (0.0, 0.0).

    Returns an empty list when fewer than 2 valid coords are present.
    """
    valid: list[tuple[int, float, float]] = []  # (bp_index, x, y)
    for i, bp in enumerate(bodyparts):
        xy = labels.get(bp)
        if xy and xy[0] is not None and xy[1] is not None:
            valid.append((i, float(xy[0]), float(xy[1])))

    if len(valid) < 2:
        return []

    # Translate to centroid
    cx = sum(v[1] for v in valid) / len(valid)
    cy = sum(v[2] for v in valid) / len(valid)
    translated = [(i, x - cx, y - cy) for i, x, y in valid]

    # Scale by maximum pairwise distance
    max_dist = 0.0
    for j in range(len(translated)):
        for k in range(j + 1, len(translated)):
            dx = translated[j][1] - translated[k][1]
            dy = translated[j][2] - translated[k][2]
            d = math.sqrt(dx * dx + dy * dy)
            if d > max_dist:
                max_dist = d

    scale = max_dist if max_dist > 1e-9 else 1.0

    # Build fixed-length output (2 * len(bodyparts)); missing bps stay at 0.0
    out = [0.0] * (2 * len(bodyparts))
    for i, x, y in translated:
        out[2 * i]     = x / scale
        out[2 * i + 1] = y / scale

    return out


def _posture_index_path(project_path: Path) -> Path:
    return project_path / POSTURE_INDEX_FILENAME


def build_posture_index(
    project_path: str | Path,
    bodyparts: list[str] | None = None,
    progress_cb: Any = None,
) -> dict:
    """
    Build a posture index from all human-labeled frames in the project.

    For each labeled frame, compute a Geometric Pose Signature using the human
    labels from CollectedData CSVs.  Saves posture_index.json next to config.yaml.

    Args:
        project_path: Root folder of the DLC project (contains config.yaml).
        bodyparts:    Ordered list of bodypart names.  If None, auto-detected
                      from config.yaml, then from the first CSV found.
        progress_cb:  Optional callable(done: int, total: int).

    Returns the index dict.
    """
    project_path = Path(project_path)
    labeled_base = project_path / "labeled-data"
    if not labeled_base.is_dir():
        raise FileNotFoundError(f"labeled-data not found: {labeled_base}")

    # Auto-detect bodyparts from config.yaml when not provided
    if not bodyparts:
        cfg_path = project_path / "config.yaml"
        if cfg_path.is_file():
            try:
                import yaml as _yaml_mod
                cfg = _yaml_mod.safe_load(cfg_path.read_text())
                bodyparts = cfg.get("bodyparts", [])
            except Exception:
                bodyparts = []

    tasks: list[tuple[Path, Path]] = []
    for stem_dir in sorted(labeled_base.iterdir(), key=lambda p: _natural_keys(p.name)):
        if not stem_dir.is_dir():
            continue
        csvs = sorted(stem_dir.glob("CollectedData_*.csv"))
        for csv_path in csvs:
            tasks.append((stem_dir, csv_path))

    total_frames = sum(len(_read_labels_from_csv(cp)) for _, cp in tasks)
    done = 0
    frames_data: list[dict] = []

    for stem_dir, csv_path in tasks:
        labels_map = _read_labels_from_csv(csv_path)
        video_stem = stem_dir.name

        # Fall back to first CSV's keys if bodyparts still empty
        if not bodyparts and labels_map:
            bodyparts = list(next(iter(labels_map.values())).keys())

        for frame_name in sorted(labels_map.keys(), key=_natural_keys):
            frame_labels = labels_map[frame_name]
            sig = posture_signature(frame_labels, list(bodyparts or []))
            frames_data.append({
                "video_stem": video_stem,
                "frame":      frame_name,
                "frame_path": str((stem_dir / frame_name).resolve()),
                "labels":     frame_labels,
                "signature":  sig,
            })
            done += 1
            if progress_cb:
                progress_cb(done, total_frames)

    from datetime import datetime, timezone
    index: dict = {
        "frames":       frames_data,
        "bodyparts":    list(bodyparts or []),
        "built_at":     datetime.now(timezone.utc).isoformat(),
        "project_path": str(project_path.resolve()),
        "total_frames": len(frames_data),
    }
    _posture_index_path(project_path).write_text(json.dumps(index, indent=2))
    return index


def load_posture_index(project_path: str | Path) -> dict | None:
    """Load the posture index, or None if it doesn't exist yet."""
    path = _posture_index_path(Path(project_path))
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def find_similar_posture(
    index: dict,
    query_signature: list[float],
    k: int = 3,
    exclude_video_stem: str | None = None,
) -> list[dict]:
    """
    Return the top-k frames with the most similar Geometric Pose Signature.

    Uses cosine similarity on the signatures.  Frames whose signature length
    doesn't match the query are skipped.  Each result is the frame entry dict
    augmented with a 'score' key (cosine similarity, 0–1).
    """
    if not query_signature:
        return []

    q_len = len(query_signature)
    norm_q = math.sqrt(sum(x * x for x in query_signature)) or 1.0

    scored = []
    for entry in index.get("frames", []):
        if exclude_video_stem and entry.get("video_stem") == exclude_video_stem:
            continue
        sig = entry.get("signature", [])
        if len(sig) != q_len:
            continue
        norm_s = math.sqrt(sum(x * x for x in sig)) or 1.0
        score = sum(a / norm_q * b / norm_s for a, b in zip(query_signature, sig))
        scored.append((score, entry))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [dict(entry, score=round(score, 4)) for score, entry in scored[:k]]


def get_posture_signature_for_frame(
    index: dict,
    video_stem: str,
    frame: str,
) -> list[float]:
    """Look up the pre-computed posture signature for a specific frame."""
    for entry in index.get("frames", []):
        if entry.get("video_stem") == video_stem and entry.get("frame") == frame:
            return entry.get("signature", [])
    return []


def save_posture_result(
    stem_dir: Path,
    frame: str,
    vlm_coords: dict,
    vlm_debug: dict,
) -> None:
    """Persist posture-VLM result for one frame into _posture_vlm_results.json (upsert)."""
    import datetime
    path = Path(stem_dir) / POSTURE_RESULTS_FILENAME
    try:
        data: dict = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[frame] = {
        "vlm_coords": vlm_coords,
        "vlm_debug":  vlm_debug,
        "saved_at":   datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(data, indent=2))


def load_posture_result(stem_dir: Path, frame: str) -> tuple[dict, dict] | tuple[None, None]:
    """Return (vlm_coords, vlm_debug) for one frame, or (None, None) if absent."""
    path = Path(stem_dir) / POSTURE_RESULTS_FILENAME
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text())
        entry = data.get(frame)
        if entry:
            return entry.get("vlm_coords"), entry.get("vlm_debug")
    except (OSError, json.JSONDecodeError):
        pass
    return None, None


def refine_coords_posture_aware(
    active_frame_path: str | Path,
    reference_frame_path: str | Path,
    reference_labels: dict[str, list[float | None] | None],
    machine_coords: dict[str, list[float | None] | None],
    bodyparts: list[str],
    patch_size: int = 64,
    model: str = "qwen3-vl:32b",
) -> tuple[dict[str, list[float] | None], dict[str, dict]]:
    """
    Posture-aware VLM refinement.

    Identical mechanism to refine_coords_with_vlm (patch crops + JSON offsets)
    but uses an anatomical-context prompt:
      "The reference image shows the correct anatomical placement for this posture.
       Adjust the active label to match the anatomical proportions seen in the reference."

    Returns (coords, debug) with the same schema as refine_coords_with_vlm.
    """
    active_path = Path(active_frame_path)
    ref_path    = Path(reference_frame_path)
    result: dict[str, list[float] | None] = {}
    debug:  dict[str, dict] = {}

    callable_bps: list[str] = []
    machine_xy_map: dict[str, tuple[float, float]] = {}
    ref_crop_map:    dict[str, str] = {}
    active_crop_map: dict[str, str] = {}

    for bp in bodyparts:
        machine_xy = machine_coords.get(bp)
        ref_xy     = reference_labels.get(bp)

        if not machine_xy or machine_xy[0] is None or machine_xy[1] is None:
            result[bp] = None
            debug[bp]  = {"reason": "no_machine_coord"}
            continue

        if not ref_xy or ref_xy[0] is None or ref_xy[1] is None:
            result[bp] = list(machine_xy)
            debug[bp]  = {"reason": "no_ref_label"}
            continue

        mx, my = float(machine_xy[0]), float(machine_xy[1])
        rx, ry = float(ref_xy[0]),     float(ref_xy[1])

        ref_crop    = _crop_patch(ref_path,    rx, ry, patch_size)
        active_crop = _crop_patch(active_path, mx, my, patch_size)

        if not ref_crop or not active_crop:
            result[bp] = [mx, my]
            debug[bp]  = {"reason": "crop_failed"}
            continue

        callable_bps.append(bp)
        machine_xy_map[bp]  = (mx, my)
        ref_crop_map[bp]    = ref_crop
        active_crop_map[bp] = active_crop

    MAX_BATCH = 3  # 3 pairs = 6 images per call; keeps qwen3-vl within GPU budget

    def _posture_chunk(chunk: list[str]) -> dict:
        chunk_images = []
        for bp in chunk:
            chunk_images.extend([ref_crop_map[bp], active_crop_map[bp]])

        pair_desc = "\n".join(
            f"  Pair {i+1}: '{bp}'  —  image {i*2+1} = reference, image {i*2+2} = active"
            for i, bp in enumerate(chunk)
        )
        example_entries = ", ".join(
            '"' + bp + '": {"correct": true/false, "dx": integer, "dy": integer}'
            for bp in chunk
        )
        prompt = (
            "You are a precise anatomical pose-estimation assistant.\n"
            "The REFERENCE image shows the correct anatomical placement for this posture.\n"
            "Adjust the active label to match the anatomical proportions seen in the reference.\n\n"
            f"I am showing you {len(chunk)} pairs of {patch_size}\u00d7{patch_size} image crops:\n"
            f"{pair_desc}\n\n"
            "In each pair:\n"
            "  - The REFERENCE image has the named keypoint at the EXACT CENTRE.\n"
            "  - The ACTIVE image has the machine prediction at the EXACT CENTRE.\n\n"
            "For each pair, estimate the pixel offset (dx, dy) needed to move the "
            "ACTIVE centre to the anatomically correct keypoint location "
            "(positive dx = right, positive dy = down).\n\n"
            f"Reply ONLY with a JSON object (keys must match exactly):\n"
            "{" + example_entries + "}"
        )

        timeout = 90 + 45 * len(chunk)  # 64px crops are faster; generous for 3-bp chunks
        raw = err = None
        for _attempt in range(2):  # one retry on failure
            if _attempt:
                time.sleep(5)
            raw, err = _ollama_chat(
                [{"role": "user", "content": prompt, "images": chunk_images}],
                model, timeout=timeout, fmt="json",
            )
            if raw:
                break
        if not raw:
            return {"_failed": "ollama_failed", "_raw": err}

        # Extract the first well-formed JSON object (strips <think>…</think> first)
        json_str = _extract_first_json_obj(raw)
        if not json_str:
            return {"_failed": "parse_failed", "_raw": raw[:400]}
        try:
            parsed = json.loads(json_str)
        except (ValueError, TypeError):
            return {"_failed": "parse_failed", "_raw": raw[:400]}

        normalised = {k.strip().lower().replace(" ", "-"): v for k, v in parsed.items()}
        return {bp: parsed.get(bp) or normalised.get(bp.strip().lower().replace(" ", "-"))
                for bp in chunk}

    for chunk_start in range(0, len(callable_bps), MAX_BATCH):
        chunk = callable_bps[chunk_start:chunk_start + MAX_BATCH]
        chunk_result = _posture_chunk(chunk)

        if "_failed" in chunk_result:
            reason      = chunk_result["_failed"]
            raw_snippet = chunk_result.get("_raw", "")
            for bp in chunk:
                mx, my = machine_xy_map[bp]
                result[bp] = [mx, my]
                debug[bp]  = {"reason": reason, "raw": raw_snippet}
            continue

        for bp in chunk:
            mx, my = machine_xy_map[bp]
            entry  = chunk_result.get(bp)
            if not isinstance(entry, dict):
                result[bp] = [mx, my]
                debug[bp]  = {"reason": "parse_failed", "raw": str(entry)[:200]}
                continue
            try:
                dx      = float(entry.get("dx", 0))
                dy      = float(entry.get("dy", 0))
                correct = bool(entry.get("correct", False))
                result[bp] = [mx + dx, my + dy]
                debug[bp]  = {"reason": "ok", "dx": dx, "dy": dy, "correct": correct}
            except (TypeError, ValueError):
                result[bp] = [mx, my]
                debug[bp]  = {"reason": "parse_failed", "raw": str(entry)[:200]}

    return result, debug
