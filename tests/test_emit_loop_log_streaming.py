"""
Regression tests for _stream_log_lines_to_redis.

The previous implementation drove the streaming cursor from a windowed read
(`_lf.read()[-8000:]`) using a line-index cursor. Once a log file grew past
~8 KB, the windowed text contained fewer lines than the cursor, so
`new_lines = all_lines[cursor:]` was empty forever — silently freezing the
SSE log feed at the last line that fit in the window. This caused the
/jobs page to stop updating mid-training.

These tests pin the new byte-offset behavior: every complete line written to
the file is pushed to Redis exactly once, regardless of file size, and a
trailing partial line is held until the writer terminates it with \\n.
"""
from __future__ import annotations

from pathlib import Path

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))

from dlc._log_stream import stream_log_lines_to_redis as _stream_log_lines_to_redis


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list] = {}
        self.expires: dict[str, int] = {}

    def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)

    def expire(self, key, seconds):
        self.expires[key] = seconds


def test_streams_complete_lines_and_holds_partial_tail(tmp_path: Path):
    log = tmp_path / "train.log"
    log.write_text("alpha\nbeta\ngamma")  # last line has no trailing newline

    redis = _FakeRedis()
    cursor = [0]

    _stream_log_lines_to_redis(redis, str(log), "k", cursor)

    assert redis.lists["k"] == ["alpha", "beta"]
    # cursor should sit just past "beta\n", leaving "gamma" buffered for later
    assert cursor[0] == len("alpha\nbeta\n")

    # writer finishes the partial line + adds another
    with log.open("a") as f:
        f.write(" extra\ndelta\n")

    _stream_log_lines_to_redis(redis, str(log), "k", cursor)

    assert redis.lists["k"] == ["alpha", "beta", "gamma extra", "delta"]


def test_no_duplicate_lines_across_polls(tmp_path: Path):
    log = tmp_path / "train.log"
    log.write_text("one\ntwo\nthree\n")

    redis = _FakeRedis()
    cursor = [0]

    _stream_log_lines_to_redis(redis, str(log), "k", cursor)
    _stream_log_lines_to_redis(redis, str(log), "k", cursor)  # idempotent
    _stream_log_lines_to_redis(redis, str(log), "k", cursor)

    assert redis.lists["k"] == ["one", "two", "three"]


def test_streams_lines_after_file_exceeds_8kb(tmp_path: Path):
    """Regression: previous line-index-over-windowed-read cursor froze once
    the file passed the 8 KB display window. Verify byte cursor handles it."""
    log = tmp_path / "train.log"

    # Write 200 setup lines (well under 8 KB), poll once.
    setup = "".join(f"setup-line-{i:03d}\n" for i in range(200))
    log.write_text(setup)
    assert log.stat().st_size < 8000  # precondition: still in window

    redis = _FakeRedis()
    cursor = [0]
    _stream_log_lines_to_redis(redis, str(log), "k", cursor)
    assert len(redis.lists["k"]) == 200

    # Append a long block of "training" lines that pushes file past 8 KB.
    # The buggy implementation's `_log = _lf.read()[-8000:]` would now
    # contain only the tail; len(all_lines) < cursor so nothing pushed.
    big_block = "".join(f"epoch-{i:04d}-loss=0.1234\n" for i in range(500))
    with log.open("a") as f:
        f.write(big_block)
    assert log.stat().st_size > 8000  # postcondition: outside window

    _stream_log_lines_to_redis(redis, str(log), "k", cursor)

    assert len(redis.lists["k"]) == 700
    assert redis.lists["k"][200] == "epoch-0000-loss=0.1234"
    assert redis.lists["k"][-1] == "epoch-0499-loss=0.1234"


def test_empty_file_is_a_noop(tmp_path: Path):
    log = tmp_path / "train.log"
    log.write_text("")
    redis = _FakeRedis()
    cursor = [0]
    _stream_log_lines_to_redis(redis, str(log), "k", cursor)
    assert "k" not in redis.lists
    assert cursor[0] == 0


def test_missing_file_does_not_raise(tmp_path: Path):
    redis = _FakeRedis()
    cursor = [0]
    _stream_log_lines_to_redis(redis, str(tmp_path / "nope.log"), "k", cursor)
    assert "k" not in redis.lists
    assert cursor[0] == 0
