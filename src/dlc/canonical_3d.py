"""Incremental 3D canonical store — anipose pose-3d flat CSV format.

Mirrors ``dlc/canonical.py`` (the 2D ``_analyzed`` store) but for triangulated
3D output. Ranges accumulate into ONE dense canonical CSV per stereo pair so
triangulating a new keyframe range never re-runs previously triangulated ranges.

Two files per pair, inside the session/video folder (``current_folder``):

    pose-3d/<pair>_3d.csv           ← raw canonical (dense, incremental)
    pose-3d-filtered/<pair>_3d.csv  ← anipose 3D median-filter, range-spliced

The anipose pose-3d CSV is a FLAT table: one column per ``<bp>_{x,y,z}``,
``<bp>_error``, ``<bp>_ncams``, ``<bp>_score`` plus the ``M_ij`` / ``center_i``
metadata columns. Row index = global frame number (named ``fnum``).

Standalone (pandas / numpy / scipy only) so both the Celery worker
(anipose/tasks.py) and the Flask routes (dlc/inline_analysis.py) can import it.
CSV-only — no HDF5 — so it works in the Flask container (which lacks full HDF5)
and on host python (which may lack a working ``tables``).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Same cam-token convention as the dlc-3D module (routes.py _CAM_RE) and the
# anipose [triangulation] cam_regex. Matches ``_cam0_`` / ``_cam1_`` etc.
_CAM_RE = re.compile(r"_cam(\d+)_")
_INDEX_NAME = "fnum"
_COORD_SUFFIXES = ("_x", "_y", "_z")


# ── pair name / paths ──────────────────────────────────────────────────────

def pair_name_from_cam0(cam0_video_path) -> str:
    """Derive a stable pair name from the cam0 stem with the cam token
    normalized (``_cam0_`` → ``_cam_``), so cam0 and cam1 map to ONE 3D file.
    Stems with no ``_cam<N>_`` token are returned unchanged."""
    stem = Path(cam0_video_path).stem
    if not _CAM_RE.search(stem):
        return stem
    return _CAM_RE.sub("_cam_", stem, count=1)


def canonical_3d_csv_path(session_dir, pair_name) -> Path:
    return Path(session_dir) / "pose-3d" / f"{pair_name}_3d.csv"


def filtered_3d_csv_path(session_dir, pair_name) -> Path:
    return Path(session_dir) / "pose-3d-filtered" / f"{pair_name}_3d.csv"


# ── atomic csv helpers ─────────────────────────────────────────────────────

def _atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(str(tmp), index=True)
    os.replace(str(tmp), str(path))


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(str(path), index_col=0)
    df.index = df.index.astype(int)
    if df.index.name is None:
        df.index.name = _INDEX_NAME
    return df


def _dense_merge(df_range: pd.DataFrame, existing: "pd.DataFrame | None") -> pd.DataFrame:
    """Combine_first over existing (range wins), preserving the existing column
    order, then reindex to a dense ``0..max`` frame range."""
    df_range = df_range.copy()
    df_range.index = df_range.index.astype(int)
    df_range.index.name = _INDEX_NAME
    merged = df_range if existing is None else df_range.combine_first(existing)
    if existing is not None:
        # combine_first unions + alphabetically re-sorts columns. Pin the
        # canonical file's existing column order so downstream positional
        # consumers see a stable layout across runs; new columns append.
        cols = list(existing.columns) + [
            c for c in merged.columns if c not in existing.columns
        ]
        merged = merged.reindex(columns=cols)
    if len(merged):
        max_idx = int(merged.index.max())
        merged = merged.reindex(
            pd.RangeIndex(start=0, stop=max_idx + 1, name=_INDEX_NAME))
    merged.index.name = _INDEX_NAME
    return merged


# ── writes ─────────────────────────────────────────────────────────────────

def write_range_to_canonical_3d(session_dir, pair_name, df_range) -> Path:
    """Merge ``df_range`` (anipose pose-3d rows indexed by GLOBAL frame number)
    into the dense raw canonical, atomically. Returns the canonical CSV path."""
    path = canonical_3d_csv_path(session_dir, pair_name)
    existing = _read_csv(path) if path.exists() else None
    merged = _dense_merge(df_range, existing)
    _atomic_write_csv(path, merged)
    return path


# ── median filter (3D) ─────────────────────────────────────────────────────

def _medfilt_data(values: np.ndarray, size: int) -> np.ndarray:
    """Median filter with the same edge-padding as
    ``anipose_src.filter_3d_funcs.medfilt_data``. Prefer the real anipose
    implementation when importable (worker); fall back to scipy otherwise."""
    try:  # worker container has anipose
        from anipose_src.filter_3d_funcs import medfilt_data as _md
        return _md(values, size=int(size))
    except Exception:
        from scipy import signal
        size = int(size)
        if size % 2 == 0:
            size += 1  # scipy.medfilt needs an odd kernel
        padsize = size + 5
        vpad = np.pad(values, (padsize, padsize), mode="median", stat_length=5)
        vpadf = signal.medfilt(vpad, kernel_size=size)
        return vpadf[padsize:-padsize]


def _interpolate(vals: np.ndarray) -> np.ndarray:
    """Linear-interpolate NaNs (mirror of filter_3d_funcs.interpolate_data)."""
    nans = np.isnan(vals)
    out = np.copy(vals)
    if nans.all() or np.mean(nans) > 0.85:
        return out
    ix = np.arange(len(vals))
    out[nans] = np.interp(ix[nans], ix[~nans], vals[~nans])
    return out


def medfilt_range_and_splice(session_dir, pair_name, start, n, config) -> Path:
    """Read ``[start, start+n)`` rows from the RAW canonical, median-filter each
    bodypart coordinate column (respecting ``offset_threshold`` when present),
    and splice those rows into the FILTERED canonical (dense combine_first +
    reindex). Returns the filtered CSV path. Absent raw canonical → no-op."""
    raw_path = canonical_3d_csv_path(session_dir, pair_name)
    filt_path = filtered_3d_csv_path(session_dir, pair_name)
    if not raw_path.exists():
        return filt_path

    raw = _read_csv(raw_path)
    start, n = int(start), int(n)
    wanted = [i for i in range(start, start + n) if i in raw.index]
    if not wanted:
        return filt_path
    seg = raw.loc[wanted].copy()

    f3d = (config or {}).get("filter3d", {}) or {}
    medfilt_size = int(f3d.get("medfilt", 15))
    offset_threshold = f3d.get("offset_threshold", None)

    bodyparts = [c[: -len("_error")] for c in seg.columns if c.endswith("_error")]
    for bp in bodyparts:
        bad = None
        err_col = f"{bp}_error"
        if offset_threshold is not None and err_col in seg.columns:
            err = seg[err_col].to_numpy(dtype=float)
            err = np.where(np.isnan(err), 1e9, err)
            bad = err > float(offset_threshold)
        for ax in ("x", "y", "z"):
            key = f"{bp}_{ax}"
            if key not in seg.columns:
                continue
            vals = seg[key].to_numpy(dtype=float).copy()
            if bad is not None:
                vals[bad] = np.nan
            vals = _interpolate(vals)
            vals = _medfilt_data(vals, medfilt_size)
            seg[key] = vals

    existing = _read_csv(filt_path) if filt_path.exists() else None
    merged = _dense_merge(seg, existing)
    _atomic_write_csv(filt_path, merged)
    return filt_path


# ── unfinalize / coverage ──────────────────────────────────────────────────

def unfinalize_3d_range(session_dir, pair_name, start, n) -> int:
    """Set rows ``[start, start+n)`` to NaN in BOTH canonicals (raw + filtered).
    Rows outside the range are untouched. Returns the number of distinct frame
    rows cleared (union across the two files); absent files contribute 0."""
    start, n = int(start), int(n)
    wanted = range(start, start + n)
    cleared: set = set()
    for path in (canonical_3d_csv_path(session_dir, pair_name),
                 filtered_3d_csv_path(session_dir, pair_name)):
        if not path.exists():
            continue
        df = _read_csv(path)
        mask = df.index.isin(wanted)
        if mask.any():
            cleared.update(int(i) for i in df.index[mask])
            df.loc[mask, :] = np.nan
            _atomic_write_csv(path, df)
    return len(cleared)


def canonical_3d_nframes(session_dir, pair_name) -> int:
    """Dense length (max frame index + 1) of the raw canonical; 0 if absent."""
    path = canonical_3d_csv_path(session_dir, pair_name)
    if not path.exists():
        return 0
    try:
        return int(len(_read_csv(path)))
    except Exception:
        return 0


def read_3d_coverage(session_dir, pair_name, buckets, total_frames=None) -> list:
    """Return ``buckets`` presence values in ``0..1`` describing which frame
    regions have non-NaN 3D rows (any x/y/z column present). Absent canonical
    → all zeros.

    ``total_frames`` is the FULL video frame count. Presence is placed by
    absolute frame number (``fnum`` = the canonical's index) and scaled over
    ``total_frames`` so the bar aligns with the seek / finalized timelines —
    the canonical is only dense up to the last-triangulated frame, so its own
    length is NOT the video length. When ``total_frames`` is missing/invalid,
    fall back to the canonical's dense length (legacy behaviour)."""
    buckets = int(buckets)
    if buckets <= 0:
        return []
    path = canonical_3d_csv_path(session_dir, pair_name)
    if not path.exists():
        return [0.0] * buckets
    try:
        df = _read_csv(path)
    except Exception:
        return [0.0] * buckets
    if len(df) == 0:
        return [0.0] * buckets

    coord_cols = [c for c in df.columns if c.endswith(_COORD_SUFFIXES)]
    if coord_cols:
        row_present = df[coord_cols].notna().any(axis=1).to_numpy()
    else:
        row_present = df.notna().any(axis=1).to_numpy()

    # Scale over the true video length; place each row at its absolute frame.
    canon_len = int(df.index.max()) + 1
    tf = int(total_frames) if total_frames and int(total_frames) > 0 else 0
    nframes = max(tf, canon_len) if tf else canon_len
    present = np.zeros(nframes, dtype=bool)
    fn = df.index.to_numpy()
    valid = (fn >= 0) & (fn < nframes)
    present[fn[valid]] = row_present[valid]

    out = []
    for b in range(buckets):
        lo = b * nframes // buckets
        hi = (b + 1) * nframes // buckets
        out.append(float(present[lo:hi].mean()) if hi > lo else 0.0)
    return out
