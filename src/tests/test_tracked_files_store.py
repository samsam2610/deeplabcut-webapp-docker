"""Tests for src/dlc/tracked_files.py — pure SQLite layer for tracked videos."""
from __future__ import annotations
from pathlib import Path

import pytest

from dlc import tracked_files as tf


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A bare DLC project root — only what tracked_files cares about."""
    p = tmp_path / "Proj-2026-07-31"
    p.mkdir()
    return p


@pytest.fixture
def clock(monkeypatch):
    """Deterministic _now() — tracked_at resolution is 1 s, so tests must control it."""
    ticks = iter([
        "2026-07-31T10:00:00Z", "2026-07-31T10:00:01Z", "2026-07-31T10:00:02Z",
        "2026-07-31T10:00:03Z", "2026-07-31T10:00:04Z", "2026-07-31T10:00:05Z",
    ])
    monkeypatch.setattr(tf, "_now", lambda: next(ticks))


def test_fresh_project_lists_nothing_and_creates_no_db(project):
    assert tf.list_tracked(project) == []
    assert not (project / tf.DB_FILENAME).exists()


def test_track_then_list(project, clock):
    tf.track(project, "/data/eggtart-1_cam0.avi")
    assert tf.list_tracked(project) == [
        {"path": "/data/eggtart-1_cam0.avi",
         "tracked_at": "2026-07-31T10:00:00Z",
         "last_opened_at": None},
    ]


def test_track_is_idempotent_and_preserves_tracked_at(project, clock):
    tf.track(project, "/data/a.avi")
    tf.touch_opened(project, "/data/a.avi")     # 10:00:01
    tf.track(project, "/data/a.avi")            # re-track must not reset anything
    rows = tf.list_tracked(project)
    assert len(rows) == 1
    assert rows[0]["tracked_at"] == "2026-07-31T10:00:00Z"
    assert rows[0]["last_opened_at"] == "2026-07-31T10:00:01Z"


def test_untrack_removes_row_and_is_a_noop_when_absent(project, clock):
    tf.track(project, "/data/a.avi")
    tf.untrack(project, "/data/a.avi")
    assert tf.list_tracked(project) == []
    tf.untrack(project, "/data/a.avi")          # must not raise
    assert tf.list_tracked(project) == []


def test_touch_opened_never_creates_a_row(project, clock):
    tf.touch_opened(project, "/data/never-tracked.avi")
    assert tf.list_tracked(project) == []


def test_ordering_recently_opened_first_never_opened_last(project, clock):
    tf.track(project, "/data/a.avi")            # tracked 10:00:00
    tf.track(project, "/data/b.avi")            # tracked 10:00:01
    tf.track(project, "/data/c.avi")            # tracked 10:00:02
    tf.touch_opened(project, "/data/a.avi")     # opened  10:00:03
    tf.touch_opened(project, "/data/c.avi")     # opened  10:00:04
    assert [r["path"] for r in tf.list_tracked(project)] == [
        "/data/c.avi",   # opened most recently
        "/data/a.avi",   # opened earlier
        "/data/b.avi",   # never opened -> last
    ]
