"""Static guards for static/js/components/progress_bar.js.

There is no DOM test runner in this project, so the invariants that actually
bite — CSS injection via a user-chosen colour, XSS via an option label, and
reverting an optimistic paint when the write fails — are guarded at the source.

See docs/superpowers/specs/2026-08-01-progress-arrow-bar-design.md.
"""
import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1]
      / "static" / "js" / "components" / "progress_bar.js")


def _src():
    assert JS.is_file(), f"missing {JS}"
    return JS.read_text()


def test_exports_the_factory():
    assert "export function makeProgressBar" in _src()


def test_every_colour_is_validated_before_it_reaches_style():
    """A colour is user input and lands in style.setProperty / style.background."""
    s = _src()
    assert 'from "./hex_color.mjs"' in s
    assert "isValidHexColor(" in s


def test_labels_are_rendered_with_textContent_never_innerHTML():
    s = _src()
    for m in re.finditer(r"innerHTML\s*=\s*(.+)", s):
        assert m.group(1).strip().startswith('""'), f"unsafe innerHTML: {m.group(0)}"


def test_a_failed_write_reverts_the_optimistic_paint():
    s = _src()
    assert re.search(r"catch[\s\S]{0,300}?(_paint|render|revert)", s), \
        "onChange rejection must restore the previous value"


def test_zero_segments_renders_nothing():
    s = _src()
    assert re.search(r"segments[\s\S]{0,200}?length[\s\S]{0,120}?return", s), \
        "an empty definition must produce no chevrons"


def test_dropdown_offers_clear_and_survives_a_segment_with_no_options():
    s = _src()
    assert "Clear" in s
    assert "No options defined" in s


def test_segments_are_buttons_and_the_dropdown_closes_on_escape():
    s = _src()
    assert 'createElement("button")' in s
    assert "Escape" in s


def test_document_listeners_are_removed_when_the_menu_closes():
    """One bar per row: attaching document listeners for the bar's lifetime
    would leak two per row and re-add them on every refresh."""
    s = _src()
    assert s.count("document.addEventListener") == s.count("document.removeEventListener"), \
        "every document listener must have a matching removal"
    assert re.search(r"_closeMenu[\s\S]{0,400}?document\.removeEventListener", s), \
        "closing the menu must detach the document listeners"
