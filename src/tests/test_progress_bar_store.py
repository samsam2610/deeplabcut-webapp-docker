"""Tests for src/dlc/progress_bar.py — definition + per-file values."""
from __future__ import annotations
from pathlib import Path

import pytest

from dlc import progress_bar as pb
from dlc import tracked_files as tf


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "Proj-2026-08-01"
    p.mkdir()
    return p


def _seg(name, options):
    return {"name": name, "options": options}


def test_fresh_project_has_no_segments(project):
    assert pb.get_definition(project) == {"segments": []}


def test_save_then_get_assigns_ids_and_preserves_order(project):
    pb.save_definition(project, [
        _seg("Label", [{"label": "Todo", "color": "#888888"},
                       {"label": "Done", "color": "#2ea043"}]),
        _seg("QC", [{"label": "Pending", "color": "#e0a800"}]),
    ])
    got = pb.get_definition(project)["segments"]
    assert [s["name"] for s in got] == ["Label", "QC"]
    assert [o["label"] for o in got[0]["options"]] == ["Todo", "Done"]
    assert got[0]["segment_id"].startswith("seg_")
    assert got[0]["options"][0]["option_id"].startswith("opt_")


def test_rename_and_recolour_keep_ids(project):
    pb.save_definition(project, [_seg("Label", [{"label": "Todo", "color": "#888888"}])])
    before = pb.get_definition(project)["segments"][0]
    sid, oid = before["segment_id"], before["options"][0]["option_id"]

    pb.save_definition(project, [{
        "segment_id": sid, "name": "Labelling",
        "options": [{"option_id": oid, "label": "Not started", "color": "#111111"}],
    }])
    after = pb.get_definition(project)["segments"][0]
    assert after["segment_id"] == sid
    assert after["name"] == "Labelling"
    assert after["options"][0]["option_id"] == oid
    assert after["options"][0]["color"] == "#111111"





def test_rejects_more_than_ten_segments(project):
    too_many = [_seg(f"S{i}", []) for i in range(11)]
    with pytest.raises(ValueError):
        pb.save_definition(project, too_many)
    assert pb.get_definition(project) == {"segments": []}


def test_accepts_exactly_ten_segments(project):
    pb.save_definition(project, [_seg(f"S{i}", []) for i in range(10)])
    assert len(pb.get_definition(project)["segments"]) == 10


def test_rejects_malformed_colour(project):
    with pytest.raises(ValueError):
        pb.save_definition(project, [_seg("Label", [{"label": "X", "color": "red"}])])
    with pytest.raises(ValueError):
        pb.save_definition(project, [
            _seg("Label", [{"label": "X", "color": "#fff; background:url(x)"}])
        ])


def test_duplicate_colours_are_allowed(project):
    """No uniqueness constraint — same colour twice in one segment is fine."""
    pb.save_definition(project, [_seg("Label", [
        {"label": "A", "color": "#2ea043"},
        {"label": "B", "color": "#2ea043"},
    ])])
    assert len(pb.get_definition(project)["segments"][0]["options"]) == 2


def test_tracking_and_the_definition_share_one_database(project):
    """Both stores open the same file via tracked_db; neither may disturb the
    other's tables."""
    tf.track(project, "/data/a.avi")
    pb.save_definition(project, [_seg("Label", [])])
    assert [r["path"] for r in tf.list_tracked(project)] == ["/data/a.avi"]
    assert len(pb.get_definition(project)["segments"]) == 1

def _video(project, path):
    """Mint a video_id the way the routes will."""
    from dlc import tracked_db as db
    with db.connect(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        vid = db.ensure_video(conn, path)
        conn.execute("COMMIT")
    return vid


def test_values_round_trip_and_clear(project):
    pb.save_definition(project, [_seg("Label", [{"label": "Done", "color": "#2ea043"}])])
    seg = pb.get_definition(project)["segments"][0]
    sid, oid = seg["segment_id"], seg["options"][0]["option_id"]
    vid = _video(project, "/data/a.avi")

    pb.set_value(project, vid, sid, oid)
    assert pb.get_values(project, [vid]) == {vid: {sid: oid}}

    pb.set_value(project, vid, sid, None)
    assert pb.get_values(project, [vid]) == {}


def test_get_values_batches_and_omits_videos_without_values(project):
    pb.save_definition(project, [_seg("Label", [{"label": "Done", "color": "#2ea043"}])])
    seg = pb.get_definition(project)["segments"][0]
    sid, oid = seg["segment_id"], seg["options"][0]["option_id"]
    a, b = _video(project, "/data/a.avi"), _video(project, "/data/b.avi")
    pb.set_value(project, a, sid, oid)
    assert set(pb.get_values(project, [a, b])) == {a}


def test_deleting_a_segment_leaves_its_values_in_the_db(project):
    """No cascade: the value survives so re-adding the ID restores it."""
    pb.save_definition(project, [_seg("Label", [{"label": "Done", "color": "#2ea043"}])])
    seg = pb.get_definition(project)["segments"][0]
    sid, oid = seg["segment_id"], seg["options"][0]["option_id"]
    vid = _video(project, "/data/a.avi")
    pb.set_value(project, vid, sid, oid)

    pb.save_definition(project, [])
    assert pb.get_definition(project) == {"segments": []}
    assert pb.get_values(project, [vid]) == {vid: {sid: oid}}


def test_values_survive_a_rename(project):
    """The whole point of video_id: moving the file keeps its progress."""
    from dlc import tracked_db as db
    pb.save_definition(project, [_seg("Label", [{"label": "Done", "color": "#2ea043"}])])
    seg = pb.get_definition(project)["segments"][0]
    sid, oid = seg["segment_id"], seg["options"][0]["option_id"]
    vid = _video(project, "/data/a.avi")
    pb.set_value(project, vid, sid, oid)

    with db.connect(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.relink_path(conn, vid, "/elsewhere/renamed.avi")
        conn.execute("COMMIT")

    assert pb.get_values(project, [vid]) == {vid: {sid: oid}}


def test_definition_and_value_writes_are_audited(project):
    from dlc import tracked_db as db
    pb.save_definition(project, [_seg("L", [{"label": "D", "color": "#2ea043"}])],
                       actor="uid-3")
    seg = pb.get_definition(project)["segments"][0]
    vid = _video(project, "/data/a.avi")
    pb.set_value(project, vid, seg["segment_id"], seg["options"][0]["option_id"],
                 actor="uid-3")
    pb.set_value(project, vid, seg["segment_id"], None, actor="uid-3")

    with db.connect(project) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit_log ORDER BY id")]
    assert actions.count("save_definition") == 1
    assert actions.count("set_value") == 1
    assert actions.count("clear_value") == 1
