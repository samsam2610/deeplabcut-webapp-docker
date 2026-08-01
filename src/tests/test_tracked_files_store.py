"""Tests for src/dlc/tracked_files.py — now keyed by video_id."""
from __future__ import annotations
from pathlib import Path

import pytest

from dlc import tracked_db as db
from dlc import tracked_files as tf


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "Proj-2026-08-01"
    p.mkdir()
    return p


def db_only_video(project, path):
    """A video row with no tracked row — proves touch_opened won't create one."""
    with db.connect(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        vid = db.ensure_video(conn, path)
        conn.execute("COMMIT")
    return vid


def test_fresh_project_lists_nothing(project):
    assert tf.list_tracked(project) == []


def test_track_returns_a_video_id_and_lists_the_path(project):
    vid = tf.track(project, "/data/a.avi")
    assert vid.startswith("vid_")
    rows = tf.list_tracked(project)
    assert len(rows) == 1
    assert rows[0]["video_id"] == vid
    assert rows[0]["path"] == "/data/a.avi"
    assert rows[0]["last_opened_at"] is None


def test_track_is_idempotent_and_keeps_the_same_id(project):
    first = tf.track(project, "/data/a.avi")
    second = tf.track(project, "/data/a.avi")
    assert first == second
    assert len(tf.list_tracked(project)) == 1


def test_untrack_removes_the_row_but_keeps_the_video_identity(project):
    """Untracking must not destroy the id — re-tracking restores its history."""
    vid = tf.track(project, "/data/a.avi")
    tf.untrack(project, vid)
    assert tf.list_tracked(project) == []
    assert tf.track(project, "/data/a.avi") == vid


def test_touch_opened_only_stamps_an_existing_row(project):
    vid = tf.track(project, "/data/a.avi")
    tf.touch_opened(project, vid)
    assert tf.list_tracked(project)[0]["last_opened_at"] is not None

    other = db_only_video(project, "/data/other.avi")
    tf.touch_opened(project, other)
    assert [r["path"] for r in tf.list_tracked(project)] == ["/data/a.avi"]


def test_renaming_via_relink_keeps_the_file_tracked(project):
    vid = tf.track(project, "/data/a.avi")
    with db.connect(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.relink_path(conn, vid, "/moved/renamed.avi")
        conn.execute("COMMIT")
    rows = tf.list_tracked(project)
    assert len(rows) == 1
    assert rows[0]["video_id"] == vid
    assert rows[0]["path"] == "/moved/renamed.avi"


def test_ordering_recently_opened_first_never_opened_last(project):
    a = tf.track(project, "/data/a.avi")
    tf.track(project, "/data/b.avi")
    c = tf.track(project, "/data/c.avi")
    tf.touch_opened(project, a)
    tf.touch_opened(project, c)
    order = [r["path"] for r in tf.list_tracked(project)]
    assert order[-1] == "/data/b.avi", "never-opened sorts last"
    assert set(order[:2]) == {"/data/a.avi", "/data/c.avi"}


def test_resolve_accepts_either_key(project):
    vid = tf.track(project, "/data/a.avi")
    assert tf.resolve(project, video_id=vid) == vid
    assert tf.resolve(project, path="/data/a.avi") == vid
    assert tf.resolve(project, video_id="vid_nope") is None
    assert tf.resolve(project, path="/nope.avi") is None


def test_every_mutation_is_audited(project):
    vid = tf.track(project, "/data/a.avi", actor="uid-7")
    tf.touch_opened(project, vid, actor="uid-7")
    tf.untrack(project, vid, actor="uid-7")
    with db.connect(project) as conn:
        rows = conn.execute(
            "SELECT action, actor FROM audit_log ORDER BY id").fetchall()
    actions = [r[0] for r in rows]
    assert "create_video" in actions
    assert actions.count("track") == 1
    assert actions.count("mark_opened") == 1
    assert actions.count("untrack") == 1
    assert {r[1] for r in rows} == {"uid-7"}
