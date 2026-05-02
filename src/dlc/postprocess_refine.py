"""refineDLC drivers and DLC predictions I/O.

Reads/writes analyzed prediction tables (DLC's MultiIndex layout):
    columns = MultiIndex[(scorer, bodyparts, coords)]
    coords ∈ {"x", "y", "likelihood"}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter as _savgol_filter


def read_predictions(path: str | Path) -> pd.DataFrame:
    """Load a DLC predictions table from .h5 or .csv into a DataFrame."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".h5":
        # DLC's analyzed h5 has a single key; pandas auto-selects it.
        return pd.read_hdf(p)
    if suf == ".csv":
        # DLC's analyzed CSV has 3 header rows for the MultiIndex.
        df = pd.read_csv(p, header=[0, 1, 2], index_col=0)
        # Ensure column-level names round-trip; some pandas versions leave them None.
        df.columns.names = ["scorer", "bodyparts", "coords"]
        return df
    raise ValueError(f"Unsupported predictions extension: {suf!r}")


def write_predictions(df: pd.DataFrame, path: str | Path) -> None:
    """Write a DLC predictions DataFrame back to .h5 or .csv preserving format."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    suf = p.suffix.lower()
    if suf == ".h5":
        df.to_hdf(p, key="df_with_missing", mode="w", format="table")
    elif suf == ".csv":
        df.to_csv(p)
    else:
        raise ValueError(f"Unsupported predictions extension: {suf!r}")


def step_likelihood_filter(df: pd.DataFrame, *, threshold: float = 0.6) -> pd.DataFrame:
    """Drop (set NaN) x/y values where likelihood < threshold.

    Likelihood column itself is left unchanged so downstream steps can re-filter.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    for bp in bodyparts:
        lh = out[(scorer, bp, "likelihood")]
        mask = lh < threshold
        out.loc[mask, (scorer, bp, "x")] = float("nan")
        out.loc[mask, (scorer, bp, "y")] = float("nan")
    return out


def step_outlier_removal(df: pd.DataFrame, *, z_threshold: float = 3.0) -> pd.DataFrame:
    """Set x/y to NaN where |modified z-score| > z_threshold (per bodypart, per coord).

    Uses the MAD-based modified z-score (0.6745 * (x - median) / MAD), which is
    robust to outliers — unlike the classical mean/std z-score where a single
    extreme value inflates the std and masks itself.
    """
    if z_threshold <= 0:
        raise ValueError(f"z_threshold must be > 0, got {z_threshold}")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    for bp in bodyparts:
        for axis in ("x", "y"):
            col = (scorer, bp, axis)
            series = out[col]
            values = series.to_numpy(dtype=float)
            finite = ~pd.isna(values)
            if finite.sum() == 0:
                continue
            med = float(pd.Series(values[finite]).median())
            mad = float(pd.Series(np.abs(values[finite] - med)).median())
            if mad == 0 or pd.isna(mad):
                continue
            mod_z = 0.6745 * (values - med) / mad
            mask = np.abs(mod_z) > z_threshold
            mask = mask & finite
            out.loc[mask, col] = float("nan")
    return out


def step_interpolation(
    df: pd.DataFrame,
    *,
    method: str = "linear",
    limit: int | None = None,
) -> pd.DataFrame:
    """Interpolate NaN gaps in x and y per bodypart."""
    if method not in {"linear", "spline", "polynomial", "nearest", "cubic"}:
        raise ValueError(f"unsupported interpolation method: {method!r}")
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be >= 1 or None, got {limit}")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    interp_kwargs = {"method": method}
    if limit is not None:
        interp_kwargs["limit"] = limit
    for bp in bodyparts:
        for axis in ("x", "y"):
            col = (scorer, bp, axis)
            out[col] = out[col].interpolate(**interp_kwargs)
    return out


def step_smoothing(
    df: pd.DataFrame,
    *,
    window: int = 5,
    polyorder: int = 2,
) -> pd.DataFrame:
    """Savitzky-Golay smoothing applied to x and y per bodypart.

    NaN gaps are preserved (savgol can't handle NaN; we mask, smooth, restore).
    """
    if window % 2 == 0 or window < 3:
        raise ValueError(f"window must be odd and >= 3, got {window}")
    if polyorder >= window:
        raise ValueError(f"polyorder ({polyorder}) must be < window ({window})")

    out = df.copy()
    scorer = out.columns.levels[0][0]
    bodyparts = out.columns.get_level_values("bodyparts").unique()
    for bp in bodyparts:
        for axis in ("x", "y"):
            col = (scorer, bp, axis)
            series = out[col]
            finite = series.notna()
            if finite.sum() < window:
                continue
            values = series.to_numpy(dtype=float, copy=True)
            smoothed = values.copy()
            mask = finite.to_numpy()
            smoothed[mask] = _savgol_filter(
                values[mask], window_length=window, polyorder=polyorder, mode="interp"
            )
            out[col] = smoothed
    return out


PIPELINE_ORDER = ("likelihood_filter", "outlier_removal", "interpolation", "smoothing")

_STEP_FUNCS = {
    "likelihood_filter": "step_likelihood_filter",
    "outlier_removal":   "step_outlier_removal",
    "interpolation":     "step_interpolation",
    "smoothing":         "step_smoothing",
}


def run_pipeline(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Apply enabled refineDLC steps in fixed order: filter → outliers → interp → smooth.

    `config` shape: {step_name: {"enabled": bool, **step_kwargs}}.
    Disabled or missing steps are skipped.
    """
    out = df
    for step in PIPELINE_ORDER:
        cfg = config.get(step) or {}
        if not cfg.get("enabled"):
            continue
        kwargs = {k: v for k, v in cfg.items() if k != "enabled"}
        func = globals()[_STEP_FUNCS[step]]
        out = func(out, **kwargs)
    return out


def run_single(df: pd.DataFrame, step: str, params: dict) -> pd.DataFrame:
    """Apply exactly one step by name."""
    if step not in _STEP_FUNCS:
        raise ValueError(f"unknown step: {step!r}")
    func = globals()[_STEP_FUNCS[step]]
    return func(df, **params)
