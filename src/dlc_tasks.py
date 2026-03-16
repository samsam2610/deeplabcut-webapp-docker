"""
DLC (DeepLabCut) Celery tasks.
All task names preserve the original `tasks.XXX` namespace.
"""
from __future__ import annotations

import os
import subprocess
import time
import traceback
from pathlib import Path

import deeplabcut as dlc

from celery_app import celery  # shared Celery instance


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
    """
    Sync video_sets in config.yaml with labeled-data folders, then create
    dummy video files in project/videos/ for each entry in video_sets.

    This replaces the fragile DLC built-in which requires the videos directory
    to exist and chokes on labeled-video filenames. Instead we:
      1. Read labeled-data sub-folder names (each name == a video stem).
      2. For every stem, keep the existing video_sets entry (preserving crop
         data and the original absolute path) if one matches; otherwise create
         a new entry pointing to project/videos/<stem>.mp4.
      3. Remove video_sets entries whose stems have no labeled-data folder.
      4. Create project/videos/ and an empty dummy file for every video_sets
         entry whose path does not already exist on disk, so downstream DLC
         steps that scan the videos/ dir don't fail.
    Config is read and written with ruamel.yaml to preserve key order and
    comments.
    """
    import ruamel.yaml as _ruamel

    try:
        _ryaml = _ruamel.YAML()
        _ryaml.preserve_quotes = True
        _cfg_path = Path(config_path)
        _cfg = _ryaml.load(_cfg_path)

        project_path    = Path(_cfg.get("project_path", _cfg_path.parent))
        labeled_data_dir = project_path / "labeled-data"
        videos_dir       = project_path / "videos"

        # Stems present in labeled-data/
        labeled_stems = {
            d.name for d in labeled_data_dir.iterdir() if d.is_dir()
        } if labeled_data_dir.is_dir() else set()

        # Build stem → (original_path, crop_value) from current video_sets
        current_video_sets = _cfg.get("video_sets") or {}
        stem_to_entry: dict = {}
        for vid_path, crop_data in current_video_sets.items():
            stem = Path(vid_path).stem
            stem_to_entry[stem] = (vid_path, crop_data)

        # Build the new video_sets, one entry per labeled-data folder
        new_video_sets = _ruamel.comments.CommentedMap()
        for stem in sorted(labeled_stems):
            if stem in stem_to_entry:
                vid_path, crop_data = stem_to_entry[stem]
            else:
                # No existing entry — point to project/videos/ with no crop info
                vid_path  = str(videos_dir / f"{stem}.mp4")
                crop_data = None
            new_video_sets[vid_path] = crop_data

        _cfg["video_sets"] = new_video_sets

        # Persist config preserving order
        with open(_cfg_path, "w") as _f:
            _ryaml.dump(_cfg, _f)

        # Ensure videos/ exists and create a dummy file for every entry whose
        # actual video is absent so DLC directory scans don't raise errors.
        videos_dir.mkdir(parents=True, exist_ok=True)
        created_dummies = []
        for vid_path in new_video_sets:
            p = Path(vid_path)
            if not p.exists():
                dummy = videos_dir / p.name
                if not dummy.exists():
                    dummy.touch()
                    created_dummies.append(dummy.name)

        return {
            "status":          "complete",
            "operation":       "add_datasets_to_video_list",
            "labeled_stems":   sorted(labeled_stems),
            "video_sets":      list(new_video_sets.keys()),
            "created_dummies": created_dummies,
        }
    except Exception:
        raise RuntimeError(traceback.format_exc()[-3000:])


# ── DLC Convert Labels CSV → H5 ──────────────────────────────────
@celery.task(bind=True, name="tasks.dlc_convert_labels_to_h5")
def dlc_convert_labels_to_h5(self, config_path: str, scorer: str = None):
    """
    Convert every labeled-data CSV to HDF5 using deeplabcut.convertcsv2h5.
    Processes folders one-by-one so a single bad CSV does not abort the rest.
    """
    from itertools import islice as _islice
    import pandas as _pd

    cfg        = dlc.auxiliaryfunctions.read_config(config_path)
    eff_scorer = scorer or cfg["scorer"]
    videos     = list(cfg.get("video_sets", {}).keys())
    stems      = [Path(v).stem for v in videos]
    folders    = [Path(config_path).parent / "labeled-data" / s for s in stems]

    converted, skipped, errors = [], [], []

    for folder in folders:
        csv_path = folder / f"CollectedData_{cfg['scorer']}.csv"
        if not csv_path.is_file():
            skipped.append(folder.name)
            continue
        # Remove a pre-existing empty H5 so DLC doesn't trip on it
        h5_path = Path(str(csv_path).replace(".csv", ".h5"))
        if h5_path.is_file():
            try:
                existing = _pd.read_hdf(str(h5_path), key="df_with_missing")
                if len(existing) == 0:
                    h5_path.unlink()
            except Exception:
                h5_path.unlink()  # corrupt/unreadable — remove it
        try:
            with open(str(csv_path)) as fh:
                head = list(_islice(fh, 5))
            header    = list(range(4)) if len(head) > 1 and "individuals" in head[1] else list(range(3))
            index_col = [0, 1, 2]     if head and head[-1].split(",")[0] == "labeled-data" else 0

            data = _pd.read_csv(str(csv_path), index_col=index_col, header=header)
            if len(data) == 0:
                skipped.append(folder.name + " (no labeled frames)")
                continue
            # Rebuild column MultiIndex with the effective scorer name to avoid
            # index inconsistency from set_levels when code count != level count
            data.columns = _pd.MultiIndex.from_tuples(
                [(eff_scorer,) + t[1:] for t in data.columns],
                names=data.columns.names,
            )
            h5_path = str(csv_path).replace(".csv", ".h5")
            data.to_hdf(h5_path, key="df_with_missing", mode="w")
            # Also rewrite CSV with updated scorer name (mirrors DLC behaviour)
            data.to_csv(str(csv_path))
            converted.append(folder.name)
        except Exception:
            errors.append(f"{folder.name}: {traceback.format_exc()[-1000:]}")

    if errors:
        raise RuntimeError(
            f"Converted {len(converted)}, skipped {len(skipped)}, "
            f"FAILED {len(errors)}:\n\n" + "\n\n".join(errors)
        )
    return {
        "status": "complete",
        "operation": "convert_labels_to_h5",
        "converted": converted,
        "skipped": skipped,
    }


# ── DLC Train Network ─────────────────────────────────────────────

# Redis key prefix used to share the training child-process PID between
# the Celery task and the Flask stop endpoint.
_TRAIN_PID_PREFIX = "dlc_train_pid:"


def _cuda_cleanup_with_timeout(timeout: int = 10) -> None:
    """
    Release GPU resources in a daemon thread so that a hung cuda.synchronize()
    cannot block the process indefinitely.  Called from subprocess finally blocks.
    """
    import threading as _thr
    def _do_cleanup():
        try:
            import gc as _gc
            _gc.collect()
            import torch as _torch
            if _torch.cuda.is_available():
                _torch.cuda.synchronize()
                _torch.cuda.empty_cache()
        except Exception:
            pass
    t = _thr.Thread(target=_do_cleanup, daemon=True)
    t.start()
    t.join(timeout=timeout)


def _dlc_train_subprocess(config_path: str, kwargs: dict, log_path: str) -> None:
    """
    Runs inside a child process spawned by dlc_train_network.
    Becomes a process-group leader immediately so that killpg() from the
    parent will also reach all grandchild processes (PyTorch DataLoader
    workers, CUDA subprocesses, etc.), preventing GPU-context leaks.
    """
    import os as _os, sys, signal as _sig, deeplabcut as _dlc

    # Become process-group leader — parent uses os.killpg(proc.pid, SIGTERM/SIGKILL)
    _os.setpgrp()

    # Ensure CUDA device numbering matches nvidia-smi (PCI bus ID order).
    # Without this, CUDA may use FASTEST_FIRST ordering, causing a mismatch
    # between the GPU index shown in the UI and the one actually used.
    _os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"

    # Install a SIGTERM handler so that the parent's graceful-stop sequence
    # (SIGTERM → wait → SIGKILL) gives Python a chance to run the finally block
    # and release the CUDA context cleanly.  Without this, only SIGKILL would be
    # used, which bypasses finally blocks and leaves the GPU hung at 100 %.
    def _sigterm_handler(signum, frame):
        raise SystemExit(0)
    _sig.signal(_sig.SIGTERM, _sigterm_handler)

    with open(log_path, "a", buffering=1) as _f:
        sys.stdout = _f
        sys.stderr = _f
        try:
            _dlc.train_network(config_path, **kwargs)
            _f.write("\n__TRAIN_COMPLETE__\n")
        except (SystemExit, KeyboardInterrupt):
            # Raised by our SIGTERM handler — not an error, just a stop request.
            _f.write("\n__TRAIN_STOPPED__\n")
        except Exception:
            import traceback as _tb
            _f.write("\n__TRAIN_ERROR__\n")
            _f.write(_tb.format_exc())
        finally:
            # Always attempt a clean CUDA shutdown.  Wrapped in a timed thread so
            # a stuck cuda.synchronize() cannot prevent the process from exiting
            # (parent will escalate to SIGKILL after ~10 s regardless).
            _cuda_cleanup_with_timeout(timeout=10)


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

        # PyTorch DLC uses `device` ("cuda:N") not `gputouse` (TF legacy).
        # Convert so the correct GPU is actually used.
        if engine == "pytorch" and "gputouse" in kwargs:
            gpu_idx = kwargs.pop("gputouse")
            kwargs.setdefault("device", f"cuda:{gpu_idx}")

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
                    # ── Graceful stop: SIGTERM → wait → SIGKILL ──────────
                    # SIGTERM allows the subprocess's finally block to run
                    # torch.cuda.synchronize() + empty_cache(), preventing
                    # the GPU from hanging at 100 % after a forced stop.
                    # SIGKILL is sent only if the process is still alive after
                    # the grace period (e.g. stuck in a CUDA kernel).
                    try:
                        os.killpg(proc.pid, _sig.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                    # Wait up to 12 s for the subprocess to exit cleanly
                    for _ in range(24):
                        if not proc.is_alive():
                            break
                        time.sleep(0.5)
                    # Force-kill anything still alive
                    if proc.is_alive():
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

        proc.join()  # block until child exits naturally or is stopped

        # Reap any orphaned children the subprocess may have spawned
        # (e.g. DLC/PyTorch dataloader workers that hold GPU memory).
        _pgid = proc.pid
        try:
            import signal as _sig
            os.killpg(_pgid, _sig.SIGKILL)
        except (ProcessLookupError, OSError):
            pass  # process group already gone — that's fine

        # Give the CUDA driver time to fully reclaim GPU resources after the
        # process group has been torn down.  1 s is often insufficient; 3 s
        # avoids the GPU appearing stuck when the next task starts.
        time.sleep(3)

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
    import os as _os, sys, signal as _sig
    from pathlib import Path as _Path

    _os.setpgrp()

    # Ensure CUDA device numbering matches nvidia-smi (PCI bus ID order).
    _os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"

    # SIGTERM handler: lets the finally block run CUDA cleanup before SIGKILL.
    def _sigterm_handler(signum, frame):
        raise SystemExit(0)
    _sig.signal(_sig.SIGTERM, _sigterm_handler)

    with open(log_path, "a", buffering=1) as _f:
        sys.stdout = _f
        sys.stderr = _f
        try:
            import importlib, deeplabcut as _dlc

            # DLC 3.x moved functions into pose_estimation_pytorch submodule.
            # This helper searches submodules if the function isn't at the top level.
            def _dlc_fn(name):
                if hasattr(_dlc, name):
                    return getattr(_dlc, name)
                for sub in ("deeplabcut.pose_estimation_pytorch",
                            "deeplabcut.pose_estimation_tensorflow"):
                    try:
                        m = importlib.import_module(sub)
                        if hasattr(m, name):
                            return getattr(m, name)
                    except Exception as _e:
                        _f.write(f"[_dlc_fn] could not import {sub}: {_e}\n")
                raise AttributeError(f"deeplabcut has no attribute '{name}'")

            _analyze_videos       = _dlc_fn("analyze_videos")
            _analyze_time_lapse   = _dlc_fn("analyze_time_lapse_frames")
            _create_labeled_video = _dlc_fn("create_labeled_video")

            p = _Path(target_path)
            create_labeled = params.get("create_labeled", False)

            # Resolve snapshot_path → local index within its train folder.
            # snapshot_path is project-relative; local_snap_index is what DLC expects.
            import yaml as _yaml
            snapshot_path = params.get("snapshot_path")
            local_snap_index = None
            _cfg_patched_analyze = False
            _cfg_original_snap_analyze = None
            if snapshot_path:
                try:
                    project_path = _Path(config_path).parent
                    snap_file    = (project_path / snapshot_path).resolve()
                    train_folder = snap_file.parent
                    snap_ext     = snap_file.suffix
                    all_snaps    = sorted(train_folder.glob(f"*{snap_ext}"),
                                         key=lambda p: p.name)
                    local_snap_index = next((i for i, sp in enumerate(all_snaps)
                                            if sp == snap_file), None)
                    if local_snap_index is not None:
                        _f.write(f"Snapshot: {snap_file.name}  →  local index {local_snap_index} of {len(all_snaps)}\n\n")
                    else:
                        _f.write(f"Warning: snapshot not found in train folder, using latest\n\n")
                except Exception as _spe:
                    _f.write(f"Warning: could not resolve snapshot_path ({_spe})\n\n")

            _skip_keys = {"create_labeled", "snapshot_path", "snapshot_index"}
            kw = {k: v for k, v in params.items()
                  if v is not None and k not in _skip_keys}
            if local_snap_index is not None:
                kw["snapshot_index"] = local_snap_index

            # kwargs shared by create_labeled_video
            label_kw = {k: kw[k] for k in ("shuffle", "trainingsetindex") if k in kw}

            def _patch_cfg_snapshot(idx):
                """Temporarily set snapshotindex in config.yaml for functions that
                read the config directly (e.g. analyze_time_lapse_frames)."""
                nonlocal _cfg_patched_analyze, _cfg_original_snap_analyze
                if idx is None:
                    return
                try:
                    with open(config_path, "r") as _cf:
                        _cd = _yaml.safe_load(_cf)
                    _cfg_original_snap_analyze = _cd.get("snapshotindex")
                    _cd["snapshotindex"] = idx
                    with open(config_path, "w") as _cf:
                        _yaml.dump(_cd, _cf, default_flow_style=False, allow_unicode=True)
                    _cfg_patched_analyze = True
                except Exception as _pe:
                    _f.write(f"Warning: could not patch snapshotindex ({_pe})\n")

            def _restore_cfg_snapshot():
                if not _cfg_patched_analyze:
                    return
                try:
                    with open(config_path, "r") as _cf:
                        _cd = _yaml.safe_load(_cf)
                    if _cfg_original_snap_analyze is None:
                        _cd.pop("snapshotindex", None)
                    else:
                        _cd["snapshotindex"] = _cfg_original_snap_analyze
                    with open(config_path, "w") as _cf:
                        _yaml.dump(_cd, _cf, default_flow_style=False, allow_unicode=True)
                except Exception as _re:
                    _f.write(f"Warning: could not restore snapshotindex ({_re})\n")

            # kw for time-lapse: strip snapshot_index (not accepted); config is patched instead
            tl_kw = {k: v for k, v in kw.items() if k != "snapshot_index"}

            if p.is_file():
                ext = p.suffix.lower()
                if ext in {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}:
                    _f.write(f"Analyzing video file: {p}\n\n")
                    _analyze_videos(config_path, [str(p)], **kw)
                    if create_labeled:
                        _f.write(f"\nCreating labeled video: {p}\n\n")
                        _create_labeled_video(config_path, [str(p)], **label_kw)
                elif ext in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}:
                    _f.write(f"Analyzing image directory (selected frame): {p.parent}\n\n")
                    _patch_cfg_snapshot(local_snap_index)
                    try:
                        _analyze_time_lapse(config_path, str(p.parent), **tl_kw)
                    finally:
                        _restore_cfg_snapshot()
                    if create_labeled:
                        _f.write(f"\nCreating labeled frames in: {p.parent}\n\n")
                        _create_labeled_video(config_path, [str(p.parent)], save_frames=True, **label_kw)
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
                    _analyze_videos(config_path, video_paths, **kw)
                    if create_labeled:
                        _f.write(f"\nCreating labeled video(s)...\n\n")
                        _create_labeled_video(config_path, video_paths, **label_kw)

                if image_files:
                    _f.write(f"\nAnalyzing {len(image_files)} image(s) in: {p}\n\n")
                    _patch_cfg_snapshot(local_snap_index)
                    try:
                        _analyze_time_lapse(config_path, str(p), **tl_kw)
                    finally:
                        _restore_cfg_snapshot()
                    if create_labeled:
                        _f.write(f"\nCreating labeled frames in: {p}\n\n")
                        _create_labeled_video(config_path, [str(p)], save_frames=True, **label_kw)

            else:
                raise FileNotFoundError(f"Target not found: {target_path}")

            _f.write("\n__ANALYZE_COMPLETE__\n")
        except (SystemExit, KeyboardInterrupt):
            _f.write("\n__ANALYZE_STOPPED__\n")
        except Exception:
            import traceback as _tb
            _f.write("\n__ANALYZE_ERROR__\n")
            _f.write(_tb.format_exc())
        finally:
            # Release GPU memory before the process exits so the driver
            # reclaims the CUDA context immediately rather than lazily.
            # Wrapped in a timed thread so a stuck synchronize() can't hang.
            _cuda_cleanup_with_timeout(timeout=10)


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
                    # SIGTERM first — lets the subprocess run CUDA cleanup
                    try:
                        os.killpg(proc.pid, _sig.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                    for _ in range(24):
                        if not proc.is_alive():
                            break
                        time.sleep(0.5)
                    if proc.is_alive():
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

        # Reap any orphaned children the subprocess may have spawned
        # (e.g. DLC/PyTorch dataloader workers that hold GPU memory).
        _pgid = proc.pid
        try:
            import signal as _sig
            os.killpg(_pgid, _sig.SIGKILL)
        except (ProcessLookupError, OSError):
            pass  # process group already gone — that's fine

        time.sleep(3)

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


# ── DLC Machine Label Frames ──────────────────────────────────────

_ML_LABEL_PID_PREFIX = "dlc_ml_pid:"


def _dlc_machine_label_subprocess(
    config_path: str, labeled_data_path: str, params: dict, log_path: str
) -> None:
    """
    Runs inside a child process spawned by dlc_machine_label_frames.
    Calls analyze_time_lapse_frames on the frames folder, then converts the
    DLC output into CollectedData_<scorer>.csv so the user can review/correct.
    """
    import os as _os, sys, signal as _sig, re as _re, csv as _csv
    from pathlib import Path as _Path

    _os.setpgrp()
    _os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"

    # SIGTERM handler: lets the finally block run CUDA cleanup before SIGKILL.
    def _sigterm_handler(signum, frame):
        raise SystemExit(0)
    _sig.signal(_sig.SIGTERM, _sigterm_handler)

    with open(log_path, "a", buffering=1) as _f:
        sys.stdout = _f
        sys.stderr = _f
        try:
            import importlib, deeplabcut as _dlc, pandas as _pd

            _f.write(f"config_path:       {config_path}\n")
            _f.write(f"labeled_data_path: {labeled_data_path}\n")
            _f.write(f"params:            {params}\n\n")

            # Read scorer + bodyparts from config
            import yaml as _yaml
            with open(config_path) as _cf:
                cfg = _yaml.safe_load(_cf) or {}
            scorer    = cfg.get("scorer", "User")
            bodyparts = list(cfg.get("bodyparts", []))

            frame_dir = _Path(labeled_data_path)

            # Detect frame type
            _img_exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
            exts = {f.suffix.lower() for f in frame_dir.iterdir()
                    if f.is_file() and not f.name.startswith(".")}
            frametype = next((e for e in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
                              if e in exts), ".png")
            _f.write(f"Detected frame type: {frametype}\n")

            # Record existing .h5 files so we can identify the new output
            existing_h5 = {str(p) for p in frame_dir.glob("*.h5")}

            # Build kwargs (strip keys not accepted by analyze_time_lapse_frames)
            _tl_keys = {"shuffle", "trainingsetindex", "gputouse", "save_as_csv"}
            kw = {k: v for k, v in params.items()
                  if k in _tl_keys and v is not None}
            kw.setdefault("save_as_csv", True)

            # Resolve the function (handles DLC 3.x module layout)
            def _dlc_fn(name):
                if hasattr(_dlc, name):
                    return getattr(_dlc, name)
                for sub in ("deeplabcut.pose_estimation_pytorch",
                            "deeplabcut.pose_estimation_tensorflow"):
                    try:
                        m = importlib.import_module(sub)
                        if hasattr(m, name):
                            return getattr(m, name)
                    except Exception:
                        pass
                raise AttributeError(f"deeplabcut has no attribute '{name}'")

            # snapshot_path is a project-relative path to the chosen snapshot.
            # Resolve its local index within its own train folder (alphabetical,
            # matching DLC's ordering) and patch snapshotindex in config.yaml.
            import yaml as _yaml
            snapshot_path = params.get("snapshot_path")
            _cfg_patched = False
            _cfg_original_snapshot = None
            if snapshot_path:
                try:
                    project_path = Path(config_path).parent
                    snap_file    = (project_path / snapshot_path).resolve()
                    train_folder = snap_file.parent
                    snap_ext     = snap_file.suffix  # .pt or .index
                    all_snaps    = sorted(train_folder.glob(f"*{snap_ext}"),
                                         key=lambda p: p.name)
                    local_idx    = next((i for i, p in enumerate(all_snaps)
                                        if p == snap_file), None)
                    if local_idx is None:
                        _f.write(f"Warning: snapshot not found in train folder, using latest\n")
                    else:
                        with open(config_path, "r") as _cf:
                            _cfg_data = _yaml.safe_load(_cf)
                        _cfg_original_snapshot = _cfg_data.get("snapshotindex")
                        _cfg_data["snapshotindex"] = local_idx
                        with open(config_path, "w") as _cf:
                            _yaml.dump(_cfg_data, _cf, default_flow_style=False, allow_unicode=True)
                        _cfg_patched = True
                        _f.write(f"Snapshot: {snap_file.name}  →  local index {local_idx} of {len(all_snaps)}\n")
                except Exception as _pe:
                    _f.write(f"Warning: could not resolve snapshot path ({_pe})\n")

            _f.write("Running analyze_time_lapse_frames…\n\n")
            try:
                _dlc_fn("analyze_time_lapse_frames")(
                    config_path, str(frame_dir), frametype=frametype, **kw
                )
            finally:
                # Always restore the original snapshotindex
                if _cfg_patched:
                    try:
                        with open(config_path, "r") as _cf:
                            _cfg_data = _yaml.safe_load(_cf)
                        if _cfg_original_snapshot is None:
                            _cfg_data.pop("snapshotindex", None)
                        else:
                            _cfg_data["snapshotindex"] = _cfg_original_snapshot
                        with open(config_path, "w") as _cf:
                            _yaml.dump(_cfg_data, _cf, default_flow_style=False, allow_unicode=True)
                        _f.write(f"Restored config snapshotindex → {_cfg_original_snapshot}\n")
                    except Exception as _re:
                        _f.write(f"Warning: could not restore snapshotindex ({_re})\n")

            # Find the newly created .h5 prediction file
            new_h5 = [p for p in frame_dir.glob("*.h5") if str(p) not in existing_h5]
            if not new_h5:
                all_h5 = sorted(frame_dir.glob("*.h5"), key=lambda p: p.stat().st_mtime)
                if not all_h5:
                    raise FileNotFoundError("No DLC prediction file (.h5) found after analysis")
                new_h5 = [all_h5[-1]]
            h5_path = sorted(new_h5, key=lambda p: p.stat().st_mtime)[-1]
            _f.write(f"\nReading predictions from: {h5_path.name}\n")

            df = _pd.read_hdf(str(h5_path))

            # ── Diagnostics ──────────────────────────────────────────
            _f.write(f"df.shape: {df.shape}\n")
            _f.write(f"df.index type: {type(df.index).__name__}\n")
            _f.write(f"df.index[:3]: {list(df.index[:3])}\n")
            _n_levels = df.columns.nlevels if hasattr(df.columns, "nlevels") else 1
            _f.write(f"Column levels: {_n_levels}\n")
            _f.write(f"Sample columns: {list(df.columns[:6])}\n")
            if _n_levels >= 2:
                h5_bodyparts = list(dict.fromkeys(df.columns.get_level_values(-2)))
                _f.write(f"Bodyparts in h5 (level -2): {h5_bodyparts}\n")
            _f.write(f"Bodyparts in config: {bodyparts}\n")
            # ─────────────────────────────────────────────────────────

            dlc_scorer = (df.columns.get_level_values(0)[0]
                          if hasattr(df.columns, "get_level_values") else None)
            _f.write(f"Scorer from h5: {dlc_scorer}\n")

            def _nat_key(s: str) -> list:
                return [int(c) if c.isdigit() else c.lower()
                        for c in _re.split(r"(\d+)", s)]

            likelihood_threshold = float(params.get("likelihood_threshold") or 0.9)
            _f.write(f"Likelihood threshold: {likelihood_threshold}\n")

            # Build a bp→column-key mapping that handles both:
            #   3-level: (scorer, bodypart, coord)
            #   4-level: (scorer, individual, bodypart, coord)
            def _find_bp_cols(bp):
                """Return (x_col, y_col, lk_col) tuples for the given bodypart, or None."""
                matches_x, matches_y, matches_lk = [], [], []
                for col in df.columns:
                    if col[-2] == bp:           # bodypart is second-to-last level
                        coord = col[-1]
                        if coord == "x":         matches_x.append(col)
                        elif coord == "y":       matches_y.append(col)
                        elif coord in ("likelihood", "p"):  matches_lk.append(col)
                if matches_x and matches_y:
                    return matches_x[0], matches_y[0], matches_lk[0] if matches_lk else None
                return None

            # Pre-resolve columns once
            bp_col_map = {}
            for bp in bodyparts:
                result = _find_bp_cols(bp)
                if result:
                    bp_col_map[bp] = result
                else:
                    _f.write(f"  WARNING: bodypart '{bp}' not found in h5 columns\n")

            labels: dict = {}
            # Use iterrows() so each `row` is a plain Series — avoids the
            # ambiguity of df.loc[tuple_idx, tuple_col] with MultiIndex rows.
            for idx, row in df.iterrows():
                img_name = _Path(str(idx[-1] if isinstance(idx, tuple) else idx)).name
                frame_labels: dict = {}
                for bp in bodyparts:
                    cols = bp_col_map.get(bp)
                    if cols is None:
                        frame_labels[bp] = None
                        continue
                    x_col, y_col, lk_col = cols
                    try:
                        x  = float(row[x_col])
                        y  = float(row[y_col])
                        lk = float(row[lk_col]) if lk_col is not None else 1.0
                        if _pd.isna(x) or _pd.isna(y) or lk < likelihood_threshold:
                            frame_labels[bp] = None
                        else:
                            frame_labels[bp] = [round(x, 4), round(y, 4)]
                    except (KeyError, TypeError, ValueError, IndexError) as _e:
                        frame_labels[bp] = None
                labels[img_name] = frame_labels

            _f.write(f"Frames parsed from h5: {len(labels)}\n")
            _labeled_count = sum(1 for fv in labels.values()
                                 if any(v is not None for v in fv.values()))
            _f.write(f"Frames with at least one labeled bodypart: {_labeled_count}\n")

            # Load existing human labels so we can preserve them
            video_stem = frame_dir.name
            csv_path   = frame_dir / f"CollectedData_{scorer}.csv"
            human_labels: dict = {}
            if csv_path.is_file():
                try:
                    with open(str(csv_path), newline="") as _hf:
                        _rows = list(_csv.reader(_hf))
                    if len(_rows) >= 4:
                        _bp_row    = _rows[1][3:]
                        _coord_row = _rows[2][3:]
                        _col_pairs = list(zip(_bp_row, _coord_row))
                        for _row in _rows[3:]:
                            if not _row:
                                continue
                            _img   = _row[2]
                            _vals  = _row[3:]
                            _bpmap: dict = {}
                            for (_bp, _c), _v in zip(_col_pairs, _vals):
                                _bpmap.setdefault(_bp, {})[_c] = _v
                            _frame: dict = {}
                            for _bp, _cd in _bpmap.items():
                                _xs = _cd.get("x", "")
                                _ys = _cd.get("y", "")
                                try:
                                    _x = float(_xs) if _xs not in ("", "NaN", "nan") else None
                                    _y = float(_ys) if _ys not in ("", "NaN", "nan") else None
                                except ValueError:
                                    _x = _y = None
                                _frame[_bp] = [_x, _y] if _x is not None and _y is not None else None
                            human_labels[_img] = _frame
                    n_human = sum(1 for fv in human_labels.values()
                                  if any(v is not None for v in fv.values()))
                    _f.write(f"Found {n_human} existing human-labeled frame(s) — these will be preserved.\n")
                except Exception as _e:
                    _f.write(f"Warning: could not read existing labels ({_e}), proceeding without merge.\n")

            def _frame_has_human_label(fname: str) -> bool:
                fv = human_labels.get(fname, {})
                return any(v is not None for v in fv.values())

            # Merge: keep human labels; fill unlabeled frames with machine predictions
            merged: dict = {}
            for fname, machine_frame in labels.items():
                if _frame_has_human_label(fname):
                    merged[fname] = human_labels[fname]
                else:
                    merged[fname] = machine_frame
            # Include any frames present only in human labels (not in ML output)
            for fname, human_frame in human_labels.items():
                if fname not in merged:
                    merged[fname] = human_frame

            # Write CollectedData CSV in DLC MultiIndex format
            frame_names = sorted(merged.keys(), key=_nat_key)

            rows_out = [
                ["scorer",    "", ""] + [scorer] * (len(bodyparts) * 2),
                ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in range(2)],
                ["coords",    "", ""] + ["x", "y"] * len(bodyparts),
            ]
            for fname in frame_names:
                row = ["labeled-data", video_stem, fname]
                for bp in bodyparts:
                    pt = merged.get(fname, {}).get(bp)
                    if pt and len(pt) == 2 and pt[0] is not None:
                        row.extend([str(round(pt[0], 4)), str(round(pt[1], 4))])
                    else:
                        row.extend(["NaN", "NaN"])
                rows_out.append(row)

            with open(str(csv_path), "w", newline="") as _out:
                _csv.writer(_out).writerows(rows_out)

            # Write CollectedData_{scorer}.h5 in DLC MultiIndex format
            h5_path = frame_dir / f"CollectedData_{scorer}.h5"
            try:
                idx_tuples = [
                    ("labeled-data", video_stem, fname)
                    for fname in frame_names
                ]
                mi = _pd.MultiIndex.from_tuples(idx_tuples,
                                                names=["", "", ""])
                col_tuples = [
                    (scorer, bp, coord)
                    for bp in bodyparts
                    for coord in ("x", "y")
                ]
                col_mi = _pd.MultiIndex.from_tuples(
                    col_tuples, names=["scorer", "bodyparts", "coords"]
                )
                import math as _math
                data_rows = []
                for fname in frame_names:
                    row_vals = []
                    for bp in bodyparts:
                        pt = merged.get(fname, {}).get(bp)
                        if pt and len(pt) == 2 and pt[0] is not None:
                            row_vals.extend([round(pt[0], 4), round(pt[1], 4)])
                        else:
                            row_vals.extend([_math.nan, _math.nan])
                    data_rows.append(row_vals)
                df_out = _pd.DataFrame(data_rows, index=mi, columns=col_mi)
                df_out.to_hdf(str(h5_path), key="df_with_missing", mode="w")
                _f.write(f"Written h5:  {h5_path.name}\n")
            except Exception as _he:
                _f.write(f"Warning: could not write h5 ({_he})\n")

            # Keep raw predictions under a stable name so the user can
            # re-apply a different threshold later without re-running the model.
            raw_h5_dest = frame_dir / "_machine_predictions_raw.h5"
            for _raw in new_h5:
                try:
                    _raw.rename(raw_h5_dest)
                    _f.write(f"Saved raw predictions → {raw_h5_dest.name}\n")
                except Exception as _re:
                    _f.write(f"Warning: could not save raw h5 ({_re})\n")
                # Delete the auto-named companion CSV (we already wrote CollectedData CSV)
                _raw_csv = _raw.with_suffix(".csv")
                if _raw_csv.is_file() and _raw_csv != csv_path:
                    try:
                        _raw_csv.unlink()
                    except Exception:
                        pass

            # Write metadata so the re-apply endpoint knows which frames were
            # machine-labeled vs human-labeled.
            import json as _json_mod
            _ml_meta = {
                "ml_frames":    [fn for fn in merged if not _frame_has_human_label(fn)],
                "human_frames": [fn for fn in merged if     _frame_has_human_label(fn)],
                "scorer":       scorer,
                "bodyparts":    bodyparts,
            }
            try:
                (frame_dir / "_ml_frames.json").write_text(
                    _json_mod.dumps(_ml_meta, indent=2)
                )
                _f.write(f"Written metadata → _ml_frames.json\n")
            except Exception as _je:
                _f.write(f"Warning: could not write _ml_frames.json ({_je})\n")

            n_machine = sum(
                1 for fname, fv in merged.items()
                if not _frame_has_human_label(fname) and any(v is not None for v in fv.values())
            )
            n_kept = sum(1 for fname in merged if _frame_has_human_label(fname))
            _f.write(f"\nMachine-labeled {n_machine} frame(s), preserved {n_kept} human label(s).\n")
            _f.write(f"Output → {csv_path.name}  +  {h5_path.name}\n")
            _f.write("\n__ML_LABEL_COMPLETE__\n")

        except (SystemExit, KeyboardInterrupt):
            _f.write("\n__ML_LABEL_STOPPED__\n")
        except Exception:
            import traceback as _tb
            _f.write("\n__ML_LABEL_ERROR__\n")
            _f.write(_tb.format_exc())
        finally:
            _cuda_cleanup_with_timeout(timeout=10)


@celery.task(bind=True, name="tasks.dlc_machine_label_frames", acks_late=False)
def dlc_machine_label_frames(
    self, config_path: str, labeled_data_path: str, params: dict = None
):
    """
    Run model inference on a labeled-data frames folder and save predictions
    as CollectedData_<scorer>.csv for manual review and correction.
    params keys: shuffle, trainingsetindex, gputouse, save_as_csv, snapshot_index
    """
    import multiprocessing as _mp
    import threading as _threading
    import tempfile
    import redis as _redis_mod

    _redis = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )

    if params is None:
        params = {}

    task_id  = self.request.id
    pid_key  = _ML_LABEL_PID_PREFIX + task_id
    stop_key = "dlc_ml_stop:" + task_id

    _tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", prefix="dlc_ml_", delete=False
    )
    log_path = _tmp.name
    _tmp.close()

    try:
        self.update_state(
            state="PROGRESS",
            meta={"progress": 5, "stage": "Preparing…", "log": ""},
        )

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"DLC config.yaml not found: {config_path}")
        if not os.path.isdir(labeled_data_path):
            raise FileNotFoundError(f"Frames folder not found: {labeled_data_path}")

        init_log = (
            f"config_path:       {config_path}\n"
            f"labeled_data_path: {labeled_data_path}\n"
            f"params:            {params}\n\n"
        )
        with open(log_path, "w") as _f:
            _f.write(init_log)

        self.update_state(
            state="PROGRESS",
            meta={"progress": 10, "stage": "Running inference…", "log": init_log},
        )

        ctx  = _mp.get_context("spawn")
        proc = ctx.Process(
            target=_dlc_machine_label_subprocess,
            args=(config_path, labeled_data_path, params, log_path),
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
                    # SIGTERM first — lets the subprocess run CUDA cleanup
                    try:
                        os.killpg(proc.pid, _sig.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                    for _ in range(24):
                        if not proc.is_alive():
                            break
                        time.sleep(0.5)
                    if proc.is_alive():
                        try:
                            os.killpg(proc.pid, _sig.SIGKILL)
                        except (ProcessLookupError, OSError):
                            pass
                    _redis.delete(stop_key, pid_key)
                    break
                try:
                    with open(log_path) as _lf:
                        _log = _lf.read()[-8000:]
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "progress": min(_progress, 90),
                            "stage":    "Running inference…",
                            "log":      _log,
                        },
                    )
                    _progress = min(_progress + 1, 90)
                except Exception:
                    pass

        _emitter = _threading.Thread(target=_emit_loop, daemon=True)
        _emitter.start()

        proc.join()

        _pgid = proc.pid
        try:
            import signal as _sig
            os.killpg(_pgid, _sig.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        time.sleep(3)

        _stop_emit.set()
        _emitter.join(timeout=5)
        _redis.delete(pid_key, stop_key)

        try:
            with open(log_path) as _lf:
                final_log = _lf.read()
        except OSError:
            final_log = ""

        if _user_killed[0]:
            raise RuntimeError("__USER_STOPPED__")

        if proc.exitcode != 0:
            if proc.exitcode is not None and proc.exitcode < 0:
                raise RuntimeError(
                    f"Machine labeling process was killed (signal {-proc.exitcode}).\n\n"
                    + final_log[-3000:]
                )
            raise RuntimeError(final_log[-5000:])

        return {
            "status":    "complete",
            "operation": "machine_label_frames",
            "log":       final_log[-8000:] or "Machine labeling complete.",
        }

    except Exception:
        _redis.delete(pid_key, stop_key)
        raise RuntimeError(traceback.format_exc()[-3000:])

    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass


# ── Re-apply likelihood threshold ────────────────────────────────
@celery.task(bind=True, name="tasks.dlc_machine_label_reapply")
def dlc_machine_label_reapply(
    self,
    stem_dir: str,
    video_stem: str,
    scorer: str,
    bodyparts: list,
    threshold: float,
):
    """
    Re-parse the saved raw predictions HDF5 with a new likelihood threshold
    and rewrite CollectedData_<scorer>.csv/.h5, preserving human labels.
    Runs in the worker container where `tables` (pytables) is available.
    """
    import math as _math
    import csv as _csv_mod
    import json as _json
    import re as _re
    import pandas as _pd

    self.update_state(state="PROGRESS", meta={"progress": 10, "stage": "Loading raw predictions…"})

    stem_path = Path(stem_dir)
    raw_h5    = stem_path / "_machine_predictions_raw.h5"
    if not raw_h5.is_file():
        raise FileNotFoundError("No saved raw predictions found for this stem.")

    # Load metadata
    meta_file = stem_path / "_ml_frames.json"
    human_frames_set: set = set()
    if meta_file.is_file():
        try:
            meta = _json.loads(meta_file.read_text())
            human_frames_set = set(meta.get("human_frames", []))
        except Exception:
            pass

    df = _pd.read_hdf(str(raw_h5))

    self.update_state(state="PROGRESS", meta={"progress": 40, "stage": "Applying threshold…"})

    def _find_bp_cols(bp):
        mx, my, ml = [], [], []
        for col in df.columns:
            if col[-2] == bp:
                c = col[-1]
                if c == "x":                           mx.append(col)
                elif c == "y":                         my.append(col)
                elif c in ("likelihood", "p"):         ml.append(col)
        if mx and my:
            return mx[0], my[0], ml[0] if ml else None
        return None

    bp_col_map = {bp: _find_bp_cols(bp) for bp in bodyparts}

    machine_labels: dict = {}
    for idx, row in df.iterrows():
        img_name = Path(str(idx[-1] if isinstance(idx, tuple) else idx)).name
        frame_labels: dict = {}
        for bp in bodyparts:
            cols = bp_col_map.get(bp)
            if cols is None:
                frame_labels[bp] = None
                continue
            x_col, y_col, lk_col = cols
            try:
                x  = float(row[x_col])
                y  = float(row[y_col])
                lk = float(row[lk_col]) if lk_col else 1.0
                if _pd.isna(x) or _pd.isna(y) or lk < threshold:
                    frame_labels[bp] = None
                else:
                    frame_labels[bp] = [round(x, 4), round(y, 4)]
            except Exception:
                frame_labels[bp] = None
        machine_labels[img_name] = frame_labels

    # Load existing human labels from CSV
    csv_path   = stem_path / f"CollectedData_{scorer}.csv"
    human_labels: dict = {}
    if csv_path.is_file() and human_frames_set:
        try:
            with open(str(csv_path), newline="") as fh:
                rows = list(_csv_mod.reader(fh))
            if len(rows) >= 4:
                bp_row    = rows[1][3:]
                coord_row = rows[2][3:]
                col_pairs = list(zip(bp_row, coord_row))
                for row in rows[3:]:
                    if not row:
                        continue
                    img = row[2]
                    if img not in human_frames_set:
                        continue
                    bpmap: dict = {}
                    for (bp, c), v in zip(col_pairs, row[3:]):
                        bpmap.setdefault(bp, {})[c] = v
                    frame: dict = {}
                    for bp, cd in bpmap.items():
                        xs = cd.get("x", ""); ys = cd.get("y", "")
                        try:
                            xv = float(xs) if xs not in ("", "NaN", "nan") else None
                            yv = float(ys) if ys not in ("", "NaN", "nan") else None
                        except ValueError:
                            xv = yv = None
                        frame[bp] = [xv, yv] if xv is not None and yv is not None else None
                    human_labels[img] = frame
        except Exception:
            pass

    # Merge: for machine-labeled frames use machine labels directly;
    # for human-labeled frames prefer the human label per bodypart,
    # but fill in any missing (None) landmarks with the updated machine label.
    merged: dict = {}
    for fname in set(machine_labels) | set(human_labels):
        mframe = machine_labels.get(fname, {})
        hframe = human_labels.get(fname, {})
        if fname in human_frames_set:
            merged[fname] = {bp: (hframe.get(bp) if hframe.get(bp) is not None else mframe.get(bp))
                             for bp in bodyparts}
        else:
            merged[fname] = mframe

    def _nat_key(s):
        return [int(c) if c.isdigit() else c.lower() for c in _re.split(r"(\d+)", s)]

    frame_names = sorted(merged.keys(), key=_nat_key)

    self.update_state(state="PROGRESS", meta={"progress": 70, "stage": "Writing CollectedData…"})

    # Write CSV
    rows_out = [
        ["scorer",    "", ""] + [scorer] * (len(bodyparts) * 2),
        ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in range(2)],
        ["coords",    "", ""] + ["x", "y"] * len(bodyparts),
    ]
    for fname in frame_names:
        row = ["labeled-data", video_stem, fname]
        for bp in bodyparts:
            pt = merged.get(fname, {}).get(bp)
            if pt and len(pt) == 2 and pt[0] is not None:
                row.extend([str(round(pt[0], 4)), str(round(pt[1], 4))])
            else:
                row.extend(["NaN", "NaN"])
        rows_out.append(row)

    with open(str(csv_path), "w", newline="") as out:
        _csv_mod.writer(out).writerows(rows_out)

    # Write H5
    h5_path    = stem_path / f"CollectedData_{scorer}.h5"
    idx_tuples = [("labeled-data", video_stem, fn) for fn in frame_names]
    mi         = _pd.MultiIndex.from_tuples(idx_tuples, names=["", "", ""])
    col_tuples = [(scorer, bp, c) for bp in bodyparts for c in ("x", "y")]
    col_mi     = _pd.MultiIndex.from_tuples(col_tuples, names=["scorer", "bodyparts", "coords"])
    data_rows  = []
    for fname in frame_names:
        rv = []
        for bp in bodyparts:
            pt = merged.get(fname, {}).get(bp)
            if pt and len(pt) == 2 and pt[0] is not None:
                rv.extend([round(pt[0], 4), round(pt[1], 4)])
            else:
                rv.extend([_math.nan, _math.nan])
        data_rows.append(rv)
    _pd.DataFrame(data_rows, index=mi, columns=col_mi).to_hdf(
        str(h5_path), key="df_with_missing", mode="w"
    )

    # Update metadata
    n_machine = sum(1 for fn in merged if fn not in human_frames_set
                    and any(v is not None for v in merged[fn].values()))
    n_human   = len(human_frames_set & set(merged))
    try:
        meta_file.write_text(_json.dumps({
            "ml_frames":    [fn for fn in merged if fn not in human_frames_set],
            "human_frames": list(human_frames_set & set(merged)),
            "scorer":       scorer,
            "bodyparts":    bodyparts,
        }, indent=2))
    except Exception:
        pass

    return {
        "status":    "ok",
        "threshold": threshold,
        "n_machine": n_machine,
        "n_human":   n_human,
        "frames":    len(merged),
    }


# ── Main Celery Task ──────────────────────────────────────────────
@celery.task(bind=True, name="tasks.run_processing")
def run_processing(self, project_id: str, task_type: str = "anipose"):
    """
    Dispatcher task.  Routes to the correct pipeline based on `task_type`.
    """
    from anipose_tasks import _run_anipose, _run_deeplabcut

    DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
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
