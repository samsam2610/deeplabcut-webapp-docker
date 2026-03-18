"""
Tests for src/dlc_unified_viewer_engine.py — memory-safe unified viewer engine.

Test-name prefix: tuv_  (test_unified_viewer)

Constraints:
  #1 — GPU routing: cv2.VideoCapture and circle drawing are CPU-only; no GPU
         allocation occurs.
  #3 — Isolated test state: real-data tests copy ONLY the target .avi and .h5
         to a tmp_path sandbox; teardown is automatic.
  #4 — Zero name collision: all helpers / fixtures prefixed with ``tuv_``.

The STRICT MEMORY LEAK TEST (test_tuv_frame_generator_no_memory_leak) uses
psutil to assert that RSS growth across 1,000 frames is ≤ 5 %.  The test is
skipped if the reference video has fewer than 1,001 frames or if psutil is not
installed.
"""
from __future__ import annotations

import csv
import gc
import os
import shutil
import sys
from pathlib import Path

import pytest

# ── Ensure src/ is on the import path ─────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import dlc_unified_viewer_engine as uv

# ── Reference data ─────────────────────────────────────────────────────────────
_MAPS_DIR    = Path(
    "/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/MAPS-DREADSS"
)
_TARGET_STEM = "MAP1_20250713_112101_3"
_TARGET_AVI  = _MAPS_DIR / f"{_TARGET_STEM}.avi"
_TARGET_H5   = _MAPS_DIR / (
    f"{_TARGET_STEM}DLC_HrnetW48_DREADDJan7shuffle1_snapshot_150.h5"
)

_DATA_AVAILABLE = _TARGET_AVI.is_file() and _TARGET_H5.is_file()
_skip_no_data   = pytest.mark.skipif(
    not _DATA_AVAILABLE,
    reason="Reference data not mounted at expected path — skipping.",
)


# ── Sandbox fixture ────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def tuv_sandbox(tmp_path) -> Path:
    """
    Copy ONLY the target .avi and .h5 to an isolated temp sandbox.
    Teardown is automatic via pytest's tmp_path fixture.
    """
    if not _DATA_AVAILABLE:
        pytest.skip("Reference data not available.")
    sandbox = tmp_path / "tuv_sandbox"
    sandbox.mkdir()
    shutil.copy2(str(_TARGET_AVI), str(sandbox / _TARGET_AVI.name))
    shutil.copy2(str(_TARGET_H5),  str(sandbox / _TARGET_H5.name))
    return sandbox


# ── Companion CSV: find_companion_csv ─────────────────────────────────────────

def test_tuv_find_csv_missing(tmp_path):
    """find_companion_csv returns None when no .csv exists alongside the video."""
    fake_video = tmp_path / "video.avi"
    fake_video.touch()
    assert uv.find_companion_csv(str(fake_video)) is None


def test_tuv_find_csv_present(tmp_path):
    """find_companion_csv returns the correct Path when the .csv exists."""
    fake_video = tmp_path / "video.avi"
    fake_csv   = tmp_path / "video.csv"
    fake_video.touch()
    fake_csv.touch()
    result = uv.find_companion_csv(str(fake_video))
    assert result is not None
    assert result == fake_csv


def test_tuv_find_csv_different_stem(tmp_path):
    """find_companion_csv does NOT return a CSV with a different stem."""
    fake_video = tmp_path / "videoA.avi"
    other_csv  = tmp_path / "videoB.csv"
    fake_video.touch()
    other_csv.touch()
    assert uv.find_companion_csv(str(fake_video)) is None


# ── Companion CSV: read_companion_csv ─────────────────────────────────────────

def test_tuv_read_csv_empty(tmp_path):
    """read_companion_csv returns an empty list when CSV has only headers."""
    csv_path = tmp_path / "test.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "frame_number", "frame_line_status", "note"],
        )
        writer.writeheader()
    assert uv.read_companion_csv(str(csv_path)) == []


def test_tuv_read_csv_parses_rows(tmp_path):
    """read_companion_csv parses expected fields correctly."""
    csv_path = tmp_path / "test.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "frame_number", "frame_line_status", "note"],
        )
        writer.writeheader()
        writer.writerow({"timestamp": "0.033", "frame_number": "1",
                         "frame_line_status": "1", "note": "reach"})
        writer.writerow({"timestamp": "0.100", "frame_number": "3",
                         "frame_line_status": "2", "note": "grasp"})

    rows = uv.read_companion_csv(str(csv_path))
    assert len(rows) == 2
    assert rows[0]["frame_number"]      == 1
    assert rows[0]["note"]              == "reach"
    assert rows[0]["frame_line_status"] == "1"
    assert rows[1]["frame_number"]      == 3
    assert rows[1]["note"]              == "grasp"


def test_tuv_read_csv_sorted_ascending(tmp_path):
    """read_companion_csv returns rows sorted by frame_number ascending."""
    csv_path = tmp_path / "test.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "frame_number", "frame_line_status", "note"],
        )
        writer.writeheader()
        for fn in [100, 50, 10, 200, 1]:
            writer.writerow({"timestamp": "", "frame_number": fn,
                              "frame_line_status": "1", "note": f"n{fn}"})

    rows  = uv.read_companion_csv(str(csv_path))
    fnums = [r["frame_number"] for r in rows]
    assert fnums == sorted(fnums)


def test_tuv_read_csv_missing_file(tmp_path):
    """read_companion_csv returns [] on OSError (file not found)."""
    assert uv.read_companion_csv(str(tmp_path / "nonexistent.csv")) == []


def test_tuv_read_csv_float_frame_numbers(tmp_path):
    """read_companion_csv converts float strings like '1.0' to int frame numbers."""
    csv_path = tmp_path / "test.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "frame_number", "frame_line_status", "note"],
        )
        writer.writeheader()
        writer.writerow({"timestamp": "0.5", "frame_number": "15.0",
                         "frame_line_status": "1", "note": "x"})
    rows = uv.read_companion_csv(str(csv_path))
    assert len(rows) == 1
    assert rows[0]["frame_number"] == 15
    assert isinstance(rows[0]["frame_number"], int)


# ── csv_row_for_frame (binary search) ─────────────────────────────────────────

def test_tuv_csv_row_hit():
    """csv_row_for_frame returns the correct row for a present frame."""
    rows = [
        {"frame_number": 10, "timestamp": "0.1", "frame_line_status": "1", "note": "a"},
        {"frame_number": 50, "timestamp": "0.5", "frame_line_status": "2", "note": "b"},
        {"frame_number": 100, "timestamp": "1.0", "frame_line_status": "3", "note": "c"},
    ]
    result = uv.csv_row_for_frame(rows, 50)
    assert result is not None
    assert result["note"] == "b"


def test_tuv_csv_row_miss():
    """csv_row_for_frame returns None for a frame not in the list."""
    rows = [{"frame_number": 10, "timestamp": "", "frame_line_status": "1", "note": "x"}]
    assert uv.csv_row_for_frame(rows, 99) is None


def test_tuv_csv_row_empty():
    """csv_row_for_frame on an empty list returns None."""
    assert uv.csv_row_for_frame([], 0) is None


def test_tuv_csv_row_first_and_last():
    """csv_row_for_frame correctly finds the first and last rows."""
    rows = [
        {"frame_number": i, "timestamp": "", "frame_line_status": "0", "note": f"f{i}"}
        for i in range(1, 201)
    ]
    first = uv.csv_row_for_frame(rows, 1)
    last  = uv.csv_row_for_frame(rows, 200)
    assert first is not None and first["note"] == "f1"
    assert last  is not None and last["note"]  == "f200"


# ── DLC h5 finder ─────────────────────────────────────────────────────────────

def test_tuv_find_h5_no_files(tmp_path):
    """find_dlc_h5 returns [] when no .h5 files match the video stem."""
    video = tmp_path / "myvideo.avi"
    video.touch()
    assert uv.find_dlc_h5(str(video)) == []


def test_tuv_find_h5_finds_matching(tmp_path):
    """find_dlc_h5 finds .h5 files starting with the video stem."""
    video      = tmp_path / "myvideo.avi"
    h5_match   = tmp_path / "myvideoSCORER_shuffle1_snap50.h5"
    h5_nomatch = tmp_path / "othervideo.h5"
    video.touch(); h5_match.touch(); h5_nomatch.touch()

    result = uv.find_dlc_h5(str(video))
    assert len(result) == 1
    assert result[0] == h5_match


def test_tuv_find_h5_prefix_filter(tmp_path):
    """find_dlc_h5 with prefix filters by scorer substring (case-insensitive)."""
    video     = tmp_path / "clip.avi"
    h5_resnet = tmp_path / "clipDLC_ResNet50_scorer_snap100.h5"
    h5_hrnet  = tmp_path / "clipDLC_HrnetW48_scorer_snap150.h5"
    video.touch(); h5_resnet.touch(); h5_hrnet.touch()

    result = uv.find_dlc_h5(str(video), prefix="hrnetw48")
    assert len(result) == 1
    assert result[0] == h5_hrnet


def test_tuv_find_h5_multiple_matches(tmp_path):
    """find_dlc_h5 returns all matching files sorted by name."""
    video = tmp_path / "vid.avi"
    h5s   = [tmp_path / f"vid_snap{n}.h5" for n in [50, 100, 150]]
    video.touch()
    for h in h5s:
        h.touch()

    result = uv.find_dlc_h5(str(video))
    assert len(result) == 3
    assert result == sorted(result)


@_skip_no_data
def test_tuv_find_h5_real_data(tuv_sandbox):
    """find_dlc_h5 locates the known .h5 alongside the reference AVI."""
    video_path = str(tuv_sandbox / _TARGET_AVI.name)
    result     = uv.find_dlc_h5(video_path)
    assert len(result) >= 1
    assert result[0].suffix == ".h5"
    assert result[0].stem.startswith(_TARGET_STEM)


# ── Frame generator: basic correctness ────────────────────────────────────────

@_skip_no_data
def test_tuv_generator_yields_jpegs(tuv_sandbox):
    """frame_generator yields valid (frame_number, jpeg_bytes) tuples."""
    import cv2
    import numpy as np

    video_path = str(tuv_sandbox / _TARGET_AVI.name)
    gen    = uv.frame_generator(video_path, start=0, end=5)
    frames = list(gen)

    assert len(frames) == 5
    fn0, jpeg0 = frames[0]
    assert fn0 == 0
    assert isinstance(jpeg0, bytes)
    assert len(jpeg0) > 0

    arr = np.frombuffer(jpeg0, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape[0] > 0 and img.shape[1] > 0


@_skip_no_data
def test_tuv_generator_frame_numbers_sequential(tuv_sandbox):
    """frame_generator yields consecutive frame numbers starting at start=."""
    video_path   = str(tuv_sandbox / _TARGET_AVI.name)
    gen          = uv.frame_generator(video_path, start=10, end=15)
    frame_numbers = [fn for fn, _ in gen]
    assert frame_numbers == list(range(10, 15))


@_skip_no_data
def test_tuv_generator_with_h5_overlay(tuv_sandbox):
    """frame_generator with h5_path returns valid JPEG with pose overlay."""
    import cv2
    import numpy as np

    video_path = str(tuv_sandbox / _TARGET_AVI.name)
    h5_path    = str(tuv_sandbox / _TARGET_H5.name)
    gen        = uv.frame_generator(
        video_path, h5_path=h5_path,
        start=0, end=3, threshold=0.0, scale=0.5,
    )
    frames = list(gen)
    assert len(frames) == 3

    _, jpeg = frames[0]
    arr = np.frombuffer(jpeg, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None


@_skip_no_data
def test_tuv_generator_scale_halves_resolution(tuv_sandbox):
    """frame_generator with scale=0.5 produces frames at approximately half resolution."""
    import cv2
    import numpy as np

    video_path = str(tuv_sandbox / _TARGET_AVI.name)

    gen_full = uv.frame_generator(video_path, start=0, end=1, scale=1.0)
    _, jpeg_full = next(gen_full)
    gen_half = uv.frame_generator(video_path, start=0, end=1, scale=0.5)
    _, jpeg_half = next(gen_half)

    img_full = cv2.imdecode(np.frombuffer(jpeg_full, np.uint8), cv2.IMREAD_COLOR)
    img_half = cv2.imdecode(np.frombuffer(jpeg_half, np.uint8), cv2.IMREAD_COLOR)

    assert img_half.shape[0] == pytest.approx(img_full.shape[0] * 0.5, abs=2)
    assert img_half.shape[1] == pytest.approx(img_full.shape[1] * 0.5, abs=2)


@_skip_no_data
def test_tuv_generator_high_threshold_no_crash(tuv_sandbox):
    """frame_generator with threshold=1.01 renders without error (no markers drawn)."""
    video_path = str(tuv_sandbox / _TARGET_AVI.name)
    h5_path    = str(tuv_sandbox / _TARGET_H5.name)
    gen        = uv.frame_generator(
        video_path, h5_path=h5_path,
        start=0, end=2, threshold=1.01,
    )
    frames = list(gen)
    assert len(frames) == 2
    for _, jpeg in frames:
        assert isinstance(jpeg, bytes) and len(jpeg) > 0


@_skip_no_data
def test_tuv_generator_overlay_differs_from_plain(tuv_sandbox):
    """Overlay frame (threshold=0) differs in pixel content from plain frame."""
    import cv2
    import numpy as np

    video_path = str(tuv_sandbox / _TARGET_AVI.name)
    h5_path    = str(tuv_sandbox / _TARGET_H5.name)

    gen_plain   = uv.frame_generator(video_path, start=0, end=1)
    _, jpeg_plain = next(gen_plain)

    gen_overlay = uv.frame_generator(
        video_path, h5_path=h5_path,
        start=0, end=1, threshold=0.0,
    )
    _, jpeg_overlay = next(gen_overlay)

    # The two JPEGs must have different bytes (markers changed pixel values)
    assert jpeg_plain != jpeg_overlay


# ── STRICT MEMORY LEAK TEST ────────────────────────────────────────────────────

@_skip_no_data
def test_tuv_frame_generator_no_memory_leak(tuv_sandbox):
    """
    STRICT MEMORY LEAK TEST (Constraint: RSS growth ≤ 5 % across 1,000 frames).

    Procedure:
      1. Advance the generator to frame 10 → call gc.collect() → record
         baseline RSS (process resident set size).
      2. Advance to frame 1,000 → call gc.collect() → record final RSS.
      3. Assert: (final - baseline) / baseline ≤ 0.05  (5 %).

    Rationale:
      If the generator accumulates frame-sized numpy arrays, JPEG byte buffers,
      or cv2.VideoCapture intermediates, the RSS will grow linearly with
      frame count.  The 5 % tolerance covers:
        • Python allocator fragmentation / arena growth.
        • The poses_np array loaded once at generator construction (constant).
      Any linear accumulation of per-frame buffers will produce much more
      than 5 % growth and will cause the test to FAIL.

    Constraint #1 — GPU routing: cv2.VideoCapture is CPU-only; no CUDA context
    is allocated.
    """
    psutil = pytest.importorskip("psutil")
    import cv2

    process = psutil.Process(os.getpid())

    video_path = str(tuv_sandbox / _TARGET_AVI.name)
    h5_path    = str(tuv_sandbox / _TARGET_H5.name)

    # Verify the video is long enough before allocating memory
    cap          = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    if total_frames < 1001:
        pytest.skip(
            f"Video has only {total_frames} frames (<1001) — "
            "cannot measure 1,000-frame memory stability."
        )

    # Force a GC pass before starting so that any pre-test garbage is cleared
    gc.collect()

    gen = uv.frame_generator(
        video_path,
        h5_path=h5_path,
        threshold=0.6,
        scale=1.0,
    )

    mem_at_10:   "int | None" = None
    mem_at_1000: "int | None" = None

    for frame_num, jpeg in gen:
        # Immediately discard the caller's reference so the engine can free
        # the bytes on the next iteration (del _prev_jpeg inside the generator).
        del jpeg

        if frame_num == 10:
            gc.collect()
            mem_at_10 = process.memory_info().rss

        elif frame_num == 1000:
            gc.collect()
            mem_at_1000 = process.memory_info().rss
            break

    assert mem_at_10   is not None, "Video has fewer than 10 frames — unexpected."
    assert mem_at_1000 is not None, "Video has fewer than 1,000 frames — unexpected."

    growth = (mem_at_1000 - mem_at_10) / mem_at_10

    assert growth <= 0.05, (
        f"Memory grew {growth * 100:.2f}% from frame 10 → frame 1,000 "
        f"(baseline {mem_at_10 / 1024**2:.1f} MB → "
        f"{mem_at_1000 / 1024**2:.1f} MB). "
        "The frame generator is leaking per-frame buffers."
    )
