"""Post-process predictions blueprint.

Routes are added in subsequent tasks. This module also exposes pure helpers
(make_run_subfolder, write_sidecar, scan_inputs) used by both the routes and
the Celery task.
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, session as flask_session

from . import ctx as _ctx

bp = Blueprint("dlc_postprocess", __name__, url_prefix="/dlc/postprocess")


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _active_project_data() -> dict | None:
    """Read active DLC project metadata from redis, or return None."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None

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


def _path_is_allowed(path) -> bool:
    """Hook for the user-data root allowlist.

    Reuses ``dlc.utils._dlc_project_security_check`` against the DATA_DIR and
    USER_DATA_DIR known to ``dlc.ctx``. Tests monkeypatch this. Production must
    have ``dlc.ctx`` populated by ``app.py`` before requests reach here.
    """
    try:
        from dlc.utils import _dlc_project_security_check
        from dlc import ctx
        data_dir = ctx.data_dir()
        user_data_dir = ctx.user_data_dir()
    except ImportError:
        return True  # fallback: tests monkeypatch; production must wire
    if data_dir is None or user_data_dir is None:
        return True
    try:
        return _dlc_project_security_check(Path(path), data_dir, user_data_dir)
    except Exception:
        return False


@bp.route("/scan", methods=["POST"])
def scan():
    body = request.get_json(silent=True) or {}
    raw = body.get("path")
    mode = body.get("mode")
    if not raw or mode not in {"file", "folder"}:
        return jsonify({"error": "path and mode (file|folder) are required"}), 400
    if not _path_is_allowed(raw):
        return jsonify({"error": "path is not under an allowed root"}), 400

    files = scan_inputs(raw, mode)
    return jsonify({"files": [str(f) for f in files]})


_VALID_ACTIONS = {
    "deeplabcut": {"filterpredictions"},
    "refineDLC": {"pipeline", "likelihood_filter", "outlier_removal",
                  "interpolation", "smoothing"},
}


@bp.route("/run", methods=["POST"])
def run():
    body = request.get_json(silent=True) or {}
    tool = body.get("tool")
    action = body.get("action")
    params = body.get("params") or {}
    inputs = body.get("inputs") or []

    if tool not in _VALID_ACTIONS or action not in _VALID_ACTIONS.get(tool, set()):
        return jsonify({"error": "unsupported tool/action"}), 400
    if not isinstance(inputs, list) or not inputs:
        return jsonify({"error": "inputs must be a non-empty list"}), 400
    for p in inputs:
        if not _path_is_allowed(p):
            return jsonify({"error": f"path not allowed: {p}"}), 400

    # config_path is accepted for backward compat but not required. The
    # deeplabcut/filterpredictions wrapper is now a self-contained scipy
    # medfilt implementation (mirrors DLC's algorithm) and does not need a
    # project config. refineDLC tools don't need a config either.
    config_path = ""

    # Dispatch by name; do NOT `from dlc.tasks import …` because tasks.py
    # imports `deeplabcut` at module top, which is not installed in the Flask
    # container. The worker container is what executes the task.
    async_result = _ctx.celery().send_task(
        "tasks.dlc_postprocess_run",
        kwargs={
            "config_path": config_path, "tool": tool, "action": action,
            "params": params, "inputs": list(inputs),
        },
    )
    return jsonify({"task_id": async_result.id})


def _async_result(task_id: str):
    from celery_app import celery
    return celery.AsyncResult(task_id)


def _revoke(task_id: str) -> None:
    from celery_app import celery
    celery.control.revoke(task_id, terminate=True)


@bp.route("/status/<task_id>", methods=["GET"])
def status(task_id: str):
    ar = _async_result(task_id)
    info = ar.info if isinstance(ar.info, dict) else {}
    return jsonify({"state": ar.state, "progress": info})


@bp.route("/cancel/<task_id>", methods=["POST"])
def cancel(task_id: str):
    _revoke(task_id)
    return jsonify({"task_id": task_id, "cancelled": True})


@bp.route("/logs/<task_id>", methods=["GET"])
def logs(task_id: str):
    ar = _async_result(task_id)
    info = ar.info if isinstance(ar.info, dict) else {}
    return jsonify({"log": info.get("log", "")})


def _active_project_root():
    """Return the active DLC project root path (parent of config.yaml), or None.

    Reads the per-user redis key ``webapp:dlc_project:<uid>``. Tests monkeypatch
    this function.
    """
    project_data = _active_project_data()
    if not project_data:
        return None
    config_path = project_data.get("config_path", "")
    if not config_path:
        return None
    p = Path(config_path)
    return p.parent if p.is_file() else None


@bp.route("/recent", methods=["GET"])
def recent():
    root = _active_project_root()
    if root is None:
        return jsonify({"runs": []})
    runs = []
    for sidecar in Path(root).rglob("postproc/*/run.json"):
        try:
            payload = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        payload["_sidecar"] = str(sidecar)
        runs.append(payload)
    runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return jsonify({"runs": runs[:20]})
