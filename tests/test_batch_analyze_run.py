"""The Batch Analyze body: model resolution, the training gate, and submission.

`run_batch` takes every impure dependency as an argument, so the whole flow —
including the gate that defers a run until training finishes — is exercised
here without celery, cv2, or a real project on disk.
"""
from __future__ import annotations

import json
import os
import sys
import time
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
        self.sets: dict[str, set] = {}

    def sadd(self, name, *values):
        s = self.sets.setdefault(name, set())
        new = {str(v) for v in values} - s
        s |= new
        return len(new)

    def smembers(self, name):
        return set(self.sets.get(name, set()))

    def hincrby(self, name, field, delta):
        d = self.h.setdefault(name, {})
        d[field] = str(int(d.get(field) or 0) + int(delta))
        return int(d[field])

    def llen(self, name):
        return len(self.lists.get(name, []))

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

    def _session_key_for(self, project):
        snaps = ba.scan_snapshots(project["root"], "pytorch", 1)
        rel, _ = ba.resolve_snapshot(snaps, "latest")
        snap_key = ba._snap_key(str(project["config"]), 1,
                                str((project["root"] / rel).resolve()))
        return f"inline:session:u1:{snap_key}"

    def test_reuses_a_live_session_rather_than_starting_a_second(self, project):
        # Two sessions on the same snapshot would fight over the same GPU.
        # A genuinely live session has a RECENT heartbeat, not just a status —
        # `_session_is_alive` reads the beat, so the fixture must carry one.
        r = Redis()
        r.h[ba._batch_key("B1")] = _record(project)
        r.h[self._session_key_for(project)] = {
            "status": "ready", "heartbeat": str(time.time())}

        sent = []
        ba.run_batch(r, "B1", requeue=lambda d: None,
                     send_task=lambda n, *, kwargs, queue: sent.append(n),
                     probe_frames=lambda p: 500, notes_for=lambda p: [],
                     sibling_for=lambda p: None, is_training=lambda x: False,
                     now=lambda: 2000.0)
        assert sent == []
        assert len(r.lists[_queue_key(r)]) == 1

    def test_restarts_a_session_whose_heartbeat_went_stale(self, project):
        """A hard-killed worker leaves status="ready" behind for the rest of
        the hash's TTL. Believing it strands every range on a queue with no
        consumer — the batch sits at 0 while the card looks warm."""
        r = Redis()
        r.h[ba._batch_key("B1")] = _record(project)
        r.h[self._session_key_for(project)] = {
            "status": "ready",
            "heartbeat": str(time.time() - 10 * 60),   # corpse
        }

        sent = []
        ba.run_batch(r, "B1", requeue=lambda d: None,
                     send_task=lambda n, *, kwargs, queue: sent.append(n),
                     probe_frames=lambda p: 500, notes_for=lambda p: [],
                     sibling_for=lambda p: None, is_training=lambda x: False,
                     now=lambda: 2000.0)
        assert sent == ["tasks.dlc_inline_session"], (
            "a stale-heartbeat session must be replaced, not trusted")
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
    """Both signals are required. Each case below is a shape observed in the
    live redis on 2026-08-06, not an invented one."""

    def _redis(self, jobs):
        r = Redis()
        r.zsets["dlc_train_jobs"] = {jid: float(i) for i, (jid, _, _) in enumerate(jobs)}
        for jid, hash_, state in jobs:
            if hash_ is not None:
                r.h[f"dlc_train_job:{jid}"] = hash_
            if state is not None:
                r.h[f"__state__:{jid}"] = state
        return r

    def _state_of(self, r, jid):
        return r.h.get(f"__state__:{jid}")

    def test_a_long_running_job_with_no_heartbeat_still_counts_as_running(self):
        # THE case that matters. dlc_train_network writes started_at and
        # status once and never heartbeats, so a healthy 3 h run has no
        # updated_at at all. Any staleness rule based on the hash alone would
        # call this finished and release the gate onto the OLD model — the
        # exact failure "queue after training" exists to prevent.
        r = self._redis([("t1", {"status": "running", "started_at": str(time.time() - 3.3 * 3600)},
                          "PROGRESS")])
        assert ba.training_is_running(r, self._state_of) is True

    def test_false_once_the_hash_says_complete(self):
        r = self._redis([("t1", {"status": "complete", "started_at": "9e8"}, "SUCCESS")])
        assert ba.training_is_running(r, self._state_of) is False

    def test_a_reaper_false_positive_does_not_release_the_gate(self):
        # monitoring._reconcile_job writes "dead" on a transient miss of the
        # Celery state and only undoes it when someone polls the Jobs page.
        # Observed 2026-08-06 on a resumed run that was logging epochs with the
        # GPU at 100%. Reading "dead" as finished would release a deferred
        # batch onto the pre-training model.
        r = self._redis([("t1", {"status": "dead", "started_at": str(time.time())},
                          "PROGRESS")])
        assert ba.training_is_running(r, self._state_of) is True

    def test_a_user_requested_stop_is_respected(self):
        # "stopped" is a human decision, not a reaper guess — do not override it.
        r = self._redis([("t1", {"status": "stopped", "started_at": str(time.time())},
                          "PROGRESS")])
        assert ba.training_is_running(r, self._state_of) is False

    def test_a_genuinely_killed_run_still_releases_via_the_dead_mans_switch(self):
        # Accepting "dead" is safe because the hash's EXISTENCE is the real
        # signal: a killed process stops sliding the TTL, so the hash vanishes
        # 2 h later and the gate releases even though Celery is stuck on
        # PROGRESS forever.
        r = self._redis([("t1", None, "PROGRESS")])
        assert ba.training_is_running(r, self._state_of) is False

    def test_false_when_celery_says_the_task_finished(self):
        # The reaper may not have flipped the hash yet; Celery is authoritative.
        r = self._redis([("t1", {"status": "running", "started_at": str(time.time())},
                          "SUCCESS")])
        assert ba.training_is_running(r, self._state_of) is False

    def test_an_expired_hash_does_not_pin_a_batch(self):
        # The zset outlives the hashes. Those ids read PENDING from the result
        # backend — a "live" Celery state — so trusting Celery alone would hold
        # a deferred batch for its full 24 h on jobs that finished days ago.
        r = self._redis([("t1", None, None)])
        assert ba.training_is_running(r, self._state_of) is False

    def test_a_dispatched_but_unstarted_task_counts_as_running(self):
        # No backend entry yet. Waiting slightly too long is far cheaper than
        # analysing with the pre-training model.
        r = self._redis([("t1", {"status": "running", "started_at": str(time.time())}, None)])
        assert ba.training_is_running(r, self._state_of) is True

    def test_a_hash_older_than_the_batch_deadline_is_ignored(self):
        r = self._redis([("t1", {"status": "running",
                                 "started_at": str(time.time() - 2 * ba.TRAINING_WAIT_DEADLINE_S)},
                          None)])
        assert ba.training_is_running(r, self._state_of) is False

    def test_one_live_job_among_many_finished_ones_is_enough(self):
        r = self._redis([
            ("done", {"status": "complete", "started_at": "9e8"}, "SUCCESS"),
            ("gone", None, None),
            ("live", {"status": "running", "started_at": str(time.time() - 7200)}, "PROGRESS"),
        ])
        assert ba.training_is_running(r, self._state_of) is True


class TestCeleryState:
    def test_reads_the_status_out_of_the_result_backend(self):
        r = Redis()
        r._store = {"celery-task-meta-t1": json.dumps({"status": "PROGRESS"})}
        r.get = lambda k: r._store.get(k)
        assert ba.celery_state(r, "t1") == "PROGRESS"

    def test_missing_or_malformed_entries_read_as_unknown(self):
        r = Redis()
        r._store = {"celery-task-meta-bad": "not json"}
        r.get = lambda k: r._store.get(k)
        assert ba.celery_state(r, "nope") is None
        assert ba.celery_state(r, "bad") is None


class TestDurableProgressCounting:
    """Progress must not depend on somebody polling in time.

    `inline:result:*` hashes expire, so a batch nobody watched used to drain
    its entire queue with `done` stuck at 0 and finish `counts_partial`.
    """

    def test_submitted_payloads_carry_their_batch_id(self, project):
        # Without this the worker cannot attribute a finished range to a batch.
        r = Redis()
        _run(r, project, frames=25_000)
        payloads = [json.loads(p) for p in r.lists[_queue_key(r)]]
        assert payloads, "expected queued ranges"
        assert all(p["batch_id"] == "B1" for p in payloads)

    def test_progress_does_not_clobber_worker_counted_frames(self, project):
        """batch_progress used to write `analyzed` from its own stale read.
        With the worker incrementing the same field, that dropped counts.

        The worker's writes are replayed directly here rather than through
        tasks._publish_result, which cannot be imported without deeplabcut;
        the worker side is covered in test_inline_analysis_worker.py.
        """
        r = Redis()
        _run(r, project, frames=25_000)
        payloads = [json.loads(p) for p in r.lists[_queue_key(r)]]
        for p in payloads:                       # what the worker does
            r.sadd("dlc:batch:B1:done", p["req_id"])
            r.hincrby("dlc:batch:B1", "analyzed", 800)
        counted = int(r.h["dlc:batch:B1"]["analyzed"])
        assert counted == 800 * len(payloads)

        ba.batch_progress(r, "B1")
        assert int(r.h["dlc:batch:B1"]["analyzed"]) == counted, (
            "polling must not overwrite what the worker already counted")

    def test_progress_still_counts_ranges_lacking_a_batch_id(self, project):
        """Payloads queued before this change carry no batch_id; the polling
        fallback must still count them."""
        r = Redis()
        _run(r, project, frames=25_000)
        payloads = [json.loads(p) for p in r.lists[_queue_key(r)]]
        for p in payloads:
            r.h[f"inline:result:{p['req_id']}"] = {
                "status": "done", "n_analyzed": "800", "n_skipped": "0"}
        prog = ba.batch_progress(r, "B1")
        assert prog["done"] == len(payloads)
        assert prog["analyzed"] == 800 * len(payloads)
