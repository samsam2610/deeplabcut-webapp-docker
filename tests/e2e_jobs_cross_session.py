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


def test_job_visible_from_session_that_did_not_start_it(live_redis):
    seed_test_job(live_redis, "tCROSS-1", project="cross-test-1")
    try:
        with sync_playwright() as p:
            # Session A — the "originating" session. Authenticates but never visits /jobs.
            br_a, ctx_a, page_a = _new_authenticated_context(p, BASE_URL, APP_TOKEN)
            # Session B — completely separate context. Visits /jobs.
            br_b, ctx_b, page_b = _new_authenticated_context(p, BASE_URL, APP_TOKEN)
            page_b.goto(f"{BASE_URL}/jobs")
            page_b.wait_for_selector('[data-task-id="tCROSS-1"]', timeout=10000)
            br_a.close(); br_b.close()
    finally:
        cleanup_test_job(live_redis, "tCROSS-1")


def test_log_visible_from_session_that_did_not_start_it(live_redis):
    seed_test_job(live_redis, "tCROSS-2", project="cross-test-2")
    live_redis.rpush("dlc_task:tCROSS-2:log", "Epoch 1/3 ...", "Epoch 2/3 ...", "Epoch 3/3 ...")
    try:
        with sync_playwright() as p:
            br, _, page = _new_authenticated_context(p, BASE_URL, APP_TOKEN)
            page.goto(f"{BASE_URL}/jobs")
            page.wait_for_selector('[data-task-id="tCROSS-2"]', timeout=10000)
            page.click('[data-task-id="tCROSS-2"]')
            page.wait_for_function(
                """() => {
                    const t = document.querySelector('#jobs-terminal');
                    return t && t.textContent.includes('Epoch 3/3');
                }""",
                timeout=10000,
            )
            term_text = page.text_content("#jobs-terminal")
            assert "Epoch 1/3" in term_text
            assert "Epoch 2/3" in term_text
            assert "Epoch 3/3" in term_text
            br.close()
    finally:
        cleanup_test_job(live_redis, "tCROSS-2")
        live_redis.delete("dlc_task:tCROSS-2:log")


def test_stop_works_from_session_that_did_not_start_it(live_redis):
    """Note: terminate on a job with no PID enters Path B (direct cleanup) —
    so it's safe to run against a seeded test job, no real subprocess involved."""
    seed_test_job(live_redis, "tCROSS-3", project="cross-test-3")
    try:
        with sync_playwright() as p:
            br, _, page = _new_authenticated_context(p, BASE_URL, APP_TOKEN)
            page.goto(f"{BASE_URL}/jobs")
            page.wait_for_selector('[data-task-id="tCROSS-3"]', timeout=10000)
            page.click('[data-task-id="tCROSS-3"]')
            page.wait_for_selector('button[data-action="stop"]', timeout=5000)
            # Auto-confirm the dialog
            page.on("dialog", lambda d: d.accept())
            page.click('button[data-action="stop"]')
            # Path B (no live PID) removes the job from the listing zset
            # entirely. The row disappears from the rail on the next list
            # poll — that's the user-visible confirmation that stop worked.
            page.wait_for_function(
                """() => !document.querySelector('[data-task-id="tCROSS-3"]')""",
                timeout=10000,
            )
            br.close()
    finally:
        cleanup_test_job(live_redis, "tCROSS-3")


def test_visibility_pause_resume(live_redis):
    seed_test_job(live_redis, "tVIS", project="visibility-test")
    live_redis.rpush("dlc_task:tVIS:log", "initial line")
    try:
        with sync_playwright() as p:
            br, _, page = _new_authenticated_context(p, BASE_URL, APP_TOKEN)
            page.goto(f"{BASE_URL}/jobs")
            page.wait_for_selector('[data-task-id="tVIS"]', timeout=10000)
            page.click('[data-task-id="tVIS"]')
            page.wait_for_function(
                "() => document.getElementById('jobs-status-pill').textContent.includes('live')",
                timeout=5000,
            )
            # Simulate tab hide → pill flips to paused
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {value: true, configurable: true});"
                " document.dispatchEvent(new Event('visibilitychange'));"
            )
            page.wait_for_function(
                "() => document.getElementById('jobs-status-pill').textContent.includes('paused')",
                timeout=2000,
            )
            # New log line lands while hidden
            live_redis.rpush("dlc_task:tVIS:log", "lined while hidden")
            # Show again → pill back to live, terminal contains the new line
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {value: false, configurable: true});"
                " document.dispatchEvent(new Event('visibilitychange'));"
            )
            page.wait_for_function(
                "() => document.getElementById('jobs-status-pill').textContent.includes('live')",
                timeout=5000,
            )
            page.wait_for_function(
                "() => document.getElementById('jobs-terminal').textContent.includes('lined while hidden')",
                timeout=5000,
            )
            br.close()
    finally:
        cleanup_test_job(live_redis, "tVIS")
        live_redis.delete("dlc_task:tVIS:log")


def test_idle_timeout_shows_reconnect(live_redis):
    seed_test_job(live_redis, "tIDLE", project="idle-test")
    try:
        with sync_playwright() as p:
            br, _, page = _new_authenticated_context(p, BASE_URL, APP_TOKEN)
            # Force a 500ms idle timeout via the test seam
            page.goto(f"{BASE_URL}/jobs?_test_idle_ms=500")
            page.wait_for_selector('[data-task-id="tIDLE"]', timeout=10000)
            page.click('[data-task-id="tIDLE"]')
            # Hide and wait > 500 ms
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {value: true, configurable: true});"
                " document.dispatchEvent(new Event('visibilitychange'));"
            )
            page.wait_for_timeout(900)  # comfortably past the 500ms timeout
            # Reconnect button must appear
            page.wait_for_selector(".jobs-reconnect-btn", timeout=2000)
            # Pill should say 'closed'
            assert "closed" in page.text_content("#jobs-status-pill")
            # Click reconnect → button removed, pill back to 'live'
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {value: false, configurable: true});"
                " document.dispatchEvent(new Event('visibilitychange'));"
            )
            page.click(".jobs-reconnect-btn")
            page.wait_for_function(
                "() => !document.querySelector('.jobs-reconnect-btn')",
                timeout=2000,
            )
            br.close()
    finally:
        cleanup_test_job(live_redis, "tIDLE")
