"""Static guards for the 2D Inline Analysis card's Tracked Files tab.

Mirrors the 3D card's guards, including the open-abort regression: a tracked
file whose video has moved must not open an empty viewer at fps 30 / 0 frames.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
HTML = SRC / "templates" / "partials" / "card_inline_analysis.html"
JS = SRC / "static" / "js" / "inline_analysis_player.js"


def test_third_tab_and_panel_exist():
    s = HTML.read_text()
    assert 'id="ia-tab-tracked"' in s
    assert s.index('id="ia-tab-browse"') < s.index('id="ia-tab-tracked"')
    m = re.search(r'<div id="ia-tab-tracked-panel"[^>]*class="([^"]*)"', s)
    assert m and "hidden" in m.group(1)
    assert 'id="ia-tracked-list"' in s


def test_launcher_error_line_precedes_the_player_section():
    s = HTML.read_text()
    assert 'id="ia-launcher-error"' in s
    assert s.index('id="ia-launcher-error"') < s.index('id="ia-player-section"')


def test_track_checkbox_precedes_the_selected_name():
    s = HTML.read_text()
    assert 'id="ia-track-checkbox"' in s
    assert s.index('id="ia-track-checkbox"') < s.index('id="ia-selected-name"')


def test_js_constructs_the_shared_component():
    s = JS.read_text()
    assert 'from "./components/tracked_files_tab.js"' in s
    assert "makeTrackedFiles(" in s


def test_open_browse_video_aborts_instead_of_falling_back_to_fps_30():
    s = JS.read_text()
    m = re.search(r"async function _iaOpenBrowseVideo\([\s\S]*?\n    \}", s)
    assert m, "missing _iaOpenBrowseVideo"
    body = m.group(0)
    assert "res.ok" in body
    assert re.search(r"info\.error", body)
    assert not re.search(r"catch\s*\([^)]*\)\s*\{\s*_iaFps\s*=\s*30", body), \
        "the silent fps-30 fallback must be gone"


def test_reset_hides_the_track_checkbox():
    s = JS.read_text()
    assert re.search(r"_trackedFiles\?\.setCurrent\(\s*null\s*\)", s)
