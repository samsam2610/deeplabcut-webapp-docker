"""Static guards for static/tracked_files_tab.js.

There is no DOM test runner here, so the invariants that actually bite —
XSS-safe rendering, checkbox revert on failure, stopPropagation on the row
checkbox — are guarded at the source level.

See docs/superpowers/specs/2026-07-31-tracked-files-design.md.
"""
import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1]
      / "static" / "js" / "components" / "tracked_files_tab.js")


def _src():
    assert JS.is_file(), f"missing {JS}"
    return JS.read_text()


def test_exports_the_factory_with_the_documented_surface():
    s = _src()
    assert "export function makeTrackedFiles" in s
    for fn in ("refresh", "setCurrent", "destroy"):
        assert re.search(rf"\b{fn}\b", s), f"factory must expose {fn}"


def test_hits_all_four_routes():
    s = _src()
    assert '"/dlc/project/tracked-files"' in s
    assert '"DELETE"' in s and '"POST"' in s
    assert "/opened" in s


def test_paths_are_rendered_with_textContent_never_innerHTML():
    """Tracked paths are user data — innerHTML on them would be an injection."""
    s = _src()
    # innerHTML is allowed ONLY to clear the list ("" assignment), never to interpolate.
    for m in re.finditer(r"innerHTML\s*=\s*(.+)", s):
        assert m.group(1).strip().startswith('""'), f"unsafe innerHTML: {m.group(0)}"


def test_row_checkbox_stops_propagation_so_untracking_never_opens_the_video():
    s = _src()
    assert "stopPropagation" in s


def test_failed_mutations_revert_the_checkbox():
    s = _src()
    # Both the untrack and track failure paths must restore checkbox state.
    assert re.search(r"catch[\s\S]{0,400}?checked\s*=\s*true", s), "untrack failure must re-check"
    assert re.search(r"catch[\s\S]{0,400}?checked\s*=\s*false", s), "track failure must un-check"


def test_uses_the_shared_relative_time_helper():
    s = _src()
    assert 'from "./relative_time.mjs"' in s
    assert "formatRelative(" in s


def test_setCurrent_loads_the_list_before_trusting_the_header_checkbox():
    """The header checkbox state comes from the in-memory row set. Without a
    first fetch, opening an ALREADY-tracked video before ever clicking the tab
    would render its checkbox unticked."""
    s = _src()
    assert re.search(r"_loaded", s), "must track whether a list fetch has succeeded"
    assert re.search(r"if\s*\(\s*_current\s*&&\s*!_loaded\s*\)\s*await\s+refresh\(\)", s), \
        "setCurrent must await refresh() on first use"


def test_listeners_are_abortable_for_destroy():
    """Header checkbox and tab button are persistent nodes — listeners must be
    removable or they accumulate across re-wiring."""
    s = _src()
    assert "AbortController" in s
    assert "signal" in s


def test_rows_render_the_shared_progress_bar():
    s = _src()
    assert 'from "./progress_bar.js"' in s
    assert "makeProgressBar(" in s


def test_bar_is_built_from_the_listing_progress_and_the_fetched_definition():
    s = _src()
    assert "/dlc/project/progress-bar" in s
    assert re.search(r"values\s*:\s*f\.progress", s), \
        "each row's bar must be seeded from that file's progress object"


def test_segment_edits_persist_through_the_value_route():
    s = _src()
    assert 'const BAR_API = "/dlc/project/progress-bar"' in s
    assert re.search(r'BAR_API\s*\+\s*"/value"', s), "must PUT to the value route"
    assert '"PUT"' in s
