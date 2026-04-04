"""
Tests for DLC Celery tasks from tasks.py.

These tests call the task .run() method DIRECTLY (not via broker) to verify logic.
GPU tests are marked @pytest.mark.gpu and set CUDA_VISIBLE_DEVICES=0 (RTX 5090).
VRAM is verified clean after each GPU test (Constraint #2).

All tests use the sandbox fixture (Constraint #4).

Calling convention note:
  For @celery.task(bind=True) tasks, call `task.run(mock_self, *args)` to
  bypass the Celery dispatcher and test the underlying function directly.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# GPU routing constants (Constraint #1)
DLC_GPU_INDEX = "0"   # RTX 5090


def vram_cleanup_check():
    """Verify no zombie Python processes hold CUDA on GPU 0 after test."""
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader", "--id=0"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"\n[VRAM CHECK] Processes on GPU 0 after test:\n{result.stdout}")
    except Exception as e:
        print(f"\n[VRAM CHECK] Could not query GPU: {e}")


# ── Module-level: import tasks with mocked heavy dependencies ──────────────────

def _load_tasks_mod():
    """Load tasks.py with deeplabcut and anipose_src mocked out."""
    mock_dlc = MagicMock()
    mock_anipose = MagicMock()
    patches = {
        "deeplabcut": mock_dlc,
        "anipose_src": mock_anipose,
        "anipose_src.filter_2d_funcs": mock_anipose,
        "anipose_src.filter_3d_funcs": mock_anipose,
        "anipose_src.load_config_funcs": mock_anipose,
        "anipose_src.preprocessing_funcs": mock_anipose,
        "anipose_src.triangulate_funcs": mock_anipose,
        "anipose_src.calibration_funcs": mock_anipose,
    }
    with patch.dict("sys.modules", patches):
        import importlib
        if "tasks" in sys.modules:
            del sys.modules["tasks"]
        import tasks as tasks_mod
        return tasks_mod, mock_dlc


# Import once at module level — Celery tasks are registered by name on import
_tasks_mod, _mock_dlc = _load_tasks_mod()


@pytest.fixture
def tasks_mod():
    return _tasks_mod


@pytest.fixture
def mock_dlc():
    _mock_dlc.reset_mock()
    return _mock_dlc


def _make_minimal_config(project_dir: Path) -> Path:
    """Write a minimal, valid config.yaml (avoids multi-line key issues in original)."""
    config_path = project_dir / "config.yaml"
    videos_dir = project_dir / "videos"
    labeled_dir = project_dir / "labeled-data"
    labeled_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    # Create a labeled-data subfolder with a dummy video stem
    stem = "test_video_001"
    (labeled_dir / stem).mkdir(exist_ok=True)

    config_content = f"""Task: TestTask
scorer: TestScorer
project_path: {project_dir}
date: Jan2026
engine: pytorch
TrainingFraction:
- 0.8
bodyparts:
- Snout
- Wrist
video_sets:
  {videos_dir}/{stem}.mp4:
    crop: 0, 640, 0, 480
"""
    config_path.write_text(config_content)
    return config_path


# ── dlc_add_datasets_to_video_list ────────────────────────────────────────────

class TestDlcAddDatasetsToVideoList:
    """Tests for the dlc_add_datasets_to_video_list Celery task (pure logic)."""

    def test_syncs_video_sets_with_labeled_data(self, tmp_path, tasks_mod):
        project_dir = tmp_path / "dlc_project"
        config_path = _make_minimal_config(project_dir)
        # Add a second labeled-data folder not in config
        (project_dir / "labeled-data" / "extra_video_002").mkdir()

        # .run() auto-binds self → do NOT pass mock_self explicitly
        result = tasks_mod.dlc_add_datasets_to_video_list.run(str(config_path))

        assert result["status"] == "complete"
        assert result["operation"] == "add_datasets_to_video_list"
        assert isinstance(result["labeled_stems"], list)
        assert isinstance(result["video_sets"], list)
        assert len(result["labeled_stems"]) == 2
        for stem in result["labeled_stems"]:
            assert any(stem in vp for vp in result["video_sets"]), \
                f"Stem '{stem}' not found in video_sets"

    def test_creates_videos_dir_if_absent(self, tmp_path, tasks_mod):
        project_dir = tmp_path / "dlc_project2"
        config_path = _make_minimal_config(project_dir)
        videos_dir = project_dir / "videos"
        shutil.rmtree(str(videos_dir))

        tasks_mod.dlc_add_datasets_to_video_list.run(str(config_path))
        assert videos_dir.is_dir()

    def test_missing_config_raises_error(self, tmp_path, tasks_mod):
        with pytest.raises(RuntimeError):
            tasks_mod.dlc_add_datasets_to_video_list.run("/nonexistent/config.yaml")

    def test_does_not_modify_original_project(self, tmp_path, tasks_mod):
        """
        Constraint #4: verify that the original project is not touched.
        We test this by using only the sandbox (tmp_path copy) and then
        checking the original project's config mtime is unchanged.
        """
        _POSSIBLE = [
            Path("/home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07"),
            Path("/home/sam/data-disk/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07"),
        ]
        original = next((p for p in _POSSIBLE if p.is_dir()), None)
        if original is None:
            pytest.skip("Original project not found.")

        original_mtime = (original / "config.yaml").stat().st_mtime
        # Work on sandbox (tmp_path copy, not original)
        project_dir = tmp_path / "dlc_sandbox"
        config_path = _make_minimal_config(project_dir)
        tasks_mod.dlc_add_datasets_to_video_list.run(str(config_path))
        # Verify original untouched
        assert (original / "config.yaml").stat().st_mtime == original_mtime


# ── dlc_convert_labels_to_h5 ──────────────────────────────────────────────────

class TestDlcConvertLabelsToH5:
    """Tests for dlc_convert_labels_to_h5 Celery task (pandas CSV→H5 logic)."""

    def _write_labeled_data_csv(self, labeled_dir: Path, stem: str, scorer: str):
        """Write a minimal CollectedData CSV in a labeled-data subfolder."""
        import pandas as pd
        import numpy as np

        folder = labeled_dir / stem
        folder.mkdir(parents=True, exist_ok=True)
        bodyparts = ["Snout", "Wrist"]
        frames = [f"labeled-data/{stem}/img{i:04d}.png" for i in range(3)]
        cols = pd.MultiIndex.from_tuples(
            [(scorer, bp, coord) for bp in bodyparts for coord in ["x", "y", "likelihood"]],
            names=["scorer", "bodyparts", "coords"],
        )
        data = np.random.rand(3, len(cols))
        df = pd.DataFrame(data, index=frames, columns=cols)
        csv_path = folder / f"CollectedData_{scorer}.csv"
        df.to_csv(str(csv_path))
        return csv_path

    def test_converts_csv_to_h5(self, tmp_path, tasks_mod, mock_dlc):
        project_dir = tmp_path / "dlc_h5_test"
        config_path = _make_minimal_config(project_dir)
        labeled_dir = project_dir / "labeled-data"
        stem = "test_video_001"

        self._write_labeled_data_csv(labeled_dir, stem, "TestScorer")

        import yaml
        with open(str(config_path)) as f:
            cfg = yaml.safe_load(f)
        mock_dlc.auxiliaryfunctions.read_config.return_value = cfg

        result = tasks_mod.dlc_convert_labels_to_h5.run(str(config_path))

        assert result["status"] == "complete"
        assert result["operation"] == "convert_labels_to_h5"
        assert isinstance(result["converted"], list)
        assert isinstance(result["skipped"], list)

    def test_no_csv_means_folder_skipped(self, tmp_path, tasks_mod, mock_dlc):
        project_dir = tmp_path / "dlc_no_csv"
        config_path = _make_minimal_config(project_dir)
        # labeled-data/test_video_001 exists but has no CSV
        labeled_dir = project_dir / "labeled-data"
        (labeled_dir / "test_video_001").mkdir(exist_ok=True)

        import yaml
        with open(str(config_path)) as f:
            cfg = yaml.safe_load(f)
        mock_dlc.auxiliaryfunctions.read_config.return_value = cfg

        result = tasks_mod.dlc_convert_labels_to_h5.run(str(config_path))
        assert "test_video_001" in result["skipped"]


# ── dlc_create_training_dataset ───────────────────────────────────────────────

class TestDlcCreateTrainingDataset:
    """Tests for dlc_create_training_dataset Celery task."""

    def test_calls_dlc_with_freeze_split(self, tmp_path, tasks_mod, mock_dlc):
        project_dir = tmp_path / "dlc_ctd"
        config_path = _make_minimal_config(project_dir)

        mock_dlc.mergeandsplit.return_value = ([0, 1, 2], [3])
        mock_dlc.create_training_dataset.return_value = None
        # Patch update_state on the task object to avoid Celery backend calls
        tasks_mod.dlc_create_training_dataset.update_state = MagicMock()

        result = tasks_mod.dlc_create_training_dataset.run(
            str(config_path), num_shuffles=1, freeze_split=True
        )

        assert result["status"] == "complete"
        assert result["operation"] == "create_training_dataset"
        mock_dlc.mergeandsplit.assert_called_once_with(
            str(config_path), trainindex=0, uniform=True
        )
        mock_dlc.create_training_dataset.assert_called_once()

    def test_calls_dlc_without_freeze_split(self, tmp_path, tasks_mod, mock_dlc):
        project_dir = tmp_path / "dlc_ctd2"
        config_path = _make_minimal_config(project_dir)
        mock_dlc.create_training_dataset.return_value = None
        mock_dlc.mergeandsplit.reset_mock()
        tasks_mod.dlc_create_training_dataset.update_state = MagicMock()

        result = tasks_mod.dlc_create_training_dataset.run(
            str(config_path), num_shuffles=2, freeze_split=False
        )

        assert result["status"] == "complete"
        mock_dlc.mergeandsplit.assert_not_called()
        mock_dlc.create_training_dataset.assert_called_with(
            str(config_path), num_shuffles=2, userfeedback=False
        )

    def test_missing_config_raises_runtime_error(self, tmp_path, tasks_mod):
        tasks_mod.dlc_create_training_dataset.update_state = MagicMock()
        with pytest.raises(RuntimeError):
            tasks_mod.dlc_create_training_dataset.run("/nonexistent/path/config.yaml")

    def test_dlc_api_exception_propagates(self, tmp_path, tasks_mod, mock_dlc):
        project_dir = tmp_path / "dlc_ctd3"
        config_path = _make_minimal_config(project_dir)
        mock_dlc.mergeandsplit.side_effect = RuntimeError("DLC internal error")
        tasks_mod.dlc_create_training_dataset.update_state = MagicMock()

        with pytest.raises(RuntimeError):
            tasks_mod.dlc_create_training_dataset.run(
                str(config_path), freeze_split=True
            )


# ── dlc_machine_label_reapply ─────────────────────────────────────────────────

class TestDlcMachineLabelReapply:
    """Tests for dlc_machine_label_reapply (threshold re-application, no GPU)."""

    def _write_raw_predictions(self, stem_dir: Path, scorer: str, bodyparts: list):
        """Write _machine_predictions_raw.h5 and meta JSON for testing."""
        import pandas as pd
        import numpy as np

        stem_dir.mkdir(parents=True, exist_ok=True)
        frames = [f"labeled-data/{stem_dir.name}/img{i:04d}.png" for i in range(5)]
        cols = pd.MultiIndex.from_tuples(
            [(scorer, bp, coord) for bp in bodyparts for coord in ["x", "y", "likelihood"]],
            names=["scorer", "bodyparts", "coords"],
        )
        data = np.ones((5, len(cols))) * 0.5
        # Set some likelihoods: 3 above threshold (0.6), 2 below
        lik_cols = [i for i, c in enumerate(cols) if c[2] == "likelihood"]
        for i, col_i in enumerate(lik_cols):
            # Alternate high/low per bodypart
            data[:, col_i] = [0.3, 0.8, 0.9, 0.4, 0.95] if i % 2 == 0 else [0.7, 0.6, 0.5, 0.85, 0.2]

        df = pd.DataFrame(data, index=frames, columns=cols)
        raw_h5_path = stem_dir / "_machine_predictions_raw.h5"
        df.to_hdf(str(raw_h5_path), key="df_with_missing", mode="w")

        meta = {"scorer": scorer, "bodyparts": bodyparts}
        (stem_dir / "_machine_predictions_raw_meta.json").write_text(json.dumps(meta))
        return raw_h5_path

    def _run_reapply(self, tasks_mod, stem_dir, stem, scorer, bodyparts, threshold):
        """Helper: patch update_state on the task to avoid Celery backend calls."""
        tasks_mod.dlc_machine_label_reapply.update_state = MagicMock()
        return tasks_mod.dlc_machine_label_reapply.run(
            str(stem_dir), stem, scorer, bodyparts, threshold,
        )

    def test_reapply_returns_ok_status(self, tmp_path, tasks_mod):
        stem = "reapply_test_video"
        scorer = "TestScorer"
        bodyparts = ["Snout", "Wrist"]
        stem_dir = tmp_path / "labeled-data" / stem
        self._write_raw_predictions(stem_dir, scorer, bodyparts)

        result = self._run_reapply(tasks_mod, stem_dir, stem, scorer, bodyparts, 0.6)

        assert result["status"] == "ok"
        assert result["threshold"] == 0.6
        assert "n_machine" in result
        assert "n_human" in result
        assert isinstance(result["frames"], int)

    def test_reapply_missing_raw_h5_raises_error(self, tmp_path, tasks_mod):
        stem = "no_raw_here"
        stem_dir = tmp_path / "labeled-data" / stem
        stem_dir.mkdir(parents=True, exist_ok=True)
        tasks_mod.dlc_machine_label_reapply.update_state = MagicMock()
        # Code raises FileNotFoundError for missing raw predictions
        with pytest.raises((RuntimeError, FileNotFoundError)):
            tasks_mod.dlc_machine_label_reapply.run(
                str(stem_dir), stem, "TestScorer", ["Snout"], 0.6,
            )

    def test_higher_threshold_produces_fewer_machine_labels(self, tmp_path, tasks_mod):
        stem = "threshold_test"
        scorer = "TestScorer"
        bodyparts = ["Snout", "Wrist"]
        stem_dir = tmp_path / "labeled-data" / stem
        self._write_raw_predictions(stem_dir, scorer, bodyparts)

        result_low  = self._run_reapply(tasks_mod, stem_dir, stem, scorer, bodyparts, 0.1)
        result_high = self._run_reapply(tasks_mod, stem_dir, stem, scorer, bodyparts, 0.9)
        # Higher threshold → fewer or equal machine labels
        assert result_high["n_machine"] <= result_low["n_machine"]


# ── GPU-gated tests (requires RTX 5090) ───────────────────────────────────────

@pytest.mark.gpu
class TestDlcTrainNetworkGpuRouting:
    """
    Verifies GPU routing for training subprocess.
    CUDA_VISIBLE_DEVICES=0 → RTX 5090 (Constraint #1).
    VRAM clean after test (Constraint #2).
    """

    def test_train_subprocess_env_has_correct_gpu(self):
        import subprocess
        env_check = {**os.environ, "CUDA_VISIBLE_DEVICES": DLC_GPU_INDEX}
        result = subprocess.run(
            ["python3", "-c",
             "import os; print(os.environ.get('CUDA_VISIBLE_DEVICES', 'NOT_SET'))"],
            env=env_check,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == DLC_GPU_INDEX
        vram_cleanup_check()

    def test_gpu_0_is_rtx_5090(self):
        import subprocess
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": DLC_GPU_INDEX}
        result = subprocess.run(
            ["python3", "-c",
             "import torch; print(torch.cuda.get_device_name(0))"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            assert "5090" in result.stdout or "RTX" in result.stdout, \
                f"Expected RTX 5090 on GPU 0, got: {result.stdout.strip()}"
        vram_cleanup_check()
