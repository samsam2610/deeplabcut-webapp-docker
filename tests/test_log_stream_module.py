"""Static-source guards for the shared client log_stream.js + its consumers.

Spec: docs/superpowers/specs/2026-05-19-jobs-sse-heartbeat-hybrid-design.md

These tests scan the JS source files to enforce the architectural invariants
of the hybrid SSE/poll-tail design without spinning up a browser/fixture
stack. They follow the existing static-guard pattern used by
``test_frame_labeler_no_auto_frame_advance.py`` and friends.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT      = Path(__file__).parent.parent
JS_DIR         = REPO_ROOT / "src" / "static" / "js"
LOG_STREAM_JS  = JS_DIR / "log_stream.js"
JOBS_JS        = JS_DIR / "jobs.js"
GPU_MONITOR_JS = JS_DIR / "gpu_monitor.js"


def test_log_stream_js_exports_api():
    """log_stream.js must define subscribe + pollTail and attach to a global."""
    assert LOG_STREAM_JS.exists(), f"missing {LOG_STREAM_JS}"
    src = LOG_STREAM_JS.read_text()

    # Function definitions (named function declarations).
    assert re.search(r"\bfunction\s+subscribe\s*\(", src), (
        "log_stream.js must define a `subscribe` function."
    )
    assert re.search(r"\bfunction\s+pollTail\s*\(", src), (
        "log_stream.js must define a `pollTail` function."
    )

    # Attached to a global as `logStream` (the consumers reference
    # window.logStream.subscribe / .pollTail).
    assert re.search(r"\.logStream\s*=\s*\{", src) or re.search(
        r"\blogStream\s*=\s*\{", src
    ), "log_stream.js must attach `logStream` to a global object."

    # Surface explicitly lists both methods.
    assert "subscribe" in src and "pollTail" in src, src[:200]


def _uses_shared_log_stream(src: str) -> bool:
    """Heuristic: the file references window.logStream AND calls .subscribe(.

    Accepts both ``window.logStream.subscribe(`` and the pattern where a
    local alias is taken (e.g. ``const ls = window.logStream; ls.subscribe(``).
    """
    references_global = "window.logStream" in src or "globalThis.logStream" in src
    calls_subscribe = ".subscribe(" in src
    return references_global and calls_subscribe


def test_jobs_js_uses_log_stream():
    """jobs.js must not construct its own EventSource — uses logStream."""
    src = JOBS_JS.read_text()

    # No direct EventSource construction for log streams.
    assert "new EventSource(" not in src, (
        "jobs.js must not construct EventSource directly — go through "
        "window.logStream.subscribe()."
    )

    # And it must reference the shared module.
    assert _uses_shared_log_stream(src), (
        "jobs.js must subscribe via window.logStream.subscribe() "
        "(directly or via a local alias)."
    )


def test_gpu_monitor_uses_log_stream():
    """gpu_monitor.js must not construct its own EventSource — uses logStream."""
    src = GPU_MONITOR_JS.read_text()

    assert "new EventSource(" not in src, (
        "gpu_monitor.js must not construct EventSource directly — go "
        "through window.logStream.subscribe()."
    )

    assert _uses_shared_log_stream(src), (
        "gpu_monitor.js must subscribe via window.logStream.subscribe() "
        "(directly or via a local alias)."
    )


def test_jobs_no_idle_timer():
    """Regression guard: the 20-min idle timer is gone from jobs.js."""
    src = JOBS_JS.read_text()
    assert "idleTimer" not in src, (
        "jobs.js must not reference an `idleTimer` — the 20-min idle "
        "timeout was removed in the heartbeat-SSE refactor (spec "
        "2026-05-19-jobs-sse-heartbeat-hybrid-design)."
    )
    # Also assert the older "20-min idle" comment phrasing is gone, as a
    # belt-and-braces guard against the timer being reintroduced under a
    # different identifier.
    assert "20-min idle" not in src, (
        "jobs.js still references '20-min idle' — was the idle timer reintroduced?"
    )
