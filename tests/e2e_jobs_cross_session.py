"""Playwright cross-session E2E tests for the /jobs page.

These tests use TWO independent browser contexts to simulate "a different
browser session entirely" and assert that a job seeded in Redis from the
test process is visible / stoppable from any context.

Skipped if Playwright isn't installed or the live stack isn't reachable.
"""
from __future__ import annotations

import os
import time

import pytest
import redis as _redis_mod

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:5000")
APP_TOKEN = os.environ.get("APP_TOKEN", "deeplabcut")


@pytest.fixture(scope="session")
def live_redis():
    """A real Redis connection — required because the SSE log-stream and
    /dlc/training/jobs both read live Redis state in the running flask
    container. Skips the test session if Redis isn't reachable."""
    url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    r = _redis_mod.Redis.from_url(url, decode_responses=True)
    try:
        r.ping()
    except _redis_mod.ConnectionError:
        pytest.skip(f"Redis not reachable at {url}")
    yield r


def seed_test_job(r, task_id: str, *, op: str = "train",
                  status: str = "running", project: str = "test-project",
                  gpu_id: str = "0") -> None:
    """Mirror the shape that worker emit_loop writes for train/analyze."""
    zset = "dlc_train_jobs" if op == "train" else "dlc_analyze_jobs"
    job_pfx = "dlc_train_job:" if op == "train" else "dlc_analyze_job:"
    r.zadd(zset, {task_id: time.time()})
    r.hset(job_pfx + task_id, mapping={
        "task_id":    task_id,
        "operation":  op,
        "status":     status,
        "engine":     "pytorch",
        "project":    project,
        "gpu_id":     gpu_id,
        "started_at": str(time.time()),
        "config_path": "/test/config.yaml",
        "log_path":    "/tmp/e2e_test.log",
    })


def cleanup_test_job(r, task_id: str, op: str = "train") -> None:
    zset = "dlc_train_jobs" if op == "train" else "dlc_analyze_jobs"
    job_pfx = "dlc_train_job:" if op == "train" else "dlc_analyze_job:"
    r.zrem(zset, task_id)
    r.delete(job_pfx + task_id)
    r.delete(f"dlc_task:{task_id}:log")


def _new_authenticated_context(p, base_url: str, token: str):
    browser = p.chromium.launch()
    ctx = browser.new_context()
    page = ctx.new_page()
    # Authenticate once; cookie persists in this context only.
    page.goto(f"{base_url}/?token={token}", wait_until="domcontentloaded")
    return browser, ctx, page
