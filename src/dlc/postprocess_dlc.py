"""Filter DLC pose predictions with the same algorithm DLC ships, locally.

We mirror DeepLabCut's ``filterpredictions`` median-filter branch verbatim
(scipy.signal.medfilt over coord columns, leaving likelihood untouched) but
drop DLC's video/config/scorer plumbing so we can run on any analyzed
.h5/.csv directly without needing the project to be set up around it.

Output layout (caller is responsible for ``output_dir``):
    <output_dir>/<input-stem>_filtered.h5
    <output_dir>/<input-stem>_filtered.csv  (if save_as_csv)

Note: ``params`` is consumed by this call (``save_as_csv`` is popped out).
Pass a copy if the caller needs to retain the original dict.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy import signal

ALLOWED_EXTS = {".h5", ".csv"}


def _read_predictions(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf == ".h5":
        return pd.read_hdf(path)
    if suf == ".csv":
        df = pd.read_csv(path, header=[0, 1, 2], index_col=0)
        df.columns.names = ["scorer", "bodyparts", "coords"]
        return df
    raise ValueError(f"unsupported input extension: {suf!r}")


def _median_filter(df: pd.DataFrame, windowlength: int) -> pd.DataFrame:
    """Apply scipy.signal.medfilt to every non-likelihood coord column.

    Mirrors deeplabcut.filterpredictions(filtertype='median') exactly.
    """
    if windowlength % 2 == 0 or windowlength < 3:
        raise ValueError(f"windowlength must be odd and >= 3, got {windowlength}")

    out = df.copy()
    mask = out.columns.get_level_values("coords") != "likelihood"
    out.loc[:, mask] = df.loc[:, mask].apply(
        signal.medfilt, args=(windowlength,), axis=0,
    )
    return out


def run_filterpredictions(
    *,
    config_path: str | Path = "",  # accepted for backward-compat; unused
    input_path: str | Path,
    output_dir: str | Path,
    params: dict,
) -> dict:
    """Filter a single analyzed file; write results into ``output_dir``.

    Returns: {"status": "success" | "failed", "output": <Path>, "error": str | None}.

    The source file is never modified. The filtered output is written directly
    into ``output_dir`` (no temp file next to the source, no shutil.move).
    """
    src = Path(input_path)
    out_dir = Path(output_dir)

    if src.suffix.lower() not in ALLOWED_EXTS:
        raise ValueError(f"unsupported input extension: {src.suffix!r}")
    if not src.is_file():
        raise FileNotFoundError(src)

    out_dir.mkdir(parents=True, exist_ok=True)

    save_as_csv  = bool(params.pop("save_as_csv", False))
    filtertype   = params.pop("filtertype", "median")
    windowlength = int(params.pop("windowlength", 5))
    # remaining params are silently ignored for median; future filtertypes can
    # consume them.

    try:
        df = _read_predictions(src)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "output": None,
                "error": f"read failed: {type(exc).__name__}: {exc}"}

    try:
        if filtertype == "median":
            filtered = _median_filter(df, windowlength)
        elif filtertype == "arima":
            return {"status": "failed", "output": None,
                    "error": "ARIMA filter not yet supported in this app; "
                             "use median or refineDLC"}
        else:
            return {"status": "failed", "output": None,
                    "error": f"unknown filtertype: {filtertype!r}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "output": None,
                "error": f"filter failed: {type(exc).__name__}: {exc}"}

    out_h5 = out_dir / (src.stem + "_filtered.h5")
    try:
        filtered.to_hdf(out_h5, key="df_with_missing", mode="w", format="table")
        if save_as_csv:
            (out_dir / (src.stem + "_filtered.csv")).write_text("")  # touch
            filtered.to_csv(out_dir / (src.stem + "_filtered.csv"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "output": None,
                "error": f"write failed: {type(exc).__name__}: {exc}"}

    return {"status": "success", "output": out_h5, "error": None}
