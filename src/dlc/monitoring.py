"""
DLC Monitoring + Machine Labeling Blueprint.

Routes:
  POST /dlc/project/machine-label-frames
  POST /dlc/project/machine-label-frames/stop
  GET /dlc/project/machine-label-raw
  POST /dlc/project/machine-label-reapply
  GET /dlc/training/jobs
  POST /dlc/training/jobs/clear
  GET /dlc/gpu/status
"""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, session as flask_session
from celery.result import AsyncResult
from werkzeug.utils import secure_filename
from . import ctx as _ctx
from dlc.utils import _get_engine_queue, _dlc_project_security_check

bp = Blueprint("dlc_monitoring", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _sec_check(p: Path) -> bool:
    return _dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())


def _natural_keys(text: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _parse_dlc_yaml_local(config_path: Path) -> dict:
    """Parse DLC config.yaml for scorer/bodyparts."""
    _yaml = _ctx.yaml_lib()
    text = config_path.read_text()
    if _yaml is not None:
        return _yaml.safe_load(text) or {}
    result = {}
    m = re.search(r'^scorer\s*:\s*(.+)$', text, re.MULTILINE)
    if m:
        result["scorer"] = m.group(1).strip().strip("\"'")
    m = re.search(r'^bodyparts\s*:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)', text, re.MULTILINE)
    if m:
        result["bodyparts"] = [
            item.strip().strip("\"'")
            for item in re.findall(r'^[ \t]*-[ \t]*(.+)$', m.group(1), re.MULTILINE)
        ]
    return result


def _get_dlc_project_and_config():
    """Return (project_data, config_dict, error_response) for the active DLC project."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, None, (jsonify({"error": "No active DLC project."}), 400)
    project_data = json.loads(raw)
    config_path  = Path(project_data.get("config_path", "") or "")
    if not config_path.is_file():
        return project_data, {}, None
    try:
        cfg = _parse_dlc_yaml_local(config_path)
    except Exception as exc:
        return project_data, {}, (jsonify({"error": f"Could not parse config.yaml: {exc}"}), 500)
    return project_data, cfg, None


@bp.route("/dlc/project/machine-label-frames", methods=["POST"])
def dlc_project_machine_label_frames():
    """
    Dispatch a Celery task to run model inference on a labeled-data frames folder
    and save predictions as CollectedData_<scorer>.csv.
    Body (JSON): { video_stem, shuffle, trainingsetindex, gputouse, snapshot_index }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    project_path = Path(project_data.get("project_path", ""))
    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400

    labeled_data_path = project_path / "labeled-data" / secure_filename(video_stem)
    if not labeled_data_path.is_dir():
        return jsonify({"error": f"Frames folder not found: {labeled_data_path}"}), 400

    def _int_or_none(key):
        v = body.get(key)
        try:
            return int(v) if v is not None and v != "" else None
        except (ValueError, TypeError):
            return None

    def _float_or(key, default):
        v = body.get(key)
        try:
            return float(v) if v is not None and v != "" else default
        except (ValueError, TypeError):
            return default

    params = {
        "shuffle":              _int_or_none("shuffle") or 1,
        "trainingsetindex":     _int_or_none("trainingsetindex") if _int_or_none("trainingsetindex") is not None else 0,
        "gputouse":             _int_or_none("gputouse"),
        "save_as_csv":          True,
        "snapshot_path":        (body.get("snapshot_path") or "").strip() or None,
        "likelihood_threshold": _float_or("likelihood_threshold", 0.9),
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_machine_label_frames",
        kwargs={
            "config_path":       config_path,
            "labeled_data_path": str(labeled_data_path),
            "params":            params,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "machine_label_frames"}), 202


@bp.route("/dlc/project/machine-label-frames/stop", methods=["POST"])
def dlc_project_machine_label_frames_stop():
    """Stop a running dlc_machine_label_frames task."""
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id is required."}), 400

    _ctx.redis_client().setex("dlc_ml_stop:" + task_id, 120, "1")
    _ctx.celery().control.revoke(task_id, terminate=False)
    try:
        AsyncResult(task_id, app=_ctx.celery()).forget()
    except Exception:
        pass
    return jsonify({"status": "stop_requested", "task_id": task_id}), 200


@bp.route("/dlc/project/machine-label-raw")
def dlc_machine_label_raw_exists():
    """Return whether _machine_predictions_raw.h5 exists for a labeled-data stem."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))

    video_stem = request.args.get("video_stem", "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir   = project_path / "labeled-data" / secure_filename(video_stem)
    raw_h5     = stem_dir / "_machine_predictions_raw.h5"
    meta_file  = stem_dir / "_ml_frames.json"
    return jsonify({
        "exists":    raw_h5.is_file(),
        "has_meta":  meta_file.is_file(),
    })


@bp.route("/dlc/project/machine-label-reapply", methods=["POST"])
def dlc_machine_label_reapply():
    """
    Dispatch a Celery task to re-apply a new likelihood threshold to the saved
    raw machine predictions without re-running the model.
    Body: { video_stem, likelihood_threshold }
    Returns: { task_id, operation }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data, cfg, err = _get_dlc_project_and_config()
    if err:
        return err

    project_path = Path(project_data.get("project_path", ""))
    scorer       = cfg.get("scorer", "User")
    bodyparts    = list(cfg.get("bodyparts", []))

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    threshold  = float(body.get("likelihood_threshold") or 0.9)

    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir = project_path / "labeled-data" / secure_filename(video_stem)
    raw_h5   = stem_dir / "_machine_predictions_raw.h5"
    if not raw_h5.is_file():
        return jsonify({"error": "No saved raw predictions found for this stem."}), 404

    engine = project_data.get("engine", "pytorch")
    task = _ctx.celery().send_task(
        "tasks.dlc_machine_label_reapply",
        kwargs={
            "stem_dir":   str(stem_dir),
            "video_stem": video_stem,
            "scorer":     scorer,
            "bodyparts":  bodyparts,
            "threshold":  threshold,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "machine_label_reapply"}), 202


@bp.route("/dlc/training/jobs")
def dlc_training_jobs():
    """Return all training and analyze jobs (running + recent) stored in Redis.

    Jobs marked 'running' are cross-checked against Celery. If the Celery task
    is no longer active (e.g. after a container restart), the Redis record is
    updated to 'dead' so the UI unblocks automatically.
    """
    _LIVE_CELERY_STATES = {"PENDING", "RECEIVED", "STARTED", "RETRY"}

    def _reconcile(redis_key: str, jid: str) -> dict | None:
        job = _ctx.redis_client().hgetall(redis_key)
        if not job:
            return None
        if job.get("status") == "running":
            celery_state = AsyncResult(jid, app=_ctx.celery()).state
            if celery_state not in _LIVE_CELERY_STATES:
                _ctx.redis_client().hset(redis_key, "status", "dead")
                job["status"] = "dead"
        return job

    jobs = []
    for jid in _ctx.redis_client().zrevrange("dlc_train_jobs", 0, 49):
        job = _reconcile("dlc_train_job:" + jid, jid)
        if job:
            job.setdefault("operation", "train")
            jobs.append(job)
    for jid in _ctx.redis_client().zrevrange("dlc_analyze_jobs", 0, 49):
        job = _reconcile("dlc_analyze_job:" + jid, jid)
        if job:
            jobs.append(job)

    # Sort combined list by started_at descending
    jobs.sort(key=lambda j: float(j.get("started_at", 0)), reverse=True)
    return jsonify({"jobs": jobs[:50]})


@bp.route("/dlc/training/jobs/clear", methods=["POST"])
def dlc_training_jobs_clear():
    """Delete all finished (non-running) train and analyze jobs from the monitor list."""
    removed = 0
    for jid in _ctx.redis_client().zrevrange("dlc_train_jobs", 0, 199):
        job = _ctx.redis_client().hgetall("dlc_train_job:" + jid)
        if job.get("status") != "running":
            _ctx.redis_client().zrem("dlc_train_jobs", jid)
            _ctx.redis_client().delete("dlc_train_job:" + jid)
            removed += 1
    for jid in _ctx.redis_client().zrevrange("dlc_analyze_jobs", 0, 199):
        job = _ctx.redis_client().hgetall("dlc_analyze_job:" + jid)
        if job.get("status") != "running":
            _ctx.redis_client().zrem("dlc_analyze_jobs", jid)
            _ctx.redis_client().delete("dlc_analyze_job:" + jid)
            removed += 1
    return jsonify({"removed": removed})


@bp.route("/dlc/project/tapnet-check")
def dlc_tapnet_check():
    """
    Scan a labeled-data folder and return which consecutive sequences have a
    labeled anchor frame (and are therefore eligible for TAPNet propagation).

    Query params: video_stem
    Returns: { sequences: [{frames, anchor, first_labeled, last_labeled}] }
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    project_path = Path(project_data.get("project_path", ""))
    video_stem   = request.args.get("video_stem", "").strip()
    if not video_stem:
        return jsonify({"error": "video_stem required."}), 400

    stem_dir = project_path / "labeled-data" / secure_filename(video_stem)
    if not stem_dir.is_dir():
        return jsonify({"error": f"Folder not found: {stem_dir}"}), 404

    try:
        from dlc_tapnet_tracker import (
            find_consecutive_sequences,
            load_dlc_labels,
            get_labeled_frame_names,
            check_anchor_frames,
        )

        pngs = sorted(stem_dir.glob("*.png"))
        frame_names = [p.name for p in pngs]
        sequences   = find_consecutive_sequences(frame_names)

        csv_candidates = sorted(stem_dir.glob("CollectedData_*.csv"))
        labeled: set[str] = set()
        if csv_candidates:
            df = load_dlc_labels(csv_candidates[0])
            labeled = get_labeled_frame_names(df)

        result = []
        for seq in sequences:
            info = check_anchor_frames(seq, labeled)
            result.append({
                "frame_count":   len(seq),
                "first_frame":   seq[0],
                "last_frame":    seq[-1],
                "first_labeled": info["first_labeled"],
                "last_labeled":  info["last_labeled"],
                "anchor":        info["anchor"],
                "propagatable":  info["anchor"] is not None,
            })

        return jsonify({
            "video_stem":       video_stem,
            "total_frames":     len(frame_names),
            "sequences":        result,
            "propagatable_count": sum(1 for r in result if r["propagatable"]),
        })

    except Exception as exc:
        import traceback
        return jsonify({"error": str(exc), "detail": traceback.format_exc()}), 500


@bp.route("/dlc/project/tapnet-propagate", methods=["POST"])
def dlc_tapnet_propagate():
    """
    Dispatch a TAPNet label-propagation Celery task.

    Body (JSON):
        video_stem              (str, required)
        tapnet_checkpoint_path  (str, required) — absolute path to TAPIR .npy
        anchor                  (str) "auto" | "first" | "last"  default "auto"
        gpu_index               (int) default 0 (RTX 5090)
        overwrite               (bool) default false
    """
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return jsonify({"error": "No active DLC project."}), 400

    project_data = json.loads(raw)
    config_path  = project_data.get("config_path", "")
    engine       = project_data.get("engine", "pytorch")
    project_path = Path(project_data.get("project_path", ""))

    if not config_path or not Path(config_path).is_file():
        return jsonify({"error": "No config.yaml in active project."}), 400

    body       = request.get_json(force=True) or {}
    video_stem = (body.get("video_stem") or "").strip()
    ckpt_path  = (body.get("tapnet_checkpoint_path") or "").strip()

    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400
    if not ckpt_path:
        return jsonify({"error": "tapnet_checkpoint_path is required."}), 400

    labeled_data_path = project_path / "labeled-data" / secure_filename(video_stem)
    if not labeled_data_path.is_dir():
        return jsonify({"error": f"Frames folder not found: {labeled_data_path}"}), 400

    params = {
        "anchor":    (body.get("anchor") or "auto").strip(),
        "gpu_index": int(body.get("gpu_index") or 0),
        "overwrite": bool(body.get("overwrite", False)),
    }

    task = _ctx.celery().send_task(
        "tasks.dlc_tapnet_propagate",
        kwargs={
            "config_path":             config_path,
            "labeled_data_path":       str(labeled_data_path),
            "tapnet_checkpoint_path":  ckpt_path,
            "params":                  params,
        },
        queue=_get_engine_queue(engine),
    )
    return jsonify({"task_id": task.id, "operation": "tapnet_propagate"}), 202


@bp.route("/dlc/project/tapnet-propagate/stop", methods=["POST"])
def dlc_tapnet_propagate_stop():
    """Stop a running tapnet_propagate task."""
    body    = request.get_json(force=True) or {}
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"error": "task_id is required."}), 400
    _ctx.celery().control.revoke(task_id, terminate=True)
    try:
        from celery.result import AsyncResult
        AsyncResult(task_id, app=_ctx.celery()).forget()
    except Exception:
        pass
    return jsonify({"status": "stop_requested", "task_id": task_id}), 200


@bp.route("/dlc/gpu/status")
def dlc_gpu_status():
    """
    Return GPU stats. Prefers the Redis cache written by the Celery worker
    during training; when no cache is present, dispatches a lightweight probe
    task to the GPU-enabled worker and waits up to 5 s for the result.
    """
    import time as _time

    raw = _ctx.redis_client().get("dlc_gpu_stats")
    ts  = _ctx.redis_client().get("dlc_gpu_stats_ts")

    # No cache — ask the GPU worker to run nvidia-smi for us
    if not raw:
        try:
            task = _ctx.celery().send_task("tasks.dlc_probe_gpu_stats")
            csv  = task.get(timeout=5, propagate=False)
            if csv:
                raw = csv
                ts  = str(_time.time())
        except Exception:
            pass

    if not raw:
        return jsonify({"gpus": [], "available": False})

    def _parse_csv(text):
        gpus = []
        for line in text.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                try:
                    gpus.append({
                        "index":        int(parts[0]),
                        "name":         parts[1],
                        "utilization":  int(parts[2]),
                        "memory_used":  int(parts[3]),
                        "memory_total": int(parts[4]),
                        "temperature":  int(parts[5]),
                    })
                except (ValueError, IndexError):
                    pass
        return gpus

    gpus = _parse_csv(raw)
    if not gpus:
        return jsonify({"gpus": [], "available": False})

    age = round(_time.time() - float(ts), 1) if ts else None
    return jsonify({"gpus": gpus, "available": True, "age_s": age})
