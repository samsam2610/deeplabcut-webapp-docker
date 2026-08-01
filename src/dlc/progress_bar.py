"""
Per-project progress arrow bar: one ordered set of segments, each offering
options that carry a user-chosen colour, plus one value per (file, segment).

Stored in the SAME DB file as tracked_files.py — <project>/tracked_files.sqlite
— because the values belong to tracked files. Schema version moves 1 -> 2;
CREATE TABLE IF NOT EXISTS means existing v1 DBs upgrade on first touch with no
migration script.

Segment and option IDs are stable and never reused: renaming or recolouring
must not disturb stored values. Deleting a segment or option does NOT delete
the values referencing it — orphaned values stay in the DB, render as unset,
and come back if the same ID is restored.

Imports no Flask, no DLC, no Redis, and never touches the filesystem beyond
its own DB file.

Schema (v2 additions):
    progress_segment(segment_id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                     name TEXT NOT NULL)
    progress_option(option_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL,
                    position INTEGER NOT NULL, label TEXT NOT NULL,
                    color TEXT NOT NULL)
    progress_value(video_path TEXT NOT NULL, segment_id TEXT NOT NULL,
                   option_id TEXT NOT NULL, set_at TEXT NOT NULL,
                   PRIMARY KEY (video_path, segment_id))
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .tracked_files import DB_FILENAME

MAX_SEGMENTS = 10
SCHEMA_VERSION = "2"

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _db_path(project_path) -> Path:
    return Path(project_path) / DB_FILENAME


@contextmanager
def _connect(project_path):
    conn = sqlite3.connect(str(_db_path(project_path)), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_segment (
            segment_id TEXT PRIMARY KEY,
            position   INTEGER NOT NULL,
            name       TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_option (
            option_id  TEXT PRIMARY KEY,
            segment_id TEXT NOT NULL,
            position   INTEGER NOT NULL,
            label      TEXT NOT NULL,
            color      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_value (
            video_path TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            option_id  TEXT NOT NULL,
            set_at     TEXT NOT NULL,
            PRIMARY KEY (video_path, segment_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 ("schema_version", SCHEMA_VERSION))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def is_valid_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX_COLOR_RE.match(value))


def get_definition(project_path) -> dict:
    """The ordered segments, each with its ordered options."""
    if not _db_path(project_path).is_file():
        return {"segments": []}
    with _connect(project_path) as conn:
        segs = conn.execute(
            "SELECT segment_id, name FROM progress_segment ORDER BY position"
        ).fetchall()
        opts = conn.execute(
            "SELECT option_id, segment_id, label, color FROM progress_option "
            "ORDER BY position"
        ).fetchall()
    by_seg: dict = {}
    for oid, sid, label, color in opts:
        by_seg.setdefault(sid, []).append(
            {"option_id": oid, "label": label, "color": color})
    return {"segments": [
        {"segment_id": sid, "name": name, "options": by_seg.get(sid, [])}
        for sid, name in segs
    ]}


def save_definition(project_path, segments) -> dict:
    """Replace the definition. Entries carrying an id keep it; entries without
    one get a fresh id. Segments/options absent from `segments` are removed
    from the definition but their progress_value rows are left untouched.

    Raises ValueError on >MAX_SEGMENTS or a colour that is not '#rrggbb'.
    """
    segments = list(segments or [])
    if len(segments) > MAX_SEGMENTS:
        raise ValueError(f"at most {MAX_SEGMENTS} segments (got {len(segments)})")
    # Validate everything BEFORE opening a write transaction, so a bad payload
    # never leaves a half-applied definition behind.
    for seg in segments:
        for opt in seg.get("options") or []:
            if not is_valid_color(opt.get("color")):
                raise ValueError(f"invalid colour: {opt.get('color')!r}")

    rows_seg = []
    rows_opt = []
    for s_pos, seg in enumerate(segments):
        sid = seg.get("segment_id") or _new_id("seg")
        rows_seg.append((sid, s_pos, str(seg.get("name", ""))))
        for o_pos, opt in enumerate(seg.get("options") or []):
            oid = opt.get("option_id") or _new_id("opt")
            rows_opt.append((oid, sid, o_pos, str(opt.get("label", "")), opt["color"]))

    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Full replace of the DEFINITION only. progress_value is never touched.
            conn.execute("DELETE FROM progress_option")
            conn.execute("DELETE FROM progress_segment")
            conn.executemany(
                "INSERT INTO progress_segment(segment_id, position, name) VALUES (?,?,?)",
                rows_seg)
            conn.executemany(
                "INSERT INTO progress_option(option_id, segment_id, position, label, color) "
                "VALUES (?,?,?,?,?)", rows_opt)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_definition(project_path)


def get_values(project_path, video_paths) -> dict:
    """{video_path: {segment_id: option_id}} for the given paths, batched into
    one query. Paths with no stored value are omitted entirely."""
    paths = [p for p in (video_paths or []) if p]
    if not paths or not _db_path(project_path).is_file():
        return {}
    out: dict = {}
    with _connect(project_path) as conn:
        # Chunked so a huge tracked list cannot exceed SQLite's variable limit.
        for i in range(0, len(paths), 400):
            chunk = paths[i:i + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT video_path, segment_id, option_id FROM progress_value "
                f"WHERE video_path IN ({marks})", chunk
            ).fetchall()
            for path, sid, oid in rows:
                out.setdefault(path, {})[sid] = oid
    return out


def set_value(project_path, video_path: str, segment_id: str, option_id) -> None:
    """Set one segment's option for one file. option_id=None clears it."""
    with _connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if option_id is None:
                conn.execute(
                    "DELETE FROM progress_value WHERE video_path=? AND segment_id=?",
                    (video_path, segment_id))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO progress_value"
                    "(video_path, segment_id, option_id, set_at) VALUES (?,?,?,?)",
                    (video_path, segment_id, option_id, _now()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
