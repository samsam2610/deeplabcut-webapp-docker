# src/dlc/jitter_prelabel.py
"""
Jitter prelabel — pure logic, no Flask/Celery dependencies.
Detects unstable frames (raw vs median-filtered spike) and upserts
them into a DLC labeled-data stem with filtered coordinates as initial labels.
"""
from __future__ import annotations
import csv
import re
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from scipy.signal import medfilt


def _parse_frame_number(filename: str) -> int:
    """Extract video frame number (MMMMM) from imgNNNN-MMMMM.png."""
    m = re.search(r"img\d+-(\d+)\.png$", str(filename))
    if not m:
        raise ValueError(f"Cannot parse frame number from: {filename}")
    return int(m.group(1))


def _apply_median_filter(series: pd.Series, window: int = 5) -> pd.Series:
    """Median-filter a coordinate series, preserving NaN positions."""
    filled = series.ffill().bfill()
    filtered = medfilt(filled.values.astype(float), kernel_size=window)
    result = pd.Series(filtered, index=series.index)
    result[series.isna()] = np.nan
    return result


def _get_scorer_and_bodyparts(df: pd.DataFrame) -> tuple[str, str | None, list[str]]:
    """Return (scorer, individuals_or_None, [bodyparts]) from MultiIndex columns."""
    scorer = df.columns.get_level_values("scorer").unique()[0]
    level_names = df.columns.names
    if "individuals" in level_names:
        individuals = df.columns.get_level_values("individuals").unique()[0]
        bodyparts = (
            df[scorer][individuals]
            .columns.get_level_values("bodyparts")
            .unique()
            .tolist()
        )
    else:
        individuals = None
        bodyparts = (
            df[scorer].columns.get_level_values("bodyparts").unique().tolist()
        )
    return scorer, individuals, bodyparts


def detect_jitter_frames(
    h5_path: Path,
    px_threshold: float = 10.0,
    min_jittery_parts: int = 3,
    max_frames: int = 200,
    window: int = 5,
) -> list[tuple[int, dict]]:
    """
    Load _machine_predictions_raw.h5, apply median filter, return jittery frames.

    Returns list of (video_frame_number, {bodypart: {"x", "y", "likelihood"}}).
    Sorted by video_frame_number ascending. Capped at max_frames (highest-displacement
    frames kept when capping).
    """
    df = pd.read_hdf(str(h5_path))
    scorer, individuals, bodyparts = _get_scorer_and_bodyparts(df)

    # Sort rows by video frame number for correct temporal filtering
    frame_nums_raw = [_parse_frame_number(Path(idx).name) for idx in df.index]
    order = np.argsort(frame_nums_raw)
    df = df.iloc[order]
    frame_nums = [frame_nums_raw[i] for i in order]

    # Build filtered series per bodypart
    filtered: dict[str, dict] = {}
    for bp in bodyparts:
        try:
            if individuals:
                x_raw = df[(scorer, individuals, bp, "x")]
                y_raw = df[(scorer, individuals, bp, "y")]
                lh    = df[(scorer, individuals, bp, "likelihood")]
            else:
                x_raw = df[(scorer, bp, "x")]
                y_raw = df[(scorer, bp, "y")]
                lh    = df[(scorer, bp, "likelihood")]
        except KeyError:
            continue
        filtered[bp] = {
            "x_raw": x_raw,
            "y_raw": y_raw,
            "x_filt": _apply_median_filter(x_raw, window),
            "y_filt": _apply_median_filter(y_raw, window),
            "likelihood": lh,
        }

    # Detect jittery frames
    results: list[tuple[int, dict, float]] = []  # (frame_num, coords, max_disp)
    for i, frame_num in enumerate(frame_nums):
        jittery_bps = 0
        coords: dict[str, dict] = {}
        max_disp = 0.0
        for bp, data in filtered.items():
            rx, ry = float(data["x_raw"].iloc[i]), float(data["y_raw"].iloc[i])
            fx, fy = float(data["x_filt"].iloc[i]), float(data["y_filt"].iloc[i])
            lh     = float(data["likelihood"].iloc[i])
            if np.isnan(rx) or np.isnan(ry) or np.isnan(fx) or np.isnan(fy):
                coords[bp] = {"x": fx if not np.isnan(fx) else rx,
                              "y": fy if not np.isnan(fy) else ry,
                              "likelihood": lh}
                continue
            disp = np.sqrt((rx - fx) ** 2 + (ry - fy) ** 2)
            if disp > px_threshold:
                jittery_bps += 1
                max_disp = max(max_disp, disp)
            coords[bp] = {"x": fx, "y": fy, "likelihood": lh}
        if jittery_bps >= min_jittery_parts:
            results.append((frame_num, coords, max_disp))

    if len(results) > max_frames:
        results.sort(key=lambda r: r[2], reverse=True)
        results = results[:max_frames]
        results.sort(key=lambda r: r[0])

    return [(frame_num, coords) for frame_num, coords, _ in results]


def upsert_frames(
    stem_dir: Path,
    video_path: Path,
    jitter_frames: list[tuple[int, dict]],
    scorer: str,
    bodyparts: list[str],
    min_lh: float = 0.6,
) -> dict:
    """
    Extract/update frames in stem_dir using filtered coordinates as initial labels.

    jitter_frames: list of (video_frame_number, {bodypart: {"x", "y", "likelihood"}})
    Returns {"added": int, "updated": int, "stem": str}.
    """
    stem_dir = Path(stem_dir)
    stem_dir.mkdir(parents=True, exist_ok=True)

    # Build {frame_num: filename} for frames already in the folder
    existing: dict[int, str] = {}
    for p in sorted(stem_dir.glob("img*-*.png")):
        try:
            existing[_parse_frame_number(p.name)] = p.name
        except ValueError:
            continue

    next_nnnn = len(existing)
    csv_path = stem_dir / f"CollectedData_{scorer}.csv"

    rows_to_write: dict[str, dict[str, tuple[float, float] | None]] = {}
    new_images: list[tuple[int, str]] = []  # (frame_num, filename) needing extraction

    added = updated = 0

    for frame_num, coords in jitter_frames:
        coord_map: dict[str, tuple[float, float] | None] = {}
        for bp in bodyparts:
            bp_data = coords.get(bp)
            if bp_data and bp_data.get("likelihood", 0) >= min_lh:
                x, y = bp_data.get("x"), bp_data.get("y")
                if x is not None and y is not None and not (np.isnan(x) or np.isnan(y)):
                    coord_map[bp] = (float(x), float(y))
                else:
                    coord_map[bp] = None
            else:
                coord_map[bp] = None

        if frame_num in existing:
            rows_to_write[existing[frame_num]] = coord_map
            updated += 1
        else:
            filename = f"img{next_nnnn:04d}-{frame_num:05d}.png"
            rows_to_write[filename] = coord_map
            new_images.append((frame_num, filename))
            next_nnnn += 1
            added += 1

    # Extract new frames from video in one pass
    if new_images:
        cap = cv2.VideoCapture(str(video_path))
        for frame_num, filename in new_images:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if ret:
                cv2.imwrite(str(stem_dir / filename), frame)
        cap.release()

    # Upsert CSV
    _upsert_csv(csv_path, stem_dir.name, scorer, bodyparts, rows_to_write)

    return {"added": added, "updated": updated, "stem": stem_dir.name}


def _upsert_csv(
    csv_path: Path,
    stem_name: str,
    scorer: str,
    bodyparts: list[str],
    rows: dict[str, dict[str, tuple[float, float] | None]],
) -> None:
    """
    Write or update a DLC MultiIndex CSV.
    rows: {filename: {bodypart: (x, y) | None}}
    """
    # Build header
    scorer_header   = ["scorer",    "", ""] + [scorer for bp in bodyparts for _ in ("x", "y")]
    bp_header       = ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in ("x", "y")]
    coords_header   = ["coords",    "", ""] + ["x", "y"] * len(bodyparts)

    # Read existing data rows
    existing_rows: dict[str, list[str]] = {}
    if csv_path.is_file():
        with open(str(csv_path), newline="") as f:
            reader = csv.reader(f)
            all_rows = list(reader)
        for row in all_rows[3:]:
            if len(row) >= 3:
                existing_rows[row[2]] = row

    # Merge updates
    for filename, coords in rows.items():
        data_vals: list[str] = []
        for bp in bodyparts:
            xy = coords.get(bp)
            if xy is not None:
                data_vals.extend([f"{xy[0]:.4f}", f"{xy[1]:.4f}"])
            else:
                data_vals.extend(["", ""])
        existing_rows[filename] = ["labeled-data", stem_name, filename] + data_vals

    # Write back
    with open(str(csv_path), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(scorer_header)
        writer.writerow(bp_header)
        writer.writerow(coords_header)
        for row in existing_rows.values():
            writer.writerow(row)
