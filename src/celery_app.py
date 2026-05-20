"""
Celery application instance — shared by all task modules.
Import `celery` from here rather than from tasks.py to avoid circular imports.
"""
import os
from celery import Celery
from celery.signals import worker_ready, worker_process_init

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
    # Redis broker visibility_timeout: how long a delivered-but-unacked message
    # waits before the broker re-queues it on the assumption that the consumer
    # died. Default is 3600s (1 h) — far too short for DLC training runs that
    # routinely take 3-6 h. When the timeout expires mid-training, the broker
    # silently re-publishes the message; the moment the original task finishes,
    # the worker picks up the duplicate and kicks off a SECOND training run
    # from epoch 1 on the same GPU. Set well above any expected task runtime.
    # (Per-task acks_late=False on dlc_train_network is not enough on its own —
    # if a missed-heartbeat or connection blip drops the early ack, the broker
    # still re-publishes once the visibility window closes.)
    broker_transport_options={"visibility_timeout": 86400},  # 24 h
)


def _kill_orphaned_dlc_subprocesses(redis_client) -> None:
    """Kill any DLC subprocess PIDs left over from a previous worker (child) crash.
    Called from worker_ready (main process startup, container start) AND from
    worker_process_init (each prefork child start, including replacement after
    a hard-time-limit kill). Idempotent: safe to call multiple times."""
    import signal as _sig
    prefixes = ("dlc_train_pid:*", "dlc_analyze_pid:*", "dlc_ml_pid:*")
    for pattern in prefixes:
        for key in redis_client.scan_iter(pattern):
            try:
                pid = int(redis_client.get(key) or 0)
                if pid:
                    try:
                        os.killpg(pid, _sig.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
            except (ValueError, TypeError):
                pass
            redis_client.delete(key)


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
    import threading as _threading
    import redis as _redis_mod

    _r = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )

    # ── 1. Kill orphaned subprocesses ─────────────────────────────────────────
    _kill_orphaned_dlc_subprocesses(_r)

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


@worker_process_init.connect
def _prefork_child_init(sender=None, **kwargs):
    """Per-child initialisation for prefork pool workers.

    1. Allow the worker child to spawn its own subprocesses.
       Celery's prefork (billiard) marks each worker child as a daemon
       process so the OS reaps it on master exit. But Python's
       multiprocessing.Process.start() asserts that daemon processes
       cannot have children — which breaks every dlc_*_subprocess spawn
       in tasks.py (train / analyze / machine-label / labeled-video all
       call mp.get_context('spawn').Process(daemon=False).start() and
       hit the assertion). Flip the flag back to non-daemon: in Docker
       the container's PID-1 init handles orphan reaping, so we don't
       need Python's daemon semantic for cleanup. The _config attr is
       private but stable across Python 3.x and is the documented
       Celery+multiprocessing workaround.

    2. Clean up orphan DLC subprocess PIDs left over from a previous
       child. This catches the case where the previous worker child
       was SIGKILLed by Celery's hard task_time_limit, leaving the
       spawned training/analyze subprocess orphaned and still holding
       the GPU.
    """
    # 1. Un-daemonise the worker child so mp.Process.start() will work
    import multiprocessing as _mp
    try:
        _mp.current_process()._config["daemon"] = False
    except (AttributeError, KeyError):
        pass  # private API — degrade gracefully if Python ever changes it

    # 2. Orphan cleanup
    import redis as _redis_mod
    _r = _redis_mod.Redis.from_url(
        os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        decode_responses=True,
    )
    _kill_orphaned_dlc_subprocesses(_r)
