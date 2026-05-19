"""Regression guard for the divergent-TTL bug:

The dlc_train_job:<id> hash had its own 2 h TTL refreshed only by the
emit_loop's per-iteration `redis.expire(job_key, 7200)`. The log list
(dlc_task:<id>:log) had a separate 2 h TTL refreshed on every RPUSH in
stream_log_lines_to_redis. If the emit_loop stalled or exited early (e.g.
training entered a long eval phase that still pushed log lines through a
different code path), only the log key got refreshed — the hash expired
while the task was still running, leaving an orphan zset entry.

Fix: stream_log_lines_to_redis takes an optional job_key arg. When lines
push, BOTH keys get their TTLs refreshed in lockstep, so the two can no
longer diverge.

These tests pin that behavior so the keys cannot drift apart again.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _write_log(tmp_path, contents: bytes) -> str:
    p = tmp_path / "fake.log"
    p.write_bytes(contents)
    return str(p)


def test_job_key_ttl_refreshed_alongside_log_key(tmp_path):
    """When job_key is passed AND lines are pushed, both keys get expire()."""
    from dlc._log_stream import stream_log_lines_to_redis

    log_path = _write_log(tmp_path, b"line one\nline two\n")
    r = MagicMock()
    cursor = [0]

    stream_log_lines_to_redis(
        r,
        log_path,
        log_list_key="dlc_task:abc:log",
        byte_cursor=cursor,
        expire_seconds=7200,
        job_key="dlc_train_job:abc",
    )

    expire_calls = r.expire.call_args_list
    keys_expired = {c.args[0] for c in expire_calls}
    assert "dlc_task:abc:log"   in keys_expired, f"log key not refreshed; got {expire_calls!r}"
    assert "dlc_train_job:abc"  in keys_expired, f"job hash key not refreshed; got {expire_calls!r}"
    for c in expire_calls:
        assert c.args[1] == 7200, f"TTL mismatch: {c.args!r}"


def test_no_job_key_means_no_hash_ttl_call(tmp_path):
    """Back-compat: callers that don't pass job_key get the old single-expire behavior."""
    from dlc._log_stream import stream_log_lines_to_redis

    log_path = _write_log(tmp_path, b"only line\n")
    r = MagicMock()
    cursor = [0]

    stream_log_lines_to_redis(r, log_path, "dlc_task:abc:log", cursor)

    # Exactly one expire — on the log key.
    assert r.expire.call_count == 1
    assert r.expire.call_args.args[0] == "dlc_task:abc:log"


def test_no_lines_means_no_expire_at_all(tmp_path):
    """No complete lines yet → neither key gets touched (preserves existing behavior)."""
    from dlc._log_stream import stream_log_lines_to_redis

    # Partial line: no trailing newline.
    log_path = _write_log(tmp_path, b"half a line with no newline")
    r = MagicMock()
    cursor = [0]

    stream_log_lines_to_redis(
        r,
        log_path,
        "dlc_task:abc:log",
        cursor,
        job_key="dlc_train_job:abc",
    )

    assert r.rpush.called is False
    assert r.expire.called is False
