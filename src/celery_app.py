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
    On worker (re)start, scan Redis for any dlc_*_pid keys left over from a
    previous worker crash.  Those child processes are orphaned — they still hold
    CUDA contexts and will cause GPU hangs if not killed.
    """
    import signal as _sig
    import redis as _redis_mod
    _r = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )
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
