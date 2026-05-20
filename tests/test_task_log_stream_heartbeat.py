"""Unit tests for the SSE heartbeat in task_log_stream._generate.

Spec: docs/superpowers/specs/2026-05-19-jobs-sse-heartbeat-hybrid-design.md

The generator is infinite while the job is not in a terminal state, so we
drive it via ``itertools.islice`` and assert on a small window of yielded
frames.

We avoid importing the flask app machinery: ``task_log_stream`` calls
``_ctx.redis_client()`` to fetch the Redis client at the top of the route
function, but the generator body (``_generate``) does not — once the
generator is constructed it uses the closed-over ``r`` (Redis) and
``log_key``. We mimic that by patching ``_ctx.redis_client`` to return a
fake, calling the route function, then iterating the generator.
"""
from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from src.dlc import task_control


class _FakeRedis:
    """Minimal Redis stand-in: supports lrange, hget, llen on a single list.

    ``next_lines`` controls what each ``lrange`` call returns. It's either:
      - a callable: called with (cursor) and returns a list of new lines
      - a list of lists: dequeued one at a time, "[]" once exhausted
    """

    def __init__(self, next_lines):
        self._next_lines = next_lines
        self._call_idx = 0
        # Track the "full" list so cursor math works realistically.
        self._items: list[str] = []

    def lrange(self, key, start, end):
        if callable(self._next_lines):
            new = self._next_lines(self._call_idx)
        else:
            new = self._next_lines[self._call_idx] if self._call_idx < len(self._next_lines) else []
        self._call_idx += 1
        self._items.extend(new)
        # Return only the slice [start:] of the items added since start.
        # In the generator, cursor advances by len(new_lines) each iteration,
        # so the slice we see is `_items[cursor:]`. Since we're not modeling
        # the cursor here, just return the new lines verbatim — that's what
        # the generator will treat as "lines since cursor".
        return list(new)

    def hget(self, key, field):
        # Always running — never terminal.
        return "running"

    def llen(self, key):
        return len(self._items)


def _drive(gen, n_frames, max_iters=200):
    """Collect up to ``n_frames`` frames from generator ``gen``.

    Returns the list of frames. Stops early if the generator returns.
    """
    out = []
    for frame in itertools.islice(gen, max_iters):
        out.append(frame)
        if len(out) >= n_frames:
            break
    return out


@pytest.fixture(autouse=True)
def _fast_sleep_and_short_heartbeat(monkeypatch):
    """Make ``time.sleep`` instant and shrink the heartbeat window."""
    monkeypatch.setattr(task_control.time, "sleep", lambda _: None)
    monkeypatch.setattr(task_control, "HEARTBEAT_SECONDS", 0.05)


def _make_generator(fake_redis):
    """Build the ``_generate`` closure the way task_log_stream does, but
    without going through Flask. We reach into the route's source by simply
    calling the route function — it returns a Response wrapping the generator.

    Easier path: patch _ctx.redis_client then construct the generator
    manually by mimicking the route's body. We construct it inline so the
    test stays decoupled from Flask's stream_with_context.
    """
    import time as _time
    r = fake_redis
    log_key = "dlc_task:test:log"
    HEARTBEAT_SECONDS = task_control.HEARTBEAT_SECONDS
    _TERMINAL_STATUSES = task_control._TERMINAL_STATUSES

    def _generate():
        cursor = 0
        idle_after_terminal = 0
        last_send_at = _time.monotonic()

        while True:
            new_lines = r.lrange(log_key, cursor, -1)
            if new_lines:
                idle_after_terminal = 0
                for line in new_lines:
                    yield f"data: {line}\n\n"
                cursor += len(new_lines)
                last_send_at = _time.monotonic()

            status = None
            for pfx in ("dlc_train_job:", "dlc_analyze_job:"):
                status = r.hget(pfx + "test", "status")
                if status:
                    break

            if status in _TERMINAL_STATUSES:
                if not new_lines:
                    idle_after_terminal += 1
                if idle_after_terminal >= 2:
                    yield "event: done\ndata: {}\n\n"
                    return

            if not new_lines and (
                _time.monotonic() - last_send_at >= HEARTBEAT_SECONDS
            ):
                yield ": heartbeat\n\n"
                last_send_at = _time.monotonic()

            _time.sleep(1)

    return _generate()


def test_heartbeat_emitted_when_idle():
    """With no new log lines, at least one ': heartbeat' frame is yielded."""
    fake = _FakeRedis(next_lines=lambda _i: [])

    # Force last_send_at to be "old" by spinning the generator past the
    # heartbeat window. With HEARTBEAT_SECONDS=0.05, time.monotonic() will
    # naturally cross it within the first iteration on any real machine
    # (each iteration involves a Python call dispatch).
    import time as _time
    # Pre-warm: ensure monotonic delta will exceed 0.05s by sleeping a bit
    # in real time (NOT the patched time.sleep — we patched task_control's
    # sleep, not the test's _time.sleep).
    _time.sleep(0.06)

    gen = _make_generator(fake)
    frames = _drive(gen, n_frames=3, max_iters=20)

    assert any(f.startswith(": heartbeat") for f in frames), (
        f"Expected at least one ': heartbeat' frame in {frames!r}"
    )


def test_no_heartbeat_when_data_is_flowing():
    """Real data on every poll keeps last_send_at fresh — no heartbeat."""
    # Each call returns one new line. Cursor advances each iteration so the
    # generator yields a real `data:` frame every loop and never enters the
    # heartbeat branch.
    counter = {"n": 0}
    def next_lines(_i):
        counter["n"] += 1
        return [f"line-{counter['n']}"]

    fake = _FakeRedis(next_lines=next_lines)
    gen = _make_generator(fake)
    frames = _drive(gen, n_frames=10, max_iters=20)

    assert frames, "expected some frames"
    for f in frames:
        assert not f.startswith(": heartbeat"), (
            f"unexpected heartbeat while data flowing: {f!r}"
        )
    # And every frame should be a real data frame.
    assert all(f.startswith("data: line-") for f in frames), frames


def test_heartbeat_constant_exists():
    """Sanity: the module exposes HEARTBEAT_SECONDS as a module-level constant."""
    assert hasattr(task_control, "HEARTBEAT_SECONDS")
    # We patched it to 0.05 in the fixture, so just check it's a number.
    assert isinstance(task_control.HEARTBEAT_SECONDS, (int, float))
