"""
DLC Dataset Curator — frame extraction and CollectedData I/O.

Pure Python (no Flask). All file operations work directly on the filesystem.
Compatible with DLC's standard MultiIndex CSV/HDF5 format.

Public API:
  extract_frame_as_png(video_path, frame_number, output_dir, seq_index=None)
      → (Path, bool)  -- (saved_path, was_duplicate)

  append_frame_to_dataset(stem_dir, video_stem, frame_name, scorer, bodyparts,
                           coords=None)
      → (csv_path, h5_path_or_None)

  update_frame_annotation(stem_dir, video_stem, frame_name, scorer, bodyparts,
                           coords)
      → (csv_path, h5_path_or_None)

HDF5 format (key="df_with_missing"):
  Index  : 3-level MultiIndex  ("labeled-data", video_stem, frame_name)
  Columns: 3-level MultiIndex  (scorer, bodypart, "x"/"y")
  Written with format='table' so DLC can re-open without issues.
"""
from __future__ import annotations

import csv as _csv
import math as _math
import re as _re
import tempfile as _tempfile
from itertools import islice as _islice
from pathlib import Path


# ── Frame naming ──────────────────────────────────────────────────────────────

def _existing_png_seq_indices(output_dir: Path) -> dict[int, Path]:
    """Return {absolute_frame_number: path} for existing img????-NNNNN.png files."""
    pat = _re.compile(r"^img\d{4}-(\d+)\.png$")
    out: dict[int, Path] = {}
    for p in output_dir.iterdir():
        m = pat.match(p.name)
        if m:
            out[int(m.group(1))] = p
    return out


# ── Core: frame extraction ────────────────────────────────────────────────────

def extract_frame_as_png(
    video_path: str | Path,
    frame_number: int,
    output_dir: str | Path,
    seq_index: int | None = None,
) -> tuple[Path, bool]:
    """
    Decode *frame_number* from *video_path* and write a lossless PNG.

    Naming convention (matches videos.py/save-frame):
        img{seq_index:04d}-{frame_number:05d}.png

    *seq_index* defaults to the count of existing PNGs in *output_dir*.

    Returns (saved_path, was_duplicate).
    was_duplicate=True means the frame already existed; no file was written.
    """
    import cv2
    import numpy as np

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Duplicate guard: if this absolute frame number is already present, skip.
    existing = _existing_png_seq_indices(output_dir)
    if frame_number in existing:
        return existing[frame_number], True

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise OSError(f"Cannot open video: {video_path}")

    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ret, frame = cap.read()
    finally:
        cap.release()

    if not ret or frame is None:
        raise ValueError(f"Could not read frame {frame_number} from {video_path}")

    if seq_index is None:
        # Count all PNGs, not just the img????-NNNNN pattern
        seq_index = len([p for p in output_dir.iterdir() if p.suffix == ".png"])

    filename = f"img{seq_index:04d}-{frame_number:05d}.png"
    out_path  = output_dir / filename

    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("cv2.imencode('.png') failed")
    out_path.write_bytes(buf.tobytes())
    return out_path, False


# ── CSV helpers ───────────────────────────────────────────────────────────────

_NATURAL_SPLIT = _re.compile(r"(\d+)")


def _natural_keys(s: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in _NATURAL_SPLIT.split(s)]


def _read_labels_csv(csv_path: Path) -> dict[str, dict[str, list | None]]:
    """
    Parse a DLC MultiIndex CSV.
    Returns {frame_name: {bodypart: [x, y] or None}}.
    """
    if not csv_path.is_file():
        return {}

    with open(str(csv_path), newline="") as fh:
        rows = list(_csv.reader(fh))

    if len(rows) < 4:
        return {}

    bodyparts_row = rows[1][3:]
    coords_row    = rows[2][3:]
    col_pairs     = list(zip(bodyparts_row, coords_row))

    labels: dict[str, dict[str, list | None]] = {}
    for row in rows[3:]:
        if not row:
            continue
        frame_name = row[2]
        vals       = row[3:]
        bp_data: dict = {}
        for (bp, coord), val in zip(col_pairs, vals):
            bp_data.setdefault(bp, {})[coord] = val

        frame_labels: dict[str, list | None] = {}
        for bp, cd in bp_data.items():
            x_s = cd.get("x", "")
            y_s = cd.get("y", "")
            try:
                x = float(x_s) if x_s not in ("", "NaN", "nan") else None
                y = float(y_s) if y_s not in ("", "NaN", "nan") else None
            except ValueError:
                x = y = None
            frame_labels[bp] = [x, y] if x is not None and y is not None else None
        labels[frame_name] = frame_labels

    return labels


def _write_labels_csv(
    csv_path: Path,
    video_stem: str,
    scorer: str,
    bodyparts: list[str],
    labels: dict[str, dict[str, list | None]],
) -> None:
    """Write *labels* to a DLC MultiIndex CSV at *csv_path*."""
    frame_names = sorted(labels.keys(), key=_natural_keys)

    header_scorer    = ["scorer",    "", ""] + [scorer] * (len(bodyparts) * 2)
    header_bodyparts = ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in range(2)]
    header_coords    = ["coords",    "", ""] + ["x", "y"] * len(bodyparts)

    rows = [header_scorer, header_bodyparts, header_coords]
    for fname in frame_names:
        row = ["labeled-data", video_stem, fname]
        for bp in bodyparts:
            pt = labels.get(fname, {}).get(bp)
            if pt and len(pt) == 2 and pt[0] is not None and pt[1] is not None:
                row.extend([str(round(pt[0], 4)), str(round(pt[1], 4))])
            else:
                row.extend(["NaN", "NaN"])
        rows.append(row)

    with open(str(csv_path), "w", newline="") as fh:
        _csv.writer(fh).writerows(rows)


# ── HDF5 helpers ──────────────────────────────────────────────────────────────

def rebuild_h5_from_csv(csv_path: Path, h5_path: Path) -> Path | None:
    """
    Rebuild *h5_path* from the current *csv_path* state.

    Uses the exact same logic as tasks.dlc_convert_labels_to_h5 so DLC can
    always read the result with pd.read_hdf(path, key='df_with_missing').

    Returns the written h5_path, or None if the CSV has no data rows.
    """
    import pandas as _pd

    with open(str(csv_path)) as fh:
        head = list(_islice(fh, 5))

    # Detect single-animal (3-level) vs multi-individual (4-level) format
    header    = list(range(4)) if len(head) > 1 and "individuals" in head[1] else list(range(3))
    index_col = [0, 1, 2] if head and head[-1].split(",")[0] == "labeled-data" else 0

    data = _pd.read_csv(str(csv_path), index_col=index_col, header=header)
    if len(data) == 0:
        return None

    # Atomic write: write to .tmp, then rename (avoids corrupt H5 on crash)
    tmp_path = Path(str(h5_path) + ".tmp")
    try:
        data.to_hdf(str(tmp_path), key="df_with_missing", mode="w")
        tmp_path.replace(h5_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return h5_path


def _invalidate_h5(stem_dir: Path, scorer: str) -> None:
    """Delete any stale CollectedData_*.h5 in *stem_dir* so it gets rebuilt."""
    for p in stem_dir.glob("CollectedData_*.h5"):
        p.unlink(missing_ok=True)


# ── Public API ────────────────────────────────────────────────────────────────

def append_frame_to_dataset(
    stem_dir: str | Path,
    video_stem: str,
    frame_name: str,
    scorer: str,
    bodyparts: list[str],
    coords: dict[str, list | None] | None = None,
) -> tuple[Path, Path | None]:
    """
    Append *frame_name* to CollectedData_<scorer>.csv (creating it if needed).

    *coords* maps bodypart → [x, y] (or None for unlabeled).  If omitted,
    all coordinates are stored as NaN.

    Also rebuilds CollectedData_<scorer>.h5 from the updated CSV.

    Returns (csv_path, h5_path).  h5_path is None if the CSV has no data rows
    after the operation (should never happen here, but guarded for safety).
    """
    stem_dir  = Path(stem_dir)
    csv_path  = stem_dir / f"CollectedData_{scorer}.csv"
    labels    = _read_labels_csv(csv_path)

    # Only append if not already present
    if frame_name not in labels:
        labels[frame_name] = coords or {}

    _write_labels_csv(csv_path, video_stem, scorer, bodyparts, labels)

    h5_path   = stem_dir / f"CollectedData_{scorer}.h5"
    written   = rebuild_h5_from_csv(csv_path, h5_path)
    return csv_path, written


def update_frame_annotation(
    stem_dir: str | Path,
    video_stem: str,
    frame_name: str,
    scorer: str,
    bodyparts: list[str],
    coords: dict[str, list | None],
) -> tuple[Path, Path | None]:
    """
    Update (or insert) *frame_name*'s coordinates in CollectedData_<scorer>.csv.

    Only the bodyparts present in *coords* are changed; others are preserved
    from the existing CSV (or kept as NaN for new entries).

    Also rebuilds CollectedData_<scorer>.h5 from the updated CSV.

    Returns (csv_path, h5_path).
    """
    stem_dir  = Path(stem_dir)
    csv_path  = stem_dir / f"CollectedData_{scorer}.csv"
    labels    = _read_labels_csv(csv_path)

    # Merge: start from existing, apply updates
    existing  = labels.get(frame_name, {})
    merged    = {bp: existing.get(bp) for bp in bodyparts}
    for bp, pt in coords.items():
        if bp in merged:
            merged[bp] = pt

    labels[frame_name] = merged

    _write_labels_csv(csv_path, video_stem, scorer, bodyparts, labels)

    h5_path = stem_dir / f"CollectedData_{scorer}.h5"
    written = rebuild_h5_from_csv(csv_path, h5_path)
    return csv_path, written
