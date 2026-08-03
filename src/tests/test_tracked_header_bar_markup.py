"""Mounts for the sort buttons and the player-header progress bar.

Five mounts across three templates in two repos — the shared component renders
into them, so a missing one silently means no sort buttons or no header bar in
that one place.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
CARD = SRC / "templates" / "partials" / "card_tracked_files.html"
IA2D = SRC / "templates" / "partials" / "card_inline_analysis.html"
# SRC is <repo>/src, so parents[1] is the directory holding both repos.
IA3D = (SRC.parents[1] / "deeplabcut-webapp-docker-supports" / "dlc-3D"
        / "src" / "templates" / "partials" / "card_inline_analysis_3d.html")

SORT_MOUNTS = [(CARD, "tf-sort"), (IA2D, "ia-sort"), (IA3D, "ia3d-sort")]
BAR_MOUNTS = [(IA2D, "ia-track-bar", "ia-selected-name"),
              (IA3D, "ia3d-track-bar", "ia3d-selected-name")]


def test_every_list_header_has_a_sort_mount():
    for path, mount_id in SORT_MOUNTS:
        assert path.is_file(), f"missing {path}"
        assert f'id="{mount_id}"' in path.read_text(), f"{path.name}: no #{mount_id}"


def test_the_sort_mount_sits_in_the_header_beside_refresh():
    for path, mount_id in SORT_MOUNTS:
        s = path.read_text()
        refresh = re.search(r'id="(tf|ia|ia3d)-(tracked-)?refresh"', s)
        assert refresh, f"{path.name}: no refresh button to anchor against"
        # Both live in the same header row, so they must be near each other.
        assert abs(s.index(f'id="{mount_id}"') - refresh.start()) < 400, \
            f"{path.name}: #{mount_id} is not in the header row"


def test_both_player_headers_have_a_bar_mount_after_the_filename():
    for path, mount_id, name_id in BAR_MOUNTS:
        s = path.read_text()
        assert f'id="{mount_id}"' in s, f"{path.name}: no #{mount_id}"
        assert s.index(f'id="{name_id}"') < s.index(f'id="{mount_id}"'), \
            f"{path.name}: the bar must follow the filename, matching row order"


def test_all_three_consumers_pass_the_new_mounts():
    panel = (SRC / "static" / "js" / "tracked_files_panel.js").read_text()
    ia2d = (SRC / "static" / "js" / "inline_analysis_player.js").read_text()
    ia3d = (SRC.parents[1] / "deeplabcut-webapp-docker-supports" / "dlc-3D"
            / "src" / "static" / "inline_analysis_3d.js").read_text()
    assert "sortMount" in panel
    for src, who in ((ia2d, "2D card"), (ia3d, "3D card")):
        assert "sortMount" in src, f"{who} does not pass sortMount"
        assert "headerBarMount" in src, f"{who} does not pass headerBarMount"
