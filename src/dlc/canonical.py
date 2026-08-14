"""Canonical per-video analysis file mechanics.

One analysis file per video: <videostem>_analyzed.h5 + .csv, DeepLabCut
format. All analysis funnels through write_to_canonical(), which re-labels
the column scorer level to a fixed project scorer and merges into the
dense canonical h5. Standalone (pandas/numpy/yaml only) so both the Celery
worker (tasks.py) and the Flask routes (inline_analysis.py) can import it
without pulling DLC/Celery deps.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

CANONICAL_SCORER_FALLBACK = "DLC_analyzed"
_COORDS = ["x", "y", "likelihood"]


def canonical_h5_path(video_path) -> Path:
    p = Path(video_path)
    return p.with_name(p.stem + "_analyzed.h5")


def canonical_csv_path(video_path) -> Path:
    return canonical_h5_path(video_path).with_suffix(".csv")


def labeled_frames(analyzed_df) -> set:
    """Frame indices 'marked' in the _analyzed coverage timeline: any bodypart
    with a finite x (presence mode; mirrors viewer._coverage_buckets). Used by the
    range worker to skip frames finalized in <stem>_analyzed.h5."""
    if analyzed_df is None or not len(analyzed_df):
        return set()
    x = analyzed_df.xs("x", level="coords", axis=1)
    return set(analyzed_df.index[x.notna().any(axis=1)].tolist())


def _read_config(config_path) -> dict:
    import yaml
    return yaml.safe_load(Path(config_path).read_text()) or {}


def canonical_scorer(config_path) -> str:
    try:
        cfg = _read_config(config_path)
    except Exception:
        return CANONICAL_SCORER_FALLBACK
    s = cfg.get("scorer")
    return str(s) if s else CANONICAL_SCORER_FALLBACK


def read_bodyparts(config_path) -> list:
    cfg = _read_config(config_path)
    bps = cfg.get("bodyparts") or []
    return [str(b) for b in bps]


def build_empty_dense_df(scorer: str, bodyparts: list, nframes: int) -> pd.DataFrame:
    cols = pd.MultiIndex.from_product(
        [[scorer], list(bodyparts), _COORDS],
        names=["scorer", "bodyparts", "coords"],
    )
    n = max(int(nframes), 0)
    data = np.full((n, len(bodyparts) * 3), np.nan, dtype=float)
    return pd.DataFrame(data, index=pd.RangeIndex(n), columns=cols)


def relabel_scorer(df: pd.DataFrame, old_scorer: str, new_scorer: str) -> pd.DataFrame:
    if old_scorer == new_scorer:
        return df
    return df.rename(columns={old_scorer: new_scorer}, level=0)


def _atomic_write_h5(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_hdf(str(tmp), key="df_with_missing", mode="w", format="fixed")
    os.replace(str(tmp), str(path))


def _atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(str(tmp))
    os.replace(str(tmp), str(path))


def write_empty(video_path, *, scorer: str, bodyparts: list, nframes: int,
                save_as_csv: bool = True):
    """Create the dense all-NaN canonical file. Returns (h5_path, csv_path)."""
    h5 = canonical_h5_path(video_path)
    df = build_empty_dense_df(scorer, bodyparts, nframes)
    _atomic_write_h5(h5, df)
    csv = canonical_csv_path(video_path)
    if save_as_csv:
        _atomic_write_csv(csv, df)
    return h5, csv


def unfinalize_range(video_path, start_frame: int, n_frames: int) -> int:
    """Un-finalize: set rows [start_frame, start_frame+n_frames) to NaN in the
    canonical _analyzed file (the inverse of write_to_canonical for that range).
    Rows outside the range are untouched; the .h5 and .csv are rewritten
    atomically so they stay consistent. Missing file -> 0. Returns rows cleared."""
    h5 = canonical_h5_path(video_path)
    if not h5.exists():
        return 0
    df = pd.read_hdf(str(h5))
    wanted = range(int(start_frame), int(start_frame) + int(n_frames))
    mask = df.index.isin(wanted)
    n = int(mask.sum())
    if n:
        df.loc[mask, :] = np.nan
        _atomic_write_h5(h5, df)
        csv = canonical_csv_path(video_path)
        if csv.exists():
            _atomic_write_csv(csv, df)
    return n


def write_to_canonical(video_path, df: pd.DataFrame, *, source_scorer: str,
                       canonical_scorer: str, save_as_csv: bool = True):
    """Re-label scorer → canonical, merge into the dense canonical h5, write.

    Returns (h5_path, csv_path).
    """
    h5 = canonical_h5_path(video_path)
    df = relabel_scorer(df, source_scorer, canonical_scorer)
    existing = pd.read_hdf(str(h5)) if h5.exists() else None
    merged = df if existing is None else df.combine_first(existing)
    if existing is not None:
        # combine_first unions + alphabetically re-sorts columns. Pin the
        # canonical file's existing column order so the DLC (scorer, bodyparts,
        # coords) layout that downstream positional consumers rely on is
        # preserved across runs.
        merged = merged.reindex(columns=existing.columns)
    if len(merged):
        max_idx = int(merged.index.max())
        merged = merged.reindex(pd.RangeIndex(start=0, stop=max_idx + 1,
                                              name=merged.index.name))
    _atomic_write_h5(h5, merged)
    csv = canonical_csv_path(video_path)
    if save_as_csv:
        _atomic_write_csv(csv, merged)
    return h5, csv
