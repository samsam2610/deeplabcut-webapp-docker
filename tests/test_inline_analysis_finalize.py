"""Behavior of the finalize-range helper: copy a layer-h5 range into the
canonical _analyzed file (curated range wins, out-of-range frames preserved)."""
import numpy as np
import pandas as pd
from pathlib import Path

from src.dlc import inline_analysis, canonical


def _layer_df(scorer, frames, bodyparts=("nose", "tail")):
    cols = pd.MultiIndex.from_product(
        [[scorer], list(bodyparts), ["x", "y", "likelihood"]],
        names=["scorer", "bodyparts", "coords"])
    data = np.zeros((len(list(frames)), len(bodyparts) * 3))
    idx = list(frames)
    for i, f in enumerate(idx):
        data[i, :] = float(f)  # value == frame number, for easy assertions
    return pd.DataFrame(data, index=pd.Index(idx, name="frame"), columns=cols)


def test_finalize_range_writes_canonical(tmp_path):
    video = tmp_path / "clip.mp4"; video.write_bytes(b"")
    scorer = "DLC_resnet50_xshuffle1_snapshot100"
    layer = tmp_path / f"clip{scorer}.h5"
    _layer_df(scorer, range(0, 10)).to_hdf(str(layer), key="df_with_missing", mode="w")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: CANON\nbodyparts:\n- nose\n- tail\n")

    h5, csv, n = inline_analysis._finalize_range_to_canonical(
        str(video), str(layer), start_frame=3, n_frames=2, config_path=str(cfg))

    assert n == 2
    assert Path(h5).name == "clip_analyzed.h5"
    assert Path(csv).is_file()
    out = pd.read_hdf(str(h5))
    assert out.columns.get_level_values(0).unique().tolist() == ["CANON"]
    assert list(out.index) == [0, 1, 2, 3, 4]
    assert out.loc[3, ("CANON", "nose", "x")] == 3
    assert out.loc[4, ("CANON", "nose", "x")] == 4
    assert np.isnan(out.loc[0, ("CANON", "nose", "x")])


def test_finalize_range_preserves_existing(tmp_path):
    video = tmp_path / "clip.mp4"; video.write_bytes(b"")
    scorer = "SRC"
    layer = tmp_path / f"clip{scorer}.h5"
    _layer_df(scorer, range(0, 10)).to_hdf(str(layer), key="df_with_missing", mode="w")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: CANON\nbodyparts:\n- nose\n- tail\n")

    pre = _layer_df("CANON", [8]); pre.loc[8, :] = 999.0
    canon_h5 = canonical.canonical_h5_path(str(video))
    pre.to_hdf(str(canon_h5), key="df_with_missing", mode="w")

    inline_analysis._finalize_range_to_canonical(
        str(video), str(layer), start_frame=3, n_frames=2, config_path=str(cfg))

    out = pd.read_hdf(str(canon_h5))
    assert out.loc[8, ("CANON", "nose", "x")] == 999
    assert out.loc[3, ("CANON", "nose", "x")] == 3


def test_finalize_range_overwrites_conflicting_canonical_value(tmp_path):
    """The curated (source) values win over a different value already present
    in canonical for the SAME frame."""
    video = tmp_path / "clip.mp4"; video.write_bytes(b"")
    scorer = "SRC"
    layer = tmp_path / f"clip{scorer}.h5"
    _layer_df(scorer, range(0, 10)).to_hdf(str(layer), key="df_with_missing", mode="w")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: CANON\nbodyparts:\n- nose\n- tail\n")

    # Pre-populate canonical frame 3 with a CONFLICTING value (99).
    pre = _layer_df("CANON", [3]); pre.loc[3, :] = 99.0
    canon_h5 = canonical.canonical_h5_path(str(video))
    pre.to_hdf(str(canon_h5), key="df_with_missing", mode="w")

    # Source frame 3 has value 3 (from _layer_df). Curated value must win.
    inline_analysis._finalize_range_to_canonical(
        str(video), str(layer), start_frame=3, n_frames=2, config_path=str(cfg))

    out = pd.read_hdf(str(canon_h5))
    assert out.loc[3, ("CANON", "nose", "x")] == 3, "curated source value must overwrite canonical"
