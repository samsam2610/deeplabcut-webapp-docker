"""
Shared SQLite layer for the project's tracked-files database.

Owns the connection, the v3 schema, the v2->v3 migration, video identity and
the audit log. tracked_files.py and progress_bar.py are thin stores on top of
this; the schema lives here because both of them open the same file and a
migration written twice will eventually disagree with itself.

Identity: every video gets a surrogate `video_id`, assigned once and never
reused. `path` is a mutable attribute, NOT identity — renaming or moving a file
is an UPDATE of video.path, and every tracked flag and progress value follows
automatically because none of them ever stored a path.

Re-identification: `fingerprint` is blake2b over "<size_bytes>|<frame_count>",
both intrinsic to the recording, so it survives rename, move and copy. It is
NOT unique — two copies of one recording share it — so it is only ever a hint
for a future re-link sweep. mtime is deliberately excluded: `cp` without -p,
rsync and restores all change it.

Auditing: every mutation writes an audit_log row inside the SAME transaction as
the change, so a change cannot commit without its log entry.

This module imports no Flask, no DLC, no Redis, and never touches the
filesystem beyond its own DB file — probing is the route layer's job.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_FILENAME = "tracked_files.sqlite"
SCHEMA_VERSION = "3"


def db_path(project_path) -> Path:
    return Path(project_path) / DB_FILENAME


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_video_id() -> str:
    return f"vid_{secrets.token_hex(8)}"


def fingerprint(size_bytes, frame_count):
    """Stable id-hint for a recording. None unless BOTH metrics are known."""
    if size_bytes is None or frame_count is None:
        return None
    raw = f"{int(size_bytes)}|{int(frame_count)}".encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


@contextmanager
def connect(project_path):
    conn = sqlite3.connect(str(db_path(project_path)), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(conn)
        yield conn
    finally:
        conn.close()


# ── Schema ──────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS video (
            video_id      TEXT PRIMARY KEY,
            path          TEXT NOT NULL,
            size_bytes    INTEGER,
            frame_count   INTEGER,
            fingerprint   TEXT,
            first_seen_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS video_path_idx ON video(path)")
    # NOT unique: two copies of one recording legitimately share a fingerprint.
    conn.execute("CREATE INDEX IF NOT EXISTS video_fingerprint_idx ON video(fingerprint)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            at        TEXT NOT NULL,
            actor     TEXT,
            entity    TEXT NOT NULL,
            entity_id TEXT,
            action    TEXT NOT NULL,
            before    TEXT,
            after     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_segment (
            segment_id TEXT PRIMARY KEY, position INTEGER NOT NULL, name TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_option (
            option_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL, position INTEGER NOT NULL,
            label TEXT NOT NULL, color TEXT NOT NULL
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    _ensure_id_keyed_tables(conn)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 ("schema_version", SCHEMA_VERSION))


def _columns(conn, table) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_id_keyed_tables(conn: sqlite3.Connection) -> None:
    """Create tracked/progress_value in their v3 shape, migrating v2 if present."""
    tracked_cols = _columns(conn, "tracked")
    value_cols = _columns(conn, "progress_value")
    legacy = "video_path" in tracked_cols or "video_path" in value_cols

    if not legacy:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked (
                video_id       TEXT PRIMARY KEY,
                tracked_at     TEXT NOT NULL,
                last_opened_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS progress_value (
                video_id   TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                option_id  TEXT NOT NULL,
                set_at     TEXT NOT NULL,
                PRIMARY KEY (video_id, segment_id)
            )
        """)
        return

    _migrate_v2_to_v3(conn, tracked_cols, value_cols)


def _migrate_v2_to_v3(conn: sqlite3.Connection, tracked_cols, value_cols) -> None:
    """One transaction: mint a video row per distinct path, re-key both tables.

    Never touches the filesystem — files may live on unmounted disks, and a
    schema upgrade must not depend on I/O it cannot guarantee. Metrics stay
    NULL and are backfilled on the next successful open.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        paths = {}          # path -> first_seen_at
        if "video_path" in tracked_cols:
            for path, tracked_at in conn.execute(
                    "SELECT video_path, tracked_at FROM tracked"):
                paths.setdefault(path, tracked_at)
        if "video_path" in value_cols:
            for (path,) in conn.execute("SELECT DISTINCT video_path FROM progress_value"):
                paths.setdefault(path, None)

        stamp = now()
        for path, first_seen in paths.items():
            conn.execute(
                "INSERT OR IGNORE INTO video(video_id, path, first_seen_at) VALUES (?,?,?)",
                (new_video_id(), path, first_seen or stamp))

        if "video_path" in tracked_cols:
            conn.execute("""
                CREATE TABLE tracked_v3 (
                    video_id TEXT PRIMARY KEY, tracked_at TEXT NOT NULL, last_opened_at TEXT)
            """)
            conn.execute("""
                INSERT INTO tracked_v3(video_id, tracked_at, last_opened_at)
                SELECT v.video_id, t.tracked_at, t.last_opened_at
                FROM tracked t JOIN video v ON v.path = t.video_path
            """)
            conn.execute("DROP TABLE tracked")
            conn.execute("ALTER TABLE tracked_v3 RENAME TO tracked")

        if "video_path" in value_cols:
            conn.execute("""
                CREATE TABLE progress_value_v3 (
                    video_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                    option_id TEXT NOT NULL, set_at TEXT NOT NULL,
                    PRIMARY KEY (video_id, segment_id))
            """)
            conn.execute("""
                INSERT INTO progress_value_v3(video_id, segment_id, option_id, set_at)
                SELECT v.video_id, p.segment_id, p.option_id, p.set_at
                FROM progress_value p JOIN video v ON v.path = p.video_path
            """)
            conn.execute("DROP TABLE progress_value")
            conn.execute("ALTER TABLE progress_value_v3 RENAME TO progress_value")

        _audit_row(conn, None, "video", None, "migrate_v2_v3",
                   None, {"videos": len(paths)})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── Audit ───────────────────────────────────────────────────────────────────

def _audit_row(conn, actor, entity, entity_id, action, before, after) -> None:
    conn.execute(
        "INSERT INTO audit_log(at, actor, entity, entity_id, action, before, after) "
        "VALUES (?,?,?,?,?,?,?)",
        (now(), actor, entity, entity_id, action,
         json.dumps(before) if before is not None else None,
         json.dumps(after) if after is not None else None))


def audit(conn, actor, entity, entity_id, action, before=None, after=None) -> None:
    """Append one audit row. Callers MUST be inside the transaction that makes
    the change, so the two commit or roll back together."""
    _audit_row(conn, actor, entity, entity_id, action, before, after)


# ── Video identity ──────────────────────────────────────────────────────────

def video_id_for_path(conn, path: str):
    row = conn.execute("SELECT video_id FROM video WHERE path=?", (path,)).fetchone()
    return row[0] if row else None


def video_row(conn, video_id: str):
    row = conn.execute(
        "SELECT video_id, path, size_bytes, frame_count, fingerprint, first_seen_at "
        "FROM video WHERE video_id=?", (video_id,)).fetchone()
    if not row:
        return None
    return {"video_id": row[0], "path": row[1], "size_bytes": row[2],
            "frame_count": row[3], "fingerprint": row[4], "first_seen_at": row[5]}


def ensure_video(conn, path: str, actor=None, size_bytes=None, frame_count=None) -> str:
    """Return the video_id for `path`, creating the row on first sight.

    Metrics are optional and best-effort: a path that could not be probed still
    gets a row, and the metrics are backfilled by a later call that has them.
    Caller owns the transaction.
    """
    row = conn.execute(
        "SELECT video_id, size_bytes, frame_count FROM video WHERE path=?", (path,)
    ).fetchone()
    if row:
        vid, have_size, have_frames = row
        if (size_bytes is not None and frame_count is not None
                and (have_size is None or have_frames is None)):
            fp = fingerprint(size_bytes, frame_count)
            conn.execute(
                "UPDATE video SET size_bytes=?, frame_count=?, fingerprint=? WHERE video_id=?",
                (size_bytes, frame_count, fp, vid))
            audit(conn, actor, "video", vid, "probe_metadata", None,
                  {"size_bytes": size_bytes, "frame_count": frame_count, "fingerprint": fp})
        return vid

    vid = new_video_id()
    fp = fingerprint(size_bytes, frame_count)
    conn.execute(
        "INSERT INTO video(video_id, path, size_bytes, frame_count, fingerprint, first_seen_at) "
        "VALUES (?,?,?,?,?,?)",
        (vid, path, size_bytes, frame_count, fp, now()))
    audit(conn, actor, "video", vid, "create_video", None,
          {"path": path, "size_bytes": size_bytes, "frame_count": frame_count,
           "fingerprint": fp})
    return vid


def relink_path(conn, video_id: str, new_path: str, actor=None) -> bool:
    """Point an existing video at a new location. Identity is unchanged, so all
    tracked state and progress values follow. False when the id is unknown."""
    row = conn.execute("SELECT path FROM video WHERE video_id=?", (video_id,)).fetchone()
    if not row:
        return False
    old_path = row[0]
    conn.execute("UPDATE video SET path=? WHERE video_id=?", (new_path, video_id))
    audit(conn, actor, "video", video_id, "relink_path",
          {"path": old_path}, {"path": new_path})
    return True
