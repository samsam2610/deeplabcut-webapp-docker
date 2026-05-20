"""Unit tests for tasks.dlc_inline_session and its pure helpers.

DLC + GPU are fully mocked — these tests run on the host without CUDA.

Mirrors the pattern in test_tf_dlc_snapshot_index_fix.py: dlc.tasks does
`import deeplabcut as dlc` at module level, so we stub sys.modules
before importing dlc.tasks.
"""
from __future__ import annotations

import os
import pickle
import sys
import time as _time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _load_dlc_tasks():
    """Stub deeplabcut + DLCLoader + apis modules before importing dlc.tasks."""
    _stub = MagicMock()
    _patches = {
        "deeplabcut": _stub,
        "deeplabcut.pose_estimation_pytorch": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.apis": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.apis.videos": MagicMock(),
        "deeplabcut.pose_estimation_pytorch.data": MagicMock(),
    }
    with patch.dict(sys.modules, _patches):
        for key in list(sys.modules):
            if key == "dlc.tasks" or key.startswith("dlc.tasks."):
                del sys.modules[key]
        from dlc import tasks as _mod
        return _mod


dlc_tasks = _load_dlc_tasks()


def _ia_time_now():
    return _time.time()


# ── helpers ──────────────────────────────────────────────────────────────

def _df_with_index(frames, all_nan_rows=None):
    all_nan_rows = all_nan_rows or set()
    cols = pd.MultiIndex.from_tuples(
        [("scorer", "nose", "x"), ("scorer", "nose", "y"), ("scorer", "nose", "likelihood")],
        names=["scorer", "bodyparts", "coords"],
    )
    data = np.ones((len(frames), 3))
    for i, f in enumerate(frames):
        if f in all_nan_rows:
            data[i, :] = np.nan
    return pd.DataFrame(data, index=list(frames), columns=cols)


# ── _filter_skip_already_done ─────────────────────────────────────────────

class TestFilterSkipAlreadyDone:
    def test_empty_existing_returns_all_target(self):
        result = dlc_tasks._filter_skip_already_done(list(range(5)), existing_df=None)
        assert result == [0, 1, 2, 3, 4]

    def test_target_subset_of_existing_returns_empty(self):
        df = _df_with_index([0, 1, 2, 3, 4])
        assert dlc_tasks._filter_skip_already_done([1, 2, 3], df) == []

    def test_nan_all_rows_are_re_analyzed(self):
        df = _df_with_index([0, 1, 2], all_nan_rows={1})
        result = dlc_tasks._filter_skip_already_done([0, 1, 2], df)
        assert 1 in result
        assert 0 not in result
        assert 2 not in result

    def test_non_contiguous_target_preserves_order(self):
        df = _df_with_index([2, 4])
        result = dlc_tasks._filter_skip_already_done([1, 2, 3, 4, 5], df)
        assert result == [1, 3, 5]


# ── _RangeVideoIterator ───────────────────────────────────────────────────

class TestRangeVideoIterator:
    def test_yields_only_requested_indices(self, tmp_path):
        video_path = tmp_path / "fake.mp4"
        video_path.write_bytes(b"")
        seeks = []
        reads = 0

        class _ParentStub:
            def __init__(self, *a, **kw):
                pass

            def set_to_frame(self, n):
                seeks.append(n)

            def read_frame(self):
                nonlocal reads
                reads += 1
                return np.zeros((4, 4, 3), dtype=np.uint8)

            def reset(self):
                pass

        with patch.object(dlc_tasks, "VideoIterator", _ParentStub):
            it = dlc_tasks._RangeVideoIterator(str(video_path), indices=[3, 5, 9])
            collected = [f for f in it]
        assert seeks == [3, 5, 9]
        assert reads == 3
        assert len(collected) == 3

    def test_non_contiguous_skip_list_preserves_order(self, tmp_path):
        video_path = tmp_path / "fake.mp4"
        video_path.write_bytes(b"")
        seeks = []

        class _ParentStub:
            def __init__(self, *a, **kw):
                pass

            def set_to_frame(self, n):
                seeks.append(n)

            def read_frame(self):
                return np.zeros((1, 1, 3), dtype=np.uint8)

            def reset(self):
                pass

        with patch.object(dlc_tasks, "VideoIterator", _ParentStub):
            list(dlc_tasks._RangeVideoIterator(str(video_path), indices=[100, 7, 42]))
        assert seeks == [100, 7, 42], "iterator must preserve caller-supplied order"


# ── _atomic_write_h5 + _atomic_write_csv ──────────────────────────────────
#
# Host Python has a PyTables / numpy ABI mismatch that prevents real
# `to_hdf` calls (only works inside the worker container). We mock to_hdf
# to write a placeholder byte to the tmp path so the .tmp + os.replace
# atomicity is verifiable on the host. See test_dlc_celery_tasks.py for
# the established pattern.

def _mock_to_hdf_writes_bytes():
    """Replace pandas.DataFrame.to_hdf with a stub that writes 'h5\\n' to the
    target path (the first positional arg). Lets atomicity tests run on host."""
    def _stub(self_df, path_or_buf, *args, **kwargs):
        with open(str(path_or_buf), "wb") as f:
            f.write(b"h5\n")
    return patch("pandas.DataFrame.to_hdf", new=_stub)


class TestAtomicWrite:
    def test_atomic_write_h5_uses_temp_then_replace(self, tmp_path):
        path = tmp_path / "out.h5"
        df = _df_with_index([0, 1, 2])
        with _mock_to_hdf_writes_bytes():
            dlc_tasks._atomic_write_h5(path, df)
        assert path.is_file()
        # No leftover .tmp
        assert not (tmp_path / "out.h5.tmp").exists()

    def test_atomic_write_h5_failure_leaves_original_intact(self, tmp_path):
        path = tmp_path / "out.h5"
        df = _df_with_index([0, 1, 2])
        with _mock_to_hdf_writes_bytes():
            dlc_tasks._atomic_write_h5(path, df)
        original = path.read_bytes()

        with patch("pandas.DataFrame.to_hdf", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                dlc_tasks._atomic_write_h5(path, df)
        assert path.read_bytes() == original, "canonical file must be untouched on failure"

    def test_atomic_write_csv_round_trip(self, tmp_path):
        path = tmp_path / "out.csv"
        df = _df_with_index([0, 1, 2])
        dlc_tasks._atomic_write_csv(path, df)
        assert path.is_file()


# ── _update_meta_pickle ───────────────────────────────────────────────────

class TestMetaPickleUpdate:
    def test_records_snapshot_in_inline_analysis_snapshots_set(self, tmp_path):
        meta_path = tmp_path / "video_meta.pickle"
        with open(meta_path, "wb") as f:
            pickle.dump({"existing_field": 1}, f)
        df = _df_with_index([0, 1])
        dlc_tasks._update_meta_pickle(meta_path, df, snapshot="snapshot-200000.pt")
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)
        assert meta["existing_field"] == 1
        assert "inline_analysis_snapshots" in meta
        assert "snapshot-200000.pt" in meta["inline_analysis_snapshots"]

    def test_creates_meta_when_missing(self, tmp_path):
        meta_path = tmp_path / "video_meta.pickle"
        df = _df_with_index([0])
        dlc_tasks._update_meta_pickle(meta_path, df, snapshot="snap.pt")
        assert meta_path.is_file()
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)
        assert "snap.pt" in meta["inline_analysis_snapshots"]


# ── _resolve_h5_path ──────────────────────────────────────────────────────

class TestResolveH5Path:
    def test_companion_path_uses_scorer_name(self):
        result = dlc_tasks._resolve_h5_path(
            "/data/videos/m3-cam1.mp4",
            scorer_name="DLC_resnet50_DREADD-AlishuffleN_snapshot-200000",
        )
        assert str(result) == (
            "/data/videos/m3-cam1DLC_resnet50_DREADD-AlishuffleN_snapshot-200000.h5"
        )

    def test_works_with_arbitrary_extension(self):
        result = dlc_tasks._resolve_h5_path("/x/y/video.avi", scorer_name="SCORER")
        assert result.name == "videoSCORER.h5"


# ── Session-lifecycle helpers (Redis-backed) ──────────────────────────────


@pytest.fixture
def ia_redis(fake_redis):
    """Fresh FakeRedis state for each test (the session-scoped fake
    persists ._hstore / ._store dicts; we clear them per test)."""
    fake_redis._store.clear()
    fake_redis._hstore.clear()
    fake_redis._zsets.clear()
    fake_redis._sets.clear()
    fake_redis._lists.clear()
    return fake_redis


class TestSessionHelpers:
    def test_publish_status_sets_hash_fields(self, ia_redis):
        dlc_tasks._publish_status(
            ia_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        h = ia_redis._hstore["inline:session:u1:k1"]
        assert h["status"] == "ready"
        assert h["project"] == "proj"
        assert h["snapshot_path"] == "snap.pt"
        assert "last_activity" in h

    def test_publish_result_sets_done(self, ia_redis):
        dlc_tasks._publish_result(
            ia_redis, req_id="r1",
            status="done", n_analyzed=42, n_skipped=8,
        )
        h = ia_redis._hstore["inline:result:r1"]
        assert h["status"] == "done"
        assert int(h["n_analyzed"]) == 42
        assert int(h["n_skipped"]) == 8

    def test_publish_result_truncates_long_error(self, ia_redis):
        dlc_tasks._publish_result(
            ia_redis, req_id="r2", status="error",
            error="x" * 5000,
        )
        h = ia_redis._hstore["inline:result:r2"]
        assert len(h["error"]) <= 500

    def test_control_says_stop_consumes_key(self, ia_redis):
        ia_redis.set("inline:control:u1:k1", "stop")
        assert dlc_tasks._control_says_stop(ia_redis, "u1", "k1") is True
        # Second call returns False — key was consumed.
        assert dlc_tasks._control_says_stop(ia_redis, "u1", "k1") is False

    def test_idle_budget_caps_at_ttl(self, ia_redis):
        dlc_tasks._publish_status(
            ia_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        budget = dlc_tasks._idle_budget(ia_redis, "u1", "k1", ttl=300)
        assert 290 <= budget <= 300, f"fresh activity → near-full TTL, got {budget}"

    def test_idle_budget_floor_when_expired(self, ia_redis):
        dlc_tasks._publish_status(
            ia_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        # Pretend 500s have passed.
        ia_redis._hstore["inline:session:u1:k1"]["last_activity"] = str(
            _ia_time_now() - 500
        )
        budget = dlc_tasks._idle_budget(ia_redis, "u1", "k1", ttl=300)
        assert budget == 1, "expired sessions still get a minimal poll budget of 1s"

    def test_blpop_returns_none_when_empty(self, ia_redis):
        assert dlc_tasks._blpop(ia_redis, "missing-queue", timeout=1) is None

    def test_blpop_returns_value_when_present(self, ia_redis):
        ia_redis.lpush("q1", "hello")
        assert dlc_tasks._blpop(ia_redis, "q1", timeout=1) == "hello"

    def test_bump_activity_updates_timestamp(self, ia_redis):
        dlc_tasks._publish_status(
            ia_redis, user_id="u1", snap_key="k1",
            status="ready", project="proj", snapshot_path="snap.pt",
        )
        # Force an old timestamp.
        ia_redis._hstore["inline:session:u1:k1"]["last_activity"] = "0"
        dlc_tasks._bump_activity(ia_redis, "u1", "k1")
        new_la = float(ia_redis._hstore["inline:session:u1:k1"]["last_activity"])
        assert new_la > 0


# ── _run_range ────────────────────────────────────────────────────────────


def _run_range_kw(scorer="SCORER", multi_animal=False):
    """Build the kwargs _run_range takes (scorer/model_cfg/multi_animal
    come from DLCLoader at session boot, not from the runner)."""
    return dict(
        scorer=scorer,
        model_cfg={"metadata": {"bodyparts": ["nose"]}},
        multi_animal=multi_animal,
    )


def _stub_create_df(predictions, dlc_scorer, multi_animal, model_cfg,
                    output_path, output_prefix, save_as_csv):
    """Stub that returns a DataFrame with the same row count as
    predictions and a minimal MultiIndex column set, indexed 0..N-1
    (matches the real create_df_from_prediction's contract for
    _run_range's reindex step)."""
    n = len(predictions)
    cols = pd.MultiIndex.from_product(
        [[dlc_scorer], ["nose"], ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"],
    )
    return pd.DataFrame(0.0, index=list(range(n)), columns=cols)


class TestRunRange:
    def test_run_range_writes_h5_and_csv(self, tmp_path):
        video_path = tmp_path / "v.mp4"
        video_path.write_bytes(b"")
        runner = MagicMock()

        def fake_video_inference(vit, pose_runner):
            return [{}, {}, {}]

        with _mock_to_hdf_writes_bytes(), \
             patch.object(dlc_tasks, "video_inference", fake_video_inference), \
             patch.object(dlc_tasks, "_dlc_create_df_from_prediction", _stub_create_df), \
             patch.object(dlc_tasks, "_RangeVideoIterator",
                          lambda p, indices: iter([None] * len(indices))):
            req = {
                "req_id": "r1", "video_path": str(video_path),
                "start_frame": 100, "n_frames": 3, "batch_size": 8,
                "save_as_csv": True, "snapshot_path": "snap.pt",
            }
            n_analyzed, n_skipped = dlc_tasks._run_range(
                runner, req=req, **_run_range_kw()
            )
        assert n_analyzed == 3
        assert n_skipped == 0
        h5_path = dlc_tasks._resolve_h5_path(str(video_path), "SCORER")
        csv_path = h5_path.with_suffix(".csv")
        assert h5_path.is_file()
        assert csv_path.is_file()

    def test_run_range_skips_already_done(self, tmp_path):
        video_path = tmp_path / "v.mp4"
        video_path.write_bytes(b"")
        runner = MagicMock()

        # Seed existing h5 by directly creating the in-memory DataFrame
        # then using a stubbed read path. We bypass the real h5 write by
        # patching pd.read_hdf to return our seed.
        seed_df = _df_with_index([100, 101])

        called_with = []

        def fake_video_inference(vit, pose_runner):
            called_with.append(list(vit))
            return [{}]  # one prediction (frame 102 only)

        h5_path = dlc_tasks._resolve_h5_path(str(video_path), "S")
        # Pretend the h5 already exists on disk by touching the file +
        # patching pd.read_hdf.
        h5_path.write_bytes(b"placeholder")

        with _mock_to_hdf_writes_bytes(), \
             patch("pandas.read_hdf", return_value=seed_df), \
             patch.object(dlc_tasks, "video_inference", fake_video_inference), \
             patch.object(dlc_tasks, "_dlc_create_df_from_prediction", _stub_create_df), \
             patch.object(dlc_tasks, "_RangeVideoIterator",
                          lambda p, indices: iter([None] * len(indices))):
            req = {
                "req_id": "r1", "video_path": str(video_path),
                "start_frame": 100, "n_frames": 3, "batch_size": 8,
                "save_as_csv": False, "snapshot_path": "snap.pt",
            }
            n_analyzed, n_skipped = dlc_tasks._run_range(
                runner, req=req, **_run_range_kw(scorer="S")
            )
        assert n_analyzed == 1
        assert n_skipped == 2

    def test_run_range_no_op_when_everything_done(self, tmp_path):
        video_path = tmp_path / "v.mp4"
        video_path.write_bytes(b"")
        runner = MagicMock()
        seed_df = _df_with_index([100, 101, 102])
        h5_path = dlc_tasks._resolve_h5_path(str(video_path), "S")
        h5_path.write_bytes(b"placeholder")
        req = {
            "req_id": "r1", "video_path": str(video_path),
            "start_frame": 100, "n_frames": 3, "batch_size": 8,
            "save_as_csv": False, "snapshot_path": "snap.pt",
        }
        # video_inference + create_df_from_prediction must NOT be called.
        with patch("pandas.read_hdf", return_value=seed_df), \
             patch.object(dlc_tasks, "video_inference",
                          side_effect=AssertionError("must not be called")), \
             patch.object(dlc_tasks, "_dlc_create_df_from_prediction",
                          side_effect=AssertionError("must not be called")):
            n_analyzed, n_skipped = dlc_tasks._run_range(
                runner, req=req, **_run_range_kw(scorer="S")
            )
        assert n_analyzed == 0
        assert n_skipped == 3


# ── _dlc_inline_session_inner ────────────────────────────────────────────


def _fake_loader_factory():
    def _make(**kwargs):
        m = MagicMock()
        m.scorer.return_value = "S"
        m.model_cfg = {"metadata": {"bodyparts": ["nose"]}}
        m.project_cfg = {"multianimalproject": False}
        return m
    return _make


class TestInlineSessionTask:
    def test_session_exits_on_ttl_with_no_work(self, ia_redis, tmp_path):
        """TTL=1s, empty queue → exits within ~1s; status=expired."""
        runner_factory = MagicMock(return_value=MagicMock())
        with patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
             patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)):
            t0 = _ia_time_now()
            dlc_tasks._dlc_inline_session_inner(
                ia_redis,
                user_id="u1",
                config_path=str(tmp_path / "config.yaml"),
                snap_key="k1",
                snapshot_path="snap.pt",
                shuffle=1,
                trainingsetindex=0,
                batch_size=8,
                ttl=1,
            )
            elapsed = _ia_time_now() - t0
        assert elapsed < 3.0, f"expected exit within ~1s+slop, took {elapsed}"
        h = ia_redis._hstore["inline:session:u1:k1"]
        assert h["status"] == "expired"

    def test_session_exits_on_control_stop(self, ia_redis, tmp_path):
        runner_factory = MagicMock(return_value=MagicMock())
        ia_redis.set("inline:control:u1:k1", "stop")
        with patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
             patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)):
            dlc_tasks._dlc_inline_session_inner(
                ia_redis, user_id="u1", config_path="cfg",
                snap_key="k1", snapshot_path="snap.pt",
                shuffle=1, trainingsetindex=0, batch_size=8, ttl=60,
            )
        h = ia_redis._hstore["inline:session:u1:k1"]
        assert h["status"] == "stopped"

    def test_session_runs_one_range_then_exits(self, ia_redis, tmp_path):
        import json as _json
        video_path = tmp_path / "v.mp4"
        video_path.write_bytes(b"")
        req = {
            "req_id": "r1", "video_path": str(video_path),
            "start_frame": 0, "n_frames": 1, "batch_size": 8,
            "save_as_csv": False, "snapshot_path": "snap.pt",
        }
        ia_redis.lpush("inline:queue:u1:k1", _json.dumps(req))
        runner_factory = MagicMock(return_value=MagicMock())
        with _mock_to_hdf_writes_bytes(), \
             patch.object(dlc_tasks, "_dlc_loader_cls", _fake_loader_factory()), \
             patch.object(dlc_tasks, "_dlc_apis_utils",
                          MagicMock(get_pose_inference_runner=runner_factory)), \
             patch.object(dlc_tasks, "video_inference", return_value=[{}]), \
             patch.object(dlc_tasks, "_dlc_create_df_from_prediction", _stub_create_df), \
             patch.object(dlc_tasks, "_RangeVideoIterator",
                          lambda p, indices: iter([None] * len(indices))):
            dlc_tasks._dlc_inline_session_inner(
                ia_redis, user_id="u1", config_path="cfg",
                snap_key="k1", snapshot_path="snap.pt",
                shuffle=1, trainingsetindex=0, batch_size=8, ttl=2,
            )
        r = ia_redis._hstore["inline:result:r1"]
        assert r["status"] == "done"
        assert int(r["n_analyzed"]) == 1
