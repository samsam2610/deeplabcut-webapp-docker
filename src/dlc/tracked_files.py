"""
Tracked video files, persisted in <project>/tracked_files.sqlite.

The user marks a video as "tracked" so it can be reopened later without
re-navigating to its folder. Identity is the absolute video path, stored
verbatim — no realpath/symlink resolution, so the stored path always matches
what the UI shows in its breadcrumb.

This module imports no Flask, no DLC, no Redis, and never touches the
filesystem beyond its own DB file — it can be unit-tested against tmp_path.
Existence of a tracked path is deliberately NOT checked here; a missing file
is discovered when the user tries to open it.

Schema (v1):
    tracked(video_path TEXT PRIMARY KEY, tracked_at TEXT NOT NULL,
            last_opened_at TEXT)
    meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
        meta keys: schema_version="1"
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_FILENAME = "tracked_files.sqlite"
SCHEMA_VERSION = "1"


def _db_path(project_path) -> Path:
    return Path(project_path) / DB_FILENAME


@contextmanager
def _connect(project_path):
    """Open the SQLite DB, applying schema on first use. Per-call connection."""
    conn = sqlite3.connect(str(_db_path(project_path)), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracked (
            video_path     TEXT PRIMARY KEY,
            tracked_at     TEXT NOT NULL,
            last_opened_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    if cur.fetchone() is None:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)",
                     ("schema_version", SCHEMA_VERSION))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def list_tracked(project_path) -> list:
    """Every tracked video, most recently opened first, never-opened last.

    SQLite has no NULLS LAST, hence the explicit `(last_opened_at IS NULL)`
    leading sort key.
    """
    if not _db_path(project_path).is_file():
        return []
    with _connect(project_path) as conn:
        rows = conn.execute(
            "SELECT video_path, tracked_at, last_opened_at FROM tracked "
            "ORDER BY (last_opened_at IS NULL), last_opened_at DESC, tracked_at DESC"
        ).fetchall()
    return [{"path": p, "tracked_at": t, "last_opened_at": o} for (p, t, o) in rows]


def track(project_path, video_path: str) -> None:
    """Start tracking `video_path`. Idempotent: re-tracking preserves the
    existing tracked_at and last_opened_at."""
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO tracked(video_path, tracked_at, last_opened_at) "
                "VALUES (?, ?, NULL)",
                (video_path, _now()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def untrack(project_path, video_path: str) -> None:
    """Stop tracking `video_path`. No-op when it was never tracked."""
    if not _db_path(project_path).is_file():
        return
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM tracked WHERE video_path=?", (video_path,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def touch_opened(project_path, video_path: str) -> None:
    """Stamp last_opened_at=now, but ONLY if the row already exists — opening
    an untracked video must never create a tracked row."""
    if not _db_path(project_path).is_file():
        return
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE tracked SET last_opened_at=? WHERE video_path=?",
                (_now(), video_path),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
