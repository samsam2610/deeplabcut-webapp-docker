"""
Tests for POST /annotate/crop-video.

Non-destructive: all writes go to tmp_path (pytest auto-teardown).
Integration tests that require ffmpeg are skipped when not available.

Real video used for integration tests:
  /home/sam/data-disk/Parra-Data/sam-mediapipe-tracking/
    Aimar-12252025-landmarks-smooth-overlay/calib_cam1_20251225_120146_0_overlay.mp4
  (226 frames, 30 fps, mpeg4)
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Path to real test video (may not be present on all machines) ──────────
_CLIP_FILENAME = (
    "Aimar-12252025-landmarks-smooth-overlay/"
    "calib_cam1_20251225_120146_0_overlay.mp4"
)
_POSSIBLE_ROOTS = [
    "/home/sam/data-disk/Parra-Data/sam-mediapipe-tracking",
    "/user-data/Parra-Data/Disk/sam-mediapipe-tracking",
]
_REAL_VIDEO: Path | None = next(
    (Path(r) / _CLIP_FILENAME for r in _POSSIBLE_ROOTS
     if (Path(r) / _CLIP_FILENAME).is_file()), None
)
_HAS_REAL_VIDEO = _REAL_VIDEO is not None
_HAS_FFMPEG     = shutil.which("ffmpeg") is not None


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_client(data_dir: Path, user_data_dir: Path, fake_redis):
    """Create a Flask test client with minimal env setup."""
    import importlib
    env = {
        "DATA_DIR":           str(data_dir),
        "USER_DATA_DIR":      str(user_data_dir),
        "CELERY_BROKER_URL":  "redis://localhost:6379/0",
        "FLASK_SECRET_KEY":   "testkey1234567890abcdef12345678",
    }
    with patch.dict(os.environ, env):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import app as app_mod
            importlib.reload(app_mod)
            app_mod.DATA_DIR     = data_dir
            app_mod.USER_DATA_DIR = user_data_dir
            app_mod._redis_client = fake_redis
            app_mod.app.config["TESTING"]        = True
            app_mod.app.config["SECRET_KEY"]     = "testkey"
            app_mod.app.config["WTF_CSRF_ENABLED"] = False
            return app_mod.app.test_client(), app_mod


def _make_small_video(path: Path, num_frames: int = 30, fps: float = 30.0) -> Path:
    """Write a tiny synthetic video using cv2.VideoWriter (no ffmpeg required)."""
    import cv2
    import numpy as np
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (64, 64))
    for i in range(num_frames):
        frame = np.full((64, 64, 3), i * 8 % 256, dtype="uint8")
        writer.write(frame)
    writer.release()
    return path


def _make_csv(path: Path, frame_count: int, fps: float = 30.0,
              annotated: dict[int, dict] | None = None) -> Path:
    """Write a companion CSV with optional per-frame annotations."""
    fieldnames = ["timestamp", "frame_number", "frame_line_status", "note"]
    annotated = annotated or {}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for fn in range(1, frame_count + 1):
            ts   = f"{fn / fps:.3f}"
            over = annotated.get(fn, {})
            writer.writerow({
                "frame_number":      fn,
                "timestamp":         ts,
                "frame_line_status": over.get("frame_line_status", "0"),
                "note":              over.get("note", ""),
            })
    return path


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Unit tests (ffmpeg mocked) ────────────────────────────────────────────

class TestCropVideoValidation:
    """Input validation — no video I/O needed."""

    def test_missing_video_path(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = client.post("/annotate/crop-video",
                           json={"start_frame": 0, "num_frames": 10},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "video_path" in resp.get_json()["error"]

    def test_missing_num_frames(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = client.post("/annotate/crop-video",
                           json={"video_path": "/fake/v.mp4", "start_frame": 0},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_zero_num_frames(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = client.post("/annotate/crop-video",
                           json={"video_path": "/fake/v.mp4",
                                 "start_frame": 0, "num_frames": 0},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "num_frames" in resp.get_json()["error"]

    def test_negative_num_frames(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = client.post("/annotate/crop-video",
                           json={"video_path": "/fake/v.mp4",
                                 "start_frame": 0, "num_frames": -5},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_video_not_found(self, tmp_path, fake_redis):
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = client.post("/annotate/crop-video",
                           json={"video_path": str(tmp_path / "nonexistent.mp4"),
                                 "start_frame": 0, "num_frames": 10},
                           content_type="application/json")
        assert resp.status_code == 404


class TestCropVideoOutputNaming:
    """Verify output file naming convention (mocked ffmpeg)."""

    def _run(self, client, video_path, start, frames, postfix="", output_dir=""):
        with patch("subprocess.run") as mock_run:
            # ffprobe call → h264 codec
            probe_result = MagicMock()
            probe_result.returncode = 0
            probe_result.stdout = json.dumps({
                "streams": [{"codec_name": "h264", "pix_fmt": "yuv420p",
                             "bit_rate": "2000000"}]
            })
            # ffmpeg call → success
            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stderr = ""
            mock_run.side_effect = [probe_result, ffmpeg_result]

            return client.post("/annotate/crop-video",
                               json={"video_path": str(video_path),
                                     "start_frame": start,
                                     "num_frames":  frames,
                                     "postfix":     postfix,
                                     "output_dir":  output_dir},
                               content_type="application/json")

    def test_default_output_dir(self, tmp_path, fake_redis):
        """Output goes into {video_parent}/{video_stem}/ by default."""
        vid = _make_small_video(tmp_path / "myvideo.mp4", num_frames=50)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._run(client, vid, 5, 20)
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        out  = Path(data["output_path"])
        assert out.parent == tmp_path / "myvideo"
        assert out.name == "myvideo_5_24.mp4"

    def test_custom_output_dir(self, tmp_path, fake_redis):
        """Output goes to the specified output_dir."""
        vid    = _make_small_video(tmp_path / "myvideo.mp4", num_frames=50)
        outdir = tmp_path / "clips"
        outdir.mkdir()
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._run(client, vid, 0, 10, output_dir=str(outdir))
        assert resp.status_code == 200
        data = resp.get_json()
        assert Path(data["output_path"]).parent == outdir

    def test_postfix_in_filename(self, tmp_path, fake_redis):
        """Postfix is appended after end frame."""
        vid = _make_small_video(tmp_path / "myvideo.mp4", num_frames=50)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._run(client, vid, 10, 15, postfix="trainset")
        assert resp.status_code == 200
        assert "trainset" in Path(resp.get_json()["output_path"]).name

    def test_filename_contains_start_and_end(self, tmp_path, fake_redis):
        """Filename encodes start and end frame numbers."""
        vid = _make_small_video(tmp_path / "myvideo.mp4", num_frames=50)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._run(client, vid, 3, 12)   # frames 3..14
        assert resp.status_code == 200
        name = Path(resp.get_json()["output_path"]).name
        assert "_3_14" in name, f"Expected _3_14 in {name}"

    def test_frame_clamping(self, tmp_path, fake_redis):
        """End frame is clamped to total_frames-1; num_frames adjusted."""
        vid = _make_small_video(tmp_path / "myvideo.mp4", num_frames=20)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._run(client, vid, 15, 100)   # would exceed 20 frames
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["end_frame"] <= 19
        assert data["num_frames"] == data["end_frame"] - data["start_frame"] + 1

    def test_no_postfix(self, tmp_path, fake_redis):
        """When postfix is empty the filename ends with end_frame.ext."""
        vid = _make_small_video(tmp_path / "clip.mp4", num_frames=50)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._run(client, vid, 0, 10)
        assert resp.status_code == 200
        stem = Path(resp.get_json()["output_path"]).stem
        # stem should be "clip_0_9" with no trailing underscore segment
        parts = stem.split("_")
        assert len(parts) == 3    # [original_stem, start, end]


class TestCropVideoFfmpegCommand:
    """Verify the ffmpeg command is constructed correctly."""

    def test_codec_from_probe_is_used(self, tmp_path, fake_redis):
        """If ffprobe reports mpeg4, ffmpeg is called with -c:v mpeg4."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=30)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)

        with patch("subprocess.run") as mock_run:
            probe_result = MagicMock()
            probe_result.returncode = 0
            probe_result.stdout = json.dumps({
                "streams": [{"codec_name": "mpeg4", "pix_fmt": "yuv420p"}]
            })
            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stderr = ""
            mock_run.side_effect = [probe_result, ffmpeg_result]

            resp = client.post("/annotate/crop-video",
                               json={"video_path": str(vid),
                                     "start_frame": 0, "num_frames": 10})
            assert resp.status_code == 200

            ffmpeg_call = mock_run.call_args_list[1]
            cmd = ffmpeg_call[0][0]
            assert "-c:v" in cmd
            assert cmd[cmd.index("-c:v") + 1] == "mpeg4"

    def test_h264_maps_to_libx264(self, tmp_path, fake_redis):
        """h264 codec name → libx264 encoder."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=30)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)

        with patch("subprocess.run") as mock_run:
            probe_result = MagicMock()
            probe_result.returncode = 0
            probe_result.stdout = json.dumps({
                "streams": [{"codec_name": "h264", "pix_fmt": "yuv420p"}]
            })
            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stderr = ""
            mock_run.side_effect = [probe_result, ffmpeg_result]

            resp = client.post("/annotate/crop-video",
                               json={"video_path": str(vid),
                                     "start_frame": 0, "num_frames": 10})
            assert resp.status_code == 200
            ffmpeg_call = mock_run.call_args_list[1]
            cmd = ffmpeg_call[0][0]
            assert cmd[cmd.index("-c:v") + 1] == "libx264"

    def test_ffmpeg_not_found_returns_500(self, tmp_path, fake_redis):
        """FileNotFoundError from subprocess → 500 with helpful message."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=30)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)

        with patch("subprocess.run") as mock_run:
            probe_result = MagicMock()
            probe_result.returncode = 0
            probe_result.stdout = json.dumps({"streams": [{"codec_name": "h264"}]})
            mock_run.side_effect = [probe_result, FileNotFoundError("ffmpeg")]

            resp = client.post("/annotate/crop-video",
                               json={"video_path": str(vid),
                                     "start_frame": 0, "num_frames": 10})
            assert resp.status_code == 500
            assert "ffmpeg" in resp.get_json()["error"]

    def test_ffmpeg_failure_returns_500(self, tmp_path, fake_redis):
        """Non-zero ffmpeg returncode → 500 with stderr in error."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=30)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)

        with patch("subprocess.run") as mock_run:
            probe_result = MagicMock()
            probe_result.returncode = 0
            probe_result.stdout = json.dumps({"streams": [{"codec_name": "h264"}]})
            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 1
            ffmpeg_result.stderr = "ffmpeg: encoder not found"
            mock_run.side_effect = [probe_result, ffmpeg_result]

            resp = client.post("/annotate/crop-video",
                               json={"video_path": str(vid),
                                     "start_frame": 0, "num_frames": 10})
            assert resp.status_code == 500


class TestCropVideoCsv:
    """Verify companion CSV is correctly cropped and remapped."""

    def _crop(self, client, vid_path, csv_path, start, frames):
        """Helper: run crop with mocked ffmpeg, return response JSON."""
        with patch("subprocess.run") as mock_run:
            probe_result = MagicMock()
            probe_result.returncode = 0
            probe_result.stdout = json.dumps({
                "streams": [{"codec_name": "h264", "pix_fmt": "yuv420p"}]
            })
            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stderr = ""
            mock_run.side_effect = [probe_result, ffmpeg_result]
            return client.post("/annotate/crop-video",
                               json={"video_path": str(vid_path),
                                     "start_frame": start,
                                     "num_frames":  frames})

    def test_csv_is_cropped_to_frame_range(self, tmp_path, fake_redis):
        """Only rows in [start_frame, end_frame] appear in the output CSV."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=60)
        _make_csv(tmp_path / "v.csv", frame_count=60, annotated={
            10: {"note": "early"},
            30: {"note": "mid"},
            50: {"note": "late"},
        })
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._crop(client, tmp_path / "v.mp4", tmp_path / "v.csv", 20, 20)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["csv_path"] is not None

        rows = _read_csv(Path(data["csv_path"]))
        notes = {int(r["frame_number"]): r["note"] for r in rows}
        # frame 30 (original) → frame 11 in clip (30-20+1=11); note="mid"
        assert any(r["note"] == "mid" for r in rows), "Expected 'mid' row in clip CSV"
        assert not any(r["note"] == "early" for r in rows), "frame 10 should be excluded"
        assert not any(r["note"] == "late" for r in rows),  "frame 50 should be excluded"

    def test_csv_frame_numbers_remapped(self, tmp_path, fake_redis):
        """Frame numbers in clip CSV are 1-indexed relative to clip start."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=60)
        _make_csv(tmp_path / "v.csv", frame_count=60, annotated={
            25: {"note": "marker"},
        })
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._crop(client, tmp_path / "v.mp4", tmp_path / "v.csv", 20, 20)
        assert resp.status_code == 200
        rows = _read_csv(Path(resp.get_json()["csv_path"]))
        marker = next((r for r in rows if r["note"] == "marker"), None)
        assert marker is not None
        # original frame 25, clip starts at 20 → remapped to 25-20+1 = 6
        assert int(marker["frame_number"]) == 6

    def test_csv_all_columns_preserved(self, tmp_path, fake_redis):
        """Output CSV retains all original columns."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=30)
        _make_csv(tmp_path / "v.csv", frame_count=30)
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._crop(client, tmp_path / "v.mp4", tmp_path / "v.csv", 0, 15)
        assert resp.status_code == 200
        with open(Path(resp.get_json()["csv_path"]), newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) >= {"timestamp", "frame_number",
                                               "frame_line_status", "note"}

    def test_no_csv_no_crash(self, tmp_path, fake_redis):
        """If no companion CSV exists, crop still succeeds; csv_path is None."""
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=30)
        # Deliberately do NOT create v.csv
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._crop(client, tmp_path / "v.mp4", None, 0, 15)
        assert resp.status_code == 200
        assert resp.get_json()["csv_path"] is None

    def test_csv_timestamps_recalculated(self, tmp_path, fake_redis):
        """Timestamps in output CSV match the new clip-relative frame numbers."""
        fps = 30.0
        vid = _make_small_video(tmp_path / "v.mp4", num_frames=60, fps=fps)
        _make_csv(tmp_path / "v.csv", frame_count=60, fps=fps, annotated={
            31: {"note": "test"},
        })
        client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)
        resp = self._crop(client, tmp_path / "v.mp4", tmp_path / "v.csv", 30, 10)
        assert resp.status_code == 200
        rows = _read_csv(Path(resp.get_json()["csv_path"]))
        row = next((r for r in rows if r["note"] == "test"), None)
        assert row is not None
        # new_fn = 31 - 30 + 1 = 2; ts = 2/30 ≈ 0.067
        expected_ts = f"{2 / fps:.3f}"
        assert row["timestamp"] == expected_ts


# ── Integration tests (real ffmpeg required) ──────────────────────────────

@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not found on host")
@pytest.mark.skipif(not _HAS_REAL_VIDEO, reason="real test video not present")
class TestCropVideoIntegration:
    """End-to-end tests using the real mpeg4 overlay video and actual ffmpeg."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, fake_redis):
        """Copy real video (and optionally a CSV) into tmp_path."""
        self.tmp_path  = tmp_path
        self.fake_redis = fake_redis

        # Copy the video so we don't risk writing next to the original
        self.video = tmp_path / _REAL_VIDEO.name
        shutil.copy2(str(_REAL_VIDEO), str(self.video))

        self.client, _ = _make_client(tmp_path / "data", tmp_path / "user", fake_redis)

    def test_clip_file_created(self):
        """A real clip file is created at the expected output path."""
        resp = self.client.post("/annotate/crop-video",
                                json={"video_path": str(self.video),
                                      "start_frame": 0,
                                      "num_frames":  30})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        out  = Path(data["output_path"])
        assert out.exists(), f"Expected output file at {out}"

    def test_clip_readable_by_opencv(self):
        """The created clip can be opened and decoded by OpenCV."""
        import cv2
        resp = self.client.post("/annotate/crop-video",
                                json={"video_path": str(self.video),
                                      "start_frame": 10,
                                      "num_frames":  20})
        assert resp.status_code == 200
        out = Path(resp.get_json()["output_path"])
        cap = cv2.VideoCapture(str(out))
        assert cap.isOpened(), f"cv2 could not open {out}"
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        assert frame_count > 0

    def test_clip_with_csv(self):
        """CSV is cropped alongside the video when a companion CSV is present."""
        _make_csv(self.video.with_suffix(".csv"), frame_count=226, fps=30.0,
                  annotated={
                      15: {"note": "before_clip"},
                      50: {"note": "in_clip"},
                      100: {"note": "also_in_clip"},
                      180: {"note": "after_clip"},
                  })
        resp = self.client.post("/annotate/crop-video",
                                json={"video_path": str(self.video),
                                      "start_frame": 40,
                                      "num_frames":  80})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["csv_path"] is not None
        rows = _read_csv(Path(data["csv_path"]))
        notes = [r["note"] for r in rows if r["note"]]
        assert "in_clip" in notes
        assert "also_in_clip" in notes
        assert "before_clip" not in notes
        assert "after_clip" not in notes

    def test_postfix_in_real_clip(self):
        """Postfix appears in the real output filename."""
        resp = self.client.post("/annotate/crop-video",
                                json={"video_path": str(self.video),
                                      "start_frame": 0,
                                      "num_frames":  15,
                                      "postfix":     "trainset"})
        assert resp.status_code == 200
        assert "trainset" in Path(resp.get_json()["output_path"]).name

    def test_custom_output_dir(self):
        """Real clip is written into a custom output directory."""
        outdir = self.tmp_path / "custom_output"
        outdir.mkdir()
        resp = self.client.post("/annotate/crop-video",
                                json={"video_path": str(self.video),
                                      "start_frame": 0,
                                      "num_frames":  15,
                                      "output_dir":  str(outdir)})
        assert resp.status_code == 200
        out = Path(resp.get_json()["output_path"])
        assert out.parent == outdir
        assert out.exists()
