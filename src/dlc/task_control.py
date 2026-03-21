"""
DLC Task Control Blueprint — pause, resume, terminate running DLC tasks.

Routes:
  POST /dlc/task/<task_id>/pause       Freeze subprocess with SIGSTOP
  POST /dlc/task/<task_id>/resume      Unfreeze subprocess with SIGCONT
  POST /dlc/task/<task_id>/terminate   SIGCONT (if paused) then SIGTERM→SIGKILL
  GET  /dlc/task/<task_id>/log-stream  Server-Sent Events: live log lines

Redis keys used:
  dlc_train_pid:<task_id>      | dlc_analyze_pid:<task_id>    — subprocess PGID
  dlc_train_pause:<task_id>    | dlc_analyze_pause:<task_id>  — pause flag
  dlc_train_stop:<task_id>     | dlc_analyze_stop:<task_id>   — stop flag (emit_loop checks)
  dlc_train_job:<task_id>      | dlc_analyze_job:<task_id>    — job hash (status field)
  dlc_task:<task_id>:log                                       — Redis list of log lines (SSE feed)
"""
from __future__ import annotations

import os
import signal
import time

from flask import Blueprint, Response, jsonify, stream_with_context

from . import ctx as _ctx

bp = Blueprint("dlc_task_control", __name__)

# ── Lookup tables keyed by namespace order (train first, analyze second) ──────
_PID_PREFIXES   = ("dlc_train_pid:",   "dlc_analyze_pid:")
_JOB_PREFIXES   = ("dlc_train_job:",   "dlc_analyze_job:")
_STOP_PREFIXES  = ("dlc_train_stop:",  "dlc_analyze_stop:")
_PAUSE_PREFIXES = ("dlc_train_pause:", "dlc_analyze_pause:")

_TERMINAL_STATUSES = {"complete", "failed", "stopped", "dead"}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_task(
    task_id: str,
) -> tuple[int | None, str | None, str | None, str | None]:
    """
    Return (pid, job_key, stop_key, pause_key) for the given task_id.
    Searches the train namespace first, then the analyze namespace.
    Returns (None, None, None, None) when the task is not found or has no PID
    (i.e. it has already finished and its PID key has been deleted).
    """
    r = _ctx.redis_client()
    for pid_pfx, job_pfx, stop_pfx, pause_pfx in zip(
        _PID_PREFIXES, _JOB_PREFIXES, _STOP_PREFIXES, _PAUSE_PREFIXES
    ):
        raw = r.get(pid_pfx + task_id)
        if raw:
            try:
                return int(raw), job_pfx + task_id, stop_pfx + task_id, pause_pfx + task_id
            except (ValueError, TypeError):
                pass
    return None, None, None, None


def _update_job_status(job_key: str, status: str) -> None:
    r = _ctx.redis_client()
    if r.exists(job_key):
        r.hset(job_key, "status", status)


# ─────────────────────────────────────────────────────────────────────────────
# Control endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/dlc/task/<task_id>/pause", methods=["POST"])
def task_pause(task_id: str):
    """
    Freeze the DLC subprocess with SIGSTOP.

    SIGSTOP cannot be caught or ignored — it immediately suspends the entire
    process group (DLC + PyTorch DataLoader workers).  The Celery emit_loop
    continues running in the parent worker process and keeps the job TTL alive.
    """
    pid, job_key, _, pause_key = _resolve_task(task_id)
    if pid is None:
        return jsonify({"error": "Task not found or no longer running."}), 404

    r = _ctx.redis_client()
    try:
        os.killpg(pid, signal.SIGSTOP)
    except (ProcessLookupError, OSError) as exc:
        return jsonify({"error": f"Could not pause process group {pid}: {exc}"}), 500

    r.setex(pause_key, 7200, "1")
    _update_job_status(job_key, "paused")
    return jsonify({"status": "paused", "task_id": task_id})


@bp.route("/dlc/task/<task_id>/resume", methods=["POST"])
def task_resume(task_id: str):
    """Unfreeze the DLC subprocess with SIGCONT and mark the job as running."""
    pid, job_key, _, pause_key = _resolve_task(task_id)
    if pid is None:
        return jsonify({"error": "Task not found or no longer running."}), 404

    r = _ctx.redis_client()
    try:
        os.killpg(pid, signal.SIGCONT)
    except (ProcessLookupError, OSError) as exc:
        return jsonify({"error": f"Could not resume process group {pid}: {exc}"}), 500

    r.delete(pause_key)
    _update_job_status(job_key, "running")
    return jsonify({"status": "running", "task_id": task_id})


@bp.route("/dlc/task/<task_id>/terminate", methods=["POST"])
def task_terminate(task_id: str):
    """
    Terminate a running or paused DLC task.

    Sequence:
      1. If the task is paused: send SIGCONT first so the process can
         receive the subsequent SIGTERM (a queued SIGTERM is not delivered
         to a SIGSTOP'd process until it is resumed).
      2. Set the Redis stop flag.  The Celery worker's emit_loop will pick
         this up within ~3 s and perform SIGTERM → wait(12 s) → SIGKILL,
         then clean up all Redis state atomically.

    This preserves the existing cleanup path (GPU pool return, job hash
    deletion, PID key deletion) that already lives in the emit_loop.
    """
    pid, job_key, stop_key, pause_key = _resolve_task(task_id)
    if pid is None:
        return jsonify({"error": "Task not found or no longer running."}), 404

    r = _ctx.redis_client()

    # Step 1 — unfreeze if paused so SIGTERM is deliverable
    if r.get(pause_key):
        try:
            os.killpg(pid, signal.SIGCONT)
        except (ProcessLookupError, OSError):
            pass
        r.delete(pause_key)

    # Step 2 — set stop flag; emit_loop handles SIGTERM → SIGKILL + cleanup
    r.setex(stop_key, 120, "1")
    _update_job_status(job_key, "stopping")
    return jsonify({"status": "terminating", "task_id": task_id})


# ─────────────────────────────────────────────────────────────────────────────
# SSE log streaming
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/dlc/task/<task_id>/log-stream")
def task_log_stream(task_id: str):
    """
    Server-Sent Events endpoint that streams live log output for a task.

    The Celery worker's emit_loop RPUSH's log lines to the Redis list
    ``dlc_task:<task_id>:log`` every ~3 seconds.  This generator reads
    that list incrementally using a cursor and yields each new line as an
    SSE ``data:`` frame.

    The stream closes automatically when the job reaches a terminal state
    AND no more new lines appear for two consecutive poll cycles.
    """
    r = _ctx.redis_client()
    log_key = f"dlc_task:{task_id}:log"

    def _generate():
        cursor = 0
        idle_after_terminal = 0

        while True:
            # Read all log lines since last cursor position
            new_lines = r.lrange(log_key, cursor, -1)
            if new_lines:
                idle_after_terminal = 0
                for line in new_lines:
                    yield f"data: {line}\n\n"
                cursor += len(new_lines)

            # Check terminal state
            status = None
            for pfx in ("dlc_train_job:", "dlc_analyze_job:"):
                status = r.hget(pfx + task_id, "status")
                if status:
                    break

            if status in _TERMINAL_STATUSES:
                if not new_lines:
                    idle_after_terminal += 1
                if idle_after_terminal >= 2:
                    yield "event: done\ndata: {}\n\n"
                    return

            time.sleep(1)

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
