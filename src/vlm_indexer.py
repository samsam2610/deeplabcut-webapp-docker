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
) -> list[dict]:
    """
    Return the top-k most similar frames from *index* for the given vector,
    drawn exclusively from stems *other than* exclude_video_stem.

    Each result is the frame entry dict augmented with a 'score' key (0–1).
    """
    if not query_vector:
        return []

    scored = []
    for entry in index.get("frames", []):
        if exclude_video_stem and entry.get("video_stem") == exclude_video_stem:
            continue
        vec = entry.get("vector", [])
        if not vec:
            continue
        score = _cosine_sim(query_vector, vec)
        scored.append((score, entry))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [dict(entry, score=round(score, 4)) for score, entry in scored[:k]]


def get_frame_vector(index: dict, video_stem: str, frame: str) -> list[float]:
    """Look up the pre-computed vector for a specific frame."""
    for entry in index.get("frames", []):
        if entry.get("video_stem") == video_stem and entry.get("frame") == frame:
            return entry.get("vector", [])
    return []


# ── VLM description helper (used during indexing with use_ollama=True) ────────

def _describe_frame(image_path: Path, model: str = "qwen3-vl:32b") -> str | None:
    """Ask qwen3-vl to produce a 1-sentence description of a labeled frame."""
    try:
        import base64, requests as _req
        with open(str(image_path), "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text":
                     "Describe the animal posture and keypoint positions in this frame in one concise sentence. "
                     "Focus on body orientation, limb positions, and any visible keypoints."},
                ],
            }],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        resp = _req.post(f"{_OLLAMA_URL}/api/chat", json=payload, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("message", {}).get("content", "")
    except Exception:
        pass
    return None


def refine_coords_with_vlm(
    active_frame_path: str | Path,
    reference_frame_path: str | Path,
    reference_labels: dict[str, list[float | None] | None],
    bodyparts: list[str],
    model: str = "qwen3-vl:32b",
) -> dict[str, list[float] | None]:
    """
    Ask qwen3-vl to suggest corrected keypoint coordinates for the active frame
    by referencing a visually similar labeled frame.

    Returns {bodypart: [x, y]} or {} on failure.
    """
    try:
        import base64, requests as _req

        def _b64(path: Path) -> str:
            with open(str(path), "rb") as fh:
                return base64.b64encode(fh.read()).decode()

        ref_coord_str = "; ".join(
            f"{bp}=({v[0]:.1f},{v[1]:.1f})" if v else f"{bp}=unknown"
            for bp, v in reference_labels.items()
            if bp in bodyparts
        )

        prompt = (
            f"You are a pose estimation assistant. "
            f"The REFERENCE image shows a labeled animal with these keypoints: {ref_coord_str}. "
            f"The ACTIVE image is the frame to correct. "
            f"Based on the animal's anatomy and the reference, estimate the (x, y) pixel coordinates "
            f"for each of these bodyparts in the ACTIVE image: {', '.join(bodyparts)}. "
            f"Reply ONLY with a JSON object: {{\"bodypart\": [x, y], ...}}. "
            f"Use null for any bodypart that is not visible."
        )

        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "REFERENCE IMAGE:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(Path(reference_frame_path))}"}},
                    {"type": "text", "text": "ACTIVE IMAGE TO CORRECT:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(Path(active_frame_path))}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "stream": False,
            "options": {"temperature": 0.0},
            "format": "json",
        }
        resp = _req.post(f"{_OLLAMA_URL}/api/chat", json=payload, timeout=120)
        if resp.status_code == 200:
            raw = resp.json().get("message", {}).get("content", "{}")
            # Extract JSON from the response (sometimes wrapped in markdown)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                coords = json.loads(m.group())
                # Validate and sanitise
                result = {}
                for bp in bodyparts:
                    val = coords.get(bp)
                    if isinstance(val, (list, tuple)) and len(val) >= 2:
                        try:
                            result[bp] = [float(val[0]), float(val[1])]
                        except (TypeError, ValueError):
                            result[bp] = None
                    else:
                        result[bp] = None
                return result
    except Exception:
        pass
    return {}
