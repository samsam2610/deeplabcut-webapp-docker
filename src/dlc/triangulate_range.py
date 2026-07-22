"""Range-triangulate orchestration (worker-side, but Flask-importable).

Keeps the Celery task (``anipose/tasks.py::process_triangulate_range``) thin:
the task just wires ``self.update_state`` to ``run_triangulate_range``.

Heavy / worker-only imports (anipose_src.*, HDF5 reads) live behind small
patchable seams (``_load_config``, ``_triangulate``, ``_get_cam_name``) so the
wiring can be unit-tested on host with those mocked. Only pandas/numpy are
imported at module load, so this module imports cleanly in the Flask container.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import canonical_3d as _c3d

_CAM_RE = re.compile(r"_cam(\d+)_")
_VIDEO_EXTS = (".avi", ".mp4", ".mkv")


# ── patchable seams (worker-only heavy imports) ────────────────────────────

def _load_config(config_path):
    from anipose_src.load_config_funcs import load_config
    return load_config(str(config_path))


def _triangulate(config, calib_folder, video_folder, pose_folder,
                 fname_dict, output_fname):
    from anipose_src.triangulate_funcs import triangulate
    return triangulate(config, calib_folder, video_folder, pose_folder,
                       fname_dict, output_fname)


def _get_cam_name(config, path):
    """Anipose cam name for a video/h5 path. Uses anipose's own resolver when
    available (worker), else the [triangulation] cam_regex directly."""
    try:
        from anipose.common import get_cam_name
        return get_cam_name(config, str(path))
    except Exception:
        cam_regex = (config or {}).get("triangulation", {}).get("cam_regex", r"cam[0-9]")
        m = re.search(cam_regex, Path(path).stem)
        return m.group(0) if m else Path(path).stem


def _filter_pose2d(config, in_h5, out_h5) -> Path:
    """Anipose 2D pose filter (worker-only imports inside). Loads points from
    ``in_h5``, runs ``filter_pose_medfilt`` or ``filter_pose_viterbi`` per
    ``config['filter']['type']`` (default ``medfilt``), writes to ``out_h5``.
    Patchable seam so the wiring is unit-testable without anipose."""
    from anipose.common import load_pose_2d, write_pose_2d
    from anipose_src.filter_2d_funcs import (
        filter_pose_medfilt, filter_pose_viterbi, wrap_points)
    model_type = (config or {}).get("model_type", "deeplabcut")
    all_points, metadata = load_pose_2d(model_type, str(in_h5))
    ftype = (config or {}).get("filter", {}).get("type", "medfilt")
    fn = filter_pose_viterbi if ftype == "viterbi" else filter_pose_medfilt
    points, scores = fn(config, all_points, metadata["bodyparts"])
    all_points = wrap_points(points, scores)
    write_pose_2d(model_type, all_points[:, :, 0], metadata, str(out_h5))
    return Path(out_h5)


# ── helpers ────────────────────────────────────────────────────────────────

def _resolve_cam1(cam0: Path):
    """Find the sibling camera video in the same folder (any cam index != cam0),
    matching the dlc-3D module's filesystem sibling logic."""
    m = re.match(r"^(.+?)_cam(\d+)_(\d{8})", cam0.stem)
    if not m:
        return None
    prefix, cam_idx, date = m.group(1), int(m.group(2)), m.group(3)
    for f in sorted(cam0.parent.iterdir()):
        if f == cam0 or f.suffix.lower() not in _VIDEO_EXTS:
            continue
        m2 = re.match(r"^(.+?)_cam(\d+)_(\d{8})", f.stem)
        if m2 and m2.group(1) == prefix and m2.group(3) == date \
                and int(m2.group(2)) != cam_idx:
            return f
    return None


def _slice_pose2d_h5(src_h5, start, n, out_h5) -> tuple[Path, int]:
    """Write a temp pose-2d h5 covering ``[start, start+n)`` with rows
    re-indexed to ``0..m-1`` (positional slice of the source _analyzed.h5).

    Returns ``(out_path, m)`` where ``m`` is the number of rows written. ``m`` is
    0 when the range lies entirely beyond the analyzed data (``start >= len(df)``);
    the caller must skip such ranges, since anipose cannot read a 0-row pose file
    (``pd.read_hdf`` raises an opaque "no datasets found" error)."""
    df = pd.read_hdf(str(src_h5))
    start = max(0, int(start))
    sliced = df.iloc[start:start + int(n)].reset_index(drop=True)
    sliced.to_hdf(str(out_h5), key="df_with_missing", mode="w", format="fixed")
    return Path(out_h5), len(sliced)


def _emit(update, progress: int, stage: str, log: str = "") -> None:
    if update is not None:
        update(progress, stage, log)


# ── orchestration ──────────────────────────────────────────────────────────

def run_triangulate_range(cam0_video, start_frame, n_frames, update=None) -> dict:
    """Triangulate ``[start_frame, start_frame+n_frames)`` for the stereo pair of
    ``cam0_video``, merge into the raw canonical 3D and median-filter-splice into
    the filtered canonical. Returns
    ``{pair_name, start_frame, n_frames, raw_csv, filtered_csv}``."""
    cam0 = Path(cam0_video).resolve()
    if not cam0.is_file():
        raise FileNotFoundError(f"cam0 video not found: {cam0}")
    current_folder = cam0.parent

    _emit(update, 5, "Resolving stereo pair…")
    cam1 = _resolve_cam1(cam0)
    if cam1 is None:
        raise RuntimeError(f"Could not resolve a cam1 sibling for {cam0.name}")

    config_path = current_folder.parent / "config.toml"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"config.toml not found at {config_path} (expected in the anipose "
            f"project root, parent of the session folder)")
    config = _load_config(config_path)

    calib_folder = current_folder / "calibration"
    calib_toml = calib_folder / "calibration.toml"
    if not calib_toml.is_file():
        raise FileNotFoundError(
            f"calibration.toml not found ({calib_toml}) — run Initialize into "
            f"anipose format first.")

    pose_folder = current_folder / "pose-2d"
    h5_0 = pose_folder / f"{cam0.stem}_analyzed.h5"
    h5_1 = pose_folder / f"{cam1.stem}_analyzed.h5"
    for h in (h5_0, h5_1):
        if not h.is_file():
            raise FileNotFoundError(
                f"pose-2d file missing ({h}) — run Initialize into anipose "
                f"format first.")

    start_frame, n_frames = int(start_frame), int(n_frames)
    pair_name = _c3d.pair_name_from_cam0(cam0)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        s0 = td / f"{cam0.stem}_analyzed.h5"
        s1 = td / f"{cam1.stem}_analyzed.h5"
        _emit(update, 25, "Slicing pose-2d range…")
        _, m0 = _slice_pose2d_h5(h5_0, start_frame, n_frames, s0)
        _, m1 = _slice_pose2d_h5(h5_1, start_frame, n_frames, s1)
        # A range entirely beyond the analyzed 2D data slices to 0 rows — anipose
        # then chokes on the empty pose file with an opaque HDF error, which would
        # crash the whole task (and abort a Triangulate-all-for-tag batch). Skip it
        # gracefully: a tag placed on a frame that was never finalized has no 2D
        # poses to triangulate.
        if min(m0, m1) == 0:
            _emit(update, 100, "Skipped — no 2D data in range")
            return {
                "pair_name":    pair_name,
                "start_frame":  start_frame,
                "n_frames":     n_frames,
                "raw_csv":      None,
                "filtered_csv": None,
                "skipped":      True,
                "reason":       (f"range [{start_frame}, {start_frame + n_frames}) "
                                 f"has no 2D pose data (analyzed source has "
                                 f"{max(m0, m1)} frames)"),
            }

        pose_by_cam = {cam0: s0, cam1: s1}
        if (config or {}).get("filter", {}).get("enabled"):
            _emit(update, 35, "Filtering pose-2d (anipose 2D filter)…")
            for cam, sliced in list(pose_by_cam.items()):
                filtered = td / f"{cam.stem}_filtered.h5"
                try:
                    _filter_pose2d(config, sliced, filtered)
                    pose_by_cam[cam] = filtered
                except Exception as exc:  # noqa: BLE001
                    _emit(update, 35, "2D filter failed — using raw slice",
                          f"{cam.name}: {exc}")

        fname_dict = {
            _get_cam_name(config, cam0): str(pose_by_cam[cam0]),
            _get_cam_name(config, cam1): str(pose_by_cam[cam1]),
        }
        tmp_out = td / f"{pair_name}_3d.csv"
        _emit(update, 45, "Triangulating range…")
        _triangulate(config, str(calib_folder), str(current_folder),
                     str(pose_folder), fname_dict, str(tmp_out))

        df = pd.read_csv(str(tmp_out))
        if "fnum" in df.columns:
            df = df.drop(columns=["fnum"])
        # local rows 0..n-1 → global frame numbers
        df.index = pd.Index(np.arange(len(df)) + start_frame, name="fnum")

        _emit(update, 70, "Merging into canonical 3D…")
        raw_csv = _c3d.write_range_to_canonical_3d(current_folder, pair_name, df)

    _emit(update, 85, "Median-filtering range…")
    filtered_csv = _c3d.medfilt_range_and_splice(
        current_folder, pair_name, start_frame, n_frames, config)

    _emit(update, 100, "Complete")
    return {
        "pair_name":    pair_name,
        "start_frame":  start_frame,
        "n_frames":     n_frames,
        "raw_csv":      str(raw_csv),
        "filtered_csv": str(filtered_csv),
    }
