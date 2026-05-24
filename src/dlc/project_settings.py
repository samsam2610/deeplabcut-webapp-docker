"""
Per-project UI settings, persisted in <project>/ui_settings.sqlite.

A tiny key→value store (the value is an opaque string; callers may store JSON).
Used to remember inline-3D keyframe-window shapes (before/after) per project.
Imports no Flask/DLC/Redis — unit-testable against tmp_path.

Schema (v1): meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_FILENAME = "ui_settings.sqlite"


def _db_path(project_path) -> Path:
    return Path(project_path) / DB_FILENAME


@contextmanager
def _connect(project_path):
    conn = sqlite3.connect(str(_db_path(project_path)), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        yield conn
    finally:
        conn.close()


def get_setting(project_path, key: str, default=None):
    """Return the stored string for `key`, or `default` if absent."""
    if not _db_path(project_path).exists():
        return default
    with _connect(project_path) as conn:
        cur = conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
    return row[0] if row else default


def set_setting(project_path, key: str, value: str) -> None:
    """Insert or replace `key` = `value` (value is stored as text)."""
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
