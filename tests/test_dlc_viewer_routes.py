"""Tests for the DLC viewer blueprint routes."""
from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True


def _seed_companion_h5(parent: Path, video_stem: str) -> Path:
    """Drop a fake companion h5 next to a video. Content unimportant — the
    /h5-variants route only checks names + file existence."""
    p = parent / f"{video_stem}DLC_HrnetW48_DREADDshuffle1_snapshot_150.h5"
    p.write_bytes(b"")
    return p


def _seed_postproc_run(parent: Path, ts: str, tool_tag: str,
                       video_stem: str, status: str = "success",
                       suffix: str = "_filtered") -> Path:
    """Build <parent>/postproc/<ts>_<tool_tag>/<video_stem>...{suffix}.h5 +
    a sidecar run.json with the given status."""
    run_dir = parent / "postproc" / f"{ts}_{tool_tag}"
    run_dir.mkdir(parents=True, exist_ok=False)
    out_h5 = run_dir / f"{video_stem}DLC_HrnetW48_DREADDshuffle1_snapshot_150{suffix}.h5"
    out_h5.write_bytes(b"")
    (run_dir / "run.json").write_text(_json.dumps({
        "run_id": run_dir.name,
        "status": status,
        "tool":   "deeplabcut" if tool_tag == "filterpredictions" else "refineDLC",
        "action": tool_tag if tool_tag == "filterpredictions" else "pipeline",
    }))
    return out_h5


def test_h5_variants_includes_companion_and_postproc(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)

    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "OM-2_cam0_FAKE_success.mp4"
    video.write_bytes(b"")
    stem = video.stem

    raw_h5 = _seed_companion_h5(tmp_path, stem)
    filtered_h5 = _seed_postproc_run(tmp_path, "20260502-113642",
                                     "filterpredictions", stem)

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["video"] == str(video)
    paths = [v["path"] for v in data["variants"]]
    assert str(raw_h5) in paths
    assert str(filtered_h5) in paths
    raw_entry = next(v for v in data["variants"] if v["path"] == str(raw_h5))
    assert raw_entry["type"] == "raw"
    flt = next(v for v in data["variants"] if v["path"] == str(filtered_h5))
    assert flt["type"] == "filtered"
    assert flt["tool_tag"] == "filterpredictions"
    assert flt["run_id"] == "20260502-113642_filterpredictions"
    assert flt["status"] == "success"
    assert flt["disabled"] is False
    # Companion comes before postproc variants in the list.
    assert paths.index(str(raw_h5)) < paths.index(str(filtered_h5))


def test_h5_variants_marks_failed_runs_disabled(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)

    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "vid.mp4"
    video.write_bytes(b"")
    stem = video.stem
    bad = _seed_postproc_run(tmp_path, "20260502-113700", "filterpredictions",
                             stem, status="failed")

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200
    entry = next(v for v in resp.get_json()["variants"] if v["path"] == str(bad))
    assert entry["status"] == "failed"
    assert entry["disabled"] is True


def test_h5_variants_only_includes_matching_video_stem(flask_test_client, tmp_path, monkeypatch):
    """A postproc dir may hold outputs for multiple videos; only the matching
    video's outputs show up."""
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "VIDEO_A.mp4"
    video.write_bytes(b"")
    other = tmp_path / "VIDEO_B.mp4"
    other.write_bytes(b"")

    mine = _seed_postproc_run(tmp_path, "20260502-120000", "filterpredictions", video.stem)

    # Re-create another video's filtered output into the SAME postproc dir.
    yours_path = (tmp_path / "postproc" / "20260502-120000_filterpredictions" /
                  f"{other.stem}DLC_HrnetW48_DREADDshuffle1_snapshot_150_filtered.h5")
    yours_path.write_bytes(b"")

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    paths = [v["path"] for v in resp.get_json()["variants"]]
    assert str(mine) in paths
    assert str(yours_path) not in paths


def test_h5_variants_empty_when_nothing_around(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "alone.mp4"
    video.write_bytes(b"")
    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200
    assert resp.get_json() == {"video": str(video), "variants": []}


def test_h5_variants_rejects_disallowed_path(flask_test_client, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: False)
    resp = client.get("/dlc/viewer/h5-variants?video=/etc/x.mp4")
    assert resp.status_code == 403
