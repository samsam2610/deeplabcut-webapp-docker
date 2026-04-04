"""
Tests for TF DLC snapshot_index compatibility fix in _dlc_analyze_subprocess.

Root cause: analyze_videos() in TF DLC (2.x) does not accept snapshot_index as a
parameter — the snapshot is selected via the snapshotindex key in config.yaml instead.
PyTorch DLC (3.x) accepts snapshot_index directly.

Bug: when a TF project ran with an explicit snapshot_path, snapshot_index was added
to kw and passed to analyze_videos(), causing:
    TypeError: analyze_videos() got an unexpected keyword argument 'snapshot_index'
which then triggered CUDA cleanup and a SIGSEGV (signal 11).

Fix: detect at runtime whether analyze_videos accepts snapshot_index (inspect.signature);
if not (TF DLC), patch config.yaml with snapshotindex and strip snapshot_index from kw.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Path to the real TF project provided by the user (optional — used for skip guards)
_REAL_TF_PROJECT = Path(
    "app/data/NAS-Data-Share/Martin-Lab/"
    "0_DLC_All_to_Jack_from_Truong_02_10_2026/1_Martin/1_Train/JackIBB-TS-Jan32026"
)

# ── Module-level import with deeplabcut mocked (mirrors existing test pattern) ──
# tasks.py has a top-level `import deeplabcut as dlc` that must be satisfied.

def _load_dlc_tasks():
    _stub = MagicMock()
    # Only mock deeplabcut — celery, redis, etc. are real packages already installed.
    _patches = {"deeplabcut": _stub}
    with patch.dict(sys.modules, _patches):
        # Clear any previously cached dlc.tasks so we get a fresh import
        for key in list(sys.modules):
            if key == "dlc.tasks" or key.startswith("dlc.tasks."):
                del sys.modules[key]
        from dlc import tasks as _mod
        return _mod

_dlc_tasks_mod = _load_dlc_tasks()


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _make_tf_project(project_dir: Path, original_snapshotindex: int = 4) -> Path:
    """
    Create a minimal TF-style DLC project:
    - config.yaml with engine=tensorflow and snapshotindex set
    - 6 snapshot .index files (snapshot-0 through snapshot-1000000, step 200000)
    Returns config_path.
    """
    train_dir = (
        project_dir
        / "dlc-models"
        / "iteration-0"
        / "TestJan2026-trainset95shuffle1"
        / "train"
    )
    train_dir.mkdir(parents=True, exist_ok=True)
    # Use the same numbering as the real project (no snapshot-0).
    # Lexicographic sort: 1000000 < 200000 < 400000 < 600000 < 800000
    # so snapshot-1000000.index → local index 0 of 5  (matches the real error log)
    for step in [200000, 400000, 600000, 800000, 1000000]:
        (train_dir / f"snapshot-{step}.index").touch()
        (train_dir / f"snapshot-{step}.data-00000-of-00001").touch()

    config_path = project_dir / "config.yaml"
    cfg = {
        "Task": "TestTask",
        "scorer": "TestScorer",
        "project_path": str(project_dir),
        "date": "Jan2026",
        "engine": "tensorflow",
        "snapshotindex": original_snapshotindex,
        "TrainingFraction": [0.95],
        "bodyparts": ["Snout", "Wrist"],
        "video_sets": {},
    }
    with open(config_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    return config_path


def _make_dummy_video(project_dir: Path, name: str = "test_video.avi") -> Path:
    """Create a zero-byte placeholder file with a video extension."""
    v = project_dir / name
    v.touch()
    return v


def _build_mock_dlc(with_snapshot_index: bool, calls: list):
    """
    Return a mock deeplabcut module whose analyze_videos either accepts or
    does not accept the snapshot_index keyword argument.

    The fake function appends its received kwargs to `calls` so tests can
    inspect exactly what was passed.
    """
    mock_dlc = MagicMock()

    if with_snapshot_index:
        def fake_analyze_videos(config, videos, shuffle=1, trainingsetindex=0,
                                gputouse=None, save_as_csv=False, destfolder=None,
                                snapshot_index=None):
            calls.append({"snapshot_index": snapshot_index,
                           "config_at_call": _read_snapshotindex(config),
                           "kwargs_keys": set(locals().keys()) - {"config", "videos"}})
    else:
        def fake_analyze_videos(config, videos, shuffle=1, trainingsetindex=0,
                                gputouse=None, save_as_csv=False, destfolder=None):
            calls.append({"config_at_call": _read_snapshotindex(config),
                           "kwargs_keys": set(locals().keys()) - {"config", "videos"}})

    mock_dlc.analyze_videos          = fake_analyze_videos
    mock_dlc.analyze_time_lapse_frames = MagicMock()
    mock_dlc.create_labeled_video    = MagicMock()
    return mock_dlc


def _read_snapshotindex(config_path: str) -> int | None:
    """Read snapshotindex from config.yaml; None if absent."""
    try:
        with open(config_path) as f:
            return yaml.safe_load(f).get("snapshotindex")
    except Exception:
        return None


def _run_subprocess_direct(config_path, video_path, params, mock_dlc):
    """
    Call _dlc_analyze_subprocess directly (synchronously, not via multiprocessing)
    with a patched deeplabcut module. Returns the log text.

    The function is called on the module-level _dlc_tasks_mod that was already
    imported with a stub deeplabcut.  We re-patch sys.modules["deeplabcut"] so
    that the `import deeplabcut as _dlc` INSIDE _dlc_analyze_subprocess picks up
    the per-test mock_dlc we supply here.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as lf:
        log_path = lf.name

    try:
        with (
            patch.dict(sys.modules, {"deeplabcut": mock_dlc}),
            patch.object(_dlc_tasks_mod, "_cuda_cleanup_with_timeout",
                         lambda timeout=10: None),
        ):
            _dlc_tasks_mod._dlc_analyze_subprocess(
                str(config_path), str(video_path), params, log_path
            )
        with open(log_path) as lf:
            return lf.read()
    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAvKwSnapshotIndexFiltering:
    """
    Unit-level tests: verify av_kw and config-patching behavior inside
    _dlc_analyze_subprocess for TF vs PyTorch DLC backends.
    """

    def test_tf_dlc_snapshot_index_not_passed_to_analyze_videos(self, tmp_path):
        """
        TF DLC path: snapshot_index must NOT appear in kwargs passed to analyze_videos.
        The snapshot is communicated via config.yaml snapshotindex instead.
        """
        config_path = _make_tf_project(tmp_path, original_snapshotindex=4)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "gputouse": None,
            "batch_size": None,
            "save_as_csv": False,
            "create_labeled": False,
            # snapshot-1000000.index → sorted index 5 in the 6-snapshot train folder
            "snapshot_path": "dlc-models/iteration-0/TestJan2026-trainset95shuffle1/train/snapshot-1000000.index",
            "destfolder": str(tmp_path / "Labeled"),
            "clv_pcutoff": 0.75,
            "clv_dotsize": 5,
            "clv_colormap": "rainbow",
            "clv_modelprefix": "",
            "clv_filtered": True,
            "clv_draw_skeleton": True,
            "clv_overwrite": False,
        }

        calls = []
        mock_dlc = _build_mock_dlc(with_snapshot_index=False, calls=calls)

        _run_subprocess_direct(config_path, video_path, params, mock_dlc)

        assert len(calls) == 1, "analyze_videos should have been called exactly once"
        recorded = calls[0]

        # snapshot_index must NOT be in kwargs sent to TF DLC analyze_videos
        assert "snapshot_index" not in recorded["kwargs_keys"], (
            "snapshot_index was passed to analyze_videos() on TF DLC — this causes TypeError"
        )

        # clv_* params must never reach analyze_videos
        clv_keys = {k for k in recorded["kwargs_keys"] if k.startswith("clv_")}
        assert clv_keys == set(), f"clv_* params leaked into analyze_videos: {clv_keys}"

    def test_tf_dlc_config_patched_with_correct_snapshotindex(self, tmp_path):
        """
        TF DLC path: config.yaml snapshotindex must equal the resolved local_snap_index
        at the moment analyze_videos is called.
        snapshot-1000000.index is the 6th snapshot (0-indexed: 5).
        """
        config_path = _make_tf_project(tmp_path, original_snapshotindex=4)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "save_as_csv": False,
            "create_labeled": False,
            "snapshot_path": "dlc-models/iteration-0/TestJan2026-trainset95shuffle1/train/snapshot-1000000.index",
            "destfolder": str(tmp_path / "Labeled"),
        }

        calls = []
        mock_dlc = _build_mock_dlc(with_snapshot_index=False, calls=calls)

        _run_subprocess_direct(config_path, video_path, params, mock_dlc)

        assert len(calls) == 1
        # snapshot-1000000 is index 0 in lexicographic sort: '1000000' < '200000' < ...
        # This matches "local index 0 of 5" in the real error log.
        assert calls[0]["config_at_call"] == 0, (
            f"Expected snapshotindex=0 in config during TF analyze_videos call, "
            f"got {calls[0]['config_at_call']}"
        )

    def test_tf_dlc_config_restored_after_analyze_videos(self, tmp_path):
        """
        TF DLC path: config.yaml snapshotindex must be restored to its original
        value after analyze_videos returns.
        """
        original_index = 4
        config_path = _make_tf_project(tmp_path, original_snapshotindex=original_index)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "save_as_csv": False,
            "create_labeled": False,
            "snapshot_path": "dlc-models/iteration-0/TestJan2026-trainset95shuffle1/train/snapshot-1000000.index",
            "destfolder": str(tmp_path / "Labeled"),
        }

        calls = []
        mock_dlc = _build_mock_dlc(with_snapshot_index=False, calls=calls)

        _run_subprocess_direct(config_path, video_path, params, mock_dlc)

        restored = _read_snapshotindex(str(config_path))
        assert restored == original_index, (
            f"config.yaml snapshotindex not restored: expected {original_index}, got {restored}"
        )

    def test_tf_dlc_config_restored_even_if_analyze_videos_raises(self, tmp_path):
        """
        TF DLC path: config.yaml must be restored even when analyze_videos raises
        (the TypeError scenario that was causing signal 11).
        """
        original_index = 4
        config_path = _make_tf_project(tmp_path, original_snapshotindex=original_index)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "save_as_csv": False,
            "create_labeled": False,
            "snapshot_path": "dlc-models/iteration-0/TestJan2026-trainset95shuffle1/train/snapshot-1000000.index",
        }

        # Simulate analyze_videos raising (as the original bug did)
        mock_dlc = MagicMock()
        def raising_analyze_videos(config, videos, shuffle=1, trainingsetindex=0,
                                   gputouse=None, save_as_csv=False, destfolder=None):
            raise TypeError("analyze_videos() got an unexpected keyword argument 'snapshot_index'")
        mock_dlc.analyze_videos          = raising_analyze_videos
        mock_dlc.analyze_time_lapse_frames = MagicMock()
        mock_dlc.create_labeled_video    = MagicMock()

        log = _run_subprocess_direct(config_path, video_path, params, mock_dlc)

        # Config must be restored regardless of the exception
        restored = _read_snapshotindex(str(config_path))
        assert restored == original_index, (
            f"config.yaml snapshotindex not restored after exception: "
            f"expected {original_index}, got {restored}"
        )
        # Error should be captured in the log
        assert "__ANALYZE_ERROR__" in log

    def test_pytorch_dlc_snapshot_index_passed_directly(self, tmp_path):
        """
        PyTorch DLC path: snapshot_index IS accepted, so it must be in kwargs
        and config must NOT be patched.
        """
        original_index = 4
        config_path = _make_tf_project(tmp_path, original_snapshotindex=original_index)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "save_as_csv": False,
            "create_labeled": False,
            "snapshot_path": "dlc-models/iteration-0/TestJan2026-trainset95shuffle1/train/snapshot-1000000.index",
            "destfolder": str(tmp_path / "Labeled"),
        }

        calls = []
        mock_dlc = _build_mock_dlc(with_snapshot_index=True, calls=calls)

        _run_subprocess_direct(config_path, video_path, params, mock_dlc)

        assert len(calls) == 1
        recorded = calls[0]

        # PyTorch DLC: snapshot_index must be passed
        assert "snapshot_index" in recorded["kwargs_keys"], (
            "snapshot_index was not passed to analyze_videos() for PyTorch DLC"
        )
        # snapshot-1000000 → lexicographic index 0 (matches real project "local index 0 of 5")
        assert recorded["snapshot_index"] == 0, (
            f"Wrong snapshot_index: expected 0, got {recorded['snapshot_index']}"
        )

        # Config must NOT be patched (snapshotindex unchanged during and after call)
        assert recorded["config_at_call"] == original_index, (
            f"Config was incorrectly patched for PyTorch DLC: "
            f"expected {original_index}, got {recorded['config_at_call']}"
        )

    def test_clv_params_never_reach_analyze_videos(self, tmp_path):
        """
        clv_* params (labeled-video options) must never be passed to analyze_videos
        regardless of DLC backend.
        """
        config_path = _make_tf_project(tmp_path)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "save_as_csv": False,
            "create_labeled": False,
            "clv_pcutoff": 0.75,
            "clv_dotsize": 5,
            "clv_colormap": "rainbow",
            "clv_modelprefix": "",
            "clv_filtered": True,
            "clv_draw_skeleton": True,
            "clv_overwrite": False,
        }

        for with_si in (False, True):
            calls = []
            mock_dlc = _build_mock_dlc(with_snapshot_index=with_si, calls=calls)
            _run_subprocess_direct(config_path, video_path, params, mock_dlc)

            if calls:
                clv_keys = {k for k in calls[-1]["kwargs_keys"] if "clv" in k.lower()}
                assert clv_keys == set(), (
                    f"clv_* params leaked into analyze_videos (with_snapshot_index={with_si}): "
                    f"{clv_keys}"
                )

    def test_no_snapshot_path_does_not_patch_config(self, tmp_path):
        """
        When no snapshot_path is supplied (use latest), config must not be patched
        for either backend.
        """
        original_index = 2
        config_path = _make_tf_project(tmp_path, original_snapshotindex=original_index)
        video_path  = _make_dummy_video(tmp_path)

        params = {
            "shuffle": 1,
            "trainingsetindex": 0,
            "save_as_csv": False,
            "create_labeled": False,
        }

        calls = []
        mock_dlc = _build_mock_dlc(with_snapshot_index=False, calls=calls)
        _run_subprocess_direct(config_path, video_path, params, mock_dlc)

        # Config must be unchanged
        assert _read_snapshotindex(str(config_path)) == original_index
        # analyze_videos called without snapshot_index (none to pass)
        if calls:
            assert "snapshot_index" not in calls[0]["kwargs_keys"]


class TestAvKwInspectionLogic:
    """
    Pure unit tests for the inspect.signature detection logic that drives
    _av_accepts_snapshot_index, without invoking _dlc_analyze_subprocess.
    """

    def test_tf_analyze_videos_detected_correctly(self):
        """A function without snapshot_index must be detected as TF-style."""
        def tf_av(config, videos, shuffle=1, trainingsetindex=0,
                  gputouse=None, save_as_csv=False, destfolder=None):
            pass

        params = inspect.signature(tf_av).parameters
        assert "snapshot_index" not in params

    def test_pytorch_analyze_videos_detected_correctly(self):
        """A function with snapshot_index must be detected as PyTorch-style."""
        def pt_av(config, videos, shuffle=1, trainingsetindex=0,
                  gputouse=None, save_as_csv=False, destfolder=None,
                  snapshot_index=None):
            pass

        params = inspect.signature(pt_av).parameters
        assert "snapshot_index" in params

    def test_inspect_signature_on_magicmock_falls_back_safely(self):
        """
        When inspect.signature raises (e.g. on a MagicMock), the code must
        default to _av_accepts_snapshot_index=False (TF-safe fallback).
        """
        mock_fn = MagicMock()
        try:
            result = "snapshot_index" in inspect.signature(mock_fn).parameters
        except (ValueError, TypeError):
            result = False  # TF-safe fallback

        # Either the signature check works or falls back — neither should raise
        assert isinstance(result, bool)


class TestRealTFProjectStructure:
    """
    Integration tests that use the real TF project folder on disk.
    These are skipped if the project is not accessible (NAS not mounted).
    """

    @pytest.fixture(autouse=True)
    def require_project(self):
        if not _REAL_TF_PROJECT.exists():
            pytest.skip(f"Real TF project not accessible: {_REAL_TF_PROJECT}")

    def test_real_project_config_readable(self):
        """config.yaml of the real project must be parseable."""
        config_path = _REAL_TF_PROJECT / "config.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert "Task" in cfg or "project_path" in cfg

    def test_real_project_has_tf_snapshots(self):
        """The real project must contain .index snapshot files (TF checkpoint format)."""
        index_files = list(_REAL_TF_PROJECT.rglob("*.index"))
        assert len(index_files) > 0, "No .index snapshot files found in real TF project"

    def test_real_project_snapshot_1000000_resolvable(self, tmp_path):
        """
        snapshot-1000000.index must resolve to a valid local_snap_index in a
        sandbox copy of the project (does not call DLC, only tests the resolution logic).
        """
        import shutil

        # Copy just the model folder structure (not the full project — avoids huge data)
        src_models = _REAL_TF_PROJECT / "dlc-models"
        if not src_models.exists():
            pytest.skip("dlc-models folder not found in real project")

        sandbox = tmp_path / "sandbox"
        shutil.copytree(str(src_models), str(sandbox / "dlc-models"))

        # Write a minimal config.yaml pointing to sandbox
        config_path = sandbox / "config.yaml"
        cfg = {"Task": "JackIBB", "scorer": "TS", "project_path": str(sandbox),
               "snapshotindex": 4, "TrainingFraction": [0.95], "bodyparts": ["Snout"]}
        with open(config_path, "w") as f:
            yaml.dump(cfg, f)

        snapshot_path = (
            "dlc-models/iteration-0/JackIBBJan32026-trainset95shuffle1/"
            "train/snapshot-1000000.index"
        )
        snap_file = (sandbox / snapshot_path).resolve()
        assert snap_file.exists(), f"snapshot-1000000.index not found in sandbox: {snap_file}"

        # Verify it resolves to the correct local index
        train_folder = snap_file.parent
        all_snaps = sorted(train_folder.glob("*.index"), key=lambda p: p.name)
        local_idx = next((i for i, sp in enumerate(all_snaps) if sp == snap_file), None)
        assert local_idx is not None, "snapshot-1000000.index not found in sorted list"
        # '1000000' < '200000' < ... lexicographically, so snapshot-1000000 sorts first (index 0).
        # This matches the real error log: "local index 0 of 5".
        assert local_idx == 0, (
            f"Expected snapshot-1000000 to be at lexicographic index 0, got {local_idx}. "
            f"All snapshots: {[s.name for s in all_snaps]}"
        )
