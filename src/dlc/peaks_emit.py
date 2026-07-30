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


def _peaks_sidecar_path(pose_h5_path) -> Path:
    """Must match dlc_3d_bp.peaks_io.peaks_sidecar_path exactly."""
    p = Path(pose_h5_path)
    return p.with_name(p.stem + "_peaks.npz")


def emit_peaks_for_video(video_path, h5_path, model_dir, snapshot_name,
                         frames, k=5, min_distance=3, device=None) -> dict:
    """Run the model over `frames` and write/merge the peak sidecar.

    `frames` is a sorted list of absolute video frame numbers. Returns
    {"sidecar": str, "n_frames": int, "bodyparts": list[str]}.

    The write is atomic-by-replace: the merged array is written to a temporary
    file in the same directory and renamed, so a crash leaves the previous
    sidecar intact rather than a truncated one.
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

    try:
        for i, fno in enumerate(frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fno)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            H, W = ((h + 31) // 32) * 32, ((w + 31) // 32) * 32
            im = cv2.copyMakeBorder(frame, 0, H - h, 0, W - w,
                                    cv2.BORDER_CONSTANT, value=0)
            rgb = (cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32)
                   / 255.0 - _MEAN) / _STD
            t_in = torch.from_numpy(rgb).permute(2, 0, 1)[None].to(device)
            with torch.no_grad():
                out = model(t_in)
            hm = torch.sigmoid(out["bodypart"]["heatmap"])[0].cpu().numpy()
            lr = out["bodypart"]["locref"][0].cpu().numpy()
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
    finally:
        cap.release()

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
