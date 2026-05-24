import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pandas as pd
from dlc import canonical as C


def test_unfinalize_clears_range_only(tmp_path):
    video = tmp_path / "vidcam0.mp4"
    df = C.build_empty_dense_df("DLC_test", ["a", "b"], 10)
    df.loc[2:5, :] = 1.0     # frames 2,3,4,5 "finalized" (finite); label slice includes 5
    C._atomic_write_h5(C.canonical_h5_path(video), df)
    C._atomic_write_csv(C.canonical_csv_path(video), df)

    n = C.unfinalize_range(video, 3, 2)   # clear frames 3,4
    assert n == 2
    out = pd.read_hdf(str(C.canonical_h5_path(video)))
    assert out.loc[3].isna().all() and out.loc[4].isna().all()           # cleared
    assert not out.loc[2].isna().all() and not out.loc[5].isna().all()   # outside range untouched
    assert C.canonical_csv_path(video).is_file()                          # csv regenerated
    csv_df = pd.read_csv(str(C.canonical_csv_path(video)), header=[0, 1, 2], index_col=0)
    assert csv_df.loc[3].isna().all() and csv_df.loc[4].isna().all()   # csv cleared too
    assert not csv_df.loc[2].isna().all()                              # csv outside-range intact


def test_unfinalize_missing_file_is_noop(tmp_path):
    assert C.unfinalize_range(tmp_path / "novid.mp4", 0, 5) == 0
