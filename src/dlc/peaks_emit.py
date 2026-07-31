"""Emit DeepLabCut candidate peaks for a video as a sparse .npz sidecar.

Stock single-animal inference argmaxes the heatmap away. Keeping the top-K peaks
lets the epipolar engine ask whether the IMAGE supports a part being on the line,
rather than only whether the marker is consistent with it.

torch, cv2, yaml and deeplabcut are imported INSIDE emit_peaks_for_video so that
the pure helpers above stay importable on a host with none of them.

extract_peaks and heatmap_to_image are copied verbatim from dlc-3D's
dlc_3d_bp/peaks.py. The two repositories build separate containers, so a shared
import would need a cross-repo bind mount. tests/test_peaks_emit_parity.py in
dlc-3D asserts the copies stay identical - if you change one, change both.
"""
import json
from pathlib import Path

import numpy as np
from scipy import ndimage


# --- verbatim copies from dlc-3D/src/dlc_3d_bp/peaks.py ---------------------

def extract_peaks(heatmap, k: int = 5, min_distance: int = 3):
    """Top-k local maxima of one bodypart's heatmap.

    Returns (xy, scores): xy is (k, 2) float32 in heatmap-cell coordinates as
    (x=column, y=row), NaN-padded; scores is (k,) float32, zero-padded. Both are
    ordered by descending score.

    Non-maximum suppression is not optional: a plain top-k returns the peak cell
    and its neighbours, so k "candidates" would be one detection counted k times.
    """
    hm = np.asarray(heatmap, dtype=np.float32)
    xy = np.full((k, 2), np.nan, dtype=np.float32)
    scores = np.zeros(k, dtype=np.float32)
    if hm.ndim != 2 or hm.size == 0:
        return xy, scores

    # A cell is a candidate when it equals the max of its neighbourhood and is
    # strictly positive, so a flat or empty heatmap yields nothing.
    size = 2 * int(min_distance) + 1
    local_max = ndimage.maximum_filter(hm, size=size, mode="nearest")
    cand = np.argwhere((hm == local_max) & (hm > 0))
    if not len(cand):
        return xy, scores

    vals = hm[cand[:, 0], cand[:, 1]]
    order = np.argsort(-vals)
    cand, vals = cand[order], vals[order]

    # Greedy suppression: keep a candidate only if it clears min_distance from
    # every candidate already kept. maximum_filter alone can still return two
    # cells of one plateau.
    kept_rc = []
    for (r, c), v in zip(cand, vals):
        if any((r - kr) ** 2 + (c - kc) ** 2 < min_distance ** 2 for kr, kc in kept_rc):
            continue
        kept_rc.append((r, c))
        idx = len(kept_rc) - 1
        xy[idx] = (float(c), float(r))   # x = column, y = row
        scores[idx] = float(v)
        if len(kept_rc) == k:
            break
    return xy, scores


def heatmap_to_image(xy_cells, stride, pad_xy=(0, 0), scale_xy=(1.0, 1.0)):
    """Map heatmap-cell coordinates to original video pixels.

    A cell's centre is at (cell + 0.5) * stride in network-input space. The
    network input was produced by resizing the original frame by scale_xy and
    then padding by pad_xy, so both are undone in that order.

    NaN padding is preserved: a missing peak stays missing.
    """
    xy = np.asarray(xy_cells, dtype=np.float64).reshape(-1, 2)
    out = np.full_like(xy, np.nan)
    ok = np.isfinite(xy).all(axis=1)
    if ok.any():
        net = (xy[ok] + 0.5) * float(stride)
        net[:, 0] -= float(pad_xy[0])
        net[:, 1] -= float(pad_xy[1])
        net[:, 0] /= float(scale_xy[0])
        net[:, 1] /= float(scale_xy[1])
        out[ok] = net
    return out.astype(np.float32)

# ---------------------------------------------------------------------------

# PIPELINE VERIFIED EMPIRICALLY against the existing pose h5 (0.344 px median,
# 0.975 px p95 on confident markers). Every constant below was measured, not
# assumed - an earlier draft was wrong on all five and produced 186-427 px error.
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
_STRIDE = 2.0

# Reading forward costs one decode per skipped frame; seeking costs a jump back
# to the nearest keyframe plus a re-decode forward to the target. Typical
# keyframe intervals in these recordings are a few tens of frames, so skipping
# wins for small gaps and seeking wins for large ones. 32 sits near that
# crossover and, crucially, makes a CONTIGUOUS range (gap 0) pure sequential
# reads -- the case "Start analysis for range" always produces.
_SEEK_THRESHOLD = 32


def _should_seek(decoder_pos, target: int, threshold: int = _SEEK_THRESHOLD) -> bool:
    """Whether to seek rather than read forward to reach `target`.

    `decoder_pos` is the frame index the next `read()` would return, or None
    when the position is unknown (start of file, or after a failed read).
    Backward moves always seek: a decoder cannot read backwards.
    """
    if decoder_pos is None or target < decoder_pos:
        return True
    return (target - decoder_pos) > int(threshold)


def _chunks(seq, n: int):
    """Yield successive n-sized lists from `seq`, never dropping an element."""
    n = max(1, int(n))
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def _peaks_sidecar_path(pose_h5_path) -> Path:
    """Must match dlc_3d_bp.peaks_io.peaks_sidecar_path exactly."""
    p = Path(pose_h5_path)
    return p.with_name(p.stem + "_peaks.npz")


def _read_wanted_frames(cap, frames, cv2):
    """Yield (index_into_frames, BGR frame) for each frame that reads cleanly.

    Seeks only when the gap justifies it (see `_should_seek`); otherwise reads
    and discards forward. An earlier version seeked on EVERY frame, which for a
    contiguous range meant a keyframe re-decode per frame and cost roughly 3x.

    A frame that fails to read is skipped, matching the previous behaviour, and
    the decoder position is marked unknown so the next frame re-seeks rather
    than trusting a position the failure may have invalidated.
    """
    pos = None                      # frame index the next read() would return
    for i, fno in enumerate(frames):
        if _should_seek(pos, fno):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fno)
            pos = fno
        while pos < fno:            # close a small gap by discarding
            ok, _ = cap.read()
            if not ok:
                pos = None
                break
            pos += 1
        if pos is None:
            continue
        ok, frame = cap.read()
        if not ok:
            pos = None
            continue
        pos = fno + 1
        yield i, frame


def emit_peaks_for_video(video_path, h5_path, model_dir, snapshot_name,
                         frames, k=5, min_distance=3, device=None,
                         batch_size=1) -> dict:
    """Run the model over `frames` and write/merge the peak sidecar.

    `frames` is a sorted list of absolute video frame numbers. Returns
    {"sidecar": str, "n_frames": int, "bodyparts": list[str]}.

    The write is atomic-by-replace: the merged array is written to a temporary
    file in the same directory and renamed, so a crash leaves the previous
    sidecar intact rather than a truncated one.

    `batch_size` defaults to 1, which is BIT-IDENTICAL to the pipeline verified
    at 0.344 px against the pose h5 (measured: 0.000e+00 difference in both xy
    and score). Batching perturbs the convolution by ~1.6e-3 because cuDNN
    selects a different algorithm per batch shape. That is harmless on a sharp
    heatmap but reshuffles which local maxima win on a FLAT one -- i.e. exactly
    the low-confidence markers this feature exists to judge. Measured on 200
    contiguous frames: batch 1 = 14.1 fps, batch 4 = 16.5, batch 8 = 15.6,
    against 10.1 for the pre-fix code. Video decoding, not the GPU, is the
    bottleneck, so batching buys ~15% at the cost of fidelity where it matters
    most. Raise it only if you accept that trade.
    """
    import cv2
    import torch
    import yaml
    import deeplabcut.pose_estimation_pytorch as dlcpt

    cfg_path = Path(model_dir) / "train" / "pytorch_config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"pytorch_config.yaml not found at {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text())
    bodyparts = list(cfg["metadata"]["bodyparts"])
    locref_std = (cfg.get("model", {}).get("heads", {}).get("bodypart", {})
                     .get("predictor", {}).get("locref_std", 7.2801))

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    model = dlcpt.models.PoseModel.build(cfg["model"])
    state = torch.load(Path(model_dir) / "train" / snapshot_name,
                       map_location=device, weights_only=False)
    model.load_state_dict(state["model"] if "model" in state else state)
    model.to(device).eval()

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    frames = sorted({int(f) for f in frames})
    n, b = len(frames), len(bodyparts)
    XY = np.full((n, b, k, 2), np.nan, np.float32)
    SC = np.zeros((n, b, k), np.float32)
    got = []

    def _preprocess(frame):
        """BGR frame -> normalised HWC float32. UNCHANGED from the verified
        pipeline: native resolution padded to a multiple of 32 (never resized),
        then ImageNet mean/std after /255."""
        h, w = frame.shape[:2]
        H, W = ((h + 31) // 32) * 32, ((w + 31) // 32) * 32
        im = cv2.copyMakeBorder(frame, 0, H - h, 0, W - w,
                                cv2.BORDER_CONSTANT, value=0)
        return (cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32)
                / 255.0 - _MEAN) / _STD

    def _run_batch(items):
        """items: list of (index_into_frames, preprocessed HWC array)."""
        if not items:
            return
        # Build a CONTIGUOUS NCHW tensor. Handing cuDNN a permuted (non-
        # contiguous) view lets it pick a different convolution algorithm, which
        # perturbs the output at ~1e-3 -- harmless on a sharp heatmap, but on a
        # flat one it reshuffles which local maxima win.
        arr = np.ascontiguousarray(
            np.stack([a for _, a in items], axis=0).transpose(0, 3, 1, 2))
        t_in = torch.from_numpy(arr).to(device)
        with torch.no_grad():
            out = model(t_in)
        hm_b = torch.sigmoid(out["bodypart"]["heatmap"]).cpu().numpy()
        lr_b = out["bodypart"]["locref"].cpu().numpy()
        for slot, (i, _) in enumerate(items):
            hm, lr = hm_b[slot], lr_b[slot]
            for j in range(b):
                cells, sc = extract_peaks(hm[j], k=k, min_distance=min_distance)
                # Padding is bottom/right only, so the origin does not shift,
                # and there is no resize: pad_xy=(0,0), scale_xy=(1,1).
                xy = heatmap_to_image(cells, _STRIDE, (0, 0), (1.0, 1.0))
                for p_i in range(k):
                    if not np.isfinite(cells[p_i]).all():
                        continue
                    c_col, c_row = int(cells[p_i, 0]), int(cells[p_i, 1])
                    xy[p_i, 0] += lr[2 * j, c_row, c_col] * locref_std
                    xy[p_i, 1] += lr[2 * j + 1, c_row, c_col] * locref_std
                XY[i, j] = xy
                SC[i, j] = sc
            got.append(i)

    try:
        pending = []
        for i, frame in _read_wanted_frames(cap, frames, cv2):
            rgb = _preprocess(frame)
            # A padded-size change cannot happen within one video, but stacking
            # would fail silently late if it ever did. Flush instead.
            if pending and pending[0][1].shape != rgb.shape:
                _run_batch(pending)
                pending = []
            pending.append((i, rgb))
            if len(pending) >= max(1, int(batch_size)):
                _run_batch(pending)
                pending = []
        _run_batch(pending)
    finally:
        cap.release()

    got.sort()

    rows = np.asarray(got, dtype=int)
    new = {
        "frames": np.asarray([frames[i] for i in rows], np.int32),
        "xy": XY[rows], "score": SC[rows],
        "bodyparts": bodyparts,
        "meta": {"k": int(k), "min_distance": int(min_distance),
                 "snapshot": str(snapshot_name), "stride": _STRIDE,
                 "locref_std": float(locref_std)},
    }

    dst = _peaks_sidecar_path(h5_path)
    merged = new
    if dst.is_file():
        merged = _merge(_read(dst), new)
    _write_atomic(dst, merged)
    return {"sidecar": str(dst), "n_frames": int(len(merged["frames"])),
            "bodyparts": bodyparts}


def _read(path) -> dict:
    with np.load(str(path), allow_pickle=False) as z:
        return {
            "frames": np.asarray(z["frames"], np.int32),
            "xy": np.asarray(z["xy"], np.float32),
            "score": np.asarray(z["score"], np.float32),
            "bodyparts": [str(x) for x in z["bodyparts"]],
            "meta": json.loads(str(z["meta"])),
        }


def _merge(old: dict, new: dict) -> dict:
    if list(old["bodyparts"]) != list(new["bodyparts"]):
        raise ValueError(
            "bodypart mismatch: sidecar has {}, incoming run has {}".format(
                list(old["bodyparts"]), list(new["bodyparts"])))
    if old["xy"].shape[2] != new["xy"].shape[2]:
        raise ValueError("k mismatch: sidecar has k={}, incoming run has k={}"
                         .format(old["xy"].shape[2], new["xy"].shape[2]))
    keep = ~np.isin(old["frames"], new["frames"])
    frames = np.concatenate([old["frames"][keep], new["frames"]])
    order = np.argsort(frames, kind="stable")
    return {
        "frames": frames[order].astype(np.int32),
        "xy": np.concatenate([old["xy"][keep], new["xy"]])[order],
        "score": np.concatenate([old["score"][keep], new["score"]])[order],
        "bodyparts": list(new["bodyparts"]),
        "meta": dict(new["meta"]),
    }


def _write_atomic(dst: Path, d: dict) -> None:
    # np.savez_compressed appends ".npz" to any path that doesn't already end
    # in ".npz". A tmp name like "<dst>.npz.tmp" ends in ".tmp", so numpy would
    # actually write "<dst>.npz.tmp.npz" and the rename below would silently
    # miss it (or, worse, race with a leftover file from a previous crash).
    # Using a tmp name that itself ends in ".npz" makes savez write exactly the
    # path we pass in, so the rename target is correct.
    tmp = dst.with_name(dst.stem + ".tmp.npz")
    np.savez_compressed(
        str(tmp), frames=d["frames"], xy=d["xy"], score=d["score"],
        bodyparts=np.asarray(d["bodyparts"], dtype=np.str_),
        meta=np.asarray(json.dumps(d["meta"]), dtype=np.str_))
    Path(tmp).replace(dst)
