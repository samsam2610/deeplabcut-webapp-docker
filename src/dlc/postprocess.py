"""Post-process predictions blueprint.

Routes are added in subsequent tasks. This module also exposes pure helpers
(make_run_subfolder, write_sidecar, scan_inputs) used by both the routes and
the Celery task.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from flask import Blueprint, jsonify

bp = Blueprint("dlc_postprocess", __name__, url_prefix="/dlc/postprocess")

# Recognised analyzed-prediction filename patterns. Lowercase compare on stem.
_ANALYZED_PATTERNS = ("resnet", "mobilenet", "efficientnet", "dlcrnetms5", "hrnet")


def _now_stamp() -> str:
    """UTC timestamp formatted YYYYMMDD-HHMMSS — exposed for monkeypatching."""
    return _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def make_run_subfolder(input_parent: str | Path, tool_tag: str) -> Path:
    """Create <input_parent>/postproc/<timestamp>_<tool_tag>/ and return it.

    Raises FileExistsError if the subfolder already exists (we never overwrite).
    """
    parent = Path(input_parent) / "postproc" / f"{_now_stamp()}_{tool_tag}"
    parent.mkdir(parents=True, exist_ok=False)
    return parent


def write_sidecar(run_dir: str | Path, payload: dict) -> Path:
    """Write run.json into the run subfolder."""
    p = Path(run_dir) / "run.json"
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p


def scan_inputs(path: str | Path, mode: str) -> list[Path]:
    """Find analyzable .h5/.csv files under `path`.

    mode == "file":   path itself must be an analyzable file.
    mode == "folder": recursive search; existing postproc/ trees are skipped.
    """
    p = Path(path)
    if mode == "file":
        if not p.is_file():
            return []
        if not _looks_analyzed(p):
            return []
        return [p]
    if mode == "folder":
        if not p.is_dir():
            return []
        results: list[Path] = []
        for child in p.rglob("*"):
            if not child.is_file():
                continue
            if "postproc" in child.relative_to(p).parts:
                continue
            if _looks_analyzed(child):
                results.append(child)
        return sorted(results)
    raise ValueError(f"unknown mode: {mode!r}")


def _looks_analyzed(path: Path) -> bool:
    suf = path.suffix.lower()
    if suf not in {".h5", ".csv"}:
        return False
    name = path.name.lower()
    if "_filtered" in name:
        return False  # already a derived file
    return any(p in name for p in _ANALYZED_PATTERNS)


@bp.route("/recent", methods=["GET"])
def recent():
    """Return recent post-process runs for the active project (stub for now)."""
    return jsonify({"runs": []})
