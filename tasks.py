"""
Celery Worker Tasks — Anipose & DeepLabCut (placeholder) Processing
Runs inside the GPU-enabled worker container.
"""

import os
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


# ── Session Init Task ─────────────────────────────────────────────
@celery.task(bind=True, name="tasks.init_session")
def init_session(self, config_path: str):
    """
    Initialize a DLC IPython-like session on the worker:
      1. Import DeepLabCut (verifies the library loads correctly)
      2. Confirm the config file is accessible on the shared volume
    Returns version info and config path on success.
    """
    self.update_state(
        state="PROGRESS",
        meta={"stage": "Loading DeepLabCut…", "log": ""},
    )

    try:
        import deeplabcut  # type: ignore[import]  # installed only in worker container
        version = getattr(deeplabcut, "__version__", "unknown")

        self.update_state(
            state="PROGRESS",
            meta={
                "stage": f"DeepLabCut {version} imported",
                "log": (
                    f"import deeplabcut  # v{version}\n"
                    f"config = '{config_path}'\n"
                ),
            },
        )

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Config not found on shared volume: {config_path}")

        return {
            "status": "ready",
            "dlc_version": version,
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
