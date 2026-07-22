"""HTTP-endpoint tests for the triangulate-range routes (dlc/inline_analysis.py).

Celery is mocked (we patch the enqueue + AsyncResult indirections). Redis is
FakeRedis. Reuses the ia_client fixture pattern from test_inline_analysis_routes.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _auth(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["uid"] = "u1"


@pytest.fixture
def ia_client(flask_test_client, dlc_sandbox_project):
    client, app_module, redis, data_dir, user_data_dir = flask_test_client
    redis._store.clear(); redis._hstore.clear(); redis._zsets.clear()
    redis._sets.clear(); redis._lists.clear()
    _auth(client)
    dest = data_dir / dlc_sandbox_project.name
    if not dest.exists():
        shutil.copytree(str(dlc_sandbox_project), str(dest))
    cfg = dest / "config.yaml"
    redis.set("webapp:dlc_project:u1", json.dumps({
        "config_path": str(cfg), "project_path": str(dest), "project": dest.name,
    }))
    yield client, app_module, redis, dest


def _make_cam0(project) -> Path:
    v = project / "videos" / "surv1_cam0_20260123_120000.avi"
    v.parent.mkdir(parents=True, exist_ok=True)
    v.write_bytes(b"")
    return v


class TestTriangulateRangeEnqueue:
    def test_enqueue_returns_req_id(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        with patch("dlc.inline_analysis._enqueue_triangulate_range",
                   return_value=MagicMock(id="celery-xyz")) as mk:
            resp = client.post("/dlc/project/triangulate/range", json={
                "cam0_video": str(v), "start_frame": 0, "n_frames": 10,
            })
        assert resp.status_code == 202, resp.get_json()
        assert resp.get_json()["req_id"] == "celery-xyz"
        assert mk.call_args.args[0] == str(v)
        assert mk.call_args.args[1] == 0
        assert mk.call_args.args[2] == 10

    def test_400_when_no_active_project(self, flask_test_client):
        client, _app, redis, _d, _u = flask_test_client
        redis._store.clear()
        _auth(client)
        resp = client.post("/dlc/project/triangulate/range", json={
            "cam0_video": "/x.avi", "start_frame": 0, "n_frames": 1})
        assert resp.status_code == 400

    def test_400_missing_cam0(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/range", json={
            "start_frame": 0, "n_frames": 1})
        assert resp.status_code == 400

    def test_400_bad_n_frames(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/range", json={
            "cam0_video": str(v), "start_frame": 0, "n_frames": 0})
        assert resp.status_code == 400
        resp = client.post("/dlc/project/triangulate/range", json={
            "cam0_video": str(v), "start_frame": 0, "n_frames": 999999})
        assert resp.status_code == 400

    def test_400_negative_start(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/range", json={
            "cam0_video": str(v), "start_frame": -1, "n_frames": 5})
        assert resp.status_code == 400

    def test_403_path_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/range", json={
            "cam0_video": "/etc/passwd", "start_frame": 0, "n_frames": 5})
        assert resp.status_code in (400, 403)


class TestTriangulateRangeStatus:
    def _fake_meta(self, status, result=None):
        # Mirrors celery backend.get_task_meta(): a dict with status + result
        # (result holds the PROGRESS meta dict / SUCCESS return / FAILURE exception).
        return {"status": status, "result": result, "traceback": None}

    def test_pending(self, ia_client):
        client, _app, _r, _p = ia_client
        with patch("dlc.inline_analysis._triangulate_task_meta",
                   return_value=self._fake_meta("PENDING")):
            resp = client.get("/dlc/project/triangulate/range/status?req_id=abc")
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["state"] == "PENDING"
        assert body["result"] is None
        assert body["error"] is None

    def test_progress(self, ia_client):
        client, _app, _r, _p = ia_client
        info = {"progress": 42, "stage": "Triangulating…"}
        with patch("dlc.inline_analysis._triangulate_task_meta",
                   return_value=self._fake_meta("PROGRESS", result=info)):
            resp = client.get("/dlc/project/triangulate/range/status?req_id=abc")
        body = resp.get_json()
        assert body["state"] == "PROGRESS"
        assert body["progress"] == 42
        assert body["stage"] == "Triangulating…"

    def test_success_includes_result(self, ia_client):
        client, _app, _r, _p = ia_client
        result = {"pair_name": "p", "raw_csv": "/x/pose-3d/p_3d.csv"}
        with patch("dlc.inline_analysis._triangulate_task_meta",
                   return_value=self._fake_meta("SUCCESS", result=result)):
            resp = client.get("/dlc/project/triangulate/range/status?req_id=abc")
        body = resp.get_json()
        assert body["state"] == "SUCCESS"
        assert body["progress"] == 100
        assert body["result"] == result

    def test_failure_includes_error(self, ia_client):
        client, _app, _r, _p = ia_client
        with patch("dlc.inline_analysis._triangulate_task_meta",
                   return_value=self._fake_meta("FAILURE", result=RuntimeError("boom"))):
            resp = client.get("/dlc/project/triangulate/range/status?req_id=abc")
        body = resp.get_json()
        assert body["state"] == "FAILURE"
        assert "boom" in body["error"]

    def test_400_missing_req_id(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get("/dlc/project/triangulate/range/status")
        assert resp.status_code == 400


class TestTriangulateCoverage:
    def test_absent_canonical_returns_zero_buckets(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.get(
            f"/dlc/project/triangulate/coverage?cam0_video={v}&buckets=8")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["buckets"] == [0.0] * 8
        assert body["nframes"] == 0

    def test_reflects_existing_canonical(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        from dlc import canonical_3d as c3d
        import pandas as pd
        pair = c3d.pair_name_from_cam0(str(v))
        df = pd.DataFrame(
            {"nose_x": [1.0] * 10, "nose_error": [1.0] * 10},
            index=pd.Index(range(10), name="fnum"))
        c3d.write_range_to_canonical_3d(v.parent, pair, df)
        resp = client.get(
            f"/dlc/project/triangulate/coverage?cam0_video={v}&buckets=2")
        body = resp.get_json()
        assert body["nframes"] == 10
        assert body["buckets"] == [1.0, 1.0]

    def test_nframes_param_scales_bar_to_full_video(self, ia_client):
        # 3D only at frames 0..9, but the video is 100 frames → the bar must
        # scale to 100 (aligns with the seek bar), not the canonical's len 10.
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        from dlc import canonical_3d as c3d
        import pandas as pd
        pair = c3d.pair_name_from_cam0(str(v))
        df = pd.DataFrame(
            {"nose_x": [1.0] * 10, "nose_error": [1.0] * 10},
            index=pd.Index(range(10), name="fnum"))
        c3d.write_range_to_canonical_3d(v.parent, pair, df)
        resp = client.get(
            f"/dlc/project/triangulate/coverage?cam0_video={v}&buckets=10&nframes=100")
        body = resp.get_json()
        assert body["nframes"] == 100
        assert body["buckets"][0] > 0.0                      # frames 0..9 → first bucket
        assert sum(1 for b in body["buckets"] if b > 0) == 1  # only the first region

    def test_400_missing_cam0(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get("/dlc/project/triangulate/coverage?buckets=4")
        assert resp.status_code == 400

    def test_403_path_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get(
            "/dlc/project/triangulate/coverage?cam0_video=/etc/x.avi&buckets=4")
        assert resp.status_code == 403


def _write_pose_canonical(v, *, source="filtered"):
    """Write a small 3D canonical for the pair derived from cam0 video ``v``.
    Two bodyparts (Wrist, MCP-1 → one bone), frames 0..2 populated."""
    from dlc import canonical_3d as c3d
    import numpy as np
    import pandas as pd
    pair = c3d.pair_name_from_cam0(str(v))
    bps = ("Wrist", "MCP-1")
    cols = {}
    for i, bp in enumerate(bps):
        cols[f"{bp}_x"] = [float(i)] * 3
        cols[f"{bp}_y"] = [float(i + 1)] * 3
        cols[f"{bp}_z"] = [float(i * 2)] * 3
        cols[f"{bp}_error"] = [1.0] * 3
    df = pd.DataFrame(cols, index=pd.Index(range(3), name="fnum"))
    if source == "raw":
        c3d.write_range_to_canonical_3d(v.parent, pair, df)
    else:
        c3d.write_range_to_canonical_3d(v.parent, pair, df)
        c3d.medfilt_range_and_splice(
            v.parent, pair, 0, 3, {"filter3d": {"medfilt": 3}})
    return pair


class TestTriangulatePoses3d:
    def test_absent_canonical_empty_at_200(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.get(f"/dlc/project/triangulate/poses-3d?cam0_video={v}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body == {"bodyparts": [], "skeleton": [],
                        "frames": [], "points": [], "bounds": None}

    def test_returns_populated_poses_and_skeleton(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        _write_pose_canonical(v, source="filtered")
        resp = client.get(f"/dlc/project/triangulate/poses-3d?cam0_video={v}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["bodyparts"] == ["Wrist", "MCP-1"]  # native CSV column order
        assert body["skeleton"] == [["Wrist", "MCP-1"]]
        assert body["frames"] == [0, 1, 2]
        assert len(body["points"]) == 3
        # each row aligned to bodyparts, each point a [x,y,z] triple
        for row in body["points"]:
            assert len(row) == 2
            assert all(len(p) == 3 for p in row)
        assert body["bounds"] is not None
        assert "center" in body["bounds"] and "size" in body["bounds"]

    def test_source_raw_vs_filtered(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        from dlc import canonical_3d as c3d
        import pandas as pd
        pair = c3d.pair_name_from_cam0(str(v))
        # raw spans 0..4; filtered only splices 1..2
        cols = {}
        for i, bp in enumerate(("Wrist", "MCP-1")):
            cols[f"{bp}_x"] = [float(i)] * 5
            cols[f"{bp}_y"] = [float(i)] * 5
            cols[f"{bp}_z"] = [float(i)] * 5
            cols[f"{bp}_error"] = [1.0] * 5
        df = pd.DataFrame(cols, index=pd.Index(range(5), name="fnum"))
        c3d.write_range_to_canonical_3d(v.parent, pair, df)
        c3d.medfilt_range_and_splice(
            v.parent, pair, 1, 2, {"filter3d": {"medfilt": 3}})
        raw = client.get(
            f"/dlc/project/triangulate/poses-3d?cam0_video={v}&source=raw"
        ).get_json()
        filt = client.get(
            f"/dlc/project/triangulate/poses-3d?cam0_video={v}&source=filtered"
        ).get_json()
        assert raw["frames"] == [0, 1, 2, 3, 4]
        assert filt["frames"] == [1, 2]

    def test_400_missing_cam0(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get("/dlc/project/triangulate/poses-3d")
        assert resp.status_code == 400

    def test_403_path_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get(
            "/dlc/project/triangulate/poses-3d?cam0_video=/etc/x.avi")
        assert resp.status_code == 403


_CONFIG_TOML = """\
# anipose config
[labeling]
scheme = [["Wrist", "MCP-1"]]

[triangulation]
cam_regex = "cam([0-9])"
ransac = false
scale_smooth = 2.0

[filter]
enabled = false
type = "medfilt"
medfilt = 7

[filter3d]
enabled = true
medfilt = 5
offset_threshold = 15
"""


def _write_config_toml(project):
    """config.toml lives at parent(session)/config.toml. cam0 is under
    project/videos/, so parent.parent == project → project/config.toml."""
    p = project / "config.toml"
    p.write_text(_CONFIG_TOML)
    return p


class TestTriangulateConfig:
    def test_get_returns_params(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        _write_config_toml(project)
        resp = client.get(f"/dlc/project/triangulate/config?cam0_video={v}")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert set(body) == {"triangulation", "filter", "filter3d"}
        assert body["triangulation"]["cam_regex"] == "cam([0-9])"
        assert body["filter"]["medfilt"] == 7
        assert body["filter3d"]["enabled"] is True

    def test_post_persists_and_echoes(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        cfg = _write_config_toml(project)
        before = cfg.read_text()
        resp = client.post("/dlc/project/triangulate/config", json={
            "cam0_video": str(v),
            "params": {"filter": {"medfilt": 11, "enabled": True},
                       "filter3d": {"offset_threshold": 42}},
        })
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["ok"] is True
        assert body["params"]["filter"]["medfilt"] == 11
        assert body["params"]["filter"]["enabled"] is True
        assert body["params"]["filter3d"]["offset_threshold"] == 42
        # persisted to disk + scheme/comment preserved
        after = cfg.read_text()
        assert "# anipose config" in after
        assert 'scheme = [["Wrist", "MCP-1"]]' in after
        assert before != after

    def test_get_absent_config_400(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)  # no config.toml written
        resp = client.get(f"/dlc/project/triangulate/config?cam0_video={v}")
        assert resp.status_code == 400
        assert "config.toml not found" in resp.get_json()["error"]

    def test_post_absent_config_400(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/config", json={
            "cam0_video": str(v), "params": {"filter": {"medfilt": 7}}})
        assert resp.status_code == 400

    def test_get_400_missing_cam0(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get("/dlc/project/triangulate/config")
        assert resp.status_code == 400

    def test_post_400_missing_cam0(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/config", json={
            "params": {"filter": {"medfilt": 7}}})
        assert resp.status_code == 400

    def test_get_403_path_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.get(
            "/dlc/project/triangulate/config?cam0_video=/etc/x.avi")
        assert resp.status_code == 403

    def test_post_403_path_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/config", json={
            "cam0_video": "/etc/x.avi", "params": {"filter": {"medfilt": 7}}})
        assert resp.status_code == 403

    def test_post_invalid_params_400(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        _write_config_toml(project)
        resp = client.post("/dlc/project/triangulate/config", json={
            "cam0_video": str(v), "params": {"filter": {"medfilt": 8}}})
        assert resp.status_code == 400
        assert "medfilt" in resp.get_json()["error"]


class TestTriangulateRefilter:
    def _write_raw_with_spike(self, v):
        """Raw canonical spanning frames 0..10, all coords 0.0 except a spike at
        frame 5 (Wrist_x=100). Returns the pair name."""
        from dlc import canonical_3d as c3d
        import pandas as pd
        pair = c3d.pair_name_from_cam0(str(v))
        cols = {}
        for i, bp in enumerate(("Wrist", "MCP-1")):
            cols[f"{bp}_x"] = [0.0] * 11
            cols[f"{bp}_y"] = [0.0] * 11
            cols[f"{bp}_z"] = [0.0] * 11
            cols[f"{bp}_error"] = [1.0] * 11
        df = pd.DataFrame(cols, index=pd.Index(range(11), name="fnum"))
        df.loc[5, "Wrist_x"] = 100.0  # spike
        c3d.write_range_to_canonical_3d(v.parent, pair, df)
        return pair

    def test_refilter_rewrites_filtered_and_smooths_spike(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        pair = self._write_raw_with_spike(v)
        from dlc import canonical_3d as c3d
        assert not c3d.filtered_3d_csv_path(v.parent, pair).exists()
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "cam0_video": str(v), "medfilt": 5, "offset_threshold": 50})
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["ok"] is True
        assert body["start"] == 0 and body["n"] == 11
        assert body["medfilt"] == 5
        # filtered canonical was (re)written and the spike is smoothed away
        import pandas as pd
        filt = pd.read_csv(
            c3d.filtered_3d_csv_path(v.parent, pair), index_col=0)
        assert abs(filt.loc[5, "Wrist_x"]) < 1.0

    def test_absent_raw_returns_400(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "cam0_video": str(v), "medfilt": 5, "offset_threshold": 15})
        assert resp.status_code == 400
        assert "triangulate" in resp.get_json()["error"]

    def test_400_missing_cam0(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "medfilt": 5, "offset_threshold": 15})
        assert resp.status_code == 400

    def test_403_path_outside_root(self, ia_client):
        client, _app, _r, _p = ia_client
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "cam0_video": "/etc/x.avi", "medfilt": 5, "offset_threshold": 15})
        assert resp.status_code == 403

    def test_400_even_medfilt(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "cam0_video": str(v), "medfilt": 4, "offset_threshold": 15})
        assert resp.status_code == 400

    def test_400_medfilt_out_of_range(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "cam0_video": str(v), "medfilt": 201, "offset_threshold": 15})
        assert resp.status_code == 400

    def test_400_negative_offset(self, ia_client):
        client, _app, _r, project = ia_client
        v = _make_cam0(project)
        resp = client.post("/dlc/project/triangulate/refilter", json={
            "cam0_video": str(v), "medfilt": 5, "offset_threshold": -1})
        assert resp.status_code == 400
