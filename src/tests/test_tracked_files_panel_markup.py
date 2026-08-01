"""Static guards for the Tracked Files management card (2D + 3D).

See docs/superpowers/specs/2026-08-01-progress-arrow-bar-design.md.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
CARD = SRC / "templates" / "partials" / "card_tracked_files.html"
PROJECT_CARD = SRC / "templates" / "partials" / "card_dlc_project.html"
INDEX = SRC / "templates" / "index.html"
PANEL_JS = SRC / "static" / "js" / "tracked_files_panel.js"
MAIN_JS = SRC / "static" / "js" / "main.js"
# SRC is <repo>/src, so parents[1] is the directory holding both repos.
DLC3D = (SRC.parents[1] / "deeplabcut-webapp-docker-supports" / "dlc-3D"
         / "src" / "templates" / "dlc_3d.html")


def test_the_open_button_sits_directly_after_annotate_video():
    s = PROJECT_CARD.read_text()
    assert 'id="btn-open-progress-tracking"' in s
    assert s.index('id="btn-open-annotate-video"') < s.index('id="btn-open-progress-tracking"')
    between = s[s.index('id="btn-open-annotate-video"'):s.index('id="btn-open-progress-tracking"')]
    assert between.count("<button") == 1, "no other button may sit between the two"


def test_card_has_the_definition_editor_and_the_file_list():
    s = CARD.read_text()
    for el in ("tracked-files-card", "tf-add-bar-btn", "tf-segment-count",
               "tf-segments", "tf-save-bar-btn", "tf-list", "tf-status"):
        assert f'id="{el}"' in s, f"missing #{el}"


def test_segment_count_input_is_clamped_zero_to_ten():
    s = CARD.read_text()
    m = re.search(r'<input[^>]*id="tf-segment-count"[^>]*>', s)
    assert m, "missing #tf-segment-count"
    assert 'min="0"' in m.group(0)
    assert 'max="10"' in m.group(0)


def test_card_is_included_by_both_page_templates():
    assert 'partials/card_tracked_files.html' in INDEX.read_text()
    assert DLC3D.is_file(), f"missing {DLC3D}"
    assert 'partials/card_tracked_files.html' in DLC3D.read_text()


def test_panel_controller_is_loaded_by_main_js():
    assert PANEL_JS.is_file(), f"missing {PANEL_JS}"
    assert "tracked_files_panel.js" in MAIN_JS.read_text()


def test_panel_reuses_the_shared_row_component_rather_than_its_own_list():
    s = PANEL_JS.read_text()
    assert 'from "./components/tracked_files_tab.js"' in s
    assert "makeTrackedFiles(" in s
