"""Wrapper around deeplabcut.filterpredictions with our output-layout contract.

Expected directory layout produced by this module:
    <output_dir>/<input-stem>_filtered.<ext>
    <output_dir>/<input-stem>_filtered.csv  (if save_as_csv)

`output_dir` must be the per-run subfolder built by the caller
(e.g. `<input-parent>/postproc/20260501-143022_filterpredictions/`).

Note: `params` is consumed by this call (`save_as_csv` is popped out). Pass a
copy if the caller needs to retain the original dict.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import deeplabcut as dlc

ALLOWED_EXTS = {".h5", ".csv"}


def run_filterpredictions(
    *,
    config_path: str | Path,
    input_path: str | Path,
    output_dir: str | Path,
    params: dict,
) -> dict:
    """Run DLC's filterpredictions on a single analyzed file; relocate output.

    Returns: {"status": "success" | "failed", "output": <Path>, "error": str | None}.

    The original file is never modified. DLC writes its output next to the
    input; we move the produced file into `output_dir` and never touch the
    source.
    """
    src = Path(input_path)
    out_dir = Path(output_dir)
    cfg = Path(config_path)

    if src.suffix.lower() not in ALLOWED_EXTS:
        raise ValueError(f"unsupported input extension: {src.suffix!r}")
    if not src.is_file():
        raise FileNotFoundError(src)

    out_dir.mkdir(parents=True, exist_ok=False)

    save_as_csv = bool(params.pop("save_as_csv", False))

    try:
        dlc.filterpredictions(
            str(cfg),
            [str(src)],
            save_as_csv=save_as_csv,
            **params,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "output": None, "error": f"{type(exc).__name__}: {exc}"}

    base = src.with_suffix("")
    produced_h5 = base.with_name(base.name + "_filtered.h5")
    relocated_h5 = out_dir / produced_h5.name

    if not produced_h5.exists():
        return {
            "status": "failed",
            "output": None,
            "error": f"DLC did not produce expected output {produced_h5}",
        }
    shutil.move(str(produced_h5), str(relocated_h5))

    if save_as_csv:
        produced_csv = base.with_name(base.name + "_filtered.csv")
        if produced_csv.exists():
            shutil.move(str(produced_csv), str(out_dir / produced_csv.name))

    return {"status": "success", "output": relocated_h5, "error": None}
