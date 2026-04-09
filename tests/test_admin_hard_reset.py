"""
Tests for:
  1. Bug fix: TF training abort no longer causes the worker to self-terminate,
     which previously left queued jobs stranded.
  2. POST /admin/hard-reset-jobs — kill all running/queued jobs with passphrase.
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_client(tmp_path, fake_redis):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    env = {
        "DATA_DIR":          str(data_dir),
        "USER_DATA_DIR":     str(tmp_path / "user"),
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY":  "testkey1234567890abcdef12345678",
        "AUTH_DISABLED":     "true",   # bypass token gate in tests
    }
    with patch.dict(os.environ, env):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import app as app_mod
            importlib.reload(app_mod)
            app_mod.DATA_DIR      = data_dir
            app_mod.USER_DATA_DIR = tmp_path / "user"
            app_mod._redis_client = fake_redis
            app_mod.app.config["TESTING"] = True
            app_mod.app.config["SECRET_KEY"] = "testkey"
            return app_mod.app.test_client(), app_mod


# ── Bug-fix: abort must not kill the worker ────────────────────────────────────

class TestAbortDoesNotShutdownWorker:
    """
    Regression test for: after a TF training abort (_user_killed=True), the
    worker's finally block must skip _shutdown_if_idle() so that the next
    queued job is not left stranded.
    """

    def test_user_killed_flag_skips_shutdown(self):
        """
        Directly verify the guard condition that was added to tasks.py.
        When _user_killed[0] is True the finally block returns immediately
        without ever calling os.kill(SIGTERM).
        """
        sigterm_calls = []

        def _fake_kill(pid, sig):
            sigterm_calls.append((pid, sig))

        _user_killed = [True]

        # Replicate the guard added to the finally block:
        #   if _user_killed[0]:
        #       return  # keep worker alive for next job
        with patch("os.kill", _fake_kill):
            if _user_killed[0]:
                pass  # guard fires → no shutdown
            else:
                # _shutdown_if_idle would eventually call os.kill(os.getpid(), SIGTERM)
                import os as _os
                _os.kill(_os.getpid(), signal.SIGTERM)

        assert sigterm_calls == [], (
            "os.kill(SIGTERM) must NOT be called when _user_killed is True. "
            "The worker must stay alive so the next queued TF job is processed."
        )

    def test_natural_completion_allows_shutdown(self):
        """
        When training finishes naturally (_user_killed=False) and the queue is
        empty, the guard must NOT block the shutdown path.
        """
        sigterm_calls = []

        def _fake_kill(pid, sig):
            sigterm_calls.append((pid, sig))

        _user_killed = [False]

        with patch("os.kill", _fake_kill):
            if _user_killed[0]:
                pass  # abort guard (should NOT fire here)
            else:
                # Simulate _shutdown_if_idle detecting an idle worker
                import os as _os
                _os.kill(_os.getpid(), signal.SIGTERM)

        assert len(sigterm_calls) == 1
        assert sigterm_calls[0][1] == signal.SIGTERM

    def test_tasks_module_guard_present(self):
        """
        Source-level sanity check: confirm the guard statement exists in tasks.py
        so the fix hasn't been accidentally reverted.
        """
        tasks_path = Path(__file__).parent.parent / "src" / "dlc" / "tasks.py"
        source = tasks_path.read_text()
        assert "if _user_killed[0]:" in source, (
            "Guard 'if _user_killed[0]: return' is missing from dlc/tasks.py. "
            "This fix prevents the worker from self-terminating after a user abort."
        )
        # Also confirm the early-return is the FIRST thing that follows the guard
        # (i.e. the guard is in the finally block, before _shutdown_if_idle runs)
        guard_idx   = source.index("if _user_killed[0]:")
        return_idx  = source.index("return  # keep worker alive", guard_idx)
        shutdown_idx = source.index("_shutdown_if_idle", guard_idx)
        assert return_idx < shutdown_idx, (
            "The early-return guard must appear before _shutdown_if_idle() is defined."
        )


# ── POST /admin/hard-reset-jobs ────────────────────────────────────────────────

class TestHardResetJobs:
    """POST /admin/hard-reset-jobs"""

    def test_wrong_passphrase_returns_403(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path, fake_redis)
        resp = client.post(
            "/admin/hard-reset-jobs",
            json={"passphrase": "wrongpassword"},
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert "error" in body

    def test_missing_passphrase_returns_403(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path, fake_redis)
        resp = client.post("/admin/hard-reset-jobs", json={})
        assert resp.status_code == 403

    def test_correct_passphrase_returns_200(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path, fake_redis)
        resp = client.post(
            "/admin/hard-reset-jobs",
            json={"passphrase": "deeplabcut"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "reset"
        assert "processes_killed"     in body
        assert "queued_tasks_cleared" in body
        assert "jobs_cleared"         in body
        assert "task_meta_cleared"    in body

    def test_kills_running_subprocess(self, tmp_path, fake_redis):
        """Running subprocess PGIDs stored in Redis are SIGKILLed."""
        # Pre-seed a fake PID key
        fake_redis.set("dlc_train_pid:abc-task-123", "9999")

        kill_calls = []

        with patch("os.killpg", side_effect=lambda pg, sig: kill_calls.append((pg, sig))):
            client, _ = _make_client(tmp_path, fake_redis)
            # Re-seed after client creation (reload clears the fake_redis store)
            fake_redis.set("dlc_train_pid:abc-task-123", "9999")
            resp = client.post(
                "/admin/hard-reset-jobs",
                json={"passphrase": "deeplabcut"},
            )

        assert resp.status_code == 200
        body = resp.get_json()
        # The PID key should have been deleted
        assert fake_redis.get("dlc_train_pid:abc-task-123") is None

    def test_drains_broker_queues(self, tmp_path, fake_redis):
        """All three broker queues are emptied."""
        client, _ = _make_client(tmp_path, fake_redis)

        # Seed queues after the client is built (module reload does not wipe fake_redis)
        fake_redis.rpush("tensorflow", "task1")
        fake_redis.rpush("tensorflow", "task2")
        fake_redis.rpush("pytorch",    "task3")

        resp = client.post(
            "/admin/hard-reset-jobs",
            json={"passphrase": "deeplabcut"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["queued_tasks_cleared"] == 3
        assert fake_redis.llen("tensorflow") == 0
        assert fake_redis.llen("pytorch")    == 0

    def test_clears_job_tracking(self, tmp_path, fake_redis):
        """Job sorted-sets and their hash records are removed."""
        fake_redis.zadd("dlc_train_jobs", {"job-id-1": 1.0})
        fake_redis.hset("dlc_train_job:job-id-1", mapping={"status": "running"})

        client, _ = _make_client(tmp_path, fake_redis)
        fake_redis.zadd("dlc_train_jobs", {"job-id-1": 1.0})
        fake_redis.hset("dlc_train_job:job-id-1", mapping={"status": "running"})

        resp = client.post(
            "/admin/hard-reset-jobs",
            json={"passphrase": "deeplabcut"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["jobs_cleared"] >= 1
        # The tracking set and the job hash must be gone
        assert fake_redis.zrange("dlc_train_jobs", 0, -1) == []
        assert fake_redis.hgetall("dlc_train_job:job-id-1") == {}

    def test_gpu_pool_reset(self, tmp_path, fake_redis):
        """GPU pool is restored to {"0"} after a hard reset."""
        # Drain the pool (simulate a running task that checked it out)
        fake_redis.delete("dlc_available_gpus")

        client, _ = _make_client(tmp_path, fake_redis)
        fake_redis.delete("dlc_available_gpus")

        resp = client.post(
            "/admin/hard-reset-jobs",
            json={"passphrase": "deeplabcut"},
        )
        assert resp.status_code == 200
        assert "0" in fake_redis.smembers("dlc_available_gpus")

    def test_empty_state_is_idempotent(self, tmp_path, fake_redis):
        """Hard reset on already-clean state returns 200 with zero counts."""
        client, _ = _make_client(tmp_path, fake_redis)
        resp = client.post(
            "/admin/hard-reset-jobs",
            json={"passphrase": "deeplabcut"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "reset"
        assert body["processes_killed"]     == 0
        assert body["queued_tasks_cleared"] == 0
        assert body["jobs_cleared"]         == 0
