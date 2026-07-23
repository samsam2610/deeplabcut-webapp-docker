"""Unit test for inline_analysis._skeleton_constraints_suggestion — derives the
finger-chain skeleton (Wrist→MCP-k→PIP-k→DIP-k) from an analyzed CSV's bodypart
names, for the params editor's 'Fill from skeleton' button."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import inline_analysis as ia  # noqa: E402


def _write_csv(path, bodyparts_row):
    header = ["scorer"] + ["DLC"] * len(bodyparts_row)
    bps = ["bodyparts"] + bodyparts_row
    coords = ["coords"] + (["x", "y", "likelihood"] * (len(bodyparts_row) // 3))
    path.write_text("\n".join(",".join(r) for r in (header, bps, coords)) + "\n0,1,2,0.9\n")


def test_suggests_finger_chain_from_csv(tmp_path):
    vid = tmp_path / "surv1_cam0_20260101.avi"
    vid.write_text("")
    _write_csv(tmp_path / "surv1_cam0_20260101_analyzed.csv",
               ["Wrist"] * 3 + ["MCP-1"] * 3 + ["PIP-1"] * 3 + ["DIP-1"] * 3 + ["Pellet"] * 3)
    assert ia._skeleton_constraints_suggestion(str(vid)) == [
        ["Wrist", "MCP-1"], ["MCP-1", "PIP-1"], ["PIP-1", "DIP-1"]]  # Pellet → no bone


def test_empty_when_no_finger_parts(tmp_path):
    vid = tmp_path / "surv1_cam0_20260101.avi"
    vid.write_text("")
    _write_csv(tmp_path / "surv1_cam0_20260101_analyzed.csv",
               ["Snout"] * 3 + ["Tail"] * 3)
    assert ia._skeleton_constraints_suggestion(str(vid)) == []


def test_empty_when_csv_missing(tmp_path):
    vid = tmp_path / "surv1_cam0_20260101.avi"
    vid.write_text("")
    assert ia._skeleton_constraints_suggestion(str(vid)) == []
