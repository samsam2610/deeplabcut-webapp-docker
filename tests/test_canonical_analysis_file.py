import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from src.dlc import canonical


def test_canonical_h5_path():
    p = canonical.canonical_h5_path("/data/vids/clipA.avi")
    assert str(p) == "/data/vids/clipA_analyzed.h5"
    assert str(canonical.canonical_csv_path("/data/vids/clipA.avi")) == "/data/vids/clipA_analyzed.csv"


def test_canonical_scorer_from_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: AliLab\nbodyparts:\n- nose\n- tail\n")
    assert canonical.canonical_scorer(str(cfg)) == "AliLab"


def test_canonical_scorer_fallback(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("bodyparts:\n- nose\n")
    assert canonical.canonical_scorer(str(cfg)) == "DLC_analyzed"


def test_read_bodyparts(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scorer: X\nbodyparts:\n- nose\n- tailbase\n")
    assert canonical.read_bodyparts(str(cfg)) == ["nose", "tailbase"]


def test_build_empty_dense_df_shape():
    df = canonical.build_empty_dense_df("S", ["nose", "tail"], 5)
    assert df.shape == (5, 6)
    assert list(df.columns.names) == ["scorer", "bodyparts", "coords"]
    assert df.columns.get_level_values("scorer").unique().tolist() == ["S"]
    assert df.isna().all().all()
    assert list(df.index) == [0, 1, 2, 3, 4]


def test_relabel_scorer():
    cols = pd.MultiIndex.from_product([["OLD"], ["nose"], ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    df = pd.DataFrame(np.ones((2, 3)), columns=cols)
    out = canonical.relabel_scorer(df, "OLD", "NEW")
    assert out.columns.get_level_values("scorer").unique().tolist() == ["NEW"]


def test_write_to_canonical_creates_then_merges(tmp_path):
    vid = tmp_path / "clip.avi"; vid.write_bytes(b"x")
    cols = pd.MultiIndex.from_product([["SNAP"], ["nose"], ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    df1 = pd.DataFrame([[1.0, 2.0, 0.9]], index=pd.Index([2]), columns=cols)
    h5, csv = canonical.write_to_canonical(str(vid), df1, source_scorer="SNAP",
                                           canonical_scorer="CANON", save_as_csv=True)
    assert h5.exists() and csv.exists()
    got = pd.read_hdf(str(h5))
    assert got.columns.get_level_values("scorer").unique().tolist() == ["CANON"]
    assert len(got) == 3
    assert got.iloc[0].isna().all()
    assert got.iloc[2, 0] == 1.0
    df2 = pd.DataFrame([[5.0, 6.0, 0.8]], index=pd.Index([0]), columns=cols)
    canonical.write_to_canonical(str(vid), df2, source_scorer="SNAP",
                                 canonical_scorer="CANON", save_as_csv=False)
    got2 = pd.read_hdf(str(h5))
    assert got2.iloc[0, 0] == 5.0 and got2.iloc[2, 0] == 1.0


def test_run_range_writes_canonical_not_scorer_named(monkeypatch, tmp_path):
    """_run_range must write <stem>_analyzed.h5 (canonical), not <stem><scorer>.h5."""
    from src.dlc import tasks
    vid = tmp_path / "clipB.avi"; vid.write_bytes(b"x")
    cols = pd.MultiIndex.from_product([["SNAPSCORER"], ["nose"], ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    fake_df = pd.DataFrame([[1.0, 2.0, 0.9]], index=pd.Index([0]), columns=cols)
    monkeypatch.setitem(tasks.__dict__, "_RangeVideoIterator", lambda *a, **k: object())
    monkeypatch.setitem(tasks.__dict__, "video_inference", lambda *a, **k: object())
    monkeypatch.setitem(tasks.__dict__, "_dlc_create_df_from_prediction",
                        lambda **k: fake_df.copy())
    req = {"video_path": str(vid), "start_frame": 0, "n_frames": 1,
           "save_as_csv": True, "snapshot_path": "/snap", "req_id": "r1"}
    n_an, n_sk = tasks._run_range(runner=object(), scorer="SNAPSCORER", model_cfg={},
                                  multi_animal=False, canonical_scorer="CANON", req=req)
    assert n_an == 1
    assert (tmp_path / "clipB_analyzed.h5").exists()
    assert not (tmp_path / "clipBSNAPSCORER.h5").exists()
    got = pd.read_hdf(str(tmp_path / "clipB_analyzed.h5"))
    assert got.columns.get_level_values("scorer").unique().tolist() == ["CANON"]
