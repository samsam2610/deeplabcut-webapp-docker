"""
Celery Worker Tasks — Anipose & DeepLabCut (placeholder) Processing
Runs inside the GPU-enabled worker container.
"""

import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path
import deeplabcut as dlc
from anipose_src.filter_2d_funcs import *
from anipose_src.filter_3d_funcs import *
from anipose_src.load_config_funcs import *
from anipose_src.preprocessing_funcs import *
from anipose_src.triangulate_funcs import *
from anipose_src.calibration_funcs import *

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
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


@celery.task(bind=True, name="tasks.process_calibrate")
def process_calibrate(self, session_path: str, config_path: str):
    """
    Camera calibration via process_session_calibrate().

    Pre-flight (one of two must be true):
      1. The calibration video folder contains videos from ≥2 distinct camera
         names as identified by [triangulation] cam_regex.
      2. A detections.pickle already exists in the calibration results folder
         (allows re-running optimisation without re-detecting board corners).

    Calls calibration_funcs.process_session_calibrate(config, session_path)
    rather than the anipose CLI so the worker can stream live progress.
    """
    import re as _re
    from glob import glob as _glob

    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")
    _ensure_config(config_path, session_path)

    # ── Load config ──────────────────────────────────────────────
    config_local = os.path.join(session_path, "config.toml")
    try:
        try:
            with open(config_local, "rb") as _f:
                config = load_config(config_local)
        except ImportError:
            config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")

    self.update_state(
        state="PROGRESS",
        meta={"progress": 5, "stage": "Checking calibration inputs…", "log": ""},
    )

    # ── Resolve calibration folders ──────────────────────────────
    pipeline_calib_videos  = config["pipeline"]["calibration_videos"]
    pipeline_calib_results = config["pipeline"]["calibration_results"]
    video_ext  = config.get("video_extension", "mkv")
    cam_regex  = config.get("triangulation", {}).get("cam_regex", "cam([0-9])")

    calib_vid_dir     = os.path.join(session_path, pipeline_calib_videos)
    calib_res_dir     = os.path.join(session_path, pipeline_calib_results)
    detections_pickle = os.path.join(calib_res_dir, "detections.pickle")

    # ── Condition 2: detections.pickle already present ───────────
    has_detections = os.path.isfile(detections_pickle)

    # ── Condition 1: ≥2 distinct camera names in video folder ────
    videos = sorted(_glob(os.path.join(calib_vid_dir, f"*.{video_ext}")))
    cam_names: set[str] = set()
    for vid in videos:
        m = _re.search(cam_regex, os.path.basename(vid))
        if m:
            cam_names.add(m.group(0))
    has_multi_cam = len(cam_names) >= 2

    if not has_detections and not has_multi_cam:
        log_msg = (
            f"Pre-flight check failed.\n"
            f"Calibration video dir : {calib_vid_dir}\n"
            f"Videos found (.{video_ext}): {[os.path.basename(v) for v in videos]}\n"
            f"Camera names via '{cam_regex}': {sorted(cam_names)}\n"
            f"detections.pickle     : {detections_pickle} — not found\n\n"
            f"Upload calibration videos from ≥2 cameras, "
            f"or supply a detections.pickle, then retry."
        )
        raise RuntimeError(log_msg)

    reason = (
        "detections.pickle found"
        if has_detections
        else f"{len(cam_names)} camera videos ({', '.join(sorted(cam_names))})"
    )
    self.update_state(
        state="PROGRESS",
        meta={
            "progress": 10,
            "stage": f"Calibrating — {reason}",
            "log": (
                f"Pre-flight OK: {reason}\n"
                f"Videos : {[os.path.basename(v) for v in videos]}\n"
                f"Calling process_session_calibrate(config, '{session_path}')…\n"
            ),
        },
    )

    # ── Run calibration ──────────────────────────────────────────
    try:
        process_session_calibrate(config, session_path)
        return {
            "status": "complete",
            "operation": "calibrate",
            "log": f"Calibration complete.\nsession_path: {session_path}",
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


@celery.task(bind=True, name="tasks.process_filter_2d")
def process_filter_2d(self, session_path: str, config_path: str):
    """Run `anipose filter` — temporal filtering of 2-D pose predictions."""
    self.update_state(
        state="PROGRESS",
        meta={"progress": 10, "stage": "Discovering folders…", "log": ""},
    )
    # ── Load config ──────────────────────────────────────────────
    config_local = os.path.join(session_path, "config.toml")
    try:
        try:
            with open(config_local, "rb") as _f:
                config = load_config(config_local)
        except ImportError:
            config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")
    
    # ── Filter 2D ──────────────────────────────────────────
    try:
        process_session_filter_2d(config, session_path)
        return {
            "status": "complete",
            "operation": "filter_2d",
            "log": f"2D filtering complete.\nsession_path: {session_path}",
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:]) 


@celery.task(bind=True, name="tasks.process_triangulate")
def process_triangulate(self, session_path: str, config_path: str):
    """
    Run multi-camera 3-D triangulation with live log streaming.

    All stdout/stderr (including tqdm bars and print() calls from
    triangulate_funcs) are captured into a rolling buffer.  A progress
    callback is passed into process_session_triangulate so that Celery
    state is updated once per trial, surfacing the accumulated log to
    the frontend poll at /status/<task_id>.
    """
    import io as _io
    import sys as _sys

    # ── Capture all print / tqdm output ──────────────────────────
    _log_buf  = _io.StringIO()
    _real_out = _sys.stdout
    _real_err = _sys.stderr
    _sys.stdout = _log_buf
    _sys.stderr = _log_buf

    def _push(stage: str, pct: int) -> None:
        """Push current log buffer + progress to Celery backend."""
        self.update_state(
            state="PROGRESS",
            meta={
                "progress": min(pct, 95),
                "stage":    stage,
                "log":      _log_buf.getvalue()[-5000:],
            },
        )

    try:
        _push("Loading config…", 5)

        # ── Load config ──────────────────────────────────────────
        config_local = os.path.join(session_path, "config.toml")
        try:
            config = load_config(config_local)
        except Exception as exc:
            raise RuntimeError(f"Failed to parse config.toml: {exc}")

        _push("Discovering trials…", 10)

        # ── Triangulate 3D (with per-trial progress callbacks) ───
        process_session_triangulate(config, session_path, progress_fn=_push)

        final_log = _log_buf.getvalue()[-5000:]
        return {
            "status":    "complete",
            "operation": "triangulate",
            "log":       final_log,
        }

    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])

    finally:
        # Always restore real stdout/stderr
        _sys.stdout = _real_out
        _sys.stderr = _real_err


@celery.task(bind=True, name="tasks.process_filter_3d")
def process_filter_3d(self, session_path: str, config_path: str):
    """Run `anipose filter-3d` — smoothing of triangulated 3-D trajectories."""
    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    self.update_state(
        state="PROGRESS",
        meta={"progress": 10, "stage": "Discovering folders…", "log": ""},
    )
    # ── Load config ──────────────────────────────────────────────
    config_local = os.path.join(session_path, "config.toml")
    try:
        config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")
    
    # ── Filter 3D ──────────────────────────────────────────
    try:
        process_session_filter_3d(config, session_path)
        return {
            "status": "complete",
            "operation": "filter_3d",
            "log": f"3D filtering complete.\nsession_path: {session_path}",
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])



# ── MediaPipe Preprocessing Tasks ────────────────────────────────
@celery.task(bind=True, name="tasks.process_organize_for_anipose")
def process_organize_for_anipose(self, session_path: str, scorer: str = "User"):
    """
    Organize DLC labeled-data folders (cam0_*, cam1_*, etc.) into
    the pose-2d/ structure expected by Anipose.
    Auto-discovers all immediate subdirectories of session_path as folder_list.
    """
    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    self.update_state(
        state="PROGRESS",
        meta={"progress": 10, "stage": "Discovering folders…", "log": ""},
    )
    # ── Load config ──────────────────────────────────────────────
    config_local = os.path.join(session_path, "config.toml")
    try:
        config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")

    try:
        folder_list = sorted([
            d.name for d in Path(session_path).iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

        self.update_state(
            state="PROGRESS",
            meta={
                "progress": 20,
                "stage": f"Organizing {len(folder_list)} folder(s)…",
                "log": f"Folders: {folder_list}\nScorer: {scorer}\n",
            },
        )

        organize_for_anipose(config, session_path, folder_list, scorer=scorer)

        return {
            "status":    "complete",
            "operation": "organize_for_anipose",
            "log":       (
                f"Organized {len(folder_list)} folder(s) into pose-2d/\n"
                f"Scorer: {scorer}\nFolders: {folder_list}"
            ),
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


@celery.task(bind=True, name="tasks.process_convert_mediapipe_csv_to_h5")
def process_convert_mediapipe_csv_to_h5(self, session_path: str, scorer: str = "User"):
    """
    Convert MediaPipe-exported CSV labeled data to DeepLabCut HDF5 format.
    Auto-discovers all immediate subdirectories of session_path as folder_list.
    """
    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    # ── Load config ──────────────────────────────────────────────
    config_local = os.path.join(session_path, "config.toml")
    try:
        config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")

    self.update_state(
        state="PROGRESS",
        meta={"progress": 10, "stage": "Discovering folders…", "log": ""},
    )

    try:
        folder_list = sorted([
            d.name for d in Path(session_path).iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

        self.update_state(
            state="PROGRESS",
            meta={
                "progress": 20,
                "stage": f"Converting {len(folder_list)} folder(s)…",
                "log": f"Folders: {folder_list}\nScorer: {scorer}\n",
            },
        )

        convert_mediapipe_csv_to_h5(config, session_path, folder_list, scorer=scorer)

        return {
            "status":    "complete",
            "operation": "convert_mediapipe_csv_to_h5",
            "log":       (
                f"Converted CSV→H5 for {len(folder_list)} folder(s)\n"
                f"Scorer: {scorer}\nFolders: {folder_list}"
            ),
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


@celery.task(bind=True, name="tasks.process_convert_3d_csv_to_mat")
def process_convert_3d_csv_to_mat(self, session_path: str,
                                   frame_w: int = 1920, frame_h: int = 1080):
    """
    Convert Anipose-filtered 3D CSVs (pose-3d-filtered/) to MediaPipe-format
    .mat arrays (landmarks variable, shape frames×landmarks×4).
    Requires frame_w and frame_h to restore 0-1 normalisation for x/y.
    """
    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    config_local = os.path.join(session_path, "config.toml")
    try:
        config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")

    self.update_state(
        state="PROGRESS",
        meta={
            "progress": 10,
            "stage": f"Converting 3D CSVs → .mat ({frame_w}×{frame_h})…",
            "log": f"frame_w={frame_w}  frame_h={frame_h}\n",
        },
    )

    try:
        convert_3d_csv_to_mat(config, session_path, frame_w=frame_w, frame_h=frame_h)
        return {
            "status":    "complete",
            "operation": "convert_3d_csv_to_mat",
            "log": (
                f"Converted filtered 3D CSVs → .mat\n"
                f"Frame size : {frame_w}×{frame_h}"
            ),
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


@celery.task(bind=True, name="tasks.process_convert_mediapipe_to_dlc_csv")
def process_convert_mediapipe_to_dlc_csv(self, session_path: str, scorer: str = "User",
                                          frame_w: int = 1920, frame_h: int = 1080):
    """
    Convert raw MediaPipe .mat arrays to DLC-format labeled-data CSVs.
    Requires frame_w and frame_h to de-normalize MediaPipe coordinates (0–1) → pixels.
    """
    if not os.path.isdir(session_path):
        raise FileNotFoundError(f"Session folder not found: {session_path}")

    config_local = os.path.join(session_path, "config.toml")
    try:
        config = load_config(config_local)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse config.toml: {exc}")

    self.update_state(
        state="PROGRESS",
        meta={
            "progress": 10,
            "stage": f"Converting .mat files ({frame_w}×{frame_h})…",
            "log": f"frame_w={frame_w}  frame_h={frame_h}  scorer={scorer}\n",
        },
    )

    try:
        convert_mediapipe_to_dlc_csv(
            config, session_path, frame_w=frame_w, frame_h=frame_h, scorer=scorer
        )
        return {
            "status":    "complete",
            "operation": "convert_mediapipe_to_dlc_csv",
            "log": (
                f"Converted .mat → DLC CSV\n"
                f"Frame size : {frame_w}×{frame_h}  |  Scorer: {scorer}"
            ),
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


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

        with open(config_path) as f:
            config_content = f.read()

        print(f"\n{'='*60}\nconfig.toml @ {config_path}\n{'='*60}\n{config_content}\n{'='*60}\n")

        self.update_state(
            state="PROGRESS",
            meta={
                "stage": f"Anipose {version} — config loaded",
                "log": (
                    f"import anipose  # v{version}\n"
                    f"config = '{config_path}'\n\n"
                    f"{'─'*40}\n"
                    f"{config_content}"
                ),
            },
        )

        return {
            "status": "ready",
            "anipose_version": version,
            "config_path": config_path,
            "config_content": config_content,
        }

    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


# ── DLC Create Training Dataset ───────────────────────────────────
@celery.task(bind=True, name="tasks.dlc_create_training_dataset")
def dlc_create_training_dataset(self, config_path: str, num_shuffles: int = 1, freeze_split: bool = True):
    """Run deeplabcut.create_training_dataset() for the given DLC config.yaml."""
    import io as _io
    import sys as _sys

    _log_buf  = _io.StringIO()
    _real_out = _sys.stdout
    _real_err = _sys.stderr
    _sys.stdout = _log_buf
    _sys.stderr = _log_buf

    try:
        self.update_state(
            state="PROGRESS",
            meta={"progress": 5, "stage": "Checking config…", "log": ""},
        )

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"DLC config.yaml not found: {config_path}")

        train_indices = None
        test_indices  = None

        if freeze_split:
            self.update_state(
                state="PROGRESS",
                meta={
                    "progress": 8,
                    "stage": "Computing frozen train/test split via mergeandsplit…",
                    "log": f"config_path: {config_path}\nnum_shuffles: {num_shuffles}\nfreeze_split: True\n",
                },
            )
            train_indices, test_indices = dlc.mergeandsplit(config_path, trainindex=0, uniform=True)

        self.update_state(
            state="PROGRESS",
            meta={
                "progress": 10,
                "stage": "Running deeplabcut.create_training_dataset…",
                "log": f"config_path: {config_path}\nnum_shuffles: {num_shuffles}\nfreeze_split: {freeze_split}\n",
            },
        )

        if freeze_split:
            for shuffle_idx in range(1, num_shuffles + 1):
                dlc.create_training_dataset(
                    config_path,
                    num_shuffles=1,
                    Shuffles=[shuffle_idx],
                    trainIndices=[train_indices],
                    testIndices=[test_indices],
                    userfeedback=False,
                )
        else:
            dlc.create_training_dataset(config_path, num_shuffles=num_shuffles, userfeedback=False)

        final_log = _log_buf.getvalue()[-5000:]
        return {
            "status":    "complete",
            "operation": "create_training_dataset",
            "log":       final_log or f"Training dataset created.\nconfig: {config_path}",
        }

    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])

    finally:
        _sys.stdout = _real_out
        _sys.stderr = _real_err


# ── DLC Add Datasets to Video List ───────────────────────────────
@celery.task(bind=True, name="tasks.dlc_add_datasets_to_video_list")
def dlc_add_datasets_to_video_list(self, config_path: str):
    """Run deeplabcut.adddatasetstovideolistandviceversa() for the given config."""
    try:
        dlc.adddatasetstovideolistandviceversa(config_path)
        return {"status": "complete", "operation": "add_datasets_to_video_list"}
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


# ── DLC Train Network ─────────────────────────────────────────────

# Redis key prefix used to share the training child-process PID between
# the Celery task and the Flask stop endpoint.
_TRAIN_PID_PREFIX = "dlc_train_pid:"

def _dlc_train_subprocess(config_path: str, kwargs: dict, log_path: str) -> None:
    """
    Runs inside a child process spawned by dlc_train_network.
    Becomes a process-group leader immediately so that killpg() from the
    parent will also reach all grandchild processes (PyTorch DataLoader
    workers, CUDA subprocesses, etc.), preventing GPU-context leaks.
    """
    import os as _os, sys, deeplabcut as _dlc

    # Become process-group leader — parent uses os.killpg(proc.pid, SIGKILL)
    _os.setpgrp()

    # Ensure CUDA device numbering matches nvidia-smi (PCI bus ID order).
    # Without this, CUDA may use FASTEST_FIRST ordering, causing a mismatch
    # between the GPU index shown in the UI and the one actually used.
    _os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"

    with open(log_path, "a", buffering=1) as _f:
        sys.stdout = _f
        sys.stderr = _f
        try:
            _dlc.train_network(config_path, **kwargs)
            _f.write("\n__TRAIN_COMPLETE__\n")
        except Exception:
            import traceback as _tb
            _f.write("\n__TRAIN_ERROR__\n")
            _f.write(_tb.format_exc())


@celery.task(bind=True, name="tasks.dlc_train_network", acks_late=False)
def dlc_train_network(self, config_path: str, engine: str = "pytorch", params: dict = None):
    """
    Run deeplabcut.train_network() in a child process so it can be killed
    cleanly without taking down the Celery worker.
    engine: 'pytorch' | 'tensorflow'
    params: engine-specific keyword arguments forwarded to train_network().
    acks_late=False overrides the global setting so that killing the worker
    does NOT re-queue the task on restart.
    """
    import multiprocessing as _mp
    import threading as _threading
    import tempfile
    import signal as _signal
    import redis as _redis_mod

    _redis = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )

    if params is None:
        params = {}

    task_id   = self.request.id
    pid_key   = _TRAIN_PID_PREFIX + task_id
    job_key   = "dlc_train_job:" + task_id
    jobs_zset = "dlc_train_jobs"

    def _job_set(status: str):
        _redis.hset(job_key, "status", status)
        if status in ("complete", "stopped", "failed"):
            _redis.expire(job_key, 3600)   # keep 1 h after finish

    # Register job so all users can see it
    _redis.hset(job_key, mapping={
        "task_id":     task_id,
        "engine":      engine,
        "project":     Path(config_path).parent.name,
        "config_path": config_path,
        "started_at":  str(time.time()),
        "status":      "running",
    })
    _redis.expire(job_key, 7200)
    _redis.zadd(jobs_zset, {task_id: time.time()})

    # Temporary file shared between child (writes) and parent (reads)
    _tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", prefix="dlc_train_", delete=False
    )
    log_path = _tmp.name
    _tmp.close()

    try:
        self.update_state(
            state="PROGRESS",
            meta={"progress": 5, "stage": "Checking config…", "log": ""},
        )

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"DLC config.yaml not found: {config_path}")

        _project_dir = Path(config_path).parent
        _td_dir      = _project_dir / "training-datasets"
        if not _td_dir.is_dir() or not any(_td_dir.iterdir()):
            raise RuntimeError(
                "No training dataset found in:\n"
                f"  {_td_dir}\n\n"
                "Please run 'Create Training Dataset' before training the network."
            )

        kwargs = {k: v for k, v in params.items() if v is not None}

        init_log = (
            f"config_path : {config_path}\n"
            f"engine      : {engine}\n"
            f"params      : {params}\n\n"
        )
        with open(log_path, "w") as _f:
            _f.write(init_log)

        self.update_state(
            state="PROGRESS",
            meta={"progress": 10, "stage": f"Starting training ({engine})…", "log": init_log},
        )

        # ── Spawn child process ──────────────────────────────────
        ctx  = _mp.get_context("spawn")
        proc = ctx.Process(
            target=_dlc_train_subprocess,
            args=(config_path, kwargs, log_path),
            daemon=False,
        )
        proc.start()

        # Advertise this task is killable (Flask reads this key; worker kills the proc)
        stop_key = "dlc_train_stop:" + task_id
        _redis.setex(pid_key, 7200, str(proc.pid))

        # ── Background thread: stream logs + watch for stop flag ─
        _stop_emit  = _threading.Event()
        _user_killed = [False]   # mutable so the closure can set it

        def _emit_loop():
            import signal as _sig
            _progress = 12
            while not _stop_emit.wait(3):
                # Check stop flag set by Flask stop endpoint
                if _redis.get(stop_key):
                    _user_killed[0] = True
                    # Kill the ENTIRE process group (training proc + all its
                    # children: PyTorch DataLoader workers, CUDA subprocesses).
                    # SIGKILL is immediate and cannot be ignored — this is
                    # intentional; leaving any child alive causes GPU hangs.
                    try:
                        os.killpg(proc.pid, _sig.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    # Purge all task state from Redis immediately
                    _redis.delete(stop_key, pid_key, job_key)
                    _redis.zrem("dlc_train_jobs", task_id)
                    break  # proc.join() will unblock shortly

                try:
                    with open(log_path) as _lf:
                        _log = _lf.read()[-8000:]
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "progress": min(_progress, 90),
                            "stage":    f"Training ({engine})…",
                            "log":      _log,
                        },
                    )
                    _progress = min(_progress + 1, 90)
                except Exception:
                    pass

                # Cache GPU stats from nvidia-smi so Flask can read them
                try:
                    _gr = subprocess.run(
                        ["nvidia-smi",
                         "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=3,
                    )
                    if _gr.returncode == 0:
                        _redis.setex("dlc_gpu_stats",    30, _gr.stdout)
                        _redis.setex("dlc_gpu_stats_ts", 30, str(time.time()))
                except Exception:
                    pass

        _emitter = _threading.Thread(target=_emit_loop, daemon=True)
        _emitter.start()

        proc.join()  # block until child exits naturally or is SIGKILLed

        _stop_emit.set()
        _emitter.join(timeout=5)
        # Clean up any leftover keys (_emit_loop already deletes them on user
        # stop, but delete idempotently here for the natural-exit path too)
        _redis.delete(pid_key, stop_key)

        # ── Check outcome ────────────────────────────────────────
        try:
            with open(log_path) as _lf:
                final_log = _lf.read()
        except OSError:
            final_log = ""

        if _user_killed[0]:
            # Keys already purged by _emit_loop; just raise the sentinel
            raise RuntimeError("__USER_STOPPED__")

        if proc.exitcode != 0:
            _job_set("failed")
            if proc.exitcode is not None and proc.exitcode < 0:
                raise RuntimeError(
                    f"Training process was killed (signal {-proc.exitcode}).\n\n"
                    + final_log[-3000:]
                )
            raise RuntimeError(final_log[-5000:])

        _job_set("complete")
        return {
            "status":    "complete",
            "operation": "train_network",
            "engine":    engine,
            "log":       final_log[-8000:] or f"Training complete.\nconfig: {config_path}",
        }

    except Exception:
        # Purge all Redis state so no stale "running" record remains
        _redis.delete(pid_key, stop_key)
        _redis.zrem("dlc_train_jobs", task_id)
        _job_set("failed")
        raise RuntimeError(traceback.format_exc()[-3000:])

    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass


# ── GPU stats probe ───────────────────────────────────────────────

@celery.task(name="tasks.dlc_probe_gpu_stats", ignore_result=False)
def dlc_probe_gpu_stats():
    """
    Run nvidia-smi on the GPU-enabled worker and cache the results in Redis.
    Called on-demand from Flask when no cached stats are available.
    """
    import redis as _redis_mod
    _redis = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            _redis.setex("dlc_gpu_stats",    60, result.stdout.strip())
            _redis.setex("dlc_gpu_stats_ts", 60, str(time.time()))
            return result.stdout.strip()
    except Exception:
        pass
    return ""


# ── DLC Analyze ───────────────────────────────────────────────────

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_ANALYZE_PID_PREFIX = "dlc_analyze_pid:"


def _dlc_analyze_subprocess(config_path: str, target_path: str, params: dict, log_path: str) -> None:
    """
    Runs inside a child process spawned by dlc_analyze.
    Detects whether the target is a video file, image file, or directory,
    then calls the appropriate DLC function(s).
    """
    import os as _os, sys, deeplabcut as _dlc
    from pathlib import Path as _Path

    _os.setpgrp()

    # Ensure CUDA device numbering matches nvidia-smi (PCI bus ID order).
    _os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"

    with open(log_path, "a", buffering=1) as _f:
        sys.stdout = _f
        sys.stderr = _f
        try:
            p = _Path(target_path)
            create_labeled = params.get("create_labeled", False)
            kw = {k: v for k, v in params.items()
                  if v is not None and k not in ("create_labeled",)}
            # kwargs shared by create_labeled_video
            label_kw = {k: kw[k] for k in ("shuffle", "trainingsetindex") if k in kw}

            if p.is_file():
                ext = p.suffix.lower()
                if ext in {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}:
                    _f.write(f"Analyzing video file: {p}\n\n")
                    _dlc.analyze_videos(config_path, [str(p)], **kw)
                    if create_labeled:
                        _f.write(f"\nCreating labeled video: {p}\n\n")
                        _dlc.create_labeled_video(config_path, [str(p)], **label_kw)
                elif ext in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}:
                    _f.write(f"Analyzing image directory (selected frame): {p.parent}\n\n")
                    _dlc.analyze_time_lapse_frames(config_path, str(p.parent), **kw)
                    if create_labeled:
                        _f.write(f"\nCreating labeled frames in: {p.parent}\n\n")
                        _dlc.create_labeled_video(config_path, [str(p.parent)], save_frames=True, **label_kw)
                else:
                    raise ValueError(f"Unsupported file type: {ext}")

            elif p.is_dir():
                files = [f for f in p.iterdir() if f.is_file()]
                video_files = [f for f in files if f.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}]
                image_files = [f for f in files if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}]

                if not video_files and not image_files:
                    raise ValueError(f"No supported video or image files found in: {p}")

                if video_files:
                    video_paths = [str(v) for v in sorted(video_files)]
                    _f.write(f"Analyzing {len(video_files)} video(s) in: {p}\n\n")
                    _dlc.analyze_videos(config_path, video_paths, **kw)
                    if create_labeled:
                        _f.write(f"\nCreating labeled video(s)...\n\n")
                        _dlc.create_labeled_video(config_path, video_paths, **label_kw)

                if image_files:
                    _f.write(f"\nAnalyzing {len(image_files)} image(s) in: {p}\n\n")
                    _dlc.analyze_time_lapse_frames(config_path, str(p), **kw)
                    if create_labeled:
                        _f.write(f"\nCreating labeled frames in: {p}\n\n")
                        _dlc.create_labeled_video(config_path, [str(p)], save_frames=True, **label_kw)

            else:
                raise FileNotFoundError(f"Target not found: {target_path}")

            _f.write("\n__ANALYZE_COMPLETE__\n")
        except Exception:
            import traceback as _tb
            _f.write("\n__ANALYZE_ERROR__\n")
            _f.write(_tb.format_exc())


@celery.task(bind=True, name="tasks.dlc_analyze", acks_late=False)
def dlc_analyze(self, config_path: str, target_path: str, params: dict = None):
    """
    Run DLC analysis (analyze_videos / analyze_time_lapse_frames) in a child
    process so it can be killed cleanly without taking down the Celery worker.
    params keys: shuffle, trainingsetindex, gputouse, save_as_csv, create_labeled, snapshot_index
    """
    import multiprocessing as _mp
    import threading as _threading
    import tempfile
    import signal as _signal
    import redis as _redis_mod

    _redis = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )

    if params is None:
        params = {}

    task_id   = self.request.id
    pid_key   = _ANALYZE_PID_PREFIX + task_id
    stop_key  = "dlc_analyze_stop:" + task_id
    job_key   = "dlc_analyze_job:" + task_id
    jobs_zset = "dlc_analyze_jobs"

    def _job_set(status: str):
        _redis.hset(job_key, "status", status)
        if status in ("complete", "stopped", "failed"):
            _redis.expire(job_key, 3600)

    # Register job so it appears in the monitor
    _redis.hset(job_key, mapping={
        "task_id":     task_id,
        "operation":   "analyze",
        "project":     Path(config_path).parent.name,
        "config_path": config_path,
        "target_path": target_path,
        "started_at":  str(time.time()),
        "status":      "running",
    })
    _redis.expire(job_key, 7200)
    _redis.zadd(jobs_zset, {task_id: time.time()})

    _tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", prefix="dlc_analyze_", delete=False
    )
    log_path = _tmp.name
    _tmp.close()

    try:
        self.update_state(
            state="PROGRESS",
            meta={"progress": 5, "stage": "Checking target…", "log": ""},
        )

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"DLC config.yaml not found: {config_path}")
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Target not found: {target_path}")

        init_log = (
            f"config_path  : {config_path}\n"
            f"target_path  : {target_path}\n"
            f"params       : {params}\n\n"
        )
        with open(log_path, "w") as _f:
            _f.write(init_log)

        self.update_state(
            state="PROGRESS",
            meta={"progress": 10, "stage": "Starting analysis…", "log": init_log},
        )

        ctx  = _mp.get_context("spawn")
        proc = ctx.Process(
            target=_dlc_analyze_subprocess,
            args=(config_path, target_path, params, log_path),
            daemon=False,
        )
        proc.start()
        _redis.setex(pid_key, 7200, str(proc.pid))

        _stop_emit   = _threading.Event()
        _user_killed = [False]

        def _emit_loop():
            import signal as _sig
            _progress = 12
            while not _stop_emit.wait(3):
                if _redis.get(stop_key):
                    _user_killed[0] = True
                    try:
                        os.killpg(proc.pid, _sig.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    _redis.delete(stop_key, pid_key, job_key)
                    _redis.zrem(jobs_zset, task_id)
                    break

                try:
                    with open(log_path) as _lf:
                        _log = _lf.read()[-8000:]
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "progress": min(_progress, 90),
                            "stage":    "Analyzing…",
                            "log":      _log,
                        },
                    )
                    _progress = min(_progress + 1, 90)
                except Exception:
                    pass

        _emitter = _threading.Thread(target=_emit_loop, daemon=True)
        _emitter.start()

        proc.join()

        _stop_emit.set()
        _emitter.join(timeout=5)
        _redis.delete(pid_key, stop_key)

        try:
            with open(log_path) as _lf:
                final_log = _lf.read()
        except OSError:
            final_log = ""

        if _user_killed[0]:
            _redis.delete(job_key)
            _redis.zrem(jobs_zset, task_id)
            raise RuntimeError("__USER_STOPPED__")

        if proc.exitcode != 0:
            _job_set("failed")
            if proc.exitcode is not None and proc.exitcode < 0:
                raise RuntimeError(
                    f"Analysis process was killed (signal {-proc.exitcode}).\n\n"
                    + final_log[-3000:]
                )
            raise RuntimeError(final_log[-5000:])

        _job_set("complete")
        return {
            "status":    "complete",
            "operation": "analyze",
            "log":       final_log[-8000:] or f"Analysis complete.\nconfig: {config_path}",
        }

    except Exception:
        _redis.delete(pid_key, stop_key)
        _redis.zrem(jobs_zset, task_id)
        _job_set("failed")
        raise RuntimeError(traceback.format_exc()[-3000:])

    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass


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

    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])
