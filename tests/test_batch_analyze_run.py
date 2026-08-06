"""The Batch Analyze body: model resolution, the training gate, and submission.

`run_batch` takes every impure dependency as an argument, so the whole flow —
including the gate that defers a run until training finishes — is exercised
here without celery, cv2, or a real project on disk.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import batch_analyze as ba  # noqa: E402


class Redis:
    """Just the commands run_batch uses."""

    def __init__(self):
        self.h: dict[str, dict] = {}
        self.lists: dict[str, list] = {}
        self.zsets: dict[str, dict] = {}

    def hset(self, name, key=None, value=None, mapping=None):
        d = self.h.setdefault(name, {})
        if key is not None:
            d[key] = value
        if mapping:
            d.update({k: str(v) for k, v in mapping.items()})

    def hgetall(self, name):
        return dict(self.h.get(name, {}))

    def rpush(self, name, *values):
        self.lists.setdefault(name, []).extend(values)

    def lpush(self, name, *values):
        for v in values:
            self.lists.setdefault(name, []).insert(0, v)

    def expire(self, name, secs):
        pass

    def zadd(self, name, mapping):
        self.zsets.setdefault(name, {}).update(mapping)

    def zrevrange(self, name, start, stop):
        return sorted(self.zsets.get(name, {}), key=lambda k: -self.zsets[name][k])


@pytest.fixture
def project(tmp_path):
    """A project skeleton with two snapshots and a stereo pair of videos."""
    root = tmp_path / "proj"
    train = root / "dlc-models-pytorch" / "iteration-24" / "DemoJan1-trainset70shuffle1" / "train"
    train.mkdir(parents=True)
    (train / "snapshot-best-150.pt").write_bytes(b"x")
    (train / "snapshot-180.pt").write_bytes(b"y")
    # Distinct mtimes: "latest" is an mtime sort, and two files written in the
    # same filesystem tick would make the winner arbitrary.
    os.utime(train / "snapshot-best-150.pt", (1_700_000_000, 1_700_000_000))
    os.utime(train / "snapshot-180.pt", (1_700_000_600, 1_700_000_600))
    (root / "config.yaml").write_text("engine: pytorch\n")

    vids = tmp_path / "vids"
    vids.mkdir()
    cam0 = vids / "banh-mi-1_cam0_20260704_104915.avi"
    cam1 = vids / "banh-mi-1_cam1_20260704_104915.avi"
    cam0.write_bytes(b"v")
    cam1.write_bytes(b"v")
    return {"root": root, "config": root / "config.yaml", "cam0": cam0, "cam1": cam1}


def _record(project, **over):
    rec = {
        "batch_id": "B1", "user_id": "u1",
        "config_path": str(project["config"]), "engine": "pytorch",
        "pinned_snapshot": "",
        "videos": json.dumps([str(project["cam0"])]),
        "mode": "all", "tags": "[]", "before": "200", "after": "599",
        "both_cams": "0", "policy": "latest",
        "shuffle": "1", "trainingsetindex": "0", "batch_size": "8",
        "save_as_csv": "0", "wait_for_training": "0", "seen_training": "0",
        "deadline": "99999999999", "state": "queued", "cancelled": "0",
        "created_at": "1000", "updated_at": "1000",
    }
    rec.update({k: str(v) for k, v in over.items()})
    return rec


def _run(redis, project, *, frames=1000, notes=None, sibling=None,
         training=False, **over):
    redis.h[ba._batch_key("B1")] = _record(project, **over)
    sent, requeued = [], []
    out = ba.run_batch(
        redis, "B1",
        requeue=lambda d: requeued.append(d),
        send_task=lambda n, *, kwargs, queue: sent.append((n, kwargs, queue)),
        probe_frames=lambda p: frames,
        notes_for=lambda p: notes or [],
        sibling_for=lambda p: sibling,
        is_training=lambda r: training,
        now=lambda: 2000.0,
    )
    return out, sent, requeued


def _queue_key(redis):
    keys = [k for k in redis.lists if k.startswith("inline:queue:")]
    assert len(keys) == 1, keys
    return keys[0]


class TestModelResolution:
    def test_latest_wins_over_best(self, project):
        r = Redis()
        out, sent, _ = _run(r, project, policy="latest")
        assert out["state"] == "submitted"
        assert "snapshot-180.pt" in sent[0][1]["snapshot_path"]

    def test_latest_iter_best_picks_the_best_checkpoint(self, project):
        r = Redis()
        _, sent, _ = _run(r, project, policy="latest_iter_best")
        assert "snapshot-best-150.pt" in sent[0][1]["snapshot_path"]

    def test_an_unresolvable_pin_fails_the_batch(self, project):
        r = Redis()
        out, sent, _ = _run(r, project, policy="pinned", pinned_snapshot="gone.pt")
        assert out["state"] == "failed"
        assert "no longer on disk" in out["reason"]
        # Nothing must have been started or queued on a failed resolution.
        assert sent == [] and r.lists == {}


class TestTrainingGate:
    def test_waits_while_training_runs_and_submits_nothing(self, project):
        r = Redis()
        out, sent, requeued = _run(r, project, wait_for_training=1, training=True)
        assert out["state"] == "waiting"
        assert sent == [] and r.lists == {}
        # Re-armed rather than blocking, so no worker slot is held.
        assert requeued == [ba.TRAINING_POLL_S]
        assert r.h[ba._batch_key("B1")]["seen_training"] == "1"

    def test_waits_even_when_nothing_is_running_yet(self, project):
        # Ticking the box and starting training a minute later must still
        # wait for that run. "Nothing running right now" is not permission.
        r = Redis()
        out, sent, requeued = _run(r, project, wait_for_training=1, training=False)
        assert out == {"state": "waiting", "seen_training": False}
        assert sent == [] and requeued == [ba.TRAINING_POLL_S]

    def test_submits_once_a_seen_training_run_has_finished(self, project):
        r = Redis()
        out, sent, requeued = _run(r, project, wait_for_training=1,
                                   seen_training=1, training=False)
        assert out["state"] == "submitted"
        assert requeued == []
        assert sent and sent[0][0] == "tasks.dlc_inline_session"

    def test_gives_up_at_the_deadline(self, project):
        r = Redis()
        out, sent, requeued = _run(r, project, wait_for_training=1,
                                   training=False, deadline=1999)
        assert out["state"] == "failed"
        assert "gave up waiting" in out["reason"]
        assert sent == [] and requeued == []

    def test_a_cancelled_batch_submits_nothing(self, project):
        r = Redis()
        out, sent, _ = _run(r, project, cancelled=1)
        assert out["state"] == "cancelled"
        assert sent == [] and r.lists == {}


class TestSubmission:
    def test_whole_video_chunks_under_the_route_cap(self, project):
        r = Redis()
        out, _sent, _ = _run(r, project, frames=25_000)
        payloads = [json.loads(p) for p in r.lists[_queue_key(r)]]
        assert [p["n_frames"] for p in payloads] == [10_000, 10_000, 5_000]
        assert [p["start_frame"] for p in payloads] == [0, 10_000, 20_000]
        assert len(out["req_ids"]) == 3

    def test_payloads_land_via_rpush_in_queue_order(self, project):
        # The session drains with BLPOP. RPUSH keeps batch ranges in submitted
        # order AND lets an interactive LPUSH click jump the whole batch.
        r = Redis()
        _run(r, project, frames=25_000)
        key = _queue_key(r)
        starts = [json.loads(p)["start_frame"] for p in r.lists[key]]
        assert starts == sorted(starts), "batch ranges must drain in order"

        r.lpush(key, json.dumps({"req_id": "interactive"}))
        assert json.loads(r.lists[key][0])["req_id"] == "interactive"

    def test_both_cams_submits_the_sibling_too(self, project):
        r = Redis()
        out, _sent, _ = _run(r, project, both_cams=1, frames=500,
                             sibling=str(project["cam1"]))
        paths = {json.loads(p)["video_path"] for p in r.lists[_queue_key(r)]}
        assert paths == {str(project["cam0"]), str(project["cam1"])}
        assert len(out["req_ids"]) == 2

    def test_both_cams_with_no_sibling_reports_it_and_still_runs_cam0(self, project):
        r = Redis()
        out, _sent, _ = _run(r, project, both_cams=1, frames=500, sibling=None)
        assert len(out["req_ids"]) == 1
        assert out["skipped"] == [{"video": str(project["cam0"]),
                                   "reason": "no sibling camera found"}]

    def test_tag_mode_submits_only_the_tagged_windows(self, project):
        r = Redis()
        notes = [{"frame_number": "1000", "note": "start-failure"},
                 {"frame_number": "3000", "note": "start-success"},
                 {"frame_number": "4000", "note": "something-else"}]
        out, _sent, _ = _run(r, project, mode="tag", frames=10_000, notes=notes,
                             tags=json.dumps(["start-failure", "start-success"]))
        payloads = [json.loads(p) for p in r.lists[_queue_key(r)]]
        assert [(p["start_frame"], p["n_frames"]) for p in payloads] == [
            (800, 800), (2800, 800)]
        assert out["n_frames"] == 1600

    def test_tag_windows_come_from_the_queued_camera_only(self, project):
        # Cameras are hardware triggered and only ONE is annotated: in the real
        # banh-mi-1 pair, cam0's CSV carries 141 tagged frames and cam1's
        # carries none. Reading each camera's own notes would silently analyse
        # cam0 alone — half the pair, with nothing in the UI saying so.
        r = Redis()
        notes_by_video = {
            str(project["cam0"]): [{"frame_number": "1000", "note": "start-failure"}],
            str(project["cam1"]): [],          # never annotated, as in real data
        }
        redis_rec = _record(project, both_cams=1, mode="tag",
                            tags=json.dumps(["start-failure"]))
        r.h[ba._batch_key("B1")] = redis_rec
        out = ba.run_batch(
            r, "B1", requeue=lambda d: None,
            send_task=lambda n, *, kwargs, queue: None,
            probe_frames=lambda p: 10_000,
            notes_for=lambda p: notes_by_video[str(p)],
            sibling_for=lambda p: str(project["cam1"]),
            is_training=lambda x: False, now=lambda: 2000.0)

        payloads = [json.loads(p) for p in r.lists[_queue_key(r)]]
        by_video = {}
        for p in payloads:
            by_video.setdefault(p["video_path"], []).append(
                (p["start_frame"], p["n_frames"]))
        assert by_video == {
            str(project["cam0"]): [(800, 800)],
            str(project["cam1"]): [(800, 800)],
        }, "both cameras must get the SAME ranges, taken from cam0's tags"
        assert out["skipped"] == []

    def test_tag_ranges_are_clamped_to_each_cameras_own_length(self, project):
        # A sibling a few frames shorter must not receive a range running off
        # its end — the /range route would reject it.
        r = Redis()
        lengths = {str(project["cam0"]): 10_000, str(project["cam1"]): 1_200}
        r.h[ba._batch_key("B1")] = _record(
            project, both_cams=1, mode="tag", tags=json.dumps(["t"]))
        ba.run_batch(
            r, "B1", requeue=lambda d: None,
            send_task=lambda n, *, kwargs, queue: None,
            probe_frames=lambda p: lengths[str(p)],
            notes_for=lambda p: [{"frame_number": "1000", "note": "t"}],
            sibling_for=lambda p: str(project["cam1"]),
            is_training=lambda x: False, now=lambda: 2000.0)

        for p in (json.loads(x) for x in r.lists[_queue_key(r)]):
            end = p["start_frame"] + p["n_frames"] - 1
            assert end < lengths[p["video_path"]], f"{p} runs past the video"

    def test_tag_mode_skips_a_video_with_no_matching_tags(self, project):
        r = Redis()
        out, sent, _ = _run(r, project, mode="tag", frames=10_000,
                            notes=[{"frame_number": "10", "note": "other"}],
                            tags=json.dumps(["start-failure"]))
        assert out["state"] == "failed"          # nothing to submit
        assert out["skipped"][0]["reason"] == "no frames carry any of those tags"
        # The session was still started before the videos were walked; that is
        # harmless (it idles out) and keeps the flow simple.
        assert sent and sent[0][0] == "tasks.dlc_inline_session"

    def test_an_unreadable_video_is_skipped_not_fatal(self, project):
        r = Redis()
        out, _sent, _ = _run(r, project, frames=0)
        assert out["skipped"] == [{"video": str(project["cam0"]),
                                   "reason": "could not read the video"}]

    def test_a_missing_file_is_skipped(self, project):
        r = Redis()
        out, _sent, _ = _run(r, project,
                             videos=json.dumps(["/nope/missing.avi"]))
        assert out["skipped"] == [{"video": "/nope/missing.avi",
                                   "reason": "file not found"}]

    def test_reuses_a_live_session_rather_than_starting_a_second(self, project):
        # Two sessions on the same snapshot would fight over the same GPU.
        r = Redis()
        r.h[ba._batch_key("B1")] = _record(project)
        snaps = ba.scan_snapshots(project["root"], "pytorch", 1)
        rel, _ = ba.resolve_snapshot(snaps, "latest")
        snap_key = ba._snap_key(str(project["config"]), 1,
                                str((project["root"] / rel).resolve()))
        r.h[f"inline:session:u1:{snap_key}"] = {"status": "ready"}

        sent = []
        ba.run_batch(r, "B1", requeue=lambda d: None,
                     send_task=lambda n, *, kwargs, queue: sent.append(n),
                     probe_frames=lambda p: 500, notes_for=lambda p: [],
                     sibling_for=lambda p: None, is_training=lambda x: False,
                     now=lambda: 2000.0)
        assert sent == []
        assert len(r.lists[_queue_key(r)]) == 1

    def test_writes_one_aggregate_row_on_the_jobs_surface(self, project):
        r = Redis()
        _run(r, project, frames=25_000)
        row = r.h["dlc_analyze_job:B1"]
        assert row["operation"] == "batch_analyze"
        assert row["status"] == "running"
        assert row["total"] == "3"
        assert "B1" in r.zsets["dlc_analyze_jobs"]


class TestTrainingIsRunning:
    def test_true_while_a_row_is_running(self):
        r = Redis()
        r.zsets["dlc_train_jobs"] = {"t1": 1.0}
        r.h["dlc_train_job:t1"] = {"status": "running", "updated_at": "9e9"}
        assert ba.training_is_running(r) is True

    def test_false_once_it_completes(self):
        r = Redis()
        r.zsets["dlc_train_jobs"] = {"t1": 1.0}
        r.h["dlc_train_job:t1"] = {"status": "complete", "updated_at": "9e9"}
        assert ba.training_is_running(r) is False

    def test_a_stale_running_row_does_not_pin_a_batch_forever(self):
        # A crashed training container leaves status=running behind. Without
        # the heartbeat check a deferred batch would wait out its full 24 h.
        r = Redis()
        r.zsets["dlc_train_jobs"] = {"t1": 1.0}
        r.h["dlc_train_job:t1"] = {"status": "running", "updated_at": "1"}
        assert ba.training_is_running(r) is False
