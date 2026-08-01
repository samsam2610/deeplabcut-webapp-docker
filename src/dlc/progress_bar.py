"""
Per-project progress arrow bar: one ordered set of segments, each offering
options that carry a user-chosen colour, plus one value per (file, segment).

Stored in the SAME DB file as tracked_files.py, whose schema, connection,
migration and audit helper all live in tracked_db.py. Values are keyed by
`video_id`, not by path, so renaming or moving a file keeps its progress.

Segment and option IDs are stable and never reused: renaming or recolouring
must not disturb stored values. Deleting a segment or option does NOT delete
the values referencing it — orphaned values stay in the DB, render as unset,
and come back if the same ID is restored.

Imports no Flask, no DLC, no Redis, and never touches the filesystem beyond
its own DB file.

Schema (owned by tracked_db.py):
    progress_segment(segment_id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                     name TEXT NOT NULL)
    progress_option(option_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL,
                    position INTEGER NOT NULL, label TEXT NOT NULL,
                    color TEXT NOT NULL)
    progress_value(video_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                   option_id TEXT NOT NULL, set_at TEXT NOT NULL,
                   PRIMARY KEY (video_id, segment_id))
"""
from __future__ import annotations

import re
import secrets

from .tracked_db import audit, connect, db_path, now

MAX_SEGMENTS = 10

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def is_valid_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX_COLOR_RE.match(value))


def get_definition(project_path) -> dict:
    """The ordered segments, each with its ordered options."""
    if not db_path(project_path).is_file():
        return {"segments": []}
    with connect(project_path) as conn:
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


def save_definition(project_path, segments, actor=None) -> dict:
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

    with connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = {
                "segments": conn.execute(
                    "SELECT COUNT(*) FROM progress_segment").fetchone()[0],
                "options": conn.execute(
                    "SELECT COUNT(*) FROM progress_option").fetchone()[0],
            }
            # Full replace of the DEFINITION only. progress_value is never touched.
            conn.execute("DELETE FROM progress_option")
            conn.execute("DELETE FROM progress_segment")
            conn.executemany(
                "INSERT INTO progress_segment(segment_id, position, name) VALUES (?,?,?)",
                rows_seg)
            conn.executemany(
                "INSERT INTO progress_option(option_id, segment_id, position, label, color) "
                "VALUES (?,?,?,?,?)", rows_opt)
            # Counts, not the whole definition — the log stays readable.
            audit(conn, actor, "progress_definition", None, "save_definition",
                  before, {"segments": len(rows_seg), "options": len(rows_opt)})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_definition(project_path)


def get_values(project_path, video_ids) -> dict:
    """{video_id: {segment_id: option_id}} for the given ids, batched into one
    query. Videos with no stored value are omitted entirely."""
    ids = [v for v in (video_ids or []) if v]
    if not ids or not db_path(project_path).is_file():
        return {}
    out: dict = {}
    with connect(project_path) as conn:
        # Chunked so a huge tracked list cannot exceed SQLite's variable limit.
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT video_id, segment_id, option_id FROM progress_value "
                f"WHERE video_id IN ({marks})", chunk
            ).fetchall()
            for vid, sid, oid in rows:
                out.setdefault(vid, {})[sid] = oid
    return out


def set_value(project_path, video_id: str, segment_id: str, option_id, actor=None) -> None:
    """Set one segment's option for one video. option_id=None clears it."""
    with connect(project_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            prev = conn.execute(
                "SELECT option_id FROM progress_value WHERE video_id=? AND segment_id=?",
                (video_id, segment_id)).fetchone()
            before = {"option_id": prev[0]} if prev else None
            if option_id is None:
                conn.execute(
                    "DELETE FROM progress_value WHERE video_id=? AND segment_id=?",
                    (video_id, segment_id))
                audit(conn, actor, "progress_value", video_id, "clear_value",
                      before, None)
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO progress_value"
                    "(video_id, segment_id, option_id, set_at) VALUES (?,?,?,?)",
                    (video_id, segment_id, option_id, now()))
                audit(conn, actor, "progress_value", video_id, "set_value",
                      before, {"segment_id": segment_id, "option_id": option_id})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
