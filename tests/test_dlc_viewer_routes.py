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


def test_h5_variants_returns_404_when_parent_missing(flask_test_client, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    resp = client.get("/dlc/viewer/h5-variants?video=/nonexistent/path/to/video.mp4")
    assert resp.status_code == 404


def test_h5_variants_handles_non_dict_sidecar(flask_test_client, tmp_path, monkeypatch):
    """A run.json that's a JSON list (or anything non-dict) must not 500."""
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "vid.mp4"
    video.write_bytes(b"")
    run_dir = tmp_path / "postproc" / "20260502-130000_filterpredictions"
    run_dir.mkdir(parents=True)
    (run_dir / f"{video.stem}DLC_resnet_filtered.h5").write_bytes(b"")
    (run_dir / "run.json").write_text("[\"not a dict\"]")

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200
    entry = next(v for v in resp.get_json()["variants"] if "filtered" in v["path"])
    assert entry["status"] is None
    assert entry["disabled"] is False


def test_dir_with_h5_returns_videos_with_h5_counts(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    # Two videos: one with companion + one postproc filtered, one without h5.
    v1 = tmp_path / "vidA.avi"; v1.write_bytes(b"")
    v2 = tmp_path / "vidB.mp4"; v2.write_bytes(b"")
    _seed_companion_h5(tmp_path, v1.stem)
    _seed_postproc_run(tmp_path, "20260502-120000", "filterpredictions", v1.stem)

    resp = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["path"] == str(tmp_path)
    by_name = {v["name"]: v for v in data["videos"]}
    assert by_name["vidA.avi"]["has_h5"] is True
    assert by_name["vidA.avi"]["h5_count"] == 2  # companion + postproc filtered
    assert by_name["vidB.mp4"]["has_h5"] is False
    assert by_name["vidB.mp4"]["h5_count"] == 0


def test_dir_with_h5_cache_hit_avoids_rebuild(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    v = tmp_path / "vid.avi"; v.write_bytes(b"")
    _seed_companion_h5(tmp_path, v.stem)

    calls = {"n": 0}
    real_build = vw._build_dir_with_h5
    def spy(d, mtime):
        calls["n"] += 1
        return real_build(d, mtime)
    monkeypatch.setattr(vw, "_build_dir_with_h5", spy)

    r1 = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    r2 = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls["n"] == 1, "second request must hit Redis cache"


def test_dir_with_h5_invalidates_on_dir_mtime_change(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    v = tmp_path / "vid.avi"; v.write_bytes(b"")
    _seed_companion_h5(tmp_path, v.stem)

    calls = {"n": 0}
    real_build = vw._build_dir_with_h5
    def spy(d, mtime):
        calls["n"] += 1
        return real_build(d, mtime)
    monkeypatch.setattr(vw, "_build_dir_with_h5", spy)

    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    # Touch a new file to bump dir mtime.
    import time as _time
    _time.sleep(1.1)  # mtime granularity = 1s on some FS
    (tmp_path / "newfile.txt").write_text("x")
    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert calls["n"] == 2, "dir mtime change must invalidate cache"


def test_dir_with_h5_invalidates_on_postproc_run(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    v = tmp_path / "vid.avi"; v.write_bytes(b"")
    _seed_companion_h5(tmp_path, v.stem)

    calls = {"n": 0}
    real_build = vw._build_dir_with_h5
    def spy(d, mtime):
        calls["n"] += 1
        return real_build(d, mtime)
    monkeypatch.setattr(vw, "_build_dir_with_h5", spy)

    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    import time as _time
    _time.sleep(1.1)
    _seed_postproc_run(tmp_path, "20260502-130000", "filterpredictions", v.stem)
    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert calls["n"] == 2, "new postproc/<ts>_*/ must invalidate cache"


def test_dir_with_h5_404_on_missing_path(flask_test_client, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    resp = client.get("/dlc/viewer/dir-with-h5?path=/nonexistent/dir")
    assert resp.status_code == 404


def test_dir_with_h5_403_on_disallowed_path(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: False)
    resp = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert resp.status_code == 403


def test_viewer_load_h5_invalidates_cache_on_mtime_change():
    """Regression guard: viewer_load_h5 must invalidate its in-memory
    cache when the .h5 file's mtime changes.

    Inline Analysis (and any re-analyze) REWRITES the .h5 on disk when a
    new frame-range is processed. A path-only cache served the
    pre-rewrite DataFrame, so freshly-analyzed frames read back empty and
    their markers never appeared (the dreaded "analysis finished, no
    marker"). See session 2026-05-20.
    """
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[1] / "src" / "dlc" / "viewer.py").read_text()
    fn_start = src.find("def viewer_load_h5(")
    assert fn_start > 0, "viewer_load_h5 not found"
    body = src[fn_start:fn_start + 2000]
    assert ".st_mtime" in body, (
        "viewer_load_h5 must read the file mtime to guard the cache"
    )
    assert 'cached.get("mtime")' in body, (
        "viewer_load_h5 must compare the cached mtime before returning a hit"
    )
    assert '"mtime": cur_mtime' in body, (
        "the cache entry must store the mtime it was loaded at"
    )
