# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP HTTP server to the Flask webapp exposing 9 DLC/Anipose tools to the Hermes agent, plus a jitter-prelabel Celery pipeline that finds unstable frames after analysis and adds them back to labeled-data with filtered coordinates.

**Architecture:** A manual MCP Streamable HTTP blueprint at `/mcp` (no extra packages — plain Flask JSON-RPC) handles tool discovery and dispatch. A new pure-logic module `src/dlc/jitter_prelabel.py` performs median-filter jitter detection and frame upsert, called by a new `dlc_jitter_prelabel` Celery task on the pytorch queue. Infrastructure changes add `llm-net` to the Flask container and a one-line Hermes config entry.

**Tech Stack:** Flask (blueprint + JSON-RPC), pandas + scipy (median filter), OpenCV (frame extraction), Celery (pytorch queue), Python csv module (DLC CSV upsert), Docker Compose (llm-net networking).

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/dlc/jitter_prelabel.py` | Pure jitter-detection + frame-upsert logic; no Flask/Celery deps |
| Create | `src/routes/mcp_server.py` | MCP HTTP blueprint; 9 tools; auth; dispatches to existing helpers |
| Create | `tests/test_jitter_prelabel.py` | 9 unit tests for jitter_prelabel.py |
| Create | `tests/test_mcp_server.py` | 7 unit tests for MCP blueprint |
| Modify | `src/dlc/tasks.py` | Add `dlc_jitter_prelabel` task at bottom of file |
| Modify | `src/app.py` | Register mcp_server blueprint |
| Modify | `requirements-flask.txt` | Add `scipy` (median filter; pandas/numpy already present) |
| Modify | `docker-compose.yml` | Add `llm-net`, alias `dlc-webapp`, `WEBAPP_PUBLIC_URL` env var |
| Modify | `/home/sam/docker-images/hermes-agent/hermes-data/config.yaml` | Add `mcp_servers.dlc_webapp` |

---

## Background: DLC CSV Format

Every `CollectedData_<scorer>.csv` has exactly this layout (confirmed from real data):

```
scorer,,,Ali,Ali,Ali,...           ← row 0: col0="scorer", col1="", col2="", then scorer for each bodypart×2
bodyparts,,,Snout,Snout,Wrist,...  ← row 1: col0="bodyparts", then bodypart names (each repeated for x,y)
coords,,,x,y,x,y,...               ← row 2: col0="coords", then alternating x/y
labeled-data,<stem>,img0000-158299.png,706.7,433.5,...  ← row 3+: data rows
```

Frame files: `img{NNNN:04d}-{MMMMM:05d}.png` where NNNN=order-in-folder, MMMMM=video-frame-number (zero-padded to min 5 digits). Files are PNG.

The `_machine_predictions_raw.h5` is a pandas DataFrame with MultiIndex columns `(scorer, individuals, bodypart, coord)` or `(scorer, bodypart, coord)`, index = full file paths to each frame PNG.

---

## Task 1: Jitter Prelabel Logic Module

**Files:**
- Create: `src/dlc/jitter_prelabel.py`
- Create: `tests/test_jitter_prelabel.py`

- [ ] **Step 1.1: Write the failing tests**

```python
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
    """Build a minimal _machine_predictions_raw.h5 for testing."""
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
    df.to_hdf(str(h5_path), key="df_with_missing", mode="w")
    return h5_path, df


class TestParseFrameNumber:
    def test_standard_five_digit(self):
        assert _parse_frame_number("img0000-00190.png") == 190

    def test_six_digit(self):
        assert _parse_frame_number("img0012-158299.png") == 158299

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_frame_number("notaframe.jpg")


class TestDetectJitterFrames:
    def test_basic_jitter_detection(self, tmp_path):
        """Frame with large spike in one bodypart should be detected."""
        bodyparts = ["Snout", "Wrist", "MCP-1"]
        frame_nums = list(range(10))
        h5_path, df = _make_h5(tmp_path, frame_nums, bodyparts)
        # Inject a spike: frame 5, Snout x deviates by 50px
        df_mod = pd.read_hdf(str(h5_path))
        scorer = df_mod.columns.get_level_values("scorer")[0]
        for bp in bodyparts:
            df_mod.iloc[5]  # touch to confirm indexing
        df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Snout", "x"))] = \
            df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Snout", "x"))] + 100.0
        df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Wrist", "x"))] = \
            df_mod.iloc[5, df_mod.columns.get_loc((scorer, "Wrist", "x"))] + 100.0
        df_mod.iloc[5, df_mod.columns.get_loc((scorer, "MCP-1", "x"))] = \
            df_mod.iloc[5, df_mod.columns.get_loc((scorer, "MCP-1", "x"))] + 100.0
        df_mod.to_hdf(str(h5_path), key="df_with_missing", mode="w")
        result = detect_jitter_frames(h5_path, px_threshold=20, min_jittery_parts=2)
        frame_nums_out = [r[0] for r in result]
        assert 5 in frame_nums_out

    def test_threshold_respected(self, tmp_path):
        """Frames below px_threshold should not be flagged."""
        bodyparts = ["Snout", "Wrist"]
        h5_path, _ = _make_h5(tmp_path, list(range(10)), bodyparts)
        result = detect_jitter_frames(h5_path, px_threshold=1000.0, min_jittery_parts=1)
        assert result == []

    def test_min_jittery_parts_respected(self, tmp_path):
        """Frame only jittery in 1 bodypart should not be flagged when min=2."""
        bodyparts = ["Snout", "Wrist", "MCP-1"]
        h5_path, df = _make_h5(tmp_path, list(range(10)), bodyparts)
        df_mod = pd.read_hdf(str(h5_path))
        scorer = df_mod.columns.get_level_values("scorer")[0]
        # Only spike Snout
        idx = df_mod.columns.get_loc((scorer, "Snout", "x"))
        df_mod.iloc[3, idx] += 200.0
        df_mod.to_hdf(str(h5_path), key="df_with_missing", mode="w")
        result = detect_jitter_frames(h5_path, px_threshold=50, min_jittery_parts=2)
        frame_nums_out = [r[0] for r in result]
        assert 3 not in frame_nums_out

    def test_max_frames_cap(self, tmp_path):
        """Result should not exceed max_frames."""
        bodyparts = ["Snout", "Wrist", "MCP-1"]
        h5_path, df = _make_h5(tmp_path, list(range(20)), bodyparts)
        df_mod = pd.read_hdf(str(h5_path))
        scorer = df_mod.columns.get_level_values("scorer")[0]
        # Spike every frame
        for bp in bodyparts:
            for i in range(20):
                df_mod.iloc[i, df_mod.columns.get_loc((scorer, bp, "x"))] += 500.0
        df_mod.to_hdf(str(h5_path), key="df_with_missing", mode="w")
        result = detect_jitter_frames(h5_path, px_threshold=10, min_jittery_parts=1, max_frames=5)
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
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
python -m pytest tests/test_jitter_prelabel.py -q 2>&1 | head -30
```

Expected: ImportError or ModuleNotFoundError for `dlc.jitter_prelabel`.

- [ ] **Step 1.3: Create `src/dlc/jitter_prelabel.py`**

```python
# src/dlc/jitter_prelabel.py
"""
Jitter prelabel — pure logic, no Flask/Celery dependencies.
Detects unstable frames (raw vs median-filtered spike) and upserts
them into a DLC labeled-data stem with filtered coordinates as initial labels.
"""
from __future__ import annotations
import csv
import re
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from scipy.signal import medfilt


def _parse_frame_number(filename: str) -> int:
    """Extract video frame number (MMMMM) from imgNNNN-MMMMM.png."""
    m = re.search(r"img\d+-(\d+)\.png$", str(filename))
    if not m:
        raise ValueError(f"Cannot parse frame number from: {filename}")
    return int(m.group(1))


def _apply_median_filter(series: pd.Series, window: int = 5) -> pd.Series:
    """Median-filter a coordinate series, preserving NaN positions."""
    filled = series.ffill().bfill()
    filtered = medfilt(filled.values.astype(float), kernel_size=window)
    result = pd.Series(filtered, index=series.index)
    result[series.isna()] = np.nan
    return result


def _get_scorer_and_bodyparts(df: pd.DataFrame) -> tuple[str, str | None, list[str]]:
    """Return (scorer, individuals_or_None, [bodyparts]) from MultiIndex columns."""
    scorer = df.columns.get_level_values("scorer").unique()[0]
    level_names = df.columns.names
    if "individuals" in level_names:
        individuals = df.columns.get_level_values("individuals").unique()[0]
        bodyparts = (
            df[scorer][individuals]
            .columns.get_level_values("bodyparts")
            .unique()
            .tolist()
        )
    else:
        individuals = None
        bodyparts = (
            df[scorer].columns.get_level_values("bodyparts").unique().tolist()
        )
    return scorer, individuals, bodyparts


def detect_jitter_frames(
    h5_path: Path,
    px_threshold: float = 10.0,
    min_jittery_parts: int = 3,
    max_frames: int = 200,
    window: int = 5,
) -> list[tuple[int, dict]]:
    """
    Load _machine_predictions_raw.h5, apply median filter, return jittery frames.

    Returns list of (video_frame_number, {bodypart: {"x", "y", "likelihood"}}).
    Sorted by video_frame_number ascending. Capped at max_frames (highest-displacement
    frames kept when capping).
    """
    df = pd.read_hdf(str(h5_path))
    scorer, individuals, bodyparts = _get_scorer_and_bodyparts(df)

    # Sort rows by video frame number for correct temporal filtering
    frame_nums_raw = [_parse_frame_number(Path(idx).name) for idx in df.index]
    order = np.argsort(frame_nums_raw)
    df = df.iloc[order]
    frame_nums = [frame_nums_raw[i] for i in order]

    # Build filtered series per bodypart
    filtered: dict[str, dict] = {}
    for bp in bodyparts:
        try:
            if individuals:
                x_raw = df[(scorer, individuals, bp, "x")]
                y_raw = df[(scorer, individuals, bp, "y")]
                lh    = df[(scorer, individuals, bp, "likelihood")]
            else:
                x_raw = df[(scorer, bp, "x")]
                y_raw = df[(scorer, bp, "y")]
                lh    = df[(scorer, bp, "likelihood")]
        except KeyError:
            continue
        filtered[bp] = {
            "x_raw": x_raw,
            "y_raw": y_raw,
            "x_filt": _apply_median_filter(x_raw, window),
            "y_filt": _apply_median_filter(y_raw, window),
            "likelihood": lh,
        }

    # Detect jittery frames
    results: list[tuple[int, dict, float]] = []  # (frame_num, coords, max_disp)
    for i, frame_num in enumerate(frame_nums):
        jittery_bps = 0
        coords: dict[str, dict] = {}
        max_disp = 0.0
        for bp, data in filtered.items():
            rx, ry = float(data["x_raw"].iloc[i]), float(data["y_raw"].iloc[i])
            fx, fy = float(data["x_filt"].iloc[i]), float(data["y_filt"].iloc[i])
            lh     = float(data["likelihood"].iloc[i])
            if np.isnan(rx) or np.isnan(ry) or np.isnan(fx) or np.isnan(fy):
                coords[bp] = {"x": fx if not np.isnan(fx) else rx,
                              "y": fy if not np.isnan(fy) else ry,
                              "likelihood": lh}
                continue
            disp = np.sqrt((rx - fx) ** 2 + (ry - fy) ** 2)
            if disp > px_threshold:
                jittery_bps += 1
                max_disp = max(max_disp, disp)
            coords[bp] = {"x": fx, "y": fy, "likelihood": lh}
        if jittery_bps >= min_jittery_parts:
            results.append((frame_num, coords, max_disp))

    if len(results) > max_frames:
        results.sort(key=lambda r: r[2], reverse=True)
        results = results[:max_frames]
        results.sort(key=lambda r: r[0])

    return [(frame_num, coords) for frame_num, coords, _ in results]


def upsert_frames(
    stem_dir: Path,
    video_path: Path,
    jitter_frames: list[tuple[int, dict]],
    scorer: str,
    bodyparts: list[str],
    min_lh: float = 0.6,
) -> dict:
    """
    Extract/update frames in stem_dir using filtered coordinates as initial labels.

    jitter_frames: list of (video_frame_number, {bodypart: {"x", "y", "likelihood"}})
    Returns {"added": int, "updated": int, "stem": str}.
    """
    stem_dir = Path(stem_dir)
    stem_dir.mkdir(parents=True, exist_ok=True)

    # Build {frame_num: filename} for frames already in the folder
    existing: dict[int, str] = {}
    for p in sorted(stem_dir.glob("img*-*.png")):
        try:
            existing[_parse_frame_number(p.name)] = p.name
        except ValueError:
            continue

    next_nnnn = len(existing)
    csv_path = stem_dir / f"CollectedData_{scorer}.csv"

    rows_to_write: dict[str, dict[str, tuple[float, float] | None]] = {}
    new_images: list[tuple[int, str]] = []  # (frame_num, filename) needing extraction

    added = updated = 0

    for frame_num, coords in jitter_frames:
        coord_map: dict[str, tuple[float, float] | None] = {}
        for bp in bodyparts:
            bp_data = coords.get(bp)
            if bp_data and bp_data.get("likelihood", 0) >= min_lh:
                x, y = bp_data.get("x"), bp_data.get("y")
                if x is not None and y is not None and not (np.isnan(x) or np.isnan(y)):
                    coord_map[bp] = (float(x), float(y))
                else:
                    coord_map[bp] = None
            else:
                coord_map[bp] = None

        if frame_num in existing:
            rows_to_write[existing[frame_num]] = coord_map
            updated += 1
        else:
            filename = f"img{next_nnnn:04d}-{frame_num:05d}.png"
            rows_to_write[filename] = coord_map
            new_images.append((frame_num, filename))
            next_nnnn += 1
            added += 1

    # Extract new frames from video in one pass
    if new_images:
        cap = cv2.VideoCapture(str(video_path))
        for frame_num, filename in new_images:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if ret:
                cv2.imwrite(str(stem_dir / filename), frame)
        cap.release()

    # Upsert CSV
    _upsert_csv(csv_path, stem_dir.name, scorer, bodyparts, rows_to_write)

    return {"added": added, "updated": updated, "stem": stem_dir.name}


def _upsert_csv(
    csv_path: Path,
    stem_name: str,
    scorer: str,
    bodyparts: list[str],
    rows: dict[str, dict[str, tuple[float, float] | None]],
) -> None:
    """
    Write or update a DLC MultiIndex CSV.
    rows: {filename: {bodypart: (x, y) | None}}
    """
    # Build header
    scorer_header   = ["scorer",    "", ""] + [scorer for bp in bodyparts for _ in ("x", "y")]
    bp_header       = ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in ("x", "y")]
    coords_header   = ["coords",    "", ""] + ["x", "y"] * len(bodyparts)

    # Read existing data rows
    existing_rows: dict[str, list[str]] = {}
    if csv_path.is_file():
        with open(str(csv_path), newline="") as f:
            reader = csv.reader(f)
            all_rows = list(reader)
        for row in all_rows[3:]:
            if len(row) >= 3:
                existing_rows[row[2]] = row

    # Merge updates
    for filename, coords in rows.items():
        data_vals: list[str] = []
        for bp in bodyparts:
            xy = coords.get(bp)
            if xy is not None:
                data_vals.extend([f"{xy[0]:.4f}", f"{xy[1]:.4f}"])
            else:
                data_vals.extend(["", ""])
        existing_rows[filename] = ["labeled-data", stem_name, filename] + data_vals

    # Write back
    with open(str(csv_path), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(scorer_header)
        writer.writerow(bp_header)
        writer.writerow(coords_header)
        for row in existing_rows.values():
            writer.writerow(row)
```

- [ ] **Step 1.4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_jitter_prelabel.py -v
```

Expected: 9 tests pass. If `scipy` is not installed on the host, install it:
```bash
pip install scipy
```
(The container already will have it after Task 4.)

- [ ] **Step 1.5: Run full existing test suite to confirm no regressions**

```bash
python -m pytest tests/test_vlm_verification.py -q
```

Expected: 66 tests pass.

- [ ] **Step 1.6: Commit**

```bash
git add src/dlc/jitter_prelabel.py tests/test_jitter_prelabel.py
git commit -m "feat: add jitter_prelabel pure logic module with upsert and median filter"
```

---

## Task 2: dlc_jitter_prelabel Celery Task

**Files:**
- Modify: `src/dlc/tasks.py` (append at bottom of file)
- Tests: `tests/test_jitter_prelabel.py` (append integration-level task test)

- [ ] **Step 2.1: Append the failing task test**

Add to `tests/test_jitter_prelabel.py`:

```python
class TestJitterPrelabelTask:
    """Integration-level: test the task can be imported and called synchronously."""

    def test_task_callable_with_missing_h5(self, tmp_path):
        """Task raises FileNotFoundError when _machine_predictions_raw.h5 is absent."""
        import importlib
        # Import celery app in eager mode
        os.environ.setdefault("CELERY_BROKER_URL", "memory://")
        os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
        from celery import Celery
        app = Celery()
        app.config_from_object({"task_always_eager": True})

        config_path = tmp_path / "config.yaml"
        config_path.write_text("scorer: Ali\nproject_path: " + str(tmp_path) + "\n")
        stem_dir = tmp_path / "labeled-data" / "test_stem"
        stem_dir.mkdir(parents=True)
        video_path = tmp_path / "test_stem.mp4"
        video_path.write_bytes(b"")

        from dlc.jitter_prelabel import detect_jitter_frames
        with pytest.raises(FileNotFoundError):
            detect_jitter_frames(stem_dir / "_machine_predictions_raw.h5")
```

- [ ] **Step 2.2: Run new test to confirm it fails**

```bash
python -m pytest tests/test_jitter_prelabel.py::TestJitterPrelabelTask -v
```

Expected: passes immediately (FileNotFoundError is raised by pandas when h5 is missing — this test just confirms the error contract).

- [ ] **Step 2.3: Add `dlc_jitter_prelabel` to `src/dlc/tasks.py`**

Append at the end of `src/dlc/tasks.py` (before any `if __name__ == "__main__"` block):

```python
# ── Jitter Prelabel ───────────────────────────────────────────────────────────

@celery.task(bind=True, name="tasks.dlc_jitter_prelabel")
def dlc_jitter_prelabel(
    self,
    config_path: str,
    stem_path: str,
    video_path: str,
    px_threshold: float = 10.0,
    min_jittery_parts: int = 3,
    max_frames: int = 200,
    webapp_public_url: str = "",
):
    """
    Detect jittery frames in a DLC labeled-data stem and prelabel them with
    median-filtered predictions.

    Inputs:
      config_path       — path to DLC config.yaml (for scorer name)
      stem_path         — path to labeled-data/<stem>/ folder
      video_path        — path to the source video file
      px_threshold      — pixel displacement to count as jitter (default 10)
      min_jittery_parts — min bodyparts above threshold to flag a frame (default 3)
      max_frames        — cap on frames to extract (default 200)
      webapp_public_url — base URL for deep-link in result (e.g. http://192.168.1.13:5000)
    """
    import yaml as _yaml_mod
    from dlc.jitter_prelabel import detect_jitter_frames, upsert_frames
    from pathlib import Path as _Path

    task_id = self.request.id

    def _progress(pct, stage):
        self.update_state(state="PROGRESS", meta={"progress": pct, "stage": stage})

    _progress(5, "Loading config…")

    config_path = _Path(config_path)
    stem_dir    = _Path(stem_path)
    video_path  = _Path(video_path)

    if not config_path.is_file():
        raise FileNotFoundError(f"config.yaml not found: {config_path}")
    if not stem_dir.is_dir():
        raise FileNotFoundError(f"Stem directory not found: {stem_dir}")
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    h5_path = stem_dir / "_machine_predictions_raw.h5"
    if not h5_path.is_file():
        raise FileNotFoundError(
            f"_machine_predictions_raw.h5 not found in {stem_dir}. "
            "Run 'Machine Label Frames' first."
        )

    # Read scorer and bodyparts from config.yaml
    with open(str(config_path)) as _f:
        cfg = _yaml_mod.safe_load(_f)
    scorer    = cfg.get("scorer", "")
    bodyparts = cfg.get("bodyparts", [])
    if not scorer:
        raise ValueError("scorer not found in config.yaml")
    if not bodyparts:
        raise ValueError("bodyparts not found in config.yaml")

    _progress(20, "Detecting jitter frames…")

    jitter_frames = detect_jitter_frames(
        h5_path,
        px_threshold=px_threshold,
        min_jittery_parts=min_jittery_parts,
        max_frames=max_frames,
    )

    flagged = len(jitter_frames)
    _progress(50, f"Found {flagged} jitter frames. Extracting…")

    result = upsert_frames(
        stem_dir=stem_dir,
        video_path=video_path,
        jitter_frames=jitter_frames,
        scorer=scorer,
        bodyparts=bodyparts,
    )

    _progress(95, "Writing labels…")

    # Build deep-link
    stem_name = stem_dir.name
    link = ""
    if webapp_public_url:
        app_token = os.environ.get("APP_TOKEN", "")
        link = f"{webapp_public_url}/vlm/refiner?token={app_token}&stem={stem_name}"

    return {
        "flagged_frames": flagged,
        "added":          result["added"],
        "updated":        result["updated"],
        "stem":           stem_name,
        "webapp_link":    link,
    }
```

- [ ] **Step 2.4: Confirm existing tests still pass**

```bash
python -m pytest tests/test_vlm_verification.py -q
```

Expected: 66 tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add src/dlc/tasks.py tests/test_jitter_prelabel.py
git commit -m "feat: add dlc_jitter_prelabel Celery task (pytorch queue)"
```

---

## Task 3: MCP Server Blueprint

**Files:**
- Create: `src/routes/mcp_server.py`
- Create: `tests/test_mcp_server.py`

**Context:** The MCP Streamable HTTP protocol uses JSON-RPC 2.0 over POST. We implement it manually — no extra packages. Hermes sends `initialize`, `notifications/initialized` (no response needed), `tools/list`, and `tools/call`. Sessions are stateless; each call validates `session_token` against `APP_TOKEN`.

- [ ] **Step 3.1: Write failing tests**

```python
# tests/test_mcp_server.py
import json, pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

os.environ.setdefault("APP_TOKEN", "test-token")
os.environ.setdefault("WEBAPP_PUBLIC_URL", "http://192.168.1.13:5000")
os.environ.setdefault("DATA_DIR", "/tmp/test-data")
os.environ.setdefault("USER_DATA_DIR", "/tmp/test-userdata")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

from unittest.mock import patch, MagicMock
from pathlib import Path


def _make_app():
    """Create Flask test app with mcp_server blueprint registered."""
    from flask import Flask
    import secrets as _sec
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["APP_TOKEN"] = "test-token"
    app.config["WEBAPP_PUBLIC_URL"] = "http://192.168.1.13:5000"
    app.config["APP_DATA_DIR"] = Path("/tmp/test-data")
    app.config["APP_USER_DATA_DIR"] = Path("/tmp/test-userdata")
    app.config["APP_REDIS"] = MagicMock()
    app.config["APP_CELERY"] = MagicMock()
    from routes.mcp_server import bp
    app.register_blueprint(bp)
    return app


def _post(client, method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        body["params"] = params
    return client.post("/mcp", json=body)


class TestMCPAuth:
    def test_bad_token_returns_error(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "list_dlc_projects",
                "arguments": {"session_token": "wrong"}
            })
            data = resp.get_json()
            assert "error" in data


class TestMCPInitialize:
    def test_initialize_returns_protocol_version(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hermes", "version": "1.0"}
            })
            data = resp.get_json()
            assert data["result"]["protocolVersion"] == "2024-11-05"
            assert "Mcp-Session-Id" in resp.headers


class TestMCPToolsList:
    def test_tools_list_returns_9_tools(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "tools/list")
            data = resp.get_json()
            tools = data["result"]["tools"]
            names = [t["name"] for t in tools]
            assert "list_dlc_projects" in names
            assert "list_anipose_projects" in names
            assert "jitter_prelabel" in names
            assert "get_task_status" in names
            assert "webapp_link" in names
            assert len(tools) == 9


class TestMCPToolsCall:
    def test_list_dlc_projects_empty_data_dir(self, tmp_path):
        app = _make_app()
        app.config["APP_DATA_DIR"] = tmp_path
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "list_dlc_projects",
                "arguments": {"session_token": "test-token"}
            })
            data = resp.get_json()
            assert "result" in data
            content = data["result"]["content"][0]["text"]
            assert isinstance(json.loads(content), list)

    def test_list_anipose_projects(self, tmp_path):
        app = _make_app()
        app.config["APP_DATA_DIR"] = tmp_path
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "list_anipose_projects",
                "arguments": {"session_token": "test-token"}
            })
            data = resp.get_json()
            assert "result" in data

    def test_webapp_link_contains_token(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "webapp_link",
                "arguments": {"session_token": "test-token"}
            })
            data = resp.get_json()
            link = data["result"]["content"][0]["text"]
            assert "192.168.1.13:5000" in link
            assert "token=test-token" in link

    def test_get_task_status_pending(self):
        app = _make_app()
        mock_celery = MagicMock()
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.info = None
        mock_celery.AsyncResult.return_value = mock_result
        app.config["APP_CELERY"] = mock_celery
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "get_task_status",
                "arguments": {"session_token": "test-token", "task_id": "abc-123"}
            })
            data = resp.get_json()
            content = json.loads(data["result"]["content"][0]["text"])
            assert content["state"] == "PENDING"

    def test_notifications_initialized_returns_204(self):
        app = _make_app()
        with app.test_client() as c:
            resp = c.post("/mcp", json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            })
            assert resp.status_code == 204
```

- [ ] **Step 3.2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_mcp_server.py -q 2>&1 | head -20
```

Expected: ImportError for `routes.mcp_server`.

- [ ] **Step 3.3: Create `src/routes/mcp_server.py`**

```python
# src/routes/mcp_server.py
"""
MCP Streamable HTTP server — Flask blueprint.

Implements the MCP 2024-11-05 protocol over plain HTTP POST (no SSE).
Exposes 9 tools for DLC/Anipose project management via the Hermes agent.

Auth: every tools/call passes session_token validated against APP_TOKEN.
"""
from __future__ import annotations
import json
import os
import secrets
import uuid
from pathlib import Path

from celery.result import AsyncResult
from flask import Blueprint, Response, current_app, jsonify, request

bp = Blueprint("mcp_server", __name__)

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "dlc-webapp", "version": "1.0.0"}

# ── Helpers ───────────────────────────────────────────────────────

def _app_token() -> str:
    return current_app.config.get("APP_TOKEN") or os.environ.get("APP_TOKEN", "")

def _public_url() -> str:
    return current_app.config.get("WEBAPP_PUBLIC_URL") or os.environ.get("WEBAPP_PUBLIC_URL", "")

def _data_dir() -> Path:
    return Path(current_app.config.get("APP_DATA_DIR", os.environ.get("DATA_DIR", "/app/data")))

def _user_data_dir() -> Path:
    return Path(current_app.config.get("APP_USER_DATA_DIR", os.environ.get("USER_DATA_DIR", "/user-data")))

def _celery():
    return current_app.config["APP_CELERY"]


def _check_token(session_token: str) -> None:
    tok = _app_token()
    if not tok or not secrets.compare_digest(str(session_token), str(tok)):
        raise PermissionError("Invalid session token")


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


# ── Tool definitions ──────────────────────────────────────────────

_TOOLS = [
    {
        "name": "list_dlc_projects",
        "description": "List all DeepLabCut projects available on the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string", "description": "App auth token"}
            },
            "required": ["session_token"],
        },
    },
    {
        "name": "list_anipose_projects",
        "description": "List all Anipose projects available on the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"}
            },
            "required": ["session_token"],
        },
    },
    {
        "name": "browse_project",
        "description": "List files and subdirectories within a project folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "project_id": {"type": "string", "description": "Project folder name"},
                "subpath": {"type": "string", "description": "Optional sub-path within project", "default": ""},
            },
            "required": ["session_token", "project_id"],
        },
    },
    {
        "name": "run_dlc_analysis",
        "description": "Run DLC pose estimation analysis on a video file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "config_path": {"type": "string", "description": "Full path to DLC config.yaml"},
                "video_path": {"type": "string", "description": "Full path to video file"},
            },
            "required": ["session_token", "config_path", "video_path"],
        },
    },
    {
        "name": "run_anipose_pipeline",
        "description": (
            "Run an Anipose pipeline operation. Valid operations: calibrate, filter_2d, triangulate, "
            "filter_3d, organize_for_anipose, convert_mediapipe_csv_to_h5, convert_mediapipe_to_dlc_csv, "
            "convert_3d_csv_to_mat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "project_id": {"type": "string"},
                "operation": {"type": "string", "description": "Pipeline operation name"},
                "config_path": {"type": "string", "description": "Full path to Anipose config.toml (required for non-mediapipe ops)", "default": ""},
                "scorer": {"type": "string", "description": "Scorer name for MediaPipe ops (default 'User')", "default": "User"},
            },
            "required": ["session_token", "project_id", "operation"],
        },
    },
    {
        "name": "extract_frames",
        "description": "Extract evenly-spaced frames from a video and save to labeled-data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "config_path": {"type": "string", "description": "Full path to DLC config.yaml"},
                "video_path": {"type": "string", "description": "Full path to video file"},
                "count": {"type": "integer", "description": "Number of frames to extract", "default": 20},
            },
            "required": ["session_token", "config_path", "video_path"],
        },
    },
    {
        "name": "jitter_prelabel",
        "description": (
            "Detect jittery frames (large raw vs filtered displacement) in a labeled-data stem "
            "and add them back with median-filtered coordinates as initial labels for retraining."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "config_path": {"type": "string", "description": "Full path to DLC config.yaml"},
                "stem_path": {"type": "string", "description": "Full path to labeled-data/<stem>/ folder"},
                "video_path": {"type": "string", "description": "Full path to the source video"},
                "px_threshold": {"type": "number", "description": "Pixel displacement threshold (default 10)", "default": 10},
                "min_jittery_parts": {"type": "integer", "description": "Min bodyparts above threshold per frame (default 3)", "default": 3},
                "max_frames": {"type": "integer", "description": "Max frames to extract (default 200)", "default": 200},
            },
            "required": ["session_token", "config_path", "stem_path", "video_path"],
        },
    },
    {
        "name": "get_task_status",
        "description": "Poll the status of a background Celery task by task_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["session_token", "task_id"],
        },
    },
    {
        "name": "webapp_link",
        "description": "Generate a clickable URL to open the webapp (optionally at a specific stem).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "stem": {"type": "string", "description": "Optional labeled-data stem name for VLM refiner deep-link", "default": ""},
            },
            "required": ["session_token"],
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────

def _tool_list_dlc_projects(args: dict) -> str:
    _check_token(args["session_token"])
    data_dir = _data_dir()
    if not data_dir.is_dir():
        return json.dumps([])
    projects = sorted(
        [{"id": d.name, "name": d.name, "path": str(d)}
         for d in data_dir.iterdir() if d.is_dir() and (d / "config.yaml").is_file()],
        key=lambda p: p["name"],
    )
    return json.dumps(projects)


def _tool_list_anipose_projects(args: dict) -> str:
    _check_token(args["session_token"])
    data_dir = _data_dir()
    if not data_dir.is_dir():
        return json.dumps([])
    # Anipose projects have config.toml
    projects = sorted(
        [{"id": d.name, "name": d.name, "path": str(d)}
         for d in data_dir.iterdir() if d.is_dir() and (d / "config.toml").is_file()],
        key=lambda p: p["name"],
    )
    return json.dumps(projects)


def _tool_browse_project(args: dict) -> str:
    _check_token(args["session_token"])
    project_id = args["project_id"]
    subpath = args.get("subpath", "")
    data_dir = _data_dir()
    project_dir = (data_dir / project_id).resolve()
    if not str(project_dir).startswith(str(data_dir)):
        raise ValueError("Access denied")
    target = (project_dir / subpath).resolve() if subpath else project_dir
    if not target.is_dir():
        raise FileNotFoundError(f"Directory not found: {target}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if child.name.startswith(".") or child.name.startswith("@"):
            continue
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "path": str(child),
        })
    return json.dumps({"path": str(target), "entries": entries})


def _tool_run_dlc_analysis(args: dict) -> str:
    _check_token(args["session_token"])
    config_path = args["config_path"]
    video_path = args["video_path"]
    task = _celery().send_task(
        "tasks.dlc_analyze",
        kwargs={"config_path": config_path, "target_path": video_path, "params": {}},
        queue="pytorch",
    )
    return json.dumps({"task_id": task.id, "operation": "dlc_analyze"})


_ANIPOSE_OPERATION_TASKS = {
    "calibrate":                   "tasks.process_calibrate",
    "filter_2d":                   "tasks.process_filter_2d",
    "triangulate":                 "tasks.process_triangulate",
    "filter_3d":                   "tasks.process_filter_3d",
    "organize_for_anipose":        "tasks.process_organize_for_anipose",
    "convert_mediapipe_csv_to_h5": "tasks.process_convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv":"tasks.process_convert_mediapipe_to_dlc_csv",
    "convert_3d_csv_to_mat":       "tasks.process_convert_3d_csv_to_mat",
}
_ANIPOSE_MEDIAPIPE_OPS = {
    "organize_for_anipose", "convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv", "convert_3d_csv_to_mat",
}


def _tool_run_anipose_pipeline(args: dict) -> str:
    _check_token(args["session_token"])
    project_id = args["project_id"]
    operation  = args["operation"].lower()
    config_path = args.get("config_path", "")
    scorer      = args.get("scorer", "User") or "User"
    if operation not in _ANIPOSE_OPERATION_TASKS:
        raise ValueError(f"Unknown operation '{operation}'. Valid: {sorted(_ANIPOSE_OPERATION_TASKS)}")
    data_dir = _data_dir()
    project_dir = data_dir / project_id
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project not found: {project_id}")
    if operation in _ANIPOSE_MEDIAPIPE_OPS:
        task_kwargs = {"session_path": str(project_dir), "scorer": scorer}
    else:
        task_kwargs = {"session_path": str(project_dir), "config_path": config_path}
    task = _celery().send_task(_ANIPOSE_OPERATION_TASKS[operation], kwargs=task_kwargs, queue="celery")
    return json.dumps({"task_id": task.id, "operation": operation})


def _tool_extract_frames(args: dict) -> str:
    """Extract evenly-spaced frames from video, save to labeled-data/<video_stem>/."""
    _check_token(args["session_token"])
    import cv2 as _cv2
    import yaml as _yaml
    video_path  = Path(args["video_path"])
    config_path = Path(args["config_path"])
    count       = int(args.get("count", 20))
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"config.yaml not found: {config_path}")
    with open(str(config_path)) as _f:
        cfg = _yaml.safe_load(_f)
    scorer    = cfg.get("scorer", "")
    bodyparts = cfg.get("bodyparts", [])
    project_dir = config_path.parent
    stem_dir = project_dir / "labeled-data" / video_path.stem
    stem_dir.mkdir(parents=True, exist_ok=True)
    # Existing frame numbers already present
    existing = {
        int(m.group(1))
        for p in stem_dir.glob("img*-*.png")
        if (m := __import__("re").search(r"img\d+-(\d+)\.png$", p.name))
    }
    next_nnnn = len(list(stem_dir.glob("img*-*.png")))
    cap = _cv2.VideoCapture(str(video_path))
    total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if total <= 0:
        raise ValueError(f"Cannot read frame count from {video_path}")
    step = max(1, total // count)
    frame_nums = [i * step for i in range(count) if i * step < total]
    added = 0
    cap = _cv2.VideoCapture(str(video_path))
    for fn in frame_nums:
        if fn in existing:
            continue
        cap.set(_cv2.CAP_PROP_POS_FRAMES, fn)
        ret, frame = cap.read()
        if not ret:
            continue
        filename = f"img{next_nnnn:04d}-{fn:05d}.png"
        _cv2.imwrite(str(stem_dir / filename), frame)
        next_nnnn += 1
        added += 1
    cap.release()
    return json.dumps({"added": added, "stem": stem_dir.name, "video": video_path.name})


def _tool_jitter_prelabel(args: dict) -> str:
    _check_token(args["session_token"])
    task = _celery().send_task(
        "tasks.dlc_jitter_prelabel",
        kwargs={
            "config_path":       args["config_path"],
            "stem_path":         args["stem_path"],
            "video_path":        args["video_path"],
            "px_threshold":      float(args.get("px_threshold", 10)),
            "min_jittery_parts": int(args.get("min_jittery_parts", 3)),
            "max_frames":        int(args.get("max_frames", 200)),
            "webapp_public_url": _public_url(),
        },
        queue="pytorch",
    )
    return json.dumps({"task_id": task.id, "operation": "jitter_prelabel"})


def _tool_get_task_status(args: dict) -> str:
    _check_token(args["session_token"])
    task_id = args["task_id"]
    result = AsyncResult(task_id, app=_celery())
    state = result.state
    info = result.info
    if isinstance(info, Exception):
        info = str(info)
    return json.dumps({"state": state, "result": info, "task_id": task_id})


def _tool_webapp_link(args: dict) -> str:
    _check_token(args["session_token"])
    token = _app_token()
    base = _public_url() or "http://localhost:5000"
    stem = args.get("stem", "")
    if stem:
        url = f"{base}/vlm/refiner?token={token}&stem={stem}"
    else:
        url = f"{base}/?token={token}"
    return url


_TOOL_DISPATCH = {
    "list_dlc_projects":    _tool_list_dlc_projects,
    "list_anipose_projects": _tool_list_anipose_projects,
    "browse_project":       _tool_browse_project,
    "run_dlc_analysis":     _tool_run_dlc_analysis,
    "run_anipose_pipeline": _tool_run_anipose_pipeline,
    "extract_frames":       _tool_extract_frames,
    "jitter_prelabel":      _tool_jitter_prelabel,
    "get_task_status":      _tool_get_task_status,
    "webapp_link":          _tool_webapp_link,
}


# ── Route ─────────────────────────────────────────────────────────

@bp.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    if request.method == "GET":
        # SSE not supported — signal to client to use single-response mode
        return Response(status=405)

    if request.method == "DELETE":
        return Response(status=204)

    # POST — handle JSON-RPC 2.0 message
    msg = request.get_json(force=True, silent=True) or {}
    method = msg.get("method", "")
    params = msg.get("params") or {}
    req_id = msg.get("id")

    session_id = request.headers.get("Mcp-Session-Id") or str(uuid.uuid4())

    try:
        if method == "initialize":
            result = {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _SERVER_INFO,
            }
        elif method in ("notifications/initialized", "notifications/cancelled"):
            # Notifications: no response
            return Response(status=204)
        elif method == "tools/list":
            result = {"tools": _TOOLS}
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if name not in _TOOL_DISPATCH:
                raise ValueError(f"Unknown tool: {name}")
            text = _TOOL_DISPATCH[name](arguments)
            result = _content(text)
        else:
            resp = jsonify(_err(req_id, -32601, f"Method not found: {method}"))
            resp.headers["Mcp-Session-Id"] = session_id
            return resp

        resp = jsonify(_ok(req_id, result))

    except PermissionError as exc:
        resp = jsonify(_err(req_id, -32000, str(exc)))
    except (FileNotFoundError, ValueError) as exc:
        resp = jsonify(_err(req_id, -32602, str(exc)))
    except Exception as exc:
        resp = jsonify(_err(req_id, -32603, f"Internal error: {exc}"))

    resp.headers["Mcp-Session-Id"] = session_id
    return resp
```

- [ ] **Step 3.4: Run MCP server tests**

```bash
python -m pytest tests/test_mcp_server.py -v
```

Expected: 9 tests pass. (The `run_anipose_pipeline` and `extract_frames` tools dispatch to Celery tasks — they'll succeed because `APP_CELERY` is mocked.)

- [ ] **Step 3.5: Run full suite**

```bash
python -m pytest tests/test_vlm_verification.py -q
```

Expected: 66 tests pass.

- [ ] **Step 3.6: Commit**

```bash
git add src/routes/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add MCP HTTP blueprint at /mcp with 9 DLC/Anipose tools"
```

---

## Task 4: Register Blueprint + Add scipy Dependency

**Files:**
- Modify: `src/app.py`
- Modify: `requirements-flask.txt`

- [ ] **Step 4.1: Add `scipy` to `requirements-flask.txt`**

Open `requirements-flask.txt` and add `scipy` after `numpy`:

```
flask==3.0.*
gunicorn==21.*
celery[redis]==5.3.*
redis==5.*
toml
pyyaml
numpy
scipy
pandas
opencv-python-headless
tables
psutil
```

- [ ] **Step 4.2: Register MCP blueprint in `src/app.py`**

After the existing `from routes.custom_script import bp as _custom_script_bp` line (around line 208), add:

```python
from routes.mcp_server import bp as _mcp_bp
```

And after `app.register_blueprint(_custom_script_bp)`, add:

```python
app.register_blueprint(_mcp_bp)
```

- [ ] **Step 4.3: Run full test suite to confirm nothing broke**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass (66 vlm + 9 jitter + 9 mcp = 84 total, or close depending on other test files).

- [ ] **Step 4.4: Commit**

```bash
git add src/app.py requirements-flask.txt
git commit -m "feat: register MCP blueprint and add scipy to flask requirements"
```

---

## Task 5: Infrastructure Changes

**Files:**
- Modify: `docker-compose.yml`
- Modify: `/home/sam/docker-images/hermes-agent/hermes-data/config.yaml`

- [ ] **Step 5.1: Update `docker-compose.yml`**

Add `WEBAPP_PUBLIC_URL` to the flask `environment` block and add the `networks` section:

In the `flask:` service, add to `environment:`:
```yaml
      - WEBAPP_PUBLIC_URL=http://192.168.1.13:5000
```

Add a `networks:` section to the `flask:` service:
```yaml
    networks:
      default:
      llm-net:
        aliases:
          - dlc-webapp
```

Add a top-level `networks:` block at the end of the file (alongside the existing `volumes:` block):
```yaml
networks:
  llm-net:
    external: true
```

The full `flask:` service diff looks like:

```yaml
  flask:
    ...
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - CELERY_RESULT_BACKEND=redis://redis:6379/0
      - DATA_DIR=/app/data
      - USER_DATA_DIR=/user-data
      - FLASK_APP=app.py
      - FLASK_SECRET_KEY=${FLASK_SECRET_KEY:-change-me-in-production}
      - APP_TOKEN=deeplabcut
      - OLLAMA_URL=http://172.26.0.1:11434
      - WEBAPP_PUBLIC_URL=http://192.168.1.13:5000   # ← add this line
    ...
    networks:                    # ← add this block
      default:
      llm-net:
        aliases:
          - dlc-webapp
```

- [ ] **Step 5.2: Rebuild Flask container with new scipy dependency and restart on llm-net**

```bash
cd /home/sam/docker-images/deeplabcut-webapp-docker
docker compose build flask
docker compose up -d flask
```

Wait ~30 seconds, then verify:

```bash
docker inspect deeplabcut-webapp-docker-flask-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}: {{$v.IPAddress}}{{"\n"}}{{end}}'
```

Expected: shows both `deeplabcut-webapp-docker_default` and `llm-net` entries.

Verify the `/mcp` endpoint responds:

```bash
curl -s -X POST http://localhost:5000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python3 -m json.tool
```

Expected: JSON response with `"protocolVersion": "2024-11-05"`.

- [ ] **Step 5.3: Add Hermes MCP server entry**

Edit `/home/sam/docker-images/hermes-agent/hermes-data/config.yaml`.

Add after the existing config (before or after `platform_toolsets`):

```yaml
mcp_servers:
  dlc_webapp:
    url: "http://dlc-webapp:5000/mcp"
    timeout: 300
    connect_timeout: 30
```

- [ ] **Step 5.4: Restart Hermes to pick up the new MCP server**

```bash
cd /home/sam/docker-images/hermes-agent
docker compose restart hermes-agent
```

Wait ~15 seconds, then check logs for MCP discovery:

```bash
docker logs hermes-agent --tail 30
```

Expected: lines mentioning `dlc_webapp` tools discovered, e.g.:
```
[MCP] Connected to dlc_webapp — 9 tools registered
```

- [ ] **Step 5.5: Smoke test via Hermes Telegram**

In Telegram, send Hermes:
```
My deeplabcut app token is deeplabcut. List my DLC projects.
```

Expected: Hermes calls `mcp_dlc_webapp_list_dlc_projects` with `session_token="deeplabcut"` and replies with a project list (or empty list if no projects in `/app/data`).

- [ ] **Step 5.6: Commit docker-compose change**

```bash
git add docker-compose.yml
git commit -m "feat: add llm-net + WEBAPP_PUBLIC_URL to flask service for MCP/Hermes"
```

(The Hermes config.yaml change is outside this repo — no commit needed there.)

---

## Self-Review Checklist

After all tasks complete:

- [ ] Run full test suite: `python -m pytest tests/ -q` — all pass
- [ ] Smoke test `/mcp` endpoint with curl (Step 5.2)
- [ ] Smoke test via Hermes Telegram (Step 5.5)
- [ ] Verify `jitter_prelabel` task appears in Celery worker with `celery -A tasks inspect registered`
