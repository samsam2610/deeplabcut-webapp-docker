# Vendored from https://github.com/wer-kle/refineDLC at 72cece845595435d3bafa99ff8b27b070ce05945.
# See VENDORED.md for license and details.
# Source: NOT PRESENT in upstream refineDLC at the vendored SHA.
#
# Upstream refineDLC ships four processing modules (likelihood_filter,
# position_filter, interpolate, clean_coordinates) but does NOT ship a
# smoothing module. The post-process card plan (task T7) requires a
# step_smoothing adapter, so this file exists as a stable home for that
# logic. T7 will populate the actual smoothing implementation; for now we
# vendor only a thin reference implementation (rolling mean / Savitzky-Golay
# style) so that the package's public surface is non-empty and the dep-audit
# test passes.
#
# When T7 lands, the chosen smoothing approach should be documented in
# VENDORED.md as locally authored (not vendored from upstream).
"""
smoothing.py

Coordinate-smoothing helpers for DLC post-processing. Upstream refineDLC has
no smoothing module; this is a local stub that T7 will replace/extend.
"""
from __future__ import annotations

import logging
import pandas as pd


def smooth_coordinates(input_file: str, output_file: str,
                       window: int = 5,
                       selected_bodyparts: list[str] | None = None) -> None:
    """Apply a centered rolling-mean over each ``*_x`` / ``*_y`` column.

    This is a deliberately simple placeholder so the vendored package has
    public surface area. T7 will replace this with the production smoothing
    step (Savitzky-Golay / median / configurable kernel).

    Parameters
    ----------
    input_file : str
        Path to a flat DLC CSV (single-row header, ``<bodypart>_x``/``_y``
        coord columns).
    output_file : str
        Where to write the smoothed CSV.
    window : int
        Rolling-mean window length (frames). Must be >= 1.
    selected_bodyparts : list[str] | None
        If provided, only smooth coord columns whose prefix matches one of
        these bodyparts. Otherwise smooth all ``_x`` / ``_y`` columns.
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")

    logging.info("Loading data from %s", input_file)
    data = pd.read_csv(input_file)

    if selected_bodyparts:
        coord_columns = [
            col for col in data.columns
            if any(col.startswith(bp + '_') for bp in selected_bodyparts)
            and (col.endswith('_x') or col.endswith('_y'))
        ]
    else:
        coord_columns = [
            col for col in data.columns
            if col.endswith('_x') or col.endswith('_y')
        ]

    smoothed = data.copy()
    for col in coord_columns:
        smoothed[col] = (
            data[col]
            .rolling(window=window, min_periods=1, center=True)
            .mean()
        )
        logging.info("Smoothed %s with window=%d", col, window)

    logging.info("Saving smoothed data to %s", output_file)
    smoothed.to_csv(output_file, index=False)
