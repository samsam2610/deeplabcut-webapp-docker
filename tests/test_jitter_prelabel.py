# tests/test_jitter_prelabel.py
import re, csv, pytest
import numpy as np
import pandas as pd
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from dlc.jitter_prelabel import (
    _parse_frame_number,
    detect_jitter_frames,
    upsert_frames,
)


def _make_h5(tmp_path, frame_nums, bodyparts, scorer="Ali"):
    """Build a minimal _machine_predictions_raw.h5 for testing.

    On hosts where the `tables` package has a numpy ABI mismatch, writing HDF5
    with pandas/PyTables is unavailable.  We return a placeholder path together
    with the in-memory DataFrame; callers that need detect_jitter_frames to read
    the file should patch ``pandas.read_hdf`` with the returned DataFrame.
    """
    stem = "test_stem"
    filenames = [f"img{i:04d}-{fn:05d}.png" for i, fn in enumerate(frame_nums)]
    index = [f"/data/labeled-data/{stem}/{f}" for f in filenames]
    cols = pd.MultiIndex.from_tuples(
        [(scorer, bp, coord) for bp in bodyparts for coord in ("x", "y", "likelihood")],
        names=["scorer", "bodyparts", "coords"],
    )
    np.random.seed(42)
    data = np.random.rand(len(frame_nums), len(cols)) * 100
    # Set likelihood to 0.9 for all
    lh_cols = [i for i, c in enumerate(cols) if c[2] == "likelihood"]
    data[:, lh_cols] = 0.9
    df = pd.DataFrame(data, index=index, columns=cols)
    h5_path = tmp_path / "_machine_predictions_raw.h5"
    try:
        df.to_hdf(str(h5_path), key="df_with_missing", mode="w")
    except Exception:
        # tables not available on this host; callers must patch pandas.read_hdf
        pass
    return h5_path, df


class TestParseFrameNumber:
    def test_standard_five_digit(self):
        assert _parse_frame_number("img0000-00190.png") == 190

    def test_six_digit(self):
        assert _parse_frame_number("img0012-158299.png") == 158299

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_frame_number("notaframe.jpg")


def _tables_available() -> bool:
    """Return True if the PyTables/tables package works on this host."""
    try:
        import tables  # noqa: F401
        return True
    except Exception:
        return False


class TestDetectJitterFrames:
    def _run_detect(self, h5_path, df_override, **kwargs):
        """Call detect_jitter_frames, patching pd.read_hdf when tables is broken."""
        if _tables_available():
            return detect_jitter_frames(h5_path, **kwargs)
        with patch("pandas.read_hdf", return_value=df_override):
            return detect_jitter_frames(h5_path, **kwargs)

    def test_basic_jitter_detection(self, tmp_path):
        """Frame with large spike in one bodypart should be detected."""
        bodyparts = ["Snout", "Wrist", "MCP-1"]
        frame_nums = list(range(10))
        h5_path, df = _make_h5(tmp_path, frame_nums, bodyparts)
        # Inject a spike: frame 5, Snout x deviates by 50px
        scorer = df.columns.get_level_values("scorer")[0]
        df_mod = df.copy()
        df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Snout", "x"))] = \
            df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Snout", "x"))] + 100.0
        df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Wrist", "x"))] = \
            df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Wrist", "x"))] + 100.0
        df_mod.iloc[5, df_mod.columns.get_loc((scorer, "MCP-1", "x"))] = \
            df_mod.iloc[5, df_mod.columns.get_loc((scorer, "MCP-1", "x"))] + 100.0
        if _tables_available():
            df_mod.to_hdf(str(h5_path), key="df_with_missing", mode="w")
        result = self._run_detect(h5_path, df_mod, px_threshold=20, min_jittery_parts=2)
        frame_nums_out = [r[0] for r in result]
        assert 5 in frame_nums_out

    def test_threshold_respected(self, tmp_path):
        """Frames below px_threshold should not be flagged."""
        bodyparts = ["Snout", "Wrist"]
        h5_path, df = _make_h5(tmp_path, list(range(10)), bodyparts)
        result = self._run_detect(h5_path, df, px_threshold=1000.0, min_jittery_parts=1)
        assert result == []

    def test_min_jittery_parts_respected(self, tmp_path):
        """Frame only jittery in 1 bodypart should not be flagged when min=2."""
        bodyparts = ["Snout", "Wrist", "MCP-1"]
        h5_path, df = _make_h5(tmp_path, list(range(10)), bodyparts)
        scorer = df.columns.get_level_values("scorer")[0]
        df_mod = df.copy()
        # Only spike Snout
        idx = df_mod.columns.get_loc((scorer, "Snout", "x"))
        df_mod.iloc[3, idx] += 200.0
        if _tables_available():
            df_mod.to_hdf(str(h5_path), key="df_with_missing", mode="w")
        result = self._run_detect(h5_path, df_mod, px_threshold=50, min_jittery_parts=2)
        frame_nums_out = [r[0] for r in result]
        assert 3 not in frame_nums_out

    def test_max_frames_cap(self, tmp_path):
        """Result should not exceed max_frames."""
        bodyparts = ["Snout", "Wrist", "MCP-1"]
        h5_path, df = _make_h5(tmp_path, list(range(20)), bodyparts)
        scorer = df.columns.get_level_values("scorer")[0]
        df_mod = df.copy()
        # Spike every frame
        for bp in bodyparts:
            for i in range(20):
                df_mod.iloc[i, df_mod.columns.get_loc((scorer, bp, "x"))] += 500.0
        if _tables_available():
            df_mod.to_hdf(str(h5_path), key="df_with_missing", mode="w")
        result = self._run_detect(h5_path, df_mod, px_threshold=10, min_jittery_parts=1, max_frames=5)
        assert len(result) <= 5


class TestUpsertFrames:
    def _make_stem(self, tmp_path):
        stem_dir = tmp_path / "test_stem"
        stem_dir.mkdir()
        return stem_dir

    def test_new_frame_added_with_correct_filename(self, tmp_path):
        """New frame_num creates imgNNNN-MMMMM.png and CSV row."""
        stem_dir = self._make_stem(tmp_path)
        jitter_frames = [(158299, {"Snout": {"x": 100.0, "y": 200.0, "likelihood": 0.95}})]
        scorer = "Ali"
        bodyparts = ["Snout"]
        fake_video = tmp_path / "video.mp4"
        fake_video.write_bytes(b"")
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap_cls.return_value = mock_cap
            mock_cap.read.return_value = (True, np.zeros((100, 100, 3), dtype=np.uint8))
            mock_cap.release.return_value = None
            with patch("cv2.imwrite") as mock_write:
                result = upsert_frames(stem_dir, fake_video, jitter_frames, scorer, bodyparts, min_lh=0.6)
        assert result["added"] == 1
        assert result["updated"] == 0

    def test_existing_mmmmm_updates_csv_no_new_image(self, tmp_path):
        """Frame already in folder: CSV updated, no new PNG extracted."""
        stem_dir = self._make_stem(tmp_path)
        # Pre-create the frame file
        (stem_dir / "img0000-158299.png").write_bytes(b"")
        jitter_frames = [(158299, {"Snout": {"x": 100.0, "y": 200.0, "likelihood": 0.95}})]
        scorer = "Ali"
        bodyparts = ["Snout"]
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap_cls.return_value = mock_cap
            with patch("cv2.imwrite") as mock_write:
                result = upsert_frames(
                    stem_dir, tmp_path / "video.mp4",
                    jitter_frames, scorer, bodyparts, min_lh=0.6
                )
            mock_write.assert_not_called()
        assert result["updated"] == 1
        assert result["added"] == 0

    def test_csv_created_with_correct_header(self, tmp_path):
        """When no CSV exists, one is created with 3-row header."""
        stem_dir = self._make_stem(tmp_path)
        jitter_frames = [(10, {"Snout": {"x": 50.0, "y": 60.0, "likelihood": 0.9}})]
        scorer = "Ali"
        bodyparts = ["Snout"]
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap_cls.return_value = mock_cap
            mock_cap.read.return_value = (True, np.zeros((100, 100, 3), dtype=np.uint8))
            mock_cap.release.return_value = None
            with patch("cv2.imwrite"):
                upsert_frames(stem_dir, tmp_path / "vid.mp4", jitter_frames, scorer, bodyparts)
        csv_path = stem_dir / "CollectedData_Ali.csv"
        assert csv_path.is_file()
        with open(csv_path) as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "scorer"
        assert rows[1][0] == "bodyparts"
        assert rows[2][0] == "coords"

    def test_low_likelihood_bodypart_written_as_empty(self, tmp_path):
        """Bodypart with likelihood < min_lh should have empty x,y in CSV."""
        stem_dir = self._make_stem(tmp_path)
        jitter_frames = [(10, {"Snout": {"x": 50.0, "y": 60.0, "likelihood": 0.3}})]
        scorer = "Ali"
        bodyparts = ["Snout"]
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap_cls.return_value = mock_cap
            mock_cap.read.return_value = (True, np.zeros((100, 100, 3), dtype=np.uint8))
            mock_cap.release.return_value = None
            with patch("cv2.imwrite"):
                upsert_frames(stem_dir, tmp_path / "vid.mp4", jitter_frames, scorer, bodyparts, min_lh=0.6)
        csv_path = stem_dir / "CollectedData_Ali.csv"
        with open(csv_path) as f:
            rows = list(csv.reader(f))
        data_row = rows[3]
        # x and y for Snout should be empty
        assert data_row[3] == "" and data_row[4] == ""

    def test_large_frame_number_naming(self, tmp_path):
        """Frame number > 99999 uses 6+ digits without truncation."""
        stem_dir = self._make_stem(tmp_path)
        frame_num = 158299
        jitter_frames = [(frame_num, {"Snout": {"x": 10.0, "y": 20.0, "likelihood": 0.9}})]
        scorer = "Ali"
        bodyparts = ["Snout"]
        written_paths = []
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap_cls.return_value = mock_cap
            mock_cap.read.return_value = (True, np.zeros((10, 10, 3), dtype=np.uint8))
            mock_cap.release.return_value = None
            with patch("cv2.imwrite", side_effect=lambda p, _: written_paths.append(p)):
                upsert_frames(stem_dir, tmp_path / "vid.mp4", jitter_frames, scorer, bodyparts)
        assert any("158299" in p for p in written_paths)

    def test_existing_csv_row_preserved_when_appending(self, tmp_path):
        """Existing CSV rows are preserved when a new frame is added."""
        stem_dir = self._make_stem(tmp_path)
        scorer = "Ali"
        bodyparts = ["Snout"]
        csv_path = stem_dir / "CollectedData_Ali.csv"
        # Pre-write a CSV with one existing frame
        with open(csv_path, "w", newline="") as f:
            import csv as csv_mod
            w = csv_mod.writer(f)
            w.writerow(["scorer", "", ""] + ["Ali", "Ali"])
            w.writerow(["bodyparts", "", ""] + ["Snout", "Snout"])
            w.writerow(["coords", "", ""] + ["x", "y"])
            w.writerow(["labeled-data", "test_stem", "img0000-00010.png", "50.0", "60.0"])
        # Now add a new frame
        jitter_frames = [(20, {"Snout": {"x": 70.0, "y": 80.0, "likelihood": 0.9}})]
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap_cls.return_value = mock_cap
            mock_cap.read.return_value = (True, np.zeros((10, 10, 3), dtype=np.uint8))
            mock_cap.release.return_value = None
            with patch("cv2.imwrite"):
                result = upsert_frames(stem_dir, tmp_path / "vid.mp4", jitter_frames, scorer, bodyparts)
        assert result["added"] == 1
        import csv as csv_mod
        with open(csv_path) as f:
            rows = list(csv_mod.reader(f))
        data_rows = rows[3:]
        filenames = [r[2] for r in data_rows]
        assert "img0000-00010.png" in filenames, "Existing row should be preserved"
        assert any("00020" in fn for fn in filenames), "New row should be appended"


class TestJitterPrelabelTask:
    """Integration-level: test the task can be imported and called synchronously."""

    def test_task_callable_with_missing_h5(self, tmp_path):
        """Task raises FileNotFoundError when _machine_predictions_raw.h5 is absent."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        os.environ.setdefault("CELERY_BROKER_URL", "memory://")
        os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "scorer: Ali\n"
            "project_path: " + str(tmp_path) + "\n"
            "bodyparts:\n"
            "  - Snout\n"
            "  - Wrist\n"
        )
        stem_dir = tmp_path / "labeled-data" / "test_stem"
        stem_dir.mkdir(parents=True)
        video_path = tmp_path / "test_stem.mp4"
        video_path.write_bytes(b"fake")

        # Import and call the Celery task directly (synchronous, no broker needed).
        # dlc.tasks imports `deeplabcut` at module level which is only available
        # inside the Docker worker container. Inject a stub so the module loads.
        import unittest.mock as _mock
        import types as _types
        _dlc_stub = _types.ModuleType("deeplabcut")
        _celery_app_stub = _types.ModuleType("celery_app")
        # Provide a minimal Celery stub so @celery.task decorator works
        import celery as _celery_lib
        _real_celery = _celery_lib.Celery()
        _celery_app_stub.celery = _real_celery

        with _mock.patch.dict("sys.modules", {
            "deeplabcut": _dlc_stub,
            "celery_app": _celery_app_stub,
        }):
            # Remove cached module if already imported in a previous test run
            import sys as _sys
            _sys.modules.pop("dlc.tasks", None)
            from dlc.tasks import dlc_jitter_prelabel

        with pytest.raises(FileNotFoundError, match="_machine_predictions_raw.h5"):
            dlc_jitter_prelabel.apply(kwargs={
                "config_path": str(config_path),
                "stem_path": str(stem_dir),
                "video_path": str(video_path),
            }).get()

    def test_task_happy_path_returns_expected_dict(self, tmp_path):
        """Task returns correct result dict when all inputs are valid and h5 exists."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        os.environ.setdefault("CELERY_BROKER_URL", "memory://")
        os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "scorer: Ali\n"
            "project_path: " + str(tmp_path) + "\n"
            "bodyparts:\n"
            "  - Snout\n"
            "  - Wrist\n"
        )
        stem_dir = tmp_path / "labeled-data" / "test_stem"
        stem_dir.mkdir(parents=True)
        # Create placeholder h5
        h5_path = stem_dir / "_machine_predictions_raw.h5"
        h5_path.write_bytes(b"fake")
        video_path = tmp_path / "test_stem.mp4"
        video_path.write_bytes(b"fake")

        import unittest.mock as _mock
        deeplabcut_stub = _mock.MagicMock()
        celery_app_stub = _mock.MagicMock()
        real_celery = __import__("celery")
        celery_app_stub.celery = real_celery.Celery()

        with _mock.patch.dict("sys.modules", {
            "deeplabcut": deeplabcut_stub,
            "celery_app": celery_app_stub,
        }):
            sys.modules.pop("dlc.tasks", None)
            from dlc.tasks import dlc_jitter_prelabel
            with _mock.patch("dlc.jitter_prelabel.detect_jitter_frames", return_value=[]) as mock_detect, \
                 _mock.patch("dlc.jitter_prelabel.upsert_frames", return_value={"added": 2, "updated": 1, "stem": "test_stem"}) as mock_upsert:
                result = dlc_jitter_prelabel.apply(kwargs={
                    "config_path": str(config_path),
                    "stem_path": str(stem_dir),
                    "video_path": str(video_path),
                }).get()

        assert result["flagged_frames"] == 0
        assert result["added"] == 2
        assert result["updated"] == 1
        assert result["stem"] == "test_stem"
        assert "webapp_link" in result
