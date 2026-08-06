"""Pure helpers behind Batch Analyze.

These decide which model runs and which frames get analysed across a whole
batch, so a wrong answer here is 20 videos analysed with the wrong model or
over the wrong frames — silently, because nothing downstream can tell.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc.batch_analyze import (  # noqa: E402
    RANGE_MAX_FRAMES, chunk_video, merge_windows, resolve_snapshot, tagged_frames,
)


def _snap(label, iteration, rel=None, shuffle=1):
    return {"label": label, "iteration": iteration, "shuffle": shuffle,
            "rel_path": rel or f"dlc-models-pytorch/iteration-{iteration}/x/train/{label}.pt"}


class TestResolveSnapshot:
    # Ascending, latest last — the order scan_snapshots produces.
    SNAPS = [
        _snap("snapshot-100", 23),
        _snap("snapshot-best-150", 24),
        _snap("snapshot-180", 24),
    ]

    def test_latest_takes_the_last_entry(self):
        rel, err = resolve_snapshot(self.SNAPS, "latest")
        assert err is None
        assert rel.endswith("snapshot-180.pt")

    def test_latest_iter_best_prefers_best_over_the_newer_plain_snapshot(self):
        # snapshot-180 is newer within iteration 24; "latest iteration's best"
        # must still pick the best checkpoint, or the option means nothing.
        rel, err = resolve_snapshot(self.SNAPS, "latest_iter_best")
        assert err is None
        assert rel.endswith("iteration-24/x/train/snapshot-best-150.pt")

    def test_latest_iter_best_ignores_best_from_an_older_iteration(self):
        snaps = [_snap("snapshot-best-90", 23), _snap("snapshot-180", 24)]
        rel, err = resolve_snapshot(snaps, "latest_iter_best")
        assert rel is None
        assert "iteration 24" in err and "snapshot-best" in err

    def test_pinned_returns_the_pin(self):
        pin = self.SNAPS[0]["rel_path"]
        rel, err = resolve_snapshot(self.SNAPS, "pinned", pin)
        assert (rel, err) == (pin, None)

    def test_pinned_but_deleted_fails_rather_than_substituting(self):
        # The whole point of pinning is that a different model is never
        # silently used in its place.
        rel, err = resolve_snapshot(self.SNAPS, "pinned", "gone/snapshot-1.pt")
        assert rel is None
        assert "no longer on disk" in err

    def test_pinned_with_nothing_pinned_fails(self):
        rel, err = resolve_snapshot(self.SNAPS, "pinned", "")
        assert rel is None
        assert "no model is pinned" in err

    def test_unknown_policy_and_empty_project_fail(self):
        assert resolve_snapshot(self.SNAPS, "whatever")[0] is None
        assert resolve_snapshot([], "latest")[0] is None


class TestMergeWindows:
    def test_single_frame_spans_before_plus_after_plus_itself(self):
        # 200 + 599 + the tagged frame = the 800 the inline card uses.
        assert merge_windows([1000], 200, 599, 5000) == [
            {"start": 800, "end": 1599, "n": 800}]

    def test_overlapping_windows_merge_into_one_range(self):
        out = merge_windows([1000, 1100], 200, 599, 5000)
        assert out == [{"start": 800, "end": 1699, "n": 900}]

    def test_disjoint_windows_stay_separate(self):
        out = merge_windows([100, 5000], 10, 10, 10000)
        assert out == [{"start": 90, "end": 110, "n": 21},
                       {"start": 4990, "end": 5010, "n": 21}]

    def test_adjacent_windows_merge(self):
        # Spans (10,20) and (21,31) touch without overlapping. Contiguous work
        # must become ONE range, not two — the boundary case a naive
        # "start < last.end" test gets wrong.
        assert merge_windows([10, 21], 0, 10, 1000) == [
            {"start": 10, "end": 31, "n": 22}]

    def test_a_one_frame_gap_does_not_merge(self):
        # Spans (10,20) and (22,32): one untagged frame between them, so they
        # stay separate. Guards the other side of the same boundary.
        assert merge_windows([10, 22], 0, 10, 1000) == [
            {"start": 10, "end": 20, "n": 11},
            {"start": 22, "end": 32, "n": 11}]

    def test_windows_clamp_to_the_video(self):
        out = merge_windows([5], 100, 100, 50)
        assert out == [{"start": 0, "end": 49, "n": 50}]

    def test_unreadable_video_yields_nothing(self):
        # frame_count 0 means the probe failed. There is nothing to analyse;
        # submitting frame 0 would be a guess.
        assert merge_windows([10], 5, 5, 0) == []

    def test_no_frames_yields_nothing(self):
        assert merge_windows([], 200, 599, 1000) == []


class TestChunkVideo:
    def test_short_video_is_one_range(self):
        assert chunk_video(500) == [{"start": 0, "end": 499, "n": 500}]

    def test_long_video_is_chunked_under_the_route_cap(self):
        out = chunk_video(25_000)
        assert [r["n"] for r in out] == [RANGE_MAX_FRAMES, RANGE_MAX_FRAMES, 5000]
        assert out[0]["start"] == 0 and out[1]["start"] == RANGE_MAX_FRAMES
        assert out[-1]["end"] == 24_999
        assert all(r["n"] <= RANGE_MAX_FRAMES for r in out)

    def test_exactly_the_cap_is_one_range(self):
        assert chunk_video(RANGE_MAX_FRAMES) == [
            {"start": 0, "end": RANGE_MAX_FRAMES - 1, "n": RANGE_MAX_FRAMES}]

    def test_ranges_are_contiguous_and_cover_everything(self):
        out = chunk_video(23_457)
        assert out[0]["start"] == 0
        assert sum(r["n"] for r in out) == 23_457
        for a, b in zip(out, out[1:]):
            assert b["start"] == a["end"] + 1

    def test_empty_video_yields_nothing(self):
        assert chunk_video(0) == [] and chunk_video(-5) == []


class TestTaggedFrames:
    ROWS = [
        {"frame_number": "10", "note": "start-failure"},
        {"frame_number": "20", "note": "start-success"},
        {"frame_number": "30", "note": "start-failure-2"},
        {"frame_number": "40", "note": "  start-failure  "},
        {"frame_number": "10", "note": "start-failure"},
    ]

    def test_matches_exactly_not_by_prefix(self):
        # "start-failure-2" is a different tag. The user owns the spelling;
        # a prefix match would silently pull in a tag they did not ask for.
        assert tagged_frames(self.ROWS, ["start-failure"]) == [10, 40]

    def test_unions_several_tags_and_dedupes(self):
        assert tagged_frames(self.ROWS, ["start-failure", "start-success"]) == [10, 20, 40]

    def test_no_tags_matches_nothing(self):
        assert tagged_frames(self.ROWS, []) == []
        assert tagged_frames(self.ROWS, ["  "]) == []

    def test_unknown_tag_matches_nothing(self):
        assert tagged_frames(self.ROWS, ["nope"]) == []

    def test_ragged_rows_do_not_raise(self):
        rows = [None, {}, {"note": "x"}, {"frame_number": "abc", "note": "x"},
                {"frame_number": "-3", "note": "x"}]
        assert tagged_frames(rows, ["x"]) == []


class TestPythonMatchesTheJavascript:
    """merge_windows must agree with tag_batch.mjs::mergeWindows.

    The batch panel and the 3D inline card analyse the same tag from the same
    CSV. If the two implementations disagree, a video looks analysed in one
    surface and unanalysed in the other.
    """

    JS = (Path(__file__).parent.parent.parent
          / "deeplabcut-webapp-docker-supports/dlc-3D/src/static/components"
          / "viewer/internal/tag_batch.mjs")

    def test_the_javascript_source_still_exists(self):
        if not self.JS.is_file():
            pytest.skip("dlc-3D module not checked out beside the webapp")

    def test_the_merge_rule_is_still_end_plus_one(self):
        if not self.JS.is_file():
            pytest.skip("dlc-3D module not checked out beside the webapp")
        src = self.JS.read_text()
        # If this literal changes, merge_windows above must change with it.
        assert "s.start <= last.end + 1" in src, (
            "tag_batch.mjs changed its merge rule; dlc/batch_analyze.merge_windows "
            "must be updated to match or the two surfaces will disagree")
