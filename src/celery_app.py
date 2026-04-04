"""
Celery application instance — shared by all task modules.
Import `celery` from here rather than from tasks.py to avoid circular imports.
"""
import os
from celery import Celery
from celery.signals import worker_ready

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


# ── Worker startup: kill stale GPU processes from a previous crash ─
@worker_ready.connect
def _kill_stale_gpu_processes(sender, **kwargs):
    """
    On worker (re)start:
      1. Kill any orphaned DLC subprocess PIDs left over from a previous crash.
         Those processes still hold CUDA contexts and cause GPU hangs.
      2. Re-initialise the GPU pool — dlc_available_gpus = {"0"}.
         GPU 0 = RTX 5090 (ONLY GPU used by DLC tasks; GPU 1 is NEVER touched).
      3. Start the Reaper background thread.
    """
    import signal as _sig
    import threading as _threading
    import redis as _redis_mod

    _r = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )

    # ── 1. Kill orphaned subprocesses ─────────────────────────────────────────
    prefixes = ("dlc_train_pid:*", "dlc_analyze_pid:*", "dlc_ml_pid:*")
    for pattern in prefixes:
        for key in _r.scan_iter(pattern):
            try:
                pid = int(_r.get(key) or 0)
                if pid:
                    try:
                        os.killpg(pid, _sig.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
            except (ValueError, TypeError):
                pass
            _r.delete(key)

    # ── 2. Re-initialise GPU pool ─────────────────────────────────────────────
    # Hard constraint: GPU 0 = RTX 5090 for ALL DLC tasks.
    # GPU 1 = Blackwell A6000 is reserved for LLM/orchestrator — never add it here.
    _r.delete("dlc_available_gpus")
    _r.sadd("dlc_available_gpus", "0")

    # ── 3. Start the Reaper ───────────────────────────────────────────────────
    def _reaper_loop():
        """
        Background daemon that wakes every 30 s and detects jobs whose
        subprocess PID has vanished without proper cleanup (e.g. OOM kill,
        SIGKILL from outside, container restart).

        For each such "running" job:
          - Marks its status "dead" in Redis.
          - Returns the checked-out GPU ID to dlc_available_gpus so the
            next task can proceed without hitting an empty pool.

        Safety guarantee: paused jobs are NEVER touched.  Their PID is
        intentionally kept alive (just SIGSTOP'd), so os.kill(pid, 0)
        succeeds and the Reaper leaves them alone.
        """
        import os as _os_
        import time as _t_

        _ZSETS = [
            ("dlc_train_jobs",   "dlc_train_job:",   "dlc_train_pid:"),
            ("dlc_analyze_jobs", "dlc_analyze_job:", "dlc_analyze_pid:"),
        ]

        while True:
            _t_.sleep(30)
            try:
                for zset_key, job_pfx, pid_pfx in _ZSETS:
                    for jid in _r.zrevrange(zset_key, 0, 99):
                        job = _r.hgetall(f"{job_pfx}{jid}")
                        status = job.get("status", "")

                        # Only reap actively "running" jobs.
                        # "paused" jobs have a live (but frozen) PID — skip them.
                        if status != "running":
                            continue

                        raw_pid = _r.get(f"{pid_pfx}{jid}")
                        if not raw_pid:
                            continue

                        try:
                            pid = int(raw_pid)
                            _os_.kill(pid, 0)   # 0 = probe: raises if PID gone
                        except (ProcessLookupError, OSError):
                            # PID is gone — mark dead and return GPU
                            _r.hset(f"{job_pfx}{jid}", "status", "dead")
                            gpu_id = job.get("gpu_id")
                            if gpu_id:
                                _r.sadd("dlc_available_gpus", gpu_id)
                            _r.delete(f"{pid_pfx}{jid}")
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass  # never let the reaper die on a transient error

    _threading.Thread(target=_reaper_loop, daemon=True, name="dlc-reaper").start()
