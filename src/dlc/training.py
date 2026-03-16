"""
DLC Training Blueprint.

Routes:
  POST /dlc/project/create-training-dataset
  POST /dlc/project/add-datasets-to-video-list
  GET /dlc/project/snapshots
  POST /dlc/project/train-network
  POST /dlc/project/train-network/stop
"""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, session as flask_session
from celery.result import AsyncResult
import dlc_ctx as _ctx
from dlc.utils import (
    _engine_info, _get_engine_queue,
    _TF_ENGINE_ALIASES,
)

bp = Blueprint("dlc_training", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


@bp.route("/dlc/project/create-training-dataset", methods=["POST"])
def dlc_create_training_dataset():
    """Dispatch a Celery task to run deeplabcut.create_training_dataset()."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    body = request.get_json(force=True) or {}
    try:
        num_shuffles = int(body.get("num_shuffles", 1))
    except (TypeError, ValueError):
        num_shuffles = 1
    if num_shuffles < 1:
        num_shuffles = 1
    freeze_split = bool(body.get("freeze_split", True))

    task = _ctx.celery().send_task(
        "tasks.dlc_create_training_dataset",
        kwargs={"config_path": config_path, "num_shuffles": num_shuffles, "freeze_split": freeze_split},
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "create_training_dataset"}), 202


@bp.route("/dlc/project/add-datasets-to-video-list", methods=["POST"])
def dlc_add_datasets_to_video_list():
    """Call deeplabcut.adddatasetstovideolistandviceversa() on the active project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    _pd = json.loads(raw)
    config_path = _pd.get("config_path", "")
    engine      = _pd.get("engine", "pytorch")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    task = _ctx.celery().send_task(
        "tasks.dlc_add_datasets_to_video_list",
        kwargs={"config_path": config_path},
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "status": "dispatched"}), 202


@bp.route("/dlc/project/snapshots", methods=["GET"])
def dlc_project_snapshots():
    """
    List available model snapshots in the active DLC project.
    Returns engine-appropriate snapshot files from the models folder.
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    if not project_path.is_dir():
        return jsonify({"error": "Project directory not found."}), 404

    engine = project_data.get("engine", "pytorch")
    models_folder, _, _ = _engine_info(engine)

    import re as _re

    # Optional shuffle filter — limits snapshots to the given shuffle number so
    # returned indices match DLC's per-shuffle snapshot_index parameter.
    shuffle_param = request.args.get("shuffle", "").strip()
    shuffle_filter = int(shuffle_param) if shuffle_param.isdigit() else None

    # Find snapshot files: *.pt for pytorch, *.index for tensorflow
    snap_ext = "*.index" if engine in _TF_ENGINE_ALIASES else "*.pt"
    snap_pattern = f"{models_folder}/**/train/{snap_ext}"

    models_root = project_path / models_folder

    def _parse_folder_iter(p):
        """Extract iteration number from the iteration-N folder in the path."""
        try:
            folder = p.relative_to(models_root).parts[0]
            m = _re.search(r'iteration[-_](\d+)', folder, _re.IGNORECASE)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def _parse_shuffle(p):
        """Extract shuffle number from the train-folder name (e.g. …shuffle3/train)."""
        try:
            # train-folder is two levels deep under models_root: iteration-N / <name> / train
            train_folder = p.relative_to(models_root).parts[1]
            m = _re.search(r'shuffle(\d+)', train_folder, _re.IGNORECASE)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    raw_snaps = []
    for snap in project_path.glob(snap_pattern):
        if shuffle_filter is not None and _parse_shuffle(snap) != shuffle_filter:
            continue
        raw_snaps.append({
            "stem":         snap.stem,
            "folder_iter":  _parse_folder_iter(snap),
            "rel_path":     str(snap.relative_to(project_path)),
            "mtime":        snap.stat().st_mtime,
        })

    # Sort: by folder iteration ascending (None last), then mtime
    raw_snaps.sort(key=lambda s: (s["folder_iter"] is None, s["folder_iter"] or 0, s["mtime"]))

    # index here is positional within the sorted list for this shuffle —
    # this matches what DLC's snapshot_index parameter expects.
    snapshots = [
        {
            "label":      s["stem"],
            "iteration":  s["folder_iter"],
            "index":      i,
            "rel_path":   s["rel_path"],
        }
        for i, s in enumerate(raw_snaps)
    ]

    latest = raw_snaps[-1] if raw_snaps else None

    return jsonify({
        "snapshots":        snapshots,
        "engine":           engine,
        "latest_label":     latest["stem"]        if latest else None,
        "latest_iteration": latest["folder_iter"] if latest else None,
    })


@bp.route("/dlc/project/train-network", methods=["POST"])
def dlc_train_network():
    """
    Dispatch a Celery task to run deeplabcut.train_network().
    Body (JSON) fields:
      engine        : "pytorch" | "tensorflow"  (informational, also forwarded)
      shuffle       : int  (default 1)
      trainingsetindex : int  (default 0)
      gputouse      : int | null  (common: GPU index)
      --- TensorFlow only ---
      maxiters      : int
      displayiters  : int
      saveiters     : int
      --- PyTorch only ---
      epochs        : int | null
      save_epochs   : int | null
      batch_size    : int | null
      device        : str | null   e.g. "cuda", "cpu", "mps"
      detector_epochs      : int | null
      detector_batch_size  : int | null
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "config.yaml not found in project."}), 404

    body   = request.get_json(force=True) or {}
    engine = (body.get("engine") or "pytorch").lower()

    def _int_or_none(key):
        v = body.get(key)
        if v is None or str(v).strip() == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _str_or_none(key):
        v = body.get(key)
        if v is None or str(v).strip() == "":
            return None
        return str(v).strip()

    # Common params
    params = {
        "shuffle":          _int_or_none("shuffle") or 1,
        "trainingsetindex": _int_or_none("trainingsetindex") or 0,
    }
    gputouse = _int_or_none("gputouse")
    if gputouse is not None:
        params["gputouse"] = gputouse

    if engine == "tensorflow":
        for key in ("maxiters", "displayiters", "saveiters"):
            v = _int_or_none(key)
            if v is not None:
                params[key] = v
    else:
        for key in ("epochs", "save_epochs", "batch_size", "detector_epochs", "detector_batch_size"):
            v = _int_or_none(key)
            if v is not None:
                params[key] = v
        device = _str_or_none("device")
        if device:
            params["device"] = device

    task = _ctx.celery().send_task(
        "tasks.dlc_train_network",
        kwargs={"config_path": config_path, "engine": engine, "params": params},
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "train_network", "engine": engine}), 202


@bp.route("/dlc/project/train-network/stop", methods=["POST"])
def dlc_train_network_stop():
    """
    Request a stop of a running train_network task.
    Sets a Redis flag that the Celery worker polls; the worker kills its own
    child process (SIGTERM→SIGKILL) from inside the worker container.
    Also revokes the Celery task so it won't be re-picked-up on restart.
    Body (JSON): { "task_id": "<celery task id>" }
    """
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id is required."}), 400

    # Tell the worker's background thread to killpg the entire process tree
    _ctx.redis_client().setex("dlc_train_stop:" + task_id, 120, "1")

    # Revoke the task in Celery (prevents re-pickup on worker restart)
    _ctx.celery().control.revoke(task_id, terminate=False)

    # Purge the Celery result so the task can't be seen as "still running"
    try:
        AsyncResult(task_id, app=_ctx.celery()).forget()
    except Exception:
        pass

    # Remove the job from the sorted set so it disappears from the monitor
    _ctx.redis_client().zrem("dlc_train_jobs", task_id)
    # Mark job as stopped in its hash (it will expire on its own TTL)
    _ctx.redis_client().hset("dlc_train_job:" + task_id, "status", "stopped")
    _ctx.redis_client().expire("dlc_train_job:" + task_id, 3600)

    return jsonify({"status": "stop_requested", "task_id": task_id}), 200
