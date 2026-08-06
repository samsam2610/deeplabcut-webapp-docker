"""HTTP surface of the Batch Analyze blueprint.

Deliberately does NOT use the `dlc_sandbox_project` fixture: none of these
routes read the model tree (the task does), and that fixture copies a
multi-gigabyte project per test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def ba_client(flask_test_client):
    """Client with a minimal single-animal PyTorch project active in redis."""
    client, app_module, redis, data_dir, _user_data_dir = flask_test_client
    for store in (redis._store, redis._hstore, redis._lists):
        store.clear()

    project = data_dir / "proj"
    (project / "videos").mkdir(parents=True)
    (project / "config.yaml").write_text(
        "multianimalproject: false\nengine: pytorch\n")
    video = project / "videos" / "banh-mi-1_cam0_20260704_104915.avi"
    video.write_bytes(b"v")

    # NOTE: session_transaction pushes and pops its own request context, which
    # pops the one flask_test_client holds open, so every test in this file
    # ERRORS during teardown after passing. That is a wart in the shared
    # conftest fixture, not in these tests — test_inline_analysis_routes.py and
    # the other route-test files behave identically. Fixing it means changing
    # the fixture for every route test in the repo.
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["uid"] = "u1"
    redis.set("webapp:dlc_project:u1", json.dumps({
        "config_path":  str(project / "config.yaml"),
        "project_path": str(project),
        "project":      "proj",
        "engine":       "pytorch",
    }))
    yield client, redis, project, str(video)


def _start(client, **over):
    body = {"videos": [over.pop("video")], "mode": "all", "policy": "latest"}
    body.update(over)
    return client.post("/dlc/project/batch-analyze/start", json=body)


class TestStart:
    def test_dispatches_the_batch_task_and_records_the_request(self, ba_client):
        client, redis, project, video = ba_client
        sent = []
        with patch("dlc.batch_analyze._celery_send_task",
                   side_effect=lambda n, *, kwargs, queue: sent.append((n, kwargs, queue))):
            res = _start(client, video=video, mode="tag",
                         tags=["start-failure", "start-success"],
                         before=200, after=599, both_cams=True,
                         wait_for_training=True)
        assert res.status_code == 202
        batch_id = res.get_json()["batch_id"]

        # Queue `celery`, not `pytorch` — the batch task does no GPU work.
        assert sent == [("tasks.dlc_batch_analyze", {"batch_id": batch_id}, "celery")]

        rec = redis.hgetall(f"dlc:batch:{batch_id}")
        assert json.loads(rec["tags"]) == ["start-failure", "start-success"]
        assert rec["mode"] == "tag"
        assert rec["both_cams"] == "1"
        assert rec["wait_for_training"] == "1"
        assert rec["user_id"] == "u1"
        assert float(rec["deadline"]) > float(rec["created_at"])

    def test_captures_the_pin_at_queue_time(self, ba_client):
        # Resolution happens later (possibly after training), but the pin is
        # read now so re-pinning mid-wait cannot silently change the model.
        client, redis, project, video = ba_client
        with patch("dlc.batch_analyze._project_settings.get_setting",
                   return_value="dlc-models-pytorch/iteration-24/x/train/snapshot-best-150.pt"):
            with patch("dlc.batch_analyze._celery_send_task"):
                res = _start(client, video=video, policy="pinned")
        rec = redis.hgetall(f"dlc:batch:{res.get_json()['batch_id']}")
        assert rec["pinned_snapshot"].endswith("snapshot-best-150.pt")

    def test_rejects_an_empty_queue(self, ba_client):
        client, _redis, _project, _video = ba_client
        res = client.post("/dlc/project/batch-analyze/start",
                          json={"videos": [], "mode": "all", "policy": "latest"})
        assert res.status_code == 400
        assert "queue at least one video" in res.get_json()["error"]

    def test_rejects_tag_mode_with_no_tags(self, ba_client):
        client, _redis, _project, video = ba_client
        res = _start(client, video=video, mode="tag", tags=[])
        assert res.status_code == 400
        assert "at least one tag" in res.get_json()["error"]

    def test_rejects_an_unknown_model_policy(self, ba_client):
        client, _redis, _project, video = ba_client
        res = _start(client, video=video, policy="whatever")
        assert res.status_code == 400
        assert "model option" in res.get_json()["error"]

    def test_rejects_an_unknown_mode(self, ba_client):
        client, _redis, _project, video = ba_client
        res = _start(client, video=video, mode="sideways")
        assert res.status_code == 400

    def test_rejects_a_path_outside_the_data_root(self, ba_client):
        client, _redis, _project, _video = ba_client
        res = client.post("/dlc/project/batch-analyze/start",
                          json={"videos": ["/etc/passwd.avi"], "mode": "all",
                                "policy": "latest"})
        assert res.status_code == 403

    def test_requires_an_active_project(self, ba_client):
        client, redis, _project, video = ba_client
        redis.delete("webapp:dlc_project:u1")
        res = _start(client, video=video)
        assert res.status_code == 400
        assert "No active DLC project" in res.get_json()["error"]


class TestStatus:
    def _submitted(self, client, redis, video, req_ids):
        with patch("dlc.batch_analyze._celery_send_task"):
            batch_id = _start(client, video=video).get_json()["batch_id"]
        redis.hset(f"dlc:batch:{batch_id}", mapping={
            "state": "submitted", "req_ids": json.dumps(req_ids),
            "n_frames": "1600", "snapshot_label": "snapshot-180",
        })
        return batch_id

    def test_rolls_up_per_range_results(self, ba_client):
        client, redis, _project, video = ba_client
        batch_id = self._submitted(client, redis, video, ["r1", "r2", "r3"])
        redis.hset("inline:result:r1", mapping={"status": "done", "n_analyzed": "800", "n_skipped": "0"})
        redis.hset("inline:result:r2", mapping={"status": "done", "n_analyzed": "300", "n_skipped": "500"})
        # r3 still pending

        d = client.get(f"/dlc/project/batch-analyze/status?batch_id={batch_id}").get_json()
        assert d["state"] == "submitted"          # not all ranges are in yet
        assert (d["ranges_done"], d["n_ranges"]) == (2, 3)
        assert (d["frames_analyzed"], d["frames_skipped"]) == (1100, 500)
        assert d["snapshot"] == "snapshot-180"

    def test_flips_to_complete_once_every_range_reports(self, ba_client):
        client, redis, _project, video = ba_client
        batch_id = self._submitted(client, redis, video, ["r1", "r2"])
        redis.hset("inline:result:r1", mapping={"status": "done", "n_analyzed": "10"})
        redis.hset("inline:result:r2", mapping={"status": "error", "error": "boom"})

        d = client.get(f"/dlc/project/batch-analyze/status?batch_id={batch_id}").get_json()
        assert d["state"] == "complete"
        assert d["ranges_error"] == 1
        assert d["last_error"] == "boom"
        # The state is persisted, so the Jobs row stops showing "running".
        assert redis.hgetall(f"dlc:batch:{batch_id}")["state"] == "complete"

    def test_unknown_batch_is_404(self, ba_client):
        client, _redis, _project, _video = ba_client
        assert client.get("/dlc/project/batch-analyze/status?batch_id=nope").status_code == 404


class TestCancel:
    def test_cancels_a_batch_that_has_not_submitted(self, ba_client):
        client, redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            batch_id = _start(client, video=video, wait_for_training=True).get_json()["batch_id"]
        redis.hset(f"dlc:batch:{batch_id}", "state", "waiting")

        d = client.post("/dlc/project/batch-analyze/cancel",
                        json={"batch_id": batch_id}).get_json()
        assert d["state"] == "cancelled"

    def test_a_submitted_batch_keeps_its_state(self, ba_client):
        # Its ranges are already on the session queue; stopping those is the
        # session's job, so cancel must not claim to have stopped them.
        client, redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            batch_id = _start(client, video=video).get_json()["batch_id"]
        redis.hset(f"dlc:batch:{batch_id}", "state", "submitted")

        d = client.post("/dlc/project/batch-analyze/cancel",
                        json={"batch_id": batch_id}).get_json()
        assert d["state"] == "submitted"

    def test_unknown_batch_is_404(self, ba_client):
        client, _redis, _project, _video = ba_client
        res = client.post("/dlc/project/batch-analyze/cancel", json={"batch_id": "nope"})
        assert res.status_code == 404


class TestSnapshotAndGpu:
    def test_an_explicit_snapshot_beats_the_persisted_pin(self, ba_client):
        # The dropdown is what the user is looking at; the persisted pin is
        # only a default. Running the pin while the dropdown shows something
        # else is a silent wrong-model run.
        client, redis, project, video = ba_client
        train = project / "dlc-models-pytorch" / "iteration-9" / "x" / "train"
        train.mkdir(parents=True)
        (train / "snapshot-200.pt").write_bytes(b"x")
        rel = "dlc-models-pytorch/iteration-9/x/train/snapshot-200.pt"

        with patch("dlc.batch_analyze._project_settings.get_setting",
                   return_value="some/other/snapshot-050.pt"):
            with patch("dlc.batch_analyze._celery_send_task"):
                res = _start(client, video=video, policy="pinned", snapshot_rel=rel)
        assert res.status_code == 202
        rec = redis.hgetall(f"dlc:batch:{res.get_json()['batch_id']}")
        assert rec["pinned_snapshot"] == rel

    def test_a_snapshot_outside_the_project_is_rejected(self, ba_client):
        client, _redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            res = _start(client, video=video, policy="pinned",
                         snapshot_rel="../../../etc/passwd.pt")
        assert res.status_code in (403, 404)

    def test_a_missing_snapshot_is_rejected(self, ba_client):
        client, _redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            res = _start(client, video=video, policy="pinned",
                         snapshot_rel="dlc-models-pytorch/nope/train/snapshot-1.pt")
        assert res.status_code == 404

    def test_no_explicit_snapshot_falls_back_to_the_persisted_pin(self, ba_client):
        client, redis, _project, video = ba_client
        with patch("dlc.batch_analyze._project_settings.get_setting",
                   return_value="kept/snapshot-050.pt"):
            with patch("dlc.batch_analyze._celery_send_task"):
                res = _start(client, video=video, policy="pinned")
        rec = redis.hgetall(f"dlc:batch:{res.get_json()['batch_id']}")
        assert rec["pinned_snapshot"] == "kept/snapshot-050.pt"

    def test_gputouse_round_trips_into_the_record(self, ba_client):
        client, redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            res = _start(client, video=video, gputouse=1)
        rec = redis.hgetall(f"dlc:batch:{res.get_json()['batch_id']}")
        assert rec["gputouse"] == "1"


class TestJobsSurface:
    def test_a_batch_resolves_to_complete_without_a_browser(self, ba_client):
        # The Jobs page must not show a finished batch as "running" forever
        # just because nobody had the panel open. A synthetic batch_id reads
        # PENDING from the Celery backend, which counts as LIVE — so the row
        # has to be reconciled from the batch's own record instead.
        client, redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            batch_id = _start(client, video=video).get_json()["batch_id"]
        redis.hset(f"dlc:batch:{batch_id}", mapping={
            "state": "submitted", "req_ids": json.dumps(["r1", "r2"]),
        })
        redis.hset("inline:result:r1", mapping={"status": "done", "n_analyzed": "10"})
        redis.hset("inline:result:r2", mapping={"status": "done", "n_analyzed": "10"})
        redis.hset(f"dlc_analyze_job:{batch_id}", mapping={
            "task_id": batch_id, "operation": "batch_analyze", "status": "running",
        })

        from dlc.monitoring import _reconcile_job
        job = _reconcile_job(f"dlc_analyze_job:{batch_id}", batch_id)
        assert job["status"] == "complete"
        assert "2/2 ranges" in job["stage"]

    def test_an_unfinished_batch_still_reads_running(self, ba_client):
        client, redis, _project, video = ba_client
        with patch("dlc.batch_analyze._celery_send_task"):
            batch_id = _start(client, video=video).get_json()["batch_id"]
        redis.hset(f"dlc:batch:{batch_id}", mapping={
            "state": "submitted", "req_ids": json.dumps(["r1", "r2"]),
        })
        redis.hset("inline:result:r1", mapping={"status": "done", "n_analyzed": "10"})
        redis.hset(f"dlc_analyze_job:{batch_id}", mapping={
            "task_id": batch_id, "operation": "batch_analyze", "status": "running",
        })

        from dlc.monitoring import _reconcile_job
        job = _reconcile_job(f"dlc_analyze_job:{batch_id}", batch_id)
        assert job["status"] == "running"
        assert "1/2 ranges" in job["stage"]

    def test_an_expired_analyze_job_is_not_mislabelled_train(self, ba_client):
        # Orphan stubs carry no `operation`, and _row_from_zset_job's
        # `or "train"` fallback turned every expired analyze/batch row into
        # "train — <uuid>" on the Jobs page.
        client, redis, _project, _video = ba_client
        redis.zadd("dlc_analyze_jobs", {"gone-id": 1.0})

        from dlc.monitoring import _zset_rows
        rows = [r for r in _zset_rows() if r["id"] == "gone-id"]
        assert rows and rows[0]["kind"] == "analyze", rows
        assert rows[0]["state"] == "orphaned"
