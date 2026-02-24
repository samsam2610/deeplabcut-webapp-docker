"""
Celery Worker Tasks — Anipose & DeepLabCut (placeholder) Processing
Runs inside the GPU-enabled worker container.
"""

import os
import shutil
import subprocess
import traceback
from pathlib import Path

from celery import Celery, Task

# ── Celery Setup ──────────────────────────────────────────────────
celery = Celery(
    "tasks",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)
celery.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86400,
    worker_prefetch_multiplier=1,      # one heavy task at a time
    task_acks_late=True,               # re-deliver on crash
    task_time_limit=7200,              # hard kill after 2 h
    task_soft_time_limit=6900,         # soft warning at 1 h 55 min
)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))


# ── Helpers ───────────────────────────────────────────────────────
def _run_cmd(cmd: list[str], cwd: str, task: Task, stage: str, progress: int):
    """
    Run a shell command, stream its output, and push progress updates
    back to the Celery result backend.
    """
    task.update_state(
        state="PROGRESS",
        meta={"progress": progress, "stage": stage, "log": f"Running: {' '.join(cmd)}"},
    )

    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    combined_output = (result.stdout or "") + "\n" + (result.stderr or "")

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(cmd)})\n"
            f"Exit code: {result.returncode}\n"
            f"Output:\n{combined_output[-2000:]}"  # last 2 kB
        )

    task.update_state(
        state="PROGRESS",
        meta={
            "progress": progress + 10,
            "stage": f"{stage} — done",
            "log": combined_output[-2000:],
        },
    )

    return combined_output


# ── Anipose Pipeline ─────────────────────────────────────────────
def _run_anipose(project_dir: str, task: Task):
    """
    Execute the three-stage Anipose CLI pipeline:
      1. anipose analyze   — run DLC inference on each camera view
      2. anipose filter    — apply median / Viterbi filtering
      3. anipose triangulate — multi-camera 3-D triangulation
    """
    stages = [
        (["anipose", "analyze"],      "Analyzing poses",         20),
        (["anipose", "filter"],       "Filtering predictions",   50),
        (["anipose", "triangulate"],  "Triangulating 3-D poses", 75),
    ]

    logs: list[str] = []
    for cmd, stage_label, progress_pct in stages:
        output = _run_cmd(cmd, cwd=project_dir, task=task, stage=stage_label, progress=progress_pct)
        logs.append(output)

    return "\n---\n".join(logs)


# ── DeepLabCut Placeholder ────────────────────────────────────────
def _run_deeplabcut(project_dir: str, task: Task):
    """
    Placeholder for a future DeepLabCut-only workflow.
    When implemented this would typically:
      1. Create / load a DLC project
      2. Run deeplabcut.analyze_videos()
      3. Optionally run deeplabcut.filterpredictions()
      4. Run deeplabcut.create_labeled_video()
    """
    task.update_state(
        state="PROGRESS",
        meta={
            "progress": 10,
            "stage": "DeepLabCut — not yet implemented",
            "log": "DLC pipeline is a placeholder. Add your logic here.",
        },
    )

    # ── TODO: Implement DLC workflow ──────────────────────────────
    # import deeplabcut
    # config_path = os.path.join(project_dir, "config.yaml")
    # video_dir   = os.path.join(project_dir, "videos-raw")
    #
    # deeplabcut.analyze_videos(config_path, [video_dir], ...)
    # deeplabcut.filterpredictions(config_path, [video_dir], ...)
    # deeplabcut.create_labeled_video(config_path, [video_dir], ...)

    return "DeepLabCut processing is not yet implemented."


# ── Session Processing Tasks ──────────────────────────────────────
def _ensure_config(config_path: str, session_path: str) -> None:
    """Copy session config.toml into session_path if it isn't already there."""
    dest = os.path.join(session_path, "config.toml")
    if os.path.abspath(config_path) != os.path.abspath(dest):
        shutil.copy2(config_path, dest)


def _session_task_wrapper(self, session_path: str, config_path: str,
                           cmd: list[str], stage: str, operation: str) -> dict:
    """Shared body for all four single-step session tasks."""
    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")
    _ensure_config(config_path, session_path)
    try:
        log = _run_cmd(cmd, cwd=session_path, task=self, stage=stage, progress=20)
        return {"status": "complete", "operation": operation, "log": log[-3000:]}
    except Exception as exc:
        self.update_state(
            state="FAILURE",
            meta={"progress": 0, "stage": "Error", "log": traceback.format_exc()[-3000:]},
        )
        raise exc


@celery.task(bind=True, name="tasks.process_calibrate")
def process_calibrate(self, session_path: str, config_path: str):
    """Run `anipose calibrate` — camera calibration from checkerboard videos."""
    return _session_task_wrapper(
        self, session_path, config_path,
        cmd=["anipose", "calibrate"],
        stage="Calibrating cameras",
        operation="calibrate",
    )


@celery.task(bind=True, name="tasks.process_filter_2d")
def process_filter_2d(self, session_path: str, config_path: str):
    """Run `anipose filter` — temporal filtering of 2-D pose predictions."""
    return _session_task_wrapper(
        self, session_path, config_path,
        cmd=["anipose", "filter"],
        stage="Filtering 2-D predictions",
        operation="filter_2d",
    )


@celery.task(bind=True, name="tasks.process_triangulate")
def process_triangulate(self, session_path: str, config_path: str):
    """Run `anipose triangulate` — multi-camera 3-D triangulation."""
    return _session_task_wrapper(
        self, session_path, config_path,
        cmd=["anipose", "triangulate"],
        stage="Triangulating 3-D poses",
        operation="triangulate",
    )


@celery.task(bind=True, name="tasks.process_filter_3d")
def process_filter_3d(self, session_path: str, config_path: str):
    """Run `anipose filter-3d` — smoothing of triangulated 3-D trajectories."""
    return _session_task_wrapper(
        self, session_path, config_path,
        cmd=["anipose", "filter-3d"],
        stage="Filtering 3-D trajectories",
        operation="filter_3d",
    )


# ── Session Init Task ─────────────────────────────────────────────
@celery.task(bind=True, name="tasks.init_anipose_session")
def init_anipose_session(self, config_path: str):
    """
    Initialize an Anipose IPython-like session on the worker:
      1. Import Anipose (verifies the library loads correctly)
      2. Confirm the config file is accessible on the shared volume
    Returns version info and config path on success.
    """
    self.update_state(
        state="PROGRESS",
        meta={"stage": "Loading Anipose…", "log": ""},
    )

    try:
        import anipose  # type: ignore[import]  # installed only in worker container
        version = getattr(anipose, "__version__", "unknown")

        self.update_state(
            state="PROGRESS",
            meta={
                "stage": f"Anipose {version} imported",
                "log": (
                    f"import anipose  # v{version}\n"
                    f"config = '{config_path}'\n"
                ),
            },
        )

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Config not found on shared volume: {config_path}")

        return {
            "status": "ready",
            "anipose_version": version,
            "config_path": config_path,
        }

    except Exception as exc:
        self.update_state(
            state="FAILURE",
            meta={"stage": "Session init failed", "log": traceback.format_exc()},
        )
        raise exc


# ── Main Celery Task ──────────────────────────────────────────────
@celery.task(bind=True, name="tasks.run_processing")
def run_processing(self, project_id: str, task_type: str = "anipose"):
    """
    Dispatcher task.  Routes to the correct pipeline based on `task_type`.
    """
    project_dir = str(DATA_DIR / project_id)

    if not os.path.isdir(project_dir):
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    self.update_state(
        state="PROGRESS",
        meta={"progress": 5, "stage": "Validating project structure…", "log": ""},
    )

    try:
        # ── Route to the right pipeline ───────────────────────────
        if task_type == "anipose":
            log_output = _run_anipose(project_dir, task=self)

        elif task_type == "deeplabcut":
            log_output = _run_deeplabcut(project_dir, task=self)

        else:
            raise ValueError(f"Unknown task_type: '{task_type}'. Expected 'anipose' or 'deeplabcut'.")

        # ── Success ───────────────────────────────────────────────
        return {
            "project_id": project_id,
            "task_type": task_type,
            "status": "complete",
            "log": log_output[-3000:],
        }

    except Exception as exc:
        self.update_state(
            state="FAILURE",
            meta={
                "progress": 0,
                "stage": "Error",
                "log": traceback.format_exc()[-3000:],
            },
        )
        raise exc
