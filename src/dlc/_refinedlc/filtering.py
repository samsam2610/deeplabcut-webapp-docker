# Vendored from https://github.com/wer-kle/refineDLC at 72cece845595435d3bafa99ff8b27b070ce05945.
# See VENDORED.md for license and details.
# Source: refinedlc/likelihood_filter.py
#
# MIT License
# Copyright (c) 2025 Weronika Klecel, Hadley Rahael & Samantha A. Brooks
"""
likelihood_filter.py

Filters DeepLabCut data based on likelihood scores.
Low likelihood values result in NaNs in coordinate columns; likelihood values are retained.
Supports single-file or batch-directory processing.
Filtering can be based on a fixed threshold (--threshold) or by removing a percentage of lowest values (--percentile).
"""
import logging
import pandas as pd
from pathlib import Path


def likelihood_filter(input_file: str, output_file: str,
                      threshold: float = None,
                      percentile: float = None,
                      summary: list = None):
    """Apply likelihood-based filtering and record summary data if requested."""
    logging.info("Loading data from %s", input_file)
    data = pd.read_csv(input_file)

    likelihood_cols = [col for col in data.columns if col.endswith('_likelihood')]
    if not likelihood_cols:
        logging.warning("No likelihood columns found in %s. Saving unchanged.", input_file)
        data.to_csv(output_file, index=False)
        return

    total_frames = len(data)
    for col in likelihood_cols:
        base = col[:-len('_likelihood')]
        # Determine threshold or percentile threshold value
        if percentile is not None:
            thresh_val = data[col].quantile(percentile / 100.0)
            logging.info("Removing lowest %.2f%% frames on %s (threshold=%.4f)", percentile, col, thresh_val)
            mask = data[col] < thresh_val
            # record threshold value
            if summary is not None:
                summary.append({'file': Path(input_file).name,
                                'bodypart': base,
                                'value': thresh_val})
        else:
            thresh_val = threshold
            logging.info("Applying fixed threshold on %s (threshold=%.4f)", col, thresh_val)
            mask = data[col] < thresh_val
            # record percent removed
            if summary is not None:
                percent_removed = mask.sum() / total_frames * 100
                summary.append({'file': Path(input_file).name,
                                'bodypart': base,
                                'value': percent_removed})

        # Apply filtering: set coords to NaN
        for suffix in ['_x', '_y']:
            coord_col = f"{base}{suffix}"
            if coord_col in data.columns:
                data.loc[mask, coord_col] = pd.NA

    logging.info("Saving filtered data to %s", output_file)
    data.to_csv(output_file, index=False)


def process_file(input_path: str, output_dir: str,
                 threshold: float = None,
                 percentile: float = None,
                 summary: list = None):
    process_file_name = Path(input_path).name
    out_path = Path(output_dir) / process_file_name
    likelihood_filter(str(input_path), str(out_path), threshold=threshold,
                      percentile=percentile, summary=summary)
