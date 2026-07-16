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
    # tasks.py imports `deeplabcut` at module load; skip on hosts without it
    # (the worker container has it, and the live smoke covers the integration).
    tasks = pytest.importorskip("src.dlc.tasks", reason="deeplabcut not importable on host")
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


# ─── UI wiring guards (canonical analysis-file init button) ────────────
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[1]


def test_init_button_wired_in_analyze_and_inline2d():
    analyze_js = (_ROOT / "src/static/js/analyze.js").read_text()
    assert "/dlc/project/analysis-file/initialize" in analyze_js
    assert "av-init-file" in analyze_js
    ia2d = (_ROOT / "src/static/js/inline_analysis_player.js").read_text()
    assert "ia-init-analysis-file" in ia2d
    assert "/dlc/project/analysis-file/initialize" in ia2d
    ia2d_html = (_ROOT / "src/templates/partials/card_inline_analysis.html").read_text()
    assert 'id="ia-init-analysis-file"' in ia2d_html


def test_init_routes_registered():
    src = (_ROOT / "src/dlc/inline_analysis.py").read_text()
    assert "/dlc/project/analysis-file/initialize" in src
    assert "/dlc/project/analysis-file/status" in src
    assert "already initialized" in src   # the 409 lock


def test_write_to_canonical_preserves_column_order(tmp_path):
    """Re-merging must keep the existing canonical column order (combine_first
    otherwise alphabetically re-sorts the MultiIndex columns)."""
    vid = tmp_path / "clipC.avi"; vid.write_bytes(b"x")
    # existing canonical file with bodyparts in a deliberate (non-alphabetical) order
    bps = ["snout", "abdomen"]
    existing = canonical.build_empty_dense_df("CANON", bps, 3)
    canonical._atomic_write_h5(canonical.canonical_h5_path(str(vid)), existing)
    # new range under a different scorer, same bodyparts
    cols = pd.MultiIndex.from_product([["SNAP"], bps, ["x", "y", "likelihood"]],
                                      names=["scorer", "bodyparts", "coords"])
    df = pd.DataFrame([[1.0]*6], index=pd.Index([1]), columns=cols)
    canonical.write_to_canonical(str(vid), df, source_scorer="SNAP",
                                 canonical_scorer="CANON", save_as_csv=False)
    got = pd.read_hdf(str(canonical.canonical_h5_path(str(vid))))
    assert got.columns.get_level_values("bodyparts").tolist() == existing.columns.get_level_values("bodyparts").tolist()


def test_labeled_frames_finite_x_included():
    df = canonical.build_empty_dense_df("S", ["nose", "tail"], 4)  # all NaN
    df.loc[1, ("S", "nose", "x")] = 5.0   # frame 1 finalized (finite x)
    df.loc[3, ("S", "tail", "x")] = 9.0   # frame 3 finalized via a different bodypart
    assert canonical.labeled_frames(df) == {1, 3}


def test_labeled_frames_all_nan_excluded():
    df = canonical.build_empty_dense_df("S", ["nose"], 3)  # all NaN
    assert canonical.labeled_frames(df) == set()


def test_labeled_frames_none_or_empty():
    assert canonical.labeled_frames(None) == set()
    assert canonical.labeled_frames(canonical.build_empty_dense_df("S", ["nose"], 0)) == set()


def test_labeled_frames_ignores_likelihood_only():
    # presence keys on x, not likelihood — a finite likelihood with NaN x is NOT labeled
    df = canonical.build_empty_dense_df("S", ["nose"], 2)
    df.loc[0, ("S", "nose", "likelihood")] = 0.9
    assert canonical.labeled_frames(df) == set()
