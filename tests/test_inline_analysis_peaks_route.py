"""Route-level tests for the peak-emission endpoint.

Follows the fixture style of tests/test_reprojection_routes.py (dlc-3D) rather
than tests/test_inline_analysis_routes.py's `flask_test_client` — that fixture
pulls in the full `app.py` import graph, and this module doesn't need it. We
build a minimal Flask app that registers only `dlc.inline_analysis.bp` and
populate `dlc.ctx` directly. Redis is the repo's existing session-scoped
`fake_redis` fixture (tests/conftest.py); its state is cleared per-test here
because that fixture is session-scoped. No `dlc_sandbox_project` (no
multi-GB project copy) and no `flask_test_client` are used anywhere in this
file.

Route contract implemented (see task-6-brief.md, as corrected by the task-6
dispatch note in the plan):
  - h5_paths is REQUIRED in the POST body, parallel to video_paths — the
    scorer string cannot be derived in the Flask request context, so the
    caller (which already receives `scorer` from /range/status) supplies the
    pose h5 paths directly.
  - Dispatch goes through `dlc.inline_analysis._dispatch_emit_peaks`, itself
    a thin wrapper over the patchable `_celery_send_task` seam — never a
    direct `from ..tasks import dlc_emit_peaks` (the worker task object is
    not importable from the Flask container).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import ctx as _ctx  # noqa: E402
import dlc.inline_analysis as inline_analysis  # noqa: E402


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def user_data_dir(tmp_path):
    d = tmp_path / "user-data"
    d.mkdir()
    return d


@pytest.fixture
def client(data_dir, user_data_dir, fake_redis):
    from flask import Flask

    # fake_redis is session-scoped (tests/conftest.py) — clear it so state
    # doesn't leak between tests in this file or from other test modules.
    fake_redis._store.clear()
    fake_redis._hstore.clear()
    fake_redis._zsets.clear()
    fake_redis._sets.clear()
    fake_redis._lists.clear()

    _ctx.setup(data_dir, user_data_dir, fake_redis, None, None, None)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(inline_analysis.bp)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


@pytest.fixture
def tmp_video(data_dir):
    p = data_dir / "vid.mp4"
    p.write_bytes(b"fake video bytes")
    return p


@pytest.fixture
def tmp_h5(data_dir):
    p = data_dir / "vidDLC_resnet50.h5"
    p.write_bytes(b"fake h5 bytes")
    return p


def test_submit_returns_a_req_id_and_queues_the_task(client, monkeypatch, tmp_video, tmp_h5):
    sent = {}
    monkeypatch.setattr("dlc.inline_analysis._dispatch_emit_peaks",
                        lambda **kw: sent.update(kw))
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)],
        "h5_paths": [str(tmp_h5)],
        "ranges": [{"start": 10, "n": 3}],
        "snapshot_path": "/models/trainset/train/snapshot-best-180.pt",
    })
    assert resp.status_code == 202
    assert resp.get_json()["req_id"]
    assert sent["frames"] == [10, 11, 12]
    assert sent["snapshot_name"] == "snapshot-best-180.pt"
    assert sent["model_dir"].endswith("/models/trainset")
    assert sent["video_paths"] == [str(tmp_video)]
    assert sent["h5_paths"] == [str(tmp_h5)]


def test_overlapping_ranges_are_deduped_and_sorted(client, monkeypatch, tmp_video, tmp_h5):
    sent = {}
    monkeypatch.setattr("dlc.inline_analysis._dispatch_emit_peaks",
                        lambda **kw: sent.update(kw))
    client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)],
        "h5_paths": [str(tmp_h5)],
        "ranges": [{"start": 5, "n": 3}, {"start": 6, "n": 3}],
        "snapshot_path": "/models/trainset/train/snap.pt",
    })
    assert sent["frames"] == [5, 6, 7, 8]


def test_missing_video_paths_is_a_400(client):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "ranges": [{"start": 0, "n": 1}], "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 400


def test_a_video_outside_the_data_root_is_a_403(client):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": ["/etc/passwd"],
        "ranges": [{"start": 0, "n": 1}], "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 403


def test_an_empty_range_list_is_a_400(client, tmp_video):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)], "ranges": [],
        "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 400


def test_a_frame_budget_over_the_cap_is_a_400(client, tmp_video):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)],
        "ranges": [{"start": 0, "n": 50_001}],
        "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 400
    assert "50000" in resp.get_json()["error"]


def test_missing_h5_paths_is_a_400(client, tmp_video):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)],
        "ranges": [{"start": 0, "n": 1}],
        "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 400


def test_mismatched_h5_paths_length_is_a_400(client, tmp_video, tmp_h5):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)],
        "h5_paths": [str(tmp_h5), str(tmp_h5)],
        "ranges": [{"start": 0, "n": 1}],
        "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 400


def test_an_h5_path_outside_the_data_root_is_a_403(client, tmp_video):
    resp = client.post("/dlc/project/inline-analysis/peaks", json={
        "video_paths": [str(tmp_video)],
        "h5_paths": ["/etc/passwd"],
        "ranges": [{"start": 0, "n": 1}],
        "snapshot_path": "/m/train/s.pt"})
    assert resp.status_code == 403


def test_status_reports_a_published_result(client, fake_redis):
    fake_redis.hset("inline:result:abc", mapping={
        "status": "done", "n_analyzed": "42", "error": ""})
    d = client.get("/dlc/project/inline-analysis/peaks/status?req_id=abc").get_json()
    assert d["status"] == "done" and d["n_frames"] == 42


def test_status_of_an_unknown_req_is_pending(client):
    d = client.get("/dlc/project/inline-analysis/peaks/status?req_id=nope").get_json()
    assert d["status"] == "pending"
