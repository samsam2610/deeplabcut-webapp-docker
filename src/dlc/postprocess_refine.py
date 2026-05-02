"""refineDLC drivers and DLC predictions I/O.

Reads/writes analyzed prediction tables (DLC's MultiIndex layout):
    columns = MultiIndex[(scorer, bodyparts, coords)]
    coords ∈ {"x", "y", "likelihood"}
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


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
