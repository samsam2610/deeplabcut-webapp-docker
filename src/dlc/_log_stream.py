"""
Log-tailing helper for DLC subprocess emit_loops.

Streams new lines from a growing log file into a Redis list, using a
byte-offset cursor so it stays correct as the file outgrows any windowed
display read kept by the caller.
"""
from __future__ import annotations


def stream_log_lines_to_redis(
    redis_client,
    log_path: str,
    log_list_key: str,
    byte_cursor: list,
    expire_seconds: int = 7200,
) -> None:
    """Tail log_path from byte_cursor[0], RPUSH complete lines to Redis.

    The cursor is a single-element list so callers (closures in emit_loops)
    can mutate it in place. Trailing partial lines (no \\n yet) are held back
    until the writer terminates them — only complete lines push.
    """
    try:
        with open(log_path, "rb") as f:
            f.seek(byte_cursor[0])
            chunk = f.read()
    except OSError:
        return
    if not chunk:
        return
    last_nl = chunk.rfind(b"\n")
    if last_nl < 0:
        return
    text = chunk[: last_nl + 1].decode("utf-8", errors="replace")
    lines = text.splitlines()
    if lines:
        redis_client.rpush(log_list_key, *lines)
        redis_client.expire(log_list_key, expire_seconds)
    byte_cursor[0] += last_nl + 1
