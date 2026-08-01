"""
Tracked video files, keyed by the surrogate video_id from tracked_db.

Tracking marks a video for quick reopening. Identity is the video_id, never the
path, so renaming or moving a file (an UPDATE of video.path) leaves the tracked
flag and its timestamps untouched.

Untracking deletes the tracked row but NEVER the video row: the identity — and
therefore the file's progress values and history — survives, so re-tracking the
same path returns the same id.

Imports no Flask, no DLC, no Redis, and never touches the filesystem.
"""
from __future__ import annotations

from .tracked_db import (  # noqa: F401  (DB_FILENAME re-exported for callers)
    DB_FILENAME, audit, connect, db_path, ensure_video, now, video_id_for_path,
)


def list_tracked(project_path) -> list:
    """Tracked videos, most recently opened first, never-opened last.

    SQLite has no NULLS LAST, hence the explicit leading sort key.
    """
    if not db_path(project_path).is_file():
        return []
    with connect(project_path) as conn:
        rows = conn.execute("""
            SELECT t.video_id, v.path, t.tracked_at, t.last_opened_at
            FROM tracked t JOIN video v ON v.video_id = t.video_id
            ORDER BY (t.last_opened_at IS NULL), t.last_opened_at DESC, t.tracked_at DESC
        """).fetchall()
    return [{"video_id": r[0], "path": r[1], "tracked_at": r[2], "last_opened_at": r[3]}
            for r in rows]


def track(project_path, path: str, actor=None, size_bytes=None, frame_count=None) -> str:
    """Start tracking `path`; returns its video_id. Idempotent: re-tracking
    preserves tracked_at, last_opened_at and the id."""
    with connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            vid = ensure_video(conn, path, actor=actor,
                               size_bytes=size_bytes, frame_count=frame_count)
            existing = conn.execute(
                "SELECT 1 FROM tracked WHERE video_id=?", (vid,)).fetchone()
            if not existing:
                stamp = now()
                conn.execute(
                    "INSERT INTO tracked(video_id, tracked_at, last_opened_at) "
                    "VALUES (?,?,NULL)", (vid, stamp))
                audit(conn, actor, "tracked", vid, "track", None,
                      {"path": path, "tracked_at": stamp})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return vid


def untrack(project_path, video_id: str, actor=None) -> None:
    """Stop tracking. The video row (identity, fingerprint, progress values)
    is deliberately left in place."""
    if not db_path(project_path).is_file():
        return
    with connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT tracked_at, last_opened_at FROM tracked WHERE video_id=?",
                (video_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM tracked WHERE video_id=?", (video_id,))
                audit(conn, actor, "tracked", video_id, "untrack",
                      {"tracked_at": row[0], "last_opened_at": row[1]}, None)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def touch_opened(project_path, video_id: str, actor=None,
                 size_bytes=None, frame_count=None) -> None:
    """Stamp last_opened_at, and backfill the video's metrics if supplied.

    Only stamps an EXISTING tracked row — opening an untracked video must never
    start tracking it.
    """
    if not db_path(project_path).is_file():
        return
    with connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT last_opened_at, (SELECT path FROM video WHERE video_id=?) "
                "FROM tracked WHERE video_id=?", (video_id, video_id)).fetchone()
            if row:
                stamp = now()
                conn.execute("UPDATE tracked SET last_opened_at=? WHERE video_id=?",
                             (stamp, video_id))
                audit(conn, actor, "tracked", video_id, "mark_opened",
                      {"last_opened_at": row[0]}, {"last_opened_at": stamp})
                if size_bytes is not None and frame_count is not None:
                    ensure_video(conn, row[1], actor=actor,
                                 size_bytes=size_bytes, frame_count=frame_count)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def resolve(project_path, video_id=None, path=None):
    """Return an existing video_id from either key, or None. Never creates."""
    if not db_path(project_path).is_file():
        return None
    with connect(project_path) as conn:
        if video_id:
            row = conn.execute(
                "SELECT video_id FROM video WHERE video_id=?", (video_id,)).fetchone()
            return row[0] if row else None
        if path:
            return video_id_for_path(conn, path)
    return None
