"""GPU smoke for inline analysis — real warm-worker round-trip.

Runs ONLY with `pytest -m gpu`. Caps n_frames=50, batch_size=8, TTL=10s.

Asserts:
  - 50 new rows in the canonical .h5
  - .csv updated
  - _meta.pickle records the snapshot in inline_analysis_snapshots
  - Worker exits within TTL + 5s
  - Disk delta < 10 MB
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.gpu


def _du_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(str(path)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _has_gpu() -> bool:
    if not os.environ.get("CUDA_VISIBLE_DEVICES"):
        return False
    try:
        out = subprocess.check_output(["nvidia-smi", "-L"], timeout=5)
        return b"GPU" in out
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _has_gpu(), reason="No GPU detected")
def test_inline_analysis_gpu_smoke(dlc_sandbox_project, fake_redis, tmp_path):
    """Boots the warm-worker against a real sandbox project, runs 50 frames,
    asserts outputs."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from dlc import tasks as dlc_tasks

    project = dlc_sandbox_project
    videos = (
        list((project / "videos").glob("*.mp4"))
        + list((project / "videos").glob("*.avi"))
    )
    if not videos:
        pytest.skip("sandbox project has no analyzable videos")
    video = videos[0]
    snapshots = sorted((project / "dlc-models-pytorch").rglob("snapshot-*.pt"))
    if not snapshots:
        pytest.skip("sandbox project has no PyTorch snapshots")
    snapshot = snapshots[-1]

    initial_du = _du_bytes(project)

    # Queue one range request before booting the worker (so BLPOP
    # immediately returns).
    req = {
        "req_id": "gpu-smoke-r1",
        "video_path": str(video),
        "start_frame": 0,
        "n_frames": 50,
        "batch_size": 8,
        "save_as_csv": True,
        "snapshot_path": str(snapshot.relative_to(project)),
    }
    fake_redis.lpush("inline:queue:u1:k1", json.dumps(req))

    t0 = time.time()
    dlc_tasks._dlc_inline_session_inner(
        fake_redis,
        user_id="u1",
        config_path=str(project / "config.yaml"),
        snap_key="k1",
        snapshot_path=str(snapshot.relative_to(project)),
        shuffle=1,
        trainingsetindex=0,
        batch_size=8,
        ttl=10,
    )
    elapsed = time.time() - t0
    assert elapsed < 15.0, f"worker should exit within TTL+5s (10+5), took {elapsed:.1f}s"

    # Assert result hash.
    h = fake_redis._hstore.get("inline:result:gpu-smoke-r1", {})
    assert h.get("status") == "done", f"unexpected status: {h}"
    assert int(h.get("n_analyzed", 0)) == 50

    # Assert canonical files updated.
    import pandas as pd
    h5_files = list(video.parent.glob(video.stem + "*.h5"))
    assert h5_files, "no h5 produced"
    df = pd.read_hdf(str(h5_files[-1]))
    assert len(df) >= 50

    csv_files = list(video.parent.glob(video.stem + "*.csv"))
    assert csv_files, "csv not produced (save_as_csv=True)"

    meta_files = list(video.parent.glob(video.stem + "*_meta.pickle"))
    assert meta_files
    with open(meta_files[-1], "rb") as f:
        meta = pickle.load(f)
    assert str(snapshot.relative_to(project)) in (
        meta.get("inline_analysis_snapshots") or set()
    )

    final_du = _du_bytes(project)
    delta = final_du - initial_du
    assert delta < 10 * 1024 * 1024, (
        f"disk delta {delta} > 10 MB (likely leaked temp files)"
    )
