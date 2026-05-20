"""
Pure SQLite layer for the test-set picker.

Each DLC project gets its own `test_set_marks.sqlite` at the project root.
The file is created on first write. All public functions are safe to call
on a project that has never been touched.

Schema (v1):
    marks(video_stem TEXT, image_name TEXT, marked_at TEXT, note TEXT,
          PRIMARY KEY (video_stem, image_name))
    meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
        meta keys: schema_version="1", default_split_mode in {random,hybrid,manual}

This module imports no Flask, no DLC, no Redis — it can be unit-tested
in isolation against tmp_path.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

DB_FILENAME = "test_set_marks.sqlite"
SCHEMA_VERSION = "1"
VALID_MODES = ("random", "hybrid", "manual")


def _db_path(project_path: Path) -> Path:
    return Path(project_path) / DB_FILENAME


@contextmanager
def _connect(project_path: Path):
    """Open the SQLite DB, applying schema on first use. Per-call connection."""
    path = _db_path(project_path)
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; we wrap writes in BEGIN IMMEDIATE
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS marks (
            video_stem  TEXT NOT NULL,
            image_name  TEXT NOT NULL,
            marked_at   TEXT NOT NULL,
            note        TEXT,
            PRIMARY KEY (video_stem, image_name)
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


def list_marks(project_path: Path) -> list[tuple[str, str]]:
    """Return every marked frame as (video_stem, image_name), sorted."""
    path = _db_path(project_path)
    if not path.is_file():
        return []
    with _connect(project_path) as conn:
        rows = conn.execute(
            "SELECT video_stem, image_name FROM marks ORDER BY video_stem, image_name"
        ).fetchall()
    return [(s, i) for (s, i) in rows]


def get_marks_grouped(project_path: Path) -> dict[str, list[str]]:
    """Return marks grouped by video_stem; values are sorted image_name lists."""
    out: dict[str, list[str]] = {}
    for stem, image in list_marks(project_path):
        out.setdefault(stem, []).append(image)
    return out


def set_mark(project_path: Path, video_stem: str, image_name: str,
             marked: bool, note: str | None = None) -> bool:
    """Add or remove a single mark. Returns the new marked state."""
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if marked:
                conn.execute(
                    "INSERT OR REPLACE INTO marks(video_stem, image_name, marked_at, note) "
                    "VALUES (?, ?, ?, ?)",
                    (video_stem, image_name, _now(), note),
                )
            else:
                conn.execute(
                    "DELETE FROM marks WHERE video_stem=? AND image_name=?",
                    (video_stem, image_name),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return bool(marked)


def bulk_set(project_path: Path, ops: Iterable[dict]) -> int:
    """Apply many add/remove ops in a single transaction. Returns count applied."""
    ops = list(ops)
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for op in ops:
                stem = op["video_stem"]
                image = op["image_name"]
                if op.get("marked", True):
                    conn.execute(
                        "INSERT OR REPLACE INTO marks(video_stem, image_name, marked_at, note) "
                        "VALUES (?, ?, ?, ?)",
                        (stem, image, _now(), op.get("note")),
                    )
                else:
                    conn.execute(
                        "DELETE FROM marks WHERE video_stem=? AND image_name=?",
                        (stem, image),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return len(ops)


def get_mode(project_path: Path) -> str:
    path = _db_path(project_path)
    if not path.is_file():
        return "random"
    with _connect(project_path) as conn:
        cur = conn.execute("SELECT value FROM meta WHERE key='default_split_mode'")
        row = cur.fetchone()
    return row[0] if row else "random"


def set_mode(project_path: Path, mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('default_split_mode', ?)",
                (mode,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def clean_stale(project_path: Path) -> int:
    """Remove marks whose <project>/labeled-data/<stem>/<image> file no longer exists.

    Returns number of marks removed.
    """
    project = Path(project_path)
    labeled = project / "labeled-data"
    removed = 0
    for stem, image in list_marks(project):
        if not (labeled / stem / image).is_file():
            set_mark(project, stem, image, False)
            removed += 1
    return removed
