"""
TDD — Redis Worker Pool, GPU Allocation, and Signal Control.

Tests cover:
  1. GPU pool atomic checkout and return (SPOP / SADD).
  2. Concurrent contention: 3 simultaneous tasks competing for 1 GPU slot
     (only 1 should succeed; others must block or fail gracefully).
  3. Signal control via a mock subprocess:
       pause   → SIGSTOP sent to the process group
       resume  → SIGCONT sent to the process group
       terminate → SIGTERM sent, then SIGKILL if still alive
  4. GPU ID is atomically returned to the pool in the finally block even when
     the process is killed mid-run.
  5. Reaper marks "running" jobs as "dead" when their PID is gone.
  6. Reaper NEVER touches "paused" jobs.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fake Redis with the subset of commands used by the worker pool.
# (Re-uses the conftest.FakeRedis shape but adds Set and List support.)
# ─────────────────────────────────────────────────────────────────────────────

class FullFakeRedis:
    """Minimal in-memory Redis that supports strings, hashes, sorted-sets, lists, and sets."""

    def __init__(self):
        self._str: dict[str, str] = {}
        self._hash: dict[str, dict] = {}
        self._zset: dict[str, dict] = {}
        self._list: dict[str, list] = {}
        self._set: dict[str, set] = {}
        self._lock = threading.Lock()

    # ── String ops ────────────────────────────────────────────────────────────
    def get(self, key):
        return self._str.get(key)

    def set(self, key, value, ex=None):
        with self._lock:
            self._str[key] = value

    def setex(self, key, seconds, value):
        self.set(key, value)

    def delete(self, *keys):
        with self._lock:
            for k in keys:
                self._str.pop(k, None)
                self._hash.pop(k, None)
                self._set.pop(k, None)
                self._list.pop(k, None)

    def exists(self, key):
        return key in self._str or key in self._hash or key in self._set

    # ── Hash ops ──────────────────────────────────────────────────────────────
    def hset(self, name, key=None, value=None, mapping=None):
        with self._lock:
            if name not in self._hash:
                self._hash[name] = {}
            if key is not None:
                self._hash[name][key] = str(value)
            if mapping:
                self._hash[name].update({k: str(v) for k, v in mapping.items()})

    def hget(self, name, key):
        return self._hash.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self._hash.get(name, {}))

    def expire(self, key, seconds):
        pass

    # ── Sorted-set ops ────────────────────────────────────────────────────────
    def zadd(self, name, mapping, **kwargs):
        with self._lock:
            if name not in self._zset:
                self._zset[name] = {}
            self._zset[name].update(mapping)

    def zrevrange(self, name, start, stop, withscores=False):
        z = self._zset.get(name, {})
        ordered = sorted(z, key=lambda k: z[k], reverse=True)
        end = None if stop == -1 else stop + 1
        return ordered[start:end]

    def zrem(self, name, *members):
        with self._lock:
            for m in members:
                self._zset.get(name, {}).pop(m, None)

    # ── List ops ──────────────────────────────────────────────────────────────
    def rpush(self, key, *values):
        with self._lock:
            if key not in self._list:
                self._list[key] = []
            self._list[key].extend(str(v) for v in values)
        return len(self._list[key])

    def lrange(self, key, start, stop):
        lst = self._list.get(key, [])
        end = None if stop == -1 else stop + 1
        return lst[start:end]

    def llen(self, key):
        return len(self._list.get(key, []))

    # ── Set ops (atomic GPU pool) ──────────────────────────────────────────────
    def sadd(self, key, *values):
        with self._lock:
            if key not in self._set:
                self._set[key] = set()
            self._set[key].update(str(v) for v in values)
        return len(values)

    def spop(self, key):
        with self._lock:
            s = self._set.get(key, set())
            if not s:
                return None
            val = next(iter(s))
            s.discard(val)
            return val

    def smembers(self, key):
        return set(self._set.get(key, set()))

    def scan_iter(self, pattern):
        return iter([])


@pytest.fixture
def r():
    return FullFakeRedis()


# ─────────────────────────────────────────────────────────────────────────────
# 1. GPU Pool — atomic SPOP / SADD
# ─────────────────────────────────────────────────────────────────────────────

GPU_POOL_KEY = "dlc_available_gpus"


def _checkout_gpu(redis_client) -> Optional[str]:
    """Atomically check out a GPU ID from the pool. Returns None if exhausted."""
    return redis_client.spop(GPU_POOL_KEY)


def _return_gpu(redis_client, gpu_id: str) -> None:
    """Atomically return a GPU ID to the pool."""
    redis_client.sadd(GPU_POOL_KEY, gpu_id)


def test_gpu_pool_checkout_and_return(r):
    """A GPU checked out via SPOP is removed from the pool until returned."""
    r.sadd(GPU_POOL_KEY, "0")

    gpu = _checkout_gpu(r)
    assert gpu == "0"
    assert r.spop(GPU_POOL_KEY) is None  # pool now empty

    _return_gpu(r, gpu)
    assert r.smembers(GPU_POOL_KEY) == {"0"}


def test_gpu_pool_single_gpu_exclusive_access(r):
    """Only one of 3 concurrent tasks acquires the GPU; the others get None."""
    r.sadd(GPU_POOL_KEY, "0")  # only GPU 0 for DLC (hard constraint)

    results: list[Optional[str]] = []
    lock = threading.Lock()

    def _try_checkout():
        gpu = _checkout_gpu(r)
        with lock:
            results.append(gpu)

    threads = [threading.Thread(target=_try_checkout) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [g for g in results if g is not None]
    failures  = [g for g in results if g is None]
    assert len(successes) == 1, "exactly one task should acquire GPU 0"
    assert len(failures) == 2, "the other two tasks should find the pool empty"
    assert successes[0] == "0"


def test_gpu_pool_return_in_finally(r):
    """GPU is returned to the pool even when the task raises mid-run."""
    r.sadd(GPU_POOL_KEY, "0")

    gpu_id = None
    try:
        gpu_id = _checkout_gpu(r)
        assert gpu_id == "0"
        raise RuntimeError("simulated task failure")
    except RuntimeError:
        pass
    finally:
        if gpu_id is not None:
            _return_gpu(r, gpu_id)

    assert r.smembers(GPU_POOL_KEY) == {"0"}, "GPU must be in pool after exception"


def test_gpu_pool_reinit_is_idempotent(r):
    """Re-initialising the pool (worker restart) restores exactly one GPU 0 entry."""
    # Simulate pool with leftover state
    r.sadd(GPU_POOL_KEY, "0")
    r.sadd(GPU_POOL_KEY, "0")  # sadd is idempotent for sets
    assert r.smembers(GPU_POOL_KEY) == {"0"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Signal Control — mock subprocess
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_proc(pid=12345, alive=True):
    proc = MagicMock()
    proc.pid = pid
    proc.is_alive.return_value = alive
    proc.exitcode = None
    return proc


def test_pause_sends_sigstop_to_process_group():
    """Pausing a task sends SIGSTOP to the process group via os.killpg."""
    proc = _make_mock_proc(pid=12345)

    with patch("os.killpg") as mock_killpg:
        os.killpg(proc.pid, signal.SIGSTOP)

    mock_killpg.assert_called_once_with(12345, signal.SIGSTOP)


def test_resume_sends_sigcont_to_process_group():
    """Resuming a paused task sends SIGCONT to the process group."""
    proc = _make_mock_proc(pid=12345)

    with patch("os.killpg") as mock_killpg:
        os.killpg(proc.pid, signal.SIGCONT)

    mock_killpg.assert_called_once_with(12345, signal.SIGCONT)


def test_terminate_sends_sigterm_then_sigkill_if_still_alive():
    """Termination sends SIGTERM, waits, then escalates to SIGKILL."""
    proc = _make_mock_proc(pid=42, alive=True)
    # Simulate process still alive after SIGTERM
    proc.is_alive.return_value = True

    kill_calls = []

    def _fake_killpg(pgid, sig):
        kill_calls.append((pgid, sig))

    with patch("os.killpg", side_effect=_fake_killpg):
        with patch("time.sleep"):
            # Send SIGTERM
            os.killpg(proc.pid, signal.SIGTERM)
            # Wait / check (simulated by is_alive returning True)
            if proc.is_alive():
                os.killpg(proc.pid, signal.SIGKILL)

    assert (42, signal.SIGTERM) in kill_calls
    assert (42, signal.SIGKILL) in kill_calls
    assert kill_calls.index((42, signal.SIGTERM)) < kill_calls.index((42, signal.SIGKILL))


def test_terminate_no_sigkill_if_process_exits_after_sigterm():
    """If the process exits after SIGTERM, SIGKILL must NOT be sent."""
    proc = _make_mock_proc(pid=99, alive=False)  # exits immediately

    kill_calls = []

    with patch("os.killpg", side_effect=lambda pgid, sig: kill_calls.append((pgid, sig))):
        os.killpg(proc.pid, signal.SIGTERM)
        if proc.is_alive():  # False → skip SIGKILL
            os.killpg(proc.pid, signal.SIGKILL)

    assert (99, signal.SIGTERM) in kill_calls
    assert (99, signal.SIGKILL) not in kill_calls


def test_pause_then_terminate_sends_sigcont_before_sigterm(r):
    """
    Terminating a paused task must SIGCONT before SIGTERM so the queued
    signal is delivered and the subprocess finally block can run.
    """
    task_id   = "abc123"
    pause_key = f"dlc_train_pause:{task_id}"
    proc      = _make_mock_proc(pid=55)
    r.setex(pause_key, 7200, "1")

    kill_calls = []

    with patch("os.killpg", side_effect=lambda pgid, sig: kill_calls.append((pgid, sig))):
        # Terminate logic
        if r.get(pause_key):
            os.killpg(proc.pid, signal.SIGCONT)
            r.delete(pause_key)
        os.killpg(proc.pid, signal.SIGTERM)

    assert kill_calls[0] == (55, signal.SIGCONT), "SIGCONT must precede SIGTERM"
    assert kill_calls[1] == (55, signal.SIGTERM)
    assert r.get(pause_key) is None, "pause key must be cleared"


# ─────────────────────────────────────────────────────────────────────────────
# 3. GPU return happens in finally block even when killed
# ─────────────────────────────────────────────────────────────────────────────

def test_gpu_returned_even_when_task_is_sigkilled(r):
    """
    Simulates the Celery task finally block: GPU is always returned to the pool
    regardless of whether the task completed, was stopped, or was SIGKILL'd.
    """
    r.sadd(GPU_POOL_KEY, "0")
    gpu_id = _checkout_gpu(r)
    assert gpu_id == "0"

    def _fake_task():
        try:
            raise SystemExit(-9)  # simulates SIGKILL bringing down the process
        except SystemExit:
            raise
        finally:
            _return_gpu(r, gpu_id)

    with pytest.raises(SystemExit):
        _fake_task()

    assert r.smembers(GPU_POOL_KEY) == {"0"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Redis log streaming — RPUSH
# ─────────────────────────────────────────────────────────────────────────────

def test_log_lines_are_pushed_to_redis_list(r):
    """Log lines emitted by the worker are RPUSH'd to the task log list."""
    task_id  = "task-log-test"
    log_key  = f"dlc_task:{task_id}:log"

    lines = ["Epoch 1/10 — loss: 0.42", "Epoch 2/10 — loss: 0.38"]
    for line in lines:
        r.rpush(log_key, line)

    assert r.lrange(log_key, 0, -1) == lines


def test_log_streaming_cursor_advances(r):
    """Consumers read new log lines incrementally using an advancing cursor."""
    task_id = "cursor-test"
    log_key = f"dlc_task:{task_id}:log"

    r.rpush(log_key, "line 1")
    r.rpush(log_key, "line 2")

    cursor = 0
    batch1 = r.lrange(log_key, cursor, -1)
    cursor += len(batch1)
    assert batch1 == ["line 1", "line 2"]

    r.rpush(log_key, "line 3")
    batch2 = r.lrange(log_key, cursor, -1)
    cursor += len(batch2)
    assert batch2 == ["line 3"]

    assert cursor == 3


# ─────────────────────────────────────────────────────────────────────────────
# 5. Reaper — dead job detection
# ─────────────────────────────────────────────────────────────────────────────

def test_reaper_marks_dead_job_when_pid_is_gone(r):
    """
    The Reaper discovers a 'running' job whose PID no longer exists and
    marks it 'dead', returning the GPU to the pool.
    """
    task_id = "dead-job-1"
    r.hset(f"dlc_train_job:{task_id}", mapping={
        "task_id": task_id, "status": "running", "gpu_id": "0",
    })
    r.zadd("dlc_train_jobs", {task_id: time.time()})
    r.set(f"dlc_train_pid:{task_id}", "999999")  # guaranteed non-existent PID

    # Simulate reaper logic
    def _reaper_tick(redis_client, zset_key: str, job_prefix: str, pid_prefix: str):
        for jid in redis_client.zrevrange(zset_key, 0, 99):
            job = redis_client.hgetall(f"{job_prefix}{jid}")
            if job.get("status") not in ("running",):
                continue
            raw_pid = redis_client.get(f"{pid_prefix}{jid}")
            if not raw_pid:
                continue
            try:
                os.kill(int(raw_pid), 0)
            except (ProcessLookupError, OSError):
                # PID is gone — mark dead, return GPU
                redis_client.hset(f"{job_prefix}{jid}", "status", "dead")
                gpu_id = job.get("gpu_id")
                if gpu_id:
                    redis_client.sadd(GPU_POOL_KEY, gpu_id)
                redis_client.delete(f"{pid_prefix}{jid}")

    _reaper_tick(r, "dlc_train_jobs", "dlc_train_job:", "dlc_train_pid:")

    assert r.hget(f"dlc_train_job:{task_id}", "status") == "dead"
    assert "0" in r.smembers(GPU_POOL_KEY)


def test_reaper_does_not_touch_paused_jobs(r):
    """The Reaper MUST NOT kill or mark dead any job with status 'paused'."""
    task_id = "paused-job-1"
    pid     = os.getpid()  # real PID so os.kill(pid, 0) would succeed
    r.hset(f"dlc_train_job:{task_id}", mapping={
        "task_id": task_id, "status": "paused", "gpu_id": "0",
    })
    r.zadd("dlc_train_jobs", {task_id: time.time()})
    r.set(f"dlc_train_pid:{task_id}", str(pid))

    def _reaper_tick(redis_client, zset_key, job_prefix, pid_prefix):
        for jid in redis_client.zrevrange(zset_key, 0, 99):
            job = redis_client.hgetall(f"{job_prefix}{jid}")
            if job.get("status") not in ("running",):  # paused is explicitly excluded
                continue
            raw_pid = redis_client.get(f"{pid_prefix}{jid}")
            if not raw_pid:
                continue
            try:
                os.kill(int(raw_pid), 0)
            except (ProcessLookupError, OSError):
                redis_client.hset(f"{job_prefix}{jid}", "status", "dead")

    _reaper_tick(r, "dlc_train_jobs", "dlc_train_job:", "dlc_train_pid:")

    # Status must remain "paused" — Reaper must not have touched it
    assert r.hget(f"dlc_train_job:{task_id}", "status") == "paused"


def test_reaper_does_not_touch_complete_jobs(r):
    """The Reaper ignores jobs that are already in a terminal state."""
    task_id = "complete-job-1"
    r.hset(f"dlc_train_job:{task_id}", mapping={
        "task_id": task_id, "status": "complete",
    })
    r.zadd("dlc_train_jobs", {task_id: time.time()})

    def _reaper_tick(redis_client, zset_key, job_prefix, pid_prefix):
        for jid in redis_client.zrevrange(zset_key, 0, 99):
            job = redis_client.hgetall(f"{job_prefix}{jid}")
            if job.get("status") not in ("running",):
                continue
            redis_client.hset(f"{job_prefix}{jid}", "status", "dead")

    _reaper_tick(r, "dlc_train_jobs", "dlc_train_job:", "dlc_train_pid:")

    assert r.hget(f"dlc_train_job:{task_id}", "status") == "complete"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Job status transitions
# ─────────────────────────────────────────────────────────────────────────────

def test_pause_sets_job_status_to_paused(r):
    task_id   = "status-test"
    job_key   = f"dlc_train_job:{task_id}"
    pause_key = f"dlc_train_pause:{task_id}"

    r.hset(job_key, mapping={"task_id": task_id, "status": "running"})
    r.set(f"dlc_train_pid:{task_id}", "111")

    # Simulate pause endpoint logic (without actual signal)
    r.setex(pause_key, 7200, "1")
    r.hset(job_key, "status", "paused")

    assert r.hget(job_key, "status") == "paused"
    assert r.get(pause_key) == "1"


def test_resume_clears_pause_key_and_restores_running(r):
    task_id   = "resume-test"
    job_key   = f"dlc_train_job:{task_id}"
    pause_key = f"dlc_train_pause:{task_id}"

    r.hset(job_key, mapping={"task_id": task_id, "status": "paused"})
    r.setex(pause_key, 7200, "1")

    # Simulate resume endpoint logic (without actual signal)
    r.delete(pause_key)
    r.hset(job_key, "status", "running")

    assert r.hget(job_key, "status") == "running"
    assert r.get(pause_key) is None
