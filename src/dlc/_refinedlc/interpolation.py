# Vendored from https://github.com/wer-kle/refineDLC at 72cece845595435d3bafa99ff8b27b070ce05945.
# See VENDORED.md for license and details.
# Source: refinedlc/interpolate.py
#
# MIT License
# Copyright (c) 2025 Weronika Klecel, Hadley Rahael & Samantha A. Brooks
"""
interpolate.py

Interpolates missing data points in DeepLabCut coordinate data.
Supports various interpolation methods and limits interpolation to gaps no larger than a user-defined maximum.
Supports single-file or batch-directory processing, with diagnostic logging of NaN counts before and after.
"""
from __future__ import annotations

import logging
import pandas as pd
from pathlib import Path


def interpolate_data(input_file: str, output_file: str, method: str, max_gap: int, selected_bodyparts: list[str] | None = None, displacement_threshold: float | None = None):
    logging.info("=== interpolate_data start for %s ===", input_file)
    try:
        data = pd.read_csv(input_file)
    except Exception as e:
        logging.error("Failed to load input file %s: %s", input_file, e)
        raise
    if selected_bodyparts:
        coord_columns = [col for col in data.columns if any(col.startswith(bp + '_') for bp in selected_bodyparts)]
    else:
        coord_columns = [col for col in data.columns if col.endswith('_x') or col.endswith('_y')]
    data_interpolated = data.copy()

    # Minimum valid points required per method
    min_points = {
        'zero': 2,
        'linear': 2,
        'slinear': 2,
        'cubic': 4,
        'spline': 4
    }

    for col in coord_columns:
        series = data[col]
        before_nans = series.isna().sum()
        valid = series.dropna()
        logging.info("Column '%s': %d NaNs before interpolation", col, before_nans)

        # Determine if fallback to linear is needed
        use_method = method
        if len(valid) < min_points.get(method, 2):
            logging.warning(
                "Column %s has only %d valid points; falling back to linear interpolation.",
                col, len(valid)
            )
            use_method = 'linear'

        logging.info(
            "Interpolating column %s with method '%s' and max_gap=%d",
            col, use_method, max_gap
        )

        # Perform interpolation for interior gaps
        if use_method == 'spline':
            # Use a cubic spline of order 3
            interp_series = series.interpolate(
                method='spline',
                order=3,
                limit=max_gap,
                limit_direction='both'
            )
        else:
            interp_series = series.interpolate(
                method=use_method,
                limit=max_gap,
                limit_direction='both'
            )
        # Fill leading/trailing small gaps via backward/forward fill
        interp_series = interp_series.fillna(method='bfill', limit=max_gap)
        interp_series = interp_series.fillna(method='ffill', limit=max_gap)

        after_nans = interp_series.isna().sum()
        logging.info("Column '%s': %d NaNs after interpolation", col, after_nans)

        data_interpolated[col] = interp_series

    # Revert large displacements to NaN if threshold is set
    if displacement_threshold is not None:
        for bp in set(col.rsplit('_', 1)[0] for col in coord_columns if col.endswith('_x')):
            x_col = f"{bp}_x"
            y_col = f"{bp}_y"
            if x_col in data_interpolated and y_col in data_interpolated:
                dx = data_interpolated[x_col].diff()
                dy = data_interpolated[y_col].diff()
                displacement = (dx ** 2 + dy ** 2) ** 0.5
                exceed = displacement > displacement_threshold
                data_interpolated.loc[exceed, x_col] = float('nan')
                data_interpolated.loc[exceed, y_col] = float('nan')
                logging.info("Bodypart %s: %d frames exceeded displacement threshold and were reverted to NaN", bp, exceed.sum())

    logging.info("Saving interpolated data to %s", output_file)
    data_interpolated.to_csv(output_file, index=False)
    logging.info("=== interpolate_data end for %s ===", input_file)


def process_file(input_path: str, output_dir: str, method: str, max_gap: int, selected_bodyparts: list[str] | None = None, displacement_threshold: float | None = None):
    filename = Path(input_path).name
    output_path = Path(output_dir) / filename
    interpolate_data(str(input_path), str(output_path), method, max_gap, selected_bodyparts, displacement_threshold)
