"""Tests for src/dlc/tracked_db.py — schema, v2->v3 migration, identity, audit."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

import pytest

from dlc import tracked_db as db


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "Proj-2026-08-01"
    p.mkdir()
    return p


def _v2_database(project: Path):
    """Build a database in the OLD path-keyed shape, as shipped before v3."""
    conn = sqlite3.connect(str(project / db.DB_FILENAME), isolation_level=None)
    conn.executescript("""
        CREATE TABLE tracked (video_path TEXT PRIMARY KEY, tracked_at TEXT NOT NULL,
                              last_opened_at TEXT);
        CREATE TABLE progress_value (video_path TEXT NOT NULL, segment_id TEXT NOT NULL,
                                     option_id TEXT NOT NULL, set_at TEXT NOT NULL,
                                     PRIMARY KEY (video_path, segment_id));
        CREATE TABLE progress_segment (segment_id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                                       name TEXT NOT NULL);
        CREATE TABLE progress_option (option_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL,
                                      position INTEGER NOT NULL, label TEXT NOT NULL,
                                      color TEXT NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '2');
        INSERT INTO tracked VALUES ('/data/a.avi', '2026-07-01T10:00:00Z', '2026-07-02T10:00:00Z');
        INSERT INTO tracked VALUES ('/data/b.avi', '2026-07-01T11:00:00Z', NULL);
        INSERT INTO progress_value VALUES ('/data/a.avi', 'seg_1', 'opt_1', '2026-07-03T10:00:00Z');
        INSERT INTO progress_value VALUES ('/data/c.avi', 'seg_1', 'opt_2', '2026-07-03T11:00:00Z');
    """)
    conn.close()


# ── Fresh database ──────────────────────────────────────────────────────────

def test_fresh_database_is_created_at_v3(project):
    with db.connect(project) as conn:
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert version[0] == "3"


def test_fresh_database_has_every_v3_table(project):
    with db.connect(project) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"video", "tracked", "progress_value", "progress_segment",
            "progress_option", "audit_log", "meta"} <= names


def test_tracked_is_keyed_by_video_id_not_path(project):
    with db.connect(project) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracked)")}
    assert "video_id" in cols
    assert "video_path" not in cols


# ── Migration ───────────────────────────────────────────────────────────────

def test_migration_creates_one_video_row_per_distinct_path(project):
    _v2_database(project)
    with db.connect(project) as conn:
        paths = {r[0] for r in conn.execute("SELECT path FROM video")}
    assert paths == {"/data/a.avi", "/data/b.avi", "/data/c.avi"}


def test_migration_preserves_tracked_rows(project):
    _v2_database(project)
    with db.connect(project) as conn:
        rows = conn.execute(
            "SELECT v.path, t.tracked_at, t.last_opened_at FROM tracked t "
            "JOIN video v ON v.video_id = t.video_id ORDER BY v.path").fetchall()
    assert rows == [
        ("/data/a.avi", "2026-07-01T10:00:00Z", "2026-07-02T10:00:00Z"),
        ("/data/b.avi", "2026-07-01T11:00:00Z", None),
    ]


def test_migration_preserves_progress_values_including_untracked_paths(project):
    """/data/c.avi has a value but was never tracked — it must still migrate."""
    _v2_database(project)
    with db.connect(project) as conn:
        rows = conn.execute(
            "SELECT v.path, p.segment_id, p.option_id FROM progress_value p "
            "JOIN video v ON v.video_id = p.video_id ORDER BY v.path").fetchall()
    assert rows == [("/data/a.avi", "seg_1", "opt_1"),
                    ("/data/c.avi", "seg_1", "opt_2")]


def test_migration_carries_tracked_at_into_first_seen_at(project):
    _v2_database(project)
    with db.connect(project) as conn:
        seen = conn.execute(
            "SELECT first_seen_at FROM video WHERE path='/data/a.avi'").fetchone()[0]
    assert seen == "2026-07-01T10:00:00Z"


def test_migration_leaves_metrics_null_because_it_never_probes(project):
    _v2_database(project)
    with db.connect(project) as conn:
        row = conn.execute(
            "SELECT size_bytes, frame_count, fingerprint FROM video "
            "WHERE path='/data/a.avi'").fetchone()
    assert row == (None, None, None)


def test_migration_is_recorded_in_the_audit_log(project):
    _v2_database(project)
    with db.connect(project) as conn:
        rows = conn.execute(
            "SELECT action, after FROM audit_log WHERE action='migrate_v2_v3'").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][1])["videos"] == 3


def test_migration_runs_once_and_is_a_noop_on_reconnect(project):
    _v2_database(project)
    with db.connect(project) as conn:
        first = conn.execute("SELECT video_id, path FROM video ORDER BY path").fetchall()
    with db.connect(project) as conn:
        second = conn.execute("SELECT video_id, path FROM video ORDER BY path").fetchall()
        migrations = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='migrate_v2_v3'").fetchone()[0]
    assert first == second, "video ids must be stable across reconnects"
    assert migrations == 1


# ── Identity ────────────────────────────────────────────────────────────────

def test_ensure_video_creates_once_and_reuses_thereafter(project):
    with db.connect(project) as conn:
        a = db.ensure_video(conn, "/data/a.avi")
        b = db.ensure_video(conn, "/data/a.avi")
        count = conn.execute("SELECT COUNT(*) FROM video").fetchone()[0]
    assert a == b
    assert a.startswith("vid_")
    assert count == 1


def test_ensure_video_backfills_metrics_on_a_later_call(project):
    with db.connect(project) as conn:
        vid = db.ensure_video(conn, "/data/a.avi")
        assert db.video_row(conn, vid)["fingerprint"] is None
        db.ensure_video(conn, "/data/a.avi", size_bytes=100, frame_count=10)
        row = db.video_row(conn, vid)
    assert row["size_bytes"] == 100
    assert row["frame_count"] == 10
    assert row["fingerprint"] == db.fingerprint(100, 10)


def test_relink_path_keeps_the_id_and_moves_the_data_with_it(project):
    with db.connect(project) as conn:
        vid = db.ensure_video(conn, "/old/a.avi")
        conn.execute("INSERT INTO tracked(video_id, tracked_at) VALUES (?, ?)",
                     (vid, db.now()))
        conn.execute("INSERT INTO progress_value VALUES (?,?,?,?)",
                     (vid, "seg_1", "opt_1", db.now()))
        assert db.relink_path(conn, vid, "/new/renamed.avi") is True
        row = db.video_row(conn, vid)
        still_tracked = conn.execute(
            "SELECT COUNT(*) FROM tracked WHERE video_id=?", (vid,)).fetchone()[0]
        still_valued = conn.execute(
            "SELECT COUNT(*) FROM progress_value WHERE video_id=?", (vid,)).fetchone()[0]
    assert row["path"] == "/new/renamed.avi"
    assert still_tracked == 1, "rename must not orphan the tracked flag"
    assert still_valued == 1, "rename must not orphan progress values"


def test_video_id_for_path_returns_none_when_unknown(project):
    with db.connect(project) as conn:
        assert db.video_id_for_path(conn, "/nope.avi") is None


# ── Fingerprint ─────────────────────────────────────────────────────────────

def test_fingerprint_is_stable_and_depends_on_both_inputs(project):
    assert db.fingerprint(100, 10) == db.fingerprint(100, 10)
    assert db.fingerprint(100, 10) != db.fingerprint(101, 10)
    assert db.fingerprint(100, 10) != db.fingerprint(100, 11)
    assert len(db.fingerprint(100, 10)) == 32          # blake2b digest_size=16


def test_fingerprint_is_none_without_both_metrics():
    assert db.fingerprint(None, 10) is None
    assert db.fingerprint(100, None) is None


def test_two_copies_of_one_recording_share_a_fingerprint_but_not_an_id(project):
    """Expected: a copy IS that recording. Hence the non-unique index."""
    with db.connect(project) as conn:
        a = db.ensure_video(conn, "/data/a.avi", size_bytes=999, frame_count=42)
        b = db.ensure_video(conn, "/backup/a.avi", size_bytes=999, frame_count=42)
        rows = conn.execute(
            "SELECT fingerprint FROM video ORDER BY path").fetchall()
    assert a != b
    assert rows[0][0] == rows[1][0]


# ── Audit ───────────────────────────────────────────────────────────────────

def test_audit_records_actor_action_and_payloads(project):
    with db.connect(project) as conn:
        db.audit(conn, "uid-123", "tracked", "vid_x", "track",
                 before=None, after={"tracked_at": "now"})
        row = conn.execute(
            "SELECT actor, entity, entity_id, action, before, after FROM audit_log"
        ).fetchone()
    assert row[0] == "uid-123"
    assert row[1] == "tracked"
    assert row[2] == "vid_x"
    assert row[3] == "track"
    assert row[4] is None
    assert json.loads(row[5]) == {"tracked_at": "now"}


def test_ensure_video_logs_creation_and_the_later_probe(project):
    with db.connect(project) as conn:
        db.ensure_video(conn, "/data/a.avi", actor="uid-1")
        db.ensure_video(conn, "/data/a.avi", actor="uid-1", size_bytes=5, frame_count=2)
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["create_video", "probe_metadata"]


def test_relink_is_audited_with_before_and_after_paths(project):
    with db.connect(project) as conn:
        vid = db.ensure_video(conn, "/old/a.avi")
        db.relink_path(conn, vid, "/new/a.avi", actor="uid-9")
        row = conn.execute(
            "SELECT before, after FROM audit_log WHERE action='relink_path'").fetchone()
    assert json.loads(row[0]) == {"path": "/old/a.avi"}
    assert json.loads(row[1]) == {"path": "/new/a.avi"}


# ── Read-path cost ──────────────────────────────────────────────────────────

def test_connecting_to_a_current_database_performs_no_write(project):
    """Every read path (list_tracked, get_values, get_definition, resolve)
    opens a connection, so ensure_schema must be read-only once the schema is
    current. Writing the version unconditionally turns each read into a durable
    WAL commit: measured 12 ms on local disk and 29 ms on the NAS, versus
    0.2 ms for a read-only connect.
    """
    with db.connect(project):
        pass                                    # create at v3
    with db.connect(project) as conn:
        assert conn.total_changes == 0, (
            "ensure_schema wrote to an already-current database — every read "
            "now pays for an fsync")


def test_the_schema_version_is_still_written_when_absent(project):
    """The no-write optimisation must not break first-time creation."""
    with db.connect(project) as conn:
        assert conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"


def test_a_stale_version_is_corrected_on_connect(project):
    with db.connect(project) as conn:
        conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    with db.connect(project) as conn:
        assert conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
