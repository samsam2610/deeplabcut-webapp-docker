"""Tests for src/dlc/marks_store.py — pure SQLite layer for test-set marks."""
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

from dlc import marks_store as ms


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A bare DLC project root — only what marks_store cares about."""
    p = tmp_path / "Proj-2026-05-19"
    p.mkdir()
    return p


def test_get_marks_on_fresh_project_returns_empty(project):
    assert ms.list_marks(project) == []
    assert ms.get_mode(project) == "random"


def test_set_mark_then_list(project):
    ms.set_mark(project, "vid_a", "img0001.png", True)
    assert ms.list_marks(project) == [("vid_a", "img0001.png")]


def test_set_mark_idempotent(project):
    ms.set_mark(project, "vid_a", "img0001.png", True)
    ms.set_mark(project, "vid_a", "img0001.png", True)
    assert ms.list_marks(project) == [("vid_a", "img0001.png")]


def test_unset_mark_removes_row(project):
    ms.set_mark(project, "vid_a", "img0001.png", True)
    ms.set_mark(project, "vid_a", "img0001.png", False)
    assert ms.list_marks(project) == []


def test_unset_nonexistent_is_noop(project):
    ms.set_mark(project, "vid_a", "img0001.png", False)
    assert ms.list_marks(project) == []


def test_bulk_set_applies_all_ops(project):
    ops = [
        {"video_stem": "vid_a", "image_name": "img0001.png", "marked": True},
        {"video_stem": "vid_a", "image_name": "img0002.png", "marked": True},
        {"video_stem": "vid_b", "image_name": "img0005.png", "marked": True},
    ]
    applied = ms.bulk_set(project, ops)
    assert applied == 3
    rows = set(ms.list_marks(project))
    assert rows == {("vid_a", "img0001.png"), ("vid_a", "img0002.png"), ("vid_b", "img0005.png")}


def test_bulk_set_mixed_add_and_remove(project):
    ms.set_mark(project, "vid_a", "img0001.png", True)
    ops = [
        {"video_stem": "vid_a", "image_name": "img0001.png", "marked": False},
        {"video_stem": "vid_a", "image_name": "img0002.png", "marked": True},
    ]
    ms.bulk_set(project, ops)
    assert ms.list_marks(project) == [("vid_a", "img0002.png")]


def test_get_set_mode_roundtrip(project):
    ms.set_mode(project, "hybrid")
    assert ms.get_mode(project) == "hybrid"
    ms.set_mode(project, "manual")
    assert ms.get_mode(project) == "manual"


def test_set_mode_rejects_unknown(project):
    with pytest.raises(ValueError):
        ms.set_mode(project, "wibble")


def test_clean_stale_removes_only_missing_files(project, tmp_path):
    # Create labeled-data/<stem>/<image> files for two frames; mark three (one missing)
    labeled = project / "labeled-data"
    (labeled / "vid_a").mkdir(parents=True)
    (labeled / "vid_a" / "img0001.png").write_bytes(b"")
    (labeled / "vid_a" / "img0002.png").write_bytes(b"")
    ms.bulk_set(project, [
        {"video_stem": "vid_a", "image_name": "img0001.png", "marked": True},
        {"video_stem": "vid_a", "image_name": "img0002.png", "marked": True},
        {"video_stem": "vid_a", "image_name": "img0003.png", "marked": True},  # missing
    ])
    removed = ms.clean_stale(project)
    assert removed == 1
    rows = set(ms.list_marks(project))
    assert rows == {("vid_a", "img0001.png"), ("vid_a", "img0002.png")}


def test_schema_version_recorded(project):
    ms.set_mark(project, "vid_a", "img0001.png", True)
    db = project / "test_set_marks.sqlite"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "1"


def test_get_marks_grouped_by_stem(project):
    ms.bulk_set(project, [
        {"video_stem": "vid_a", "image_name": "img0001.png", "marked": True},
        {"video_stem": "vid_a", "image_name": "img0002.png", "marked": True},
        {"video_stem": "vid_b", "image_name": "img0005.png", "marked": True},
    ])
    grouped = ms.get_marks_grouped(project)
    assert set(grouped["vid_a"]) == {"img0001.png", "img0002.png"}
    assert grouped["vid_b"] == ["img0005.png"]
