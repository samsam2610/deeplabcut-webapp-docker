"""
Custom Analysis Script Template
================================
Place your analysis logic inside the `run` function below.

Interface
---------
  input_paths : list[str]   – absolute paths to the input CSV file(s)
  output_dir  : str         – directory where you should write all output files

Rules
-----
  * Do NOT modify or overwrite any existing files.
  * Write all output to `output_dir` (already created, timestamped).
  * Use print() for progress messages — they appear in the UI log.
  * A filename returned in the return dict's "output_files" list will be
    shown in the UI (optional).

Example output filename pattern (already handled for you via output_dir):
  <output_dir>/my_results.csv   →  won't clash with previous runs.
"""

import os
import pandas as pd   # install with: pip install pandas


def run(input_paths: list, output_dir: str) -> None:
    """
    Parameters
    ----------
    input_paths : list of str
        Paths to the input CSV files selected in the UI.
    output_dir : str
        Directory to write output files into.
        Already timestamped – safe to write freely.
    """

    for csv_path in input_paths:
        print(f"Processing: {csv_path}")

        # ── Read data ──────────────────────────────────────────
        df = pd.read_csv(csv_path)
        print(f"  Loaded {len(df)} rows, {len(df.columns)} columns")

        # ── Your analysis here ─────────────────────────────────
        # Example: compute basic statistics
        stats = df.describe()

        # ── Save output ────────────────────────────────────────
        base_name    = os.path.splitext(os.path.basename(csv_path))[0]
        output_file  = os.path.join(output_dir, f"{base_name}_stats.csv")
        stats.to_csv(output_file)
        print(f"  Saved: {output_file}")

    print("Done.")
