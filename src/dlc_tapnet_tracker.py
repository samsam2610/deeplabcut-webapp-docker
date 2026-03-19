"""
DLC ↔ TAPNet Label Propagation Adapter
=======================================

Integrates Google DeepMind's TAPNet/TAPIR point tracker with the DeepLabCut
labeling pipeline, enabling automatic propagation of body-part labels across
consecutive video frames.

GPU constraint (enforced throughout):
  - TAPNet inference MUST run on CUDA_VISIBLE_DEVICES=0 (RTX 5090)
  - VRAM is freed by running inference inside an isolated subprocess that exits
    immediately after saving results — mirroring the existing DLC subprocess
    pattern in src/dlc/tasks.py.

Frame naming conventions supported:
  img{seq:04d}-{abs_frame}.png   ← primary (DREADD / Parra-Lab exports)
  frame{N:04d}.png               ← common alternative
  img{N:04d}.png                 ← seq-only fallback
"""

from __future__ import annotations

import gc
import json
import multiprocessing as _mp
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ─── GPU routing constant (Constraint #1) ────────────────────────────────────
# GPU 0 = RTX 5090   ← all TAPNet / DLC inference runs here
# GPU 1 = Blackwell  ← orchestrator / LLM, never touched by this module
_TAPNET_GPU_INDEX: int = 0

# ─── Frame filename regexes ──────────────────────────────────────────────────
# Priority order matters: try most-specific pattern first.
_RE_IMG_SEQ_ABS = re.compile(r"^img(\d+)-(\d+)\.png$", re.IGNORECASE)  # img0003-158302.png
_RE_FRAME_N     = re.compile(r"^frame(\d+)\.png$",      re.IGNORECASE)  # frame0050.png
_RE_IMG_SEQ     = re.compile(r"^img(\d+)\.png$",        re.IGNORECASE)  # img0042.png

# ─── TAPNet default checkpoint URL ───────────────────────────────────────────
TAPNET_CHECKPOINT_URL = (
    "https://storage.googleapis.com/dm-tapnet/tapir_checkpoint_panning.npy"
)


# ══════════════════════════════════════════════════════════════════════════════
# Frame filename utilities
# ══════════════════════════════════════════════════════════════════════════════

def parse_frame_number(filename: str) -> Optional[int]:
    """
    Extract the absolute frame number from a DLC frame filename.

    Returns None for non-image filenames or unrecognised patterns.

    Convention priority:
      1. img{seq}-{abs}.png  → abs (absolute video frame number)
      2. frame{N}.png        → N
      3. img{N}.png          → N
    """
    name = Path(filename).name
    m = _RE_IMG_SEQ_ABS.match(name)
    if m:
        return int(m.group(2))   # group 2 = abs frame number
    m = _RE_FRAME_N.match(name)
    if m:
        return int(m.group(1))
    m = _RE_IMG_SEQ.match(name)
    if m:
        return int(m.group(1))
    return None


def _parse_extraction_index(filename: str) -> Optional[int]:
    """
    Extract the extraction-order index from a DLC frame filename.

    For img{seq}-{abs}.png this is seq (the ordinal position within the
    extraction batch), which increments by 1 regardless of how many video
    frames were skipped between extracted frames.

    For other formats falls back to parse_frame_number.
    """
    name = Path(filename).name
    m = _RE_IMG_SEQ_ABS.match(name)
    if m:
        return int(m.group(1))   # group 1 = seq index, always 0,1,2,3...
    return parse_frame_number(filename)


def find_consecutive_sequences(frame_names: list[str]) -> list[list[str]]:
    """
    Group frame filenames into runs that were extracted consecutively.

    For img{seq}-{abs}.png files the extraction-order index (seq) is used
    rather than the absolute frame number, because DLC often extracts every
    Nth video frame so abs numbers can differ by 2 or more even for adjacent
    extracted frames.

    Non-image files and frames with unparseable names are silently skipped.
    Runs of length < 2 are not returned.

    Args:
        frame_names: Unsorted list of filenames (may include CSV, H5, etc.).

    Returns:
        List of groups, each group sorted by extraction index.
    """
    # Filter and parse using extraction index
    parsed: list[tuple[int, str]] = []
    for name in frame_names:
        idx = _parse_extraction_index(name)
        if idx is not None:
            parsed.append((idx, name))

    if not parsed:
        return []

    # Sort by extraction index
    parsed.sort(key=lambda t: t[0])

    sequences: list[list[str]] = []
    current: list[str] = [parsed[0][1]]
    prev_num = parsed[0][0]

    for num, name in parsed[1:]:
        if num == prev_num + 1:
            current.append(name)
        else:
            if len(current) >= 2:
                sequences.append(current)
            current = [name]
        prev_num = num

    if len(current) >= 2:
        sequences.append(current)

    return sequences


def check_anchor_frames(
    frame_names: list[str],
    labeled_frames: set[str],
) -> dict:
    """
    Determine which endpoint of a consecutive sequence is labeled.

    Args:
        frame_names: Ordered list of frames in the sequence.
        labeled_frames: Set of frame filenames that have labels in the CSV.

    Returns:
        Dict with keys:
            first_labeled (bool)
            last_labeled  (bool)
            anchor        (str | None)  — the anchor filename, preferring first
    """
    if not frame_names:
        return {"first_labeled": False, "last_labeled": False, "anchor": None}

    first, last = frame_names[0], frame_names[-1]
    first_ok = first in labeled_frames
    last_ok  = last  in labeled_frames

    anchor: Optional[str] = None
    if first_ok:
        anchor = first
    elif last_ok:
        anchor = last

    return {"first_labeled": first_ok, "last_labeled": last_ok, "anchor": anchor}


# ══════════════════════════════════════════════════════════════════════════════
# DLC label I/O
# ══════════════════════════════════════════════════════════════════════════════

def load_dlc_labels(csv_path: Path | str) -> pd.DataFrame:
    """
    Load a DLC CollectedData_<scorer>.csv into a MultiIndex DataFrame.

    The CSV has three header rows (scorer / bodyparts / coords) and three
    index columns ('' / video_stem / frame_name).  pandas read_csv with
    header=[0,1,2] and index_col=[0,1,2] reconstructs the MultiIndex.
    """
    df = pd.read_csv(
        csv_path,
        header=[0, 1, 2],
        index_col=[0, 1, 2],
    )
    # Convert data columns to float; errors='coerce' turns non-numeric → NaN.
    # (The three index columns are strings — they are unaffected by this call.)
    return df.apply(pd.to_numeric, errors="coerce")


def get_labeled_frame_names(df: pd.DataFrame) -> set[str]:
    """
    Return the set of frame names (level-2 of the index) that have at least
    one non-NaN coordinate in the DataFrame.
    """
    labeled = set()
    for idx_tuple in df.index:
        frame_name = idx_tuple[2]
        row = df.loc[idx_tuple]
        if not row.isna().all():
            labeled.add(frame_name)
    return labeled


# ══════════════════════════════════════════════════════════════════════════════
# Coordinate translation  (the "hiccup")
# ══════════════════════════════════════════════════════════════════════════════

def dlc_to_tapnet_points(
    df: pd.DataFrame,
    anchor_frame: str,
) -> tuple[np.ndarray, list[str]]:
    """
    Extract labeled coordinates from an anchor frame and format them as
    TAPNet query_points.

    DLC storage order: (x, y)  [x = column, y = row in image space]
    TAPNet query format:  (t, y, x)  where t = index within the clip

    Sub-pixel precision is preserved; NaN-labeled body parts are excluded
    so TAPNet is not given degenerate query points.

    Args:
        df:           DLC MultiIndex DataFrame (scorer × bodypart × coord).
        anchor_frame: Frame filename that serves as the query anchor (t=0
                      in the TAPNet clip, regardless of its position in df).

    Returns:
        query_points: np.ndarray, shape (N_valid, 3), dtype float64
                      Each row = (t=0, y, x).
        bodyparts:    list[str] of body-part names in the same order as rows.

    Raises:
        KeyError:  anchor_frame not found in df index.
        ValueError: no valid (non-NaN) coordinates in the anchor frame.
    """
    # Locate the anchor row ─────────────────────────────────────────────────
    # df.index is ('', video_stem, frame_name); we match on level-2.
    matching = [idx for idx in df.index if idx[2] == anchor_frame]
    if not matching:
        raise KeyError(
            f"Anchor frame '{anchor_frame}' not found in DataFrame index.\n"
            f"Available frames (first 5): {[i[2] for i in df.index[:5]]}"
        )
    anchor_idx = matching[0]
    anchor_row = df.loc[anchor_idx]  # Series: columns = (scorer, bp, coord)

    # Determine scorer from column MultiIndex ───────────────────────────────
    scorer = anchor_row.index.get_level_values(0).unique()[0]
    bodyparts_all: list[str] = list(
        anchor_row.index.get_level_values(1).unique()
    )

    query_rows: list[list[float]] = []
    valid_bps:  list[str]         = []

    for bp in bodyparts_all:
        try:
            x = float(anchor_row[(scorer, bp, "x")])
            y = float(anchor_row[(scorer, bp, "y")])
        except KeyError:
            continue
        if np.isnan(x) or np.isnan(y):
            continue
        # TAPNet convention: (t, y, x)
        query_rows.append([0.0, y, x])
        valid_bps.append(bp)

    if not query_rows:
        raise ValueError(
            f"Anchor frame '{anchor_frame}' has no valid (non-NaN) coordinates."
        )

    return np.array(query_rows, dtype=np.float64), valid_bps


def tapnet_to_dlc_labels(
    tracks: np.ndarray,
    visibilities: np.ndarray,
    bodyparts: list[str],
    frame_names: list[str],
    scorer: str,
    video_stem: str,
) -> pd.DataFrame:
    """
    Convert TAPNet output tracks into a DLC MultiIndex DataFrame.

    TAPNet output axis convention:
        tracks[t, n, :] = (x, y)  in pixel coordinates
        visibilities[t, n]         = bool  (True → visible / confident)

    Invisible points are stored as NaN (matching DLC's convention for
    un-labeled body parts).

    Sub-pixel float32 precision is preserved throughout.

    Args:
        tracks:       np.ndarray shape (T, N, 2), float32, (x, y).
        visibilities: np.ndarray shape (T, N), bool.
        bodyparts:    list[str], length N.
        frame_names:  list[str], length T, ordered to match tracks dim-0.
        scorer:       DLC scorer string (e.g. "Ali").
        video_stem:   labeled-data subfolder name (e.g. "MAP2_20250715_120050_0").

    Returns:
        DLC MultiIndex DataFrame:
            Columns: MultiIndex (scorer, bodypart, coord)
            Index:   MultiIndex ('labeled-data', video_stem, frame_name)
    """
    T, N, _ = tracks.shape
    assert len(bodyparts)   == N, f"bodyparts length {len(bodyparts)} ≠ N={N}"
    assert len(frame_names) == T, f"frame_names length {len(frame_names)} ≠ T={T}"

    # Build column MultiIndex ────────────────────────────────────────────────
    col_tuples = []
    for bp in bodyparts:
        col_tuples += [(scorer, bp, "x"), (scorer, bp, "y")]
    cols = pd.MultiIndex.from_tuples(
        col_tuples, names=["scorer", "bodyparts", "coords"]
    )

    # Build row index ────────────────────────────────────────────────────────
    idx_tuples = [("labeled-data", video_stem, fn) for fn in frame_names]
    idx = pd.MultiIndex.from_tuples(idx_tuples, names=["", "", ""])

    # Populate data array ────────────────────────────────────────────────────
    data = np.empty((T, N * 2), dtype=np.float64)
    data[:] = np.nan  # default → NaN for invisible points

    for t in range(T):
        for n in range(N):
            if visibilities[t, n]:
                x = float(tracks[t, n, 0])
                y = float(tracks[t, n, 1])
                data[t, n * 2]     = x
                data[t, n * 2 + 1] = y

    return pd.DataFrame(data, index=idx, columns=cols)


# ══════════════════════════════════════════════════════════════════════════════
# TAPNet inference subprocess
# (runs in isolated child process to free VRAM on exit — Constraint #2)
# ══════════════════════════════════════════════════════════════════════════════

def _tapnet_subprocess_worker(
    frame_paths_str: list[str],
    query_points_path: str,    # np.save path for (N,3) float64
    output_tracks_path: str,   # np.save path for (T,N,2) float32
    output_vis_path: str,      # np.save path for (T,N) bool
    checkpoint_path: str,
    gpu_index: int,
    log_path: str,
) -> None:
    """
    Isolated subprocess that:
      1. Sets CUDA_VISIBLE_DEVICES to the RTX 5090 (gpu_index=0).
      2. Imports JAX and the TAPNet model (lazy to keep worker import clean).
      3. Loads frames and runs TAPIR inference.
      4. Saves tracks + visibilities to disk.
      5. Exits — releasing all GPU memory.

    All stdout/stderr is redirected to log_path.
    """
    import os as _os, sys as _sys, signal as _sig

    # Isolate GPU immediately ─────────────────────────────────────────────────
    _os.setpgrp()
    _os.environ["CUDA_DEVICE_ORDER"]    = "PCI_BUS_ID"
    _os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_index)

    def _sigterm(_n, _f):
        raise SystemExit(0)
    _sig.signal(_sig.SIGTERM, _sigterm)

    with open(log_path, "a", buffering=1) as _log:
        _sys.stdout = _log
        _sys.stderr = _log
        try:
            _log.write(f"[tapnet] GPU index:       {gpu_index}\n")
            _log.write(f"[tapnet] Checkpoint:      {checkpoint_path}\n")
            _log.write(f"[tapnet] Frames:          {len(frame_paths_str)}\n")
            _log.flush()

            # ── Lazy imports (JAX, OpenCV, TAPNet) ──────────────────────────
            import cv2
            import numpy as _np

            try:
                import jax
                import jax.numpy as jnp
                _log.write(f"[tapnet] JAX devices: {jax.devices()}\n")
            except ImportError as exc:
                raise RuntimeError(
                    f"JAX is not installed. Install via:\n"
                    f"  pip install 'jax[cuda12_pip]' "
                    f"-f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html\n"
                    f"Original error: {exc}"
                )

            # tapnet/__init__.py imports tensorflow_datasets (only needed for
            # evaluation benchmarks, not inference). Stub it out so we can
            # import tapnet.models.tapir_model without that dependency.
            import types as _types
            for _stub in [
                "tensorflow_datasets",
                "tensorflow_datasets.core",
                "tensorflow_datasets.public_api",
            ]:
                if _stub not in _sys.modules:
                    _sys.modules[_stub] = _types.ModuleType(_stub)

            try:
                from tapnet.models import tapir_model
            except ImportError as exc:
                raise RuntimeError(
                    f"tapnet package not found. Install via:\n"
                    f"  pip install git+https://github.com/google-deepmind/tapnet.git\n"
                    f"Original error: {exc}"
                )

            # ── Load checkpoint ──────────────────────────────────────────────
            _log.write("[tapnet] Loading checkpoint…\n"); _log.flush()
            ckpt   = _np.load(checkpoint_path, allow_pickle=True).item()
            params = ckpt["params"]
            state  = ckpt.get("state", {})

            # Haiku requires the module to be instantiated inside hk.transform_with_state
            import haiku as hk
            import functools

            def _tapir_forward(frames, query_points):
                model = tapir_model.TAPIR(
                    bilinear_interp_with_depthwise_conv=False,
                    pyramid_level=0,
                )
                # Actual signature: (self, video, is_training, query_points, query_chunk_size, ...)
                # Returns a single dict (not a tuple)
                outputs = model(frames, False, query_points, query_chunk_size=64)
                return outputs

            model_fn = hk.transform_with_state(_tapir_forward)

            rng = jax.random.PRNGKey(42)

            @jax.jit
            def _inference(frames_jax, query_pts_jax):
                # model_fn.apply returns (fn_output, new_state); fn_output is the dict
                outputs, _ = model_fn.apply(
                    params, state, rng,
                    frames_jax, query_pts_jax,
                )
                return outputs

            # ── Load frames ──────────────────────────────────────────────────
            _log.write("[tapnet] Loading frames…\n"); _log.flush()
            frames_rgb: list[_np.ndarray] = []
            for fp in frame_paths_str:
                img_bgr = cv2.imread(fp)
                if img_bgr is None:
                    raise FileNotFoundError(f"Cannot read frame: {fp}")
                frames_rgb.append(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))

            H, W = frames_rgb[0].shape[:2]
            T    = len(frames_rgb)

            # Stack: (1, T, H, W, 3) float32 in [0, 255]
            frames_arr = _np.stack(frames_rgb, axis=0).astype(_np.float32)
            frames_arr = frames_arr[_np.newaxis]  # add batch dim

            # ── Load query points ────────────────────────────────────────────
            query_pts = _np.load(query_points_path)  # (N, 3): (t=0, y, x) pixels
            N = query_pts.shape[0]
            _log.write(f"[tapnet] Tracking {N} points over {T} frames ({W}×{H}).\n")
            _log.flush()

            # TAPIR expects (1, N, 3): (t, y, x) in pixel coords
            query_pts_batched = query_pts[_np.newaxis].astype(_np.float32)

            frames_jax    = jnp.array(frames_arr)
            query_pts_jax = jnp.array(query_pts_batched)

            # ── Run inference ────────────────────────────────────────────────
            _log.write("[tapnet] Running TAPIR inference…\n"); _log.flush()
            outputs = _inference(frames_jax, query_pts_jax)

            # outputs["tracks"]:    (1, N, T, 2) — (x, y) pixel coords
            # outputs["occlusion"]: (1, N, T)    — logit; negative = visible
            tracks_raw = _np.array(outputs["tracks"])    # (1, N, T, 2)
            occlusion  = _np.array(outputs["occlusion"]) # (1, N, T)

            # Reshape to (T, N, 2) and (T, N)
            tracks_out = tracks_raw[0].transpose(1, 0, 2)  # (T, N, 2)
            vis_out    = (occlusion[0] < 0.0).T            # (T, N) bool

            # ── Save results ─────────────────────────────────────────────────
            _np.save(output_tracks_path, tracks_out.astype(_np.float32))
            _np.save(output_vis_path,    vis_out)
            _log.write(f"[tapnet] Done. Saved tracks → {output_tracks_path}\n")
            _log.flush()

        except Exception as exc:
            import traceback as _tb
            _log.write(f"[tapnet ERROR] {_tb.format_exc()}\n")
            _log.flush()
            raise
        finally:
            # Explicit cleanup before process exit (belt-and-suspenders for JAX)
            try:
                import jax
                # Clear compilation cache
                jax.clear_caches()
            except Exception:
                pass
            try:
                gc.collect()
            except Exception:
                pass


def run_tapnet_inference(
    frame_paths: list[Path],
    query_points: np.ndarray,
    checkpoint_path: str,
    gpu_index: int = _TAPNET_GPU_INDEX,
    timeout: int = 600,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Run TAPNet/TAPIR inference in an isolated subprocess on the RTX 5090.

    The subprocess exits after saving results to temp files, releasing all
    GPU memory (Constraint #2).

    Args:
        frame_paths:     Ordered list of frame Paths (T frames).
        query_points:    np.ndarray (N, 3) in (t=0, y, x) pixel coords.
        checkpoint_path: Path to the TAPIR .npy checkpoint.
        gpu_index:       CUDA device index. Default 0 = RTX 5090.
        timeout:         Max seconds to wait for the subprocess.

    Returns:
        tracks:       np.ndarray (T, N, 2) float32 — (x, y) per frame per point.
        visibilities: np.ndarray (T, N) bool.
    """
    with tempfile.TemporaryDirectory(prefix="tapnet_run_") as tmpdir:
        qp_path    = os.path.join(tmpdir, "query_points.npy")
        trk_path   = os.path.join(tmpdir, "tracks.npy")
        vis_path   = os.path.join(tmpdir, "visibilities.npy")
        log_path   = os.path.join(tmpdir, "tapnet.log")

        np.save(qp_path, query_points)

        ctx  = _mp.get_context("spawn")
        proc = ctx.Process(
            target=_tapnet_subprocess_worker,
            args=(
                [str(p) for p in frame_paths],
                qp_path,
                trk_path,
                vis_path,
                checkpoint_path,
                gpu_index,
                log_path,
            ),
            daemon=False,
        )
        proc.start()
        proc.join(timeout=timeout)

        # Hard-kill if still alive after timeout
        if proc.is_alive():
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            proc.join(timeout=5)
            raise TimeoutError(
                f"TAPNet subprocess exceeded {timeout}s timeout."
            )

        try:
            with open(log_path) as lf:
                log_text = lf.read()
        except OSError:
            log_text = ""

        if proc.exitcode != 0:
            raise RuntimeError(
                f"TAPNet subprocess failed (exit {proc.exitcode}).\n\n{log_text}"
            )

        tracks       = np.load(trk_path)
        visibilities = np.load(vis_path)

    return tracks, visibilities


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point: propagate labels across a consecutive sequence
# ══════════════════════════════════════════════════════════════════════════════

def _read_config_scorer_bodyparts(config_path: Path) -> tuple[str, list[str]]:
    """Parse scorer and bodyparts from DLC config.yaml (with regex fallback)."""
    import yaml
    text = config_path.read_text()
    try:
        cfg = yaml.safe_load(text) or {}
    except Exception:
        cfg = {}
    # Regex fallback for malformed YAML
    if "scorer" not in cfg:
        m = re.search(r"^scorer\s*:\s*(.+)$", text, re.MULTILINE)
        if m:
            cfg["scorer"] = m.group(1).strip().strip("\"'")
    if "bodyparts" not in cfg:
        m = re.search(
            r"^bodyparts\s*:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)", text, re.MULTILINE
        )
        if m:
            cfg["bodyparts"] = [
                item.strip().strip("\"'")
                for item in re.findall(r"^[ \t]*-[ \t]*(.+)$", m.group(1), re.MULTILINE)
            ]
    scorer    = cfg.get("scorer", "User")
    bodyparts = list(cfg.get("bodyparts", []))
    return scorer, bodyparts


def propagate_labels(
    labeled_data_path: str | Path,
    config_path: str | Path,
    tapnet_checkpoint_path: str,
    anchor: str = "auto",
    gpu_index: int = _TAPNET_GPU_INDEX,
    overwrite_existing: bool = False,
) -> dict:
    """
    Propagate manually placed labels across all consecutive frame sequences
    in a DLC labeled-data folder using TAPNet point tracking.

    Workflow
    --------
    1. Scan *labeled_data_path* for PNG frames and group into consecutive runs.
    2. For each run, load CollectedData_<scorer>.csv and determine which
       endpoint (first or last frame) is labeled.
    3. Run TAPNet inference to propagate those labels to all other frames.
    4. Write the propagated labels back to CollectedData_<scorer>.csv,
       keeping any existing human labels intact (they are never overwritten).

    Args:
        labeled_data_path:      Path to the labeled-data/<video_stem> folder.
        config_path:            Path to the DLC project config.yaml.
        tapnet_checkpoint_path: Path to the TAPIR .npy checkpoint file.
        anchor:                 "auto" (prefer first, else last), "first", or "last".
        gpu_index:              CUDA device index. Must be 0 (RTX 5090).
        overwrite_existing:     If False (default), skip frames already labeled.

    Returns:
        Dict with keys:
            status          ("complete" | "skipped" | "no_anchor")
            sequences_found (int)
            frames_labeled  (int)
            log             (str)
    """
    labeled_data_path = Path(labeled_data_path)
    config_path       = Path(config_path)

    log_lines: list[str] = []

    def _log(msg: str):
        log_lines.append(msg)

    scorer, _ = _read_config_scorer_bodyparts(config_path)
    video_stem = labeled_data_path.name

    # ── Locate CollectedData CSV ─────────────────────────────────────────────
    csv_candidates = sorted(labeled_data_path.glob("CollectedData_*.csv"))
    if not csv_candidates:
        return {
            "status": "skipped",
            "sequences_found": 0,
            "frames_labeled": 0,
            "log": f"No CollectedData_*.csv found in {labeled_data_path}",
        }
    csv_path = csv_candidates[0]
    df = load_dlc_labels(csv_path)
    labeled_frames = get_labeled_frame_names(df)
    _log(f"Loaded {len(df)} rows; {len(labeled_frames)} frames with labels.")

    # ── Find all PNG frames & consecutive sequences ──────────────────────────
    all_pngs = sorted(labeled_data_path.glob("*.png"))
    frame_names = [p.name for p in all_pngs]
    sequences   = find_consecutive_sequences(frame_names)
    _log(f"Found {len(sequences)} consecutive sequence(s) across {len(frame_names)} frames.")

    if not sequences:
        return {
            "status": "skipped",
            "sequences_found": 0,
            "frames_labeled": 0,
            "log": "\n".join(log_lines),
        }

    total_propagated = 0

    for seq_idx, seq_frames in enumerate(sequences):
        _log(f"\n── Sequence {seq_idx+1}/{len(sequences)}: {len(seq_frames)} frames ──")

        # Determine anchor frame for this sequence
        anchor_info = check_anchor_frames(seq_frames, labeled_frames)

        if anchor == "first" and anchor_info["first_labeled"]:
            anchor_frame = seq_frames[0]
        elif anchor == "last" and anchor_info["last_labeled"]:
            anchor_frame = seq_frames[-1]
        elif anchor == "auto":
            anchor_frame = anchor_info["anchor"]
        else:
            anchor_frame = anchor_info["anchor"]

        if anchor_frame is None:
            _log(f"  No anchor frame found — skipping sequence.")
            continue

        # Frames to propagate to (all except the anchor if already labeled)
        target_frames = seq_frames[:]
        if not overwrite_existing:
            target_frames = [f for f in seq_frames if f not in labeled_frames or f == anchor_frame]

        if len(target_frames) <= 1:
            _log(f"  No unlabeled frames in sequence — skipping.")
            continue

        _log(f"  Anchor: {anchor_frame} → propagating to {len(target_frames)} frames.")

        try:
            # Extract query points from anchor
            query_pts, tracked_bps = dlc_to_tapnet_points(df, anchor_frame=anchor_frame)
            _log(f"  Tracking {len(tracked_bps)} body parts: {tracked_bps}")

            # Build ordered frame paths for this clip (anchor must be t=0)
            # Re-order so anchor frame is first for TAPNet, then re-map outputs.
            anchor_idx_in_seq = seq_frames.index(anchor_frame)

            # Build the clip: from anchor outward (forwards if first, backwards if last)
            if anchor_idx_in_seq == 0 or anchor == "first":
                clip_frames = seq_frames[anchor_idx_in_seq:]
            else:
                # Anchor is last — reverse so anchor is t=0 for TAPNet
                clip_frames = list(reversed(seq_frames[:anchor_idx_in_seq + 1]))

            clip_paths = [labeled_data_path / fn for fn in clip_frames]

            # Run TAPNet inference on this clip
            tracks, visibilities = run_tapnet_inference(
                frame_paths=clip_paths,
                query_points=query_pts,
                checkpoint_path=tapnet_checkpoint_path,
                gpu_index=gpu_index,
            )

            # Convert tracks back to DLC format
            result_df = tapnet_to_dlc_labels(
                tracks, visibilities, tracked_bps, clip_frames, scorer, video_stem
            )

            # Merge into existing DataFrame:
            # Human labels are NEVER overwritten. Only fill NaN rows.
            for fn in clip_frames:
                if fn in labeled_frames and not overwrite_existing:
                    continue  # skip already-labeled frames
                src_idx = ("labeled-data", video_stem, fn)
                if src_idx not in result_df.index:
                    continue
                # Write propagated coords for each tracked body part
                for bp in tracked_bps:
                    x_val = result_df.loc[src_idx, (scorer, bp, "x")]
                    y_val = result_df.loc[src_idx, (scorer, bp, "y")]
                    if not pd.isna(x_val) and not pd.isna(y_val):
                        # Ensure this row exists in df
                        full_idx = ("labeled-data", video_stem, fn)
                        if full_idx not in df.index:
                            # Add a new all-NaN row
                            new_row = pd.Series(np.nan, index=df.columns)
                            df.loc[full_idx] = new_row
                        df.loc[full_idx, (scorer, bp, "x")] = x_val
                        df.loc[full_idx, (scorer, bp, "y")] = y_val
                        total_propagated += 1

            _log(f"  Propagated labels for {len(clip_frames)} frames.")

        except Exception as exc:
            import traceback
            _log(f"  ERROR processing sequence: {traceback.format_exc()}")
            continue

    if total_propagated > 0:
        # Sort index and write back to CSV
        df.sort_index(inplace=True)
        df.to_csv(csv_path)
        _log(f"\nWrote updated CSV: {csv_path}")

    return {
        "status": "complete",
        "sequences_found": len(sequences),
        "frames_labeled": total_propagated,
        "log": "\n".join(log_lines),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Confirmed-anchor sidecar  (_tapnet_confirmed.json)
# ══════════════════════════════════════════════════════════════════════════════

_CONFIRMED_FILE = "_tapnet_confirmed.json"
_TAPNET_FRAMES_FILE = "_tapnet_frames.json"


def load_confirmed_anchors(folder: Path | str) -> set[str]:
    """Return the set of frame names confirmed as TAPNet anchors."""
    p = Path(folder) / _CONFIRMED_FILE
    if not p.is_file():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        return set()


def save_confirmed_anchors(folder: Path | str, frames: set[str]) -> None:
    p = Path(folder) / _CONFIRMED_FILE
    p.write_text(json.dumps(sorted(frames), indent=2))


def toggle_confirmed_anchor(folder: Path | str, frame_name: str) -> dict:
    """Add frame_name to confirmed set; return updated state."""
    confirmed = load_confirmed_anchors(folder)
    if frame_name in confirmed:
        confirmed.discard(frame_name)
        added = False
    else:
        confirmed.add(frame_name)
        added = True
    save_confirmed_anchors(folder, confirmed)
    return {"frame": frame_name, "confirmed": added, "total": len(confirmed)}


def load_tapnet_frames(folder: Path | str) -> set[str]:
    """Return set of frames that were labeled by TAPNet (not human)."""
    p = Path(folder) / _TAPNET_FRAMES_FILE
    if not p.is_file():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        return set()


def save_tapnet_frames(folder: Path | str, frames: set[str]) -> None:
    p = Path(folder) / _TAPNET_FRAMES_FILE
    p.write_text(json.dumps(sorted(frames), indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# Multi-anchor propagation
# ══════════════════════════════════════════════════════════════════════════════

def _run_segment(
    df: pd.DataFrame,
    labeled_data_path: Path,
    segment_frames: list[str],   # [anchor, f1, f2, ..., fn]  anchor is t=0
    anchor_frame: str,
    scorer: str,
    video_stem: str,
    tapnet_checkpoint_path: str,
    gpu_index: int,
    log_lines: list[str],
) -> tuple[int, set[str]]:
    """
    Run TAPNet on one segment (anchor → end).
    Returns (n_labeled, set_of_written_frame_names).
    Anchor frame labels are never overwritten.
    """
    def _log(m): log_lines.append(m)

    try:
        query_pts, tracked_bps = dlc_to_tapnet_points(df, anchor_frame=anchor_frame)
    except (KeyError, ValueError) as exc:
        _log(f"  Skipping segment from {anchor_frame}: {exc}")
        return 0, set()

    clip_paths = [labeled_data_path / fn for fn in segment_frames]
    try:
        tracks, visibilities = run_tapnet_inference(
            frame_paths=clip_paths,
            query_points=query_pts,
            checkpoint_path=tapnet_checkpoint_path,
            gpu_index=gpu_index,
        )
    except Exception as exc:
        _log(f"  TAPNet error on segment from {anchor_frame}: {exc}")
        return 0, set()

    result_df = tapnet_to_dlc_labels(
        tracks, visibilities, tracked_bps, segment_frames, scorer, video_stem
    )

    written: set[str] = set()
    n = 0
    for fn in segment_frames:
        if fn == anchor_frame:
            continue  # never overwrite the anchor
        src_idx = ("labeled-data", video_stem, fn)
        if src_idx not in result_df.index:
            continue
        full_idx = ("labeled-data", video_stem, fn)
        if full_idx not in df.index:
            df.loc[full_idx] = pd.Series(np.nan, index=df.columns)
        for bp in tracked_bps:
            x_val = result_df.loc[src_idx, (scorer, bp, "x")]
            y_val = result_df.loc[src_idx, (scorer, bp, "y")]
            if not pd.isna(x_val) and not pd.isna(y_val):
                df.loc[full_idx, (scorer, bp, "x")] = x_val
                df.loc[full_idx, (scorer, bp, "y")] = y_val
                n += 1
        written.add(fn)
    return n, written


def propagate_labels_multi_anchor(
    labeled_data_path: str | Path,
    config_path: str | Path,
    tapnet_checkpoint_path: str,
    anchor_frames: list[str],
    gpu_index: int = _TAPNET_GPU_INDEX,
) -> dict:
    """
    Multi-anchor iterative refinement.

    For a sorted list of confirmed anchor frames [A0, A1, A2, ...], runs TAPNet
    independently for each segment:
        A0 → A1  (frames strictly between A0 and A1 get new labels)
        A1 → A2
        ...
        An → end (remaining frames after last anchor)

    Anchor frames themselves are never overwritten.

    Returns same dict shape as propagate_labels().
    """
    labeled_data_path = Path(labeled_data_path)
    config_path       = Path(config_path)
    log_lines: list[str] = []
    def _log(m): log_lines.append(m)

    scorer, _ = _read_config_scorer_bodyparts(config_path)
    video_stem = labeled_data_path.name

    csv_candidates = sorted(labeled_data_path.glob("CollectedData_*.csv"))
    if not csv_candidates:
        return {"status": "skipped", "sequences_found": 0, "frames_labeled": 0,
                "log": f"No CollectedData_*.csv found in {labeled_data_path}"}

    csv_path = csv_candidates[0]
    df       = load_dlc_labels(csv_path)

    all_pngs     = sorted(labeled_data_path.glob("*.png"), key=lambda p: _parse_extraction_index(p.name) or 0)
    all_frames   = [p.name for p in all_pngs]
    sequences    = find_consecutive_sequences([p.name for p in all_pngs])

    if not sequences:
        return {"status": "skipped", "sequences_found": 0, "frames_labeled": 0,
                "log": "No consecutive sequences found."}

    # Filter + sort anchors by their position in the full frame list
    frame_order  = {fn: i for i, fn in enumerate(all_frames)}
    valid_anchors = sorted(
        [fn for fn in anchor_frames if fn in frame_order],
        key=lambda fn: frame_order[fn],
    )

    if not valid_anchors:
        return {"status": "no_anchor", "sequences_found": len(sequences),
                "frames_labeled": 0, "log": "No valid anchor frames provided."}

    _log(f"Multi-anchor mode: {len(valid_anchors)} anchors across {len(all_frames)} frames.")

    total_written = 0
    all_tapnet_frames: set[str] = set()

    # Process each consecutive sequence separately
    for seq in sequences:
        seq_set = set(seq)
        seq_anchors = [fn for fn in valid_anchors if fn in seq_set]
        if not seq_anchors:
            _log(f"  Sequence {seq[0]}…{seq[-1]}: no anchors — skipping.")
            continue

        _log(f"\n── Sequence {seq[0]} → {seq[-1]} ({len(seq)} frames, {len(seq_anchors)} anchors) ──")

        # Build segments: [A0..A1], [A1..A2], ..., [An..end]
        seq_idx_map = {fn: i for i, fn in enumerate(seq)}
        boundaries  = [seq_idx_map[a] for a in seq_anchors]

        segments = []
        for k, start_idx in enumerate(boundaries):
            end_idx = boundaries[k + 1] if k + 1 < len(boundaries) else len(seq) - 1
            segment = seq[start_idx : end_idx + 1]  # inclusive on both ends
            segments.append((seq_anchors[k], segment))

        for anchor_fn, segment in segments:
            _log(f"  Segment from {anchor_fn}: {len(segment)} frames")
            n, written = _run_segment(
                df, labeled_data_path, segment, anchor_fn,
                scorer, video_stem, tapnet_checkpoint_path, gpu_index, log_lines,
            )
            total_written  += n
            all_tapnet_frames |= written
            _log(f"    → {len(written)} frames labeled")

    if total_written > 0:
        df.sort_index(inplace=True)
        df.to_csv(csv_path)
        _log(f"\nWrote updated CSV: {csv_path}")

    # Persist which frames are TAPNet-generated (merge with existing record)
    existing_tapnet = load_tapnet_frames(labeled_data_path)
    save_tapnet_frames(labeled_data_path, existing_tapnet | all_tapnet_frames)

    return {
        "status":          "complete",
        "sequences_found": len(sequences),
        "frames_labeled":  total_written,
        "log":             "\n".join(log_lines),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def _download_checkpoint(dest: str) -> None:
    """Download the default TAPIR checkpoint if it doesn't exist."""
    if os.path.exists(dest):
        return
    print(f"Downloading TAPIR checkpoint → {dest} …")
    import urllib.request
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    urllib.request.urlretrieve(TAPNET_CHECKPOINT_URL, dest)
    print("Download complete.")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Propagate DLC labels across consecutive frames using TAPNet."
    )
    parser.add_argument("config",  help="Path to DLC config.yaml")
    parser.add_argument("frames",  help="Path to labeled-data/<video_stem> folder")
    parser.add_argument(
        "--checkpoint",
        default=os.path.expanduser("~/.tapnet/tapir_checkpoint_panning.npy"),
        help="Path to TAPIR .npy checkpoint (will auto-download if absent).",
    )
    parser.add_argument(
        "--anchor",
        choices=["auto", "first", "last"],
        default="auto",
        help="Which endpoint to use as the tracking anchor.",
    )
    parser.add_argument(
        "--gpu", type=int, default=_TAPNET_GPU_INDEX,
        help=f"CUDA device index. Default {_TAPNET_GPU_INDEX} (RTX 5090).",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing labels (default: preserve human labels).",
    )
    args = parser.parse_args(argv)

    _download_checkpoint(args.checkpoint)

    result = propagate_labels(
        labeled_data_path=args.frames,
        config_path=args.config,
        tapnet_checkpoint_path=args.checkpoint,
        anchor=args.anchor,
        gpu_index=args.gpu,
        overwrite_existing=args.overwrite,
    )

    print(result["log"])
    print(
        f"\nStatus: {result['status']} | "
        f"Sequences: {result['sequences_found']} | "
        f"Frames labeled: {result['frames_labeled']}"
    )
    return 0 if result["status"] in ("complete", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
