"""The Batch Analyze panel's markup and its controller must agree.

A control whose id the JS never looks up is a button that silently does
nothing, and a `$("ba-…")` with no matching element is a listener that is
never attached. Neither shows up in a Python or a unit test — the page just
quietly does less than it appears to.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
HTML = SRC / "templates" / "partials" / "card_analyze.html"
JS = SRC / "static" / "js" / "batch_analyze.js"
MAIN_JS = SRC / "static" / "js" / "main.js"
CSS = SRC / "static" / "css" / "components.css"

sys.path.insert(0, str(SRC))


def _html_ids() -> set[str]:
    return {m for m in re.findall(r'id="(ba-[a-z0-9-]+)"', HTML.read_text())}


def _js_ids() -> set[str]:
    """Every "ba-…" the controller names, minus the ones that are CSS classes
    or a radio-group name rather than element ids.

    A plain string scan rather than a `$("…")` scan on purpose: ids also reach
    the DOM indirectly (the TABS table holds them as data and looks them up as
    `$(t.btn)`), and a narrower pattern would miss exactly those.
    """
    css_classes = set(re.findall(r'\.(ba-[a-z0-9-]+)', CSS.read_text()))
    named_groups = {"ba-policy"}          # <input name="ba-policy">, not an id
    return (set(re.findall(r'"(ba-[a-z0-9-]+)"', JS.read_text()))
            - css_classes - named_groups)


def test_every_panel_element_is_used_by_the_controller():
    unused = _html_ids() - _js_ids()
    assert not unused, f"markup the controller never touches: {sorted(unused)}"


def test_every_id_the_controller_looks_up_exists_in_the_markup():
    missing = _js_ids() - _html_ids()
    assert not missing, f"controller looks up ids that do not exist: {sorted(missing)}"


def test_the_three_model_radios_are_the_three_the_backend_accepts():
    from dlc.batch_analyze import MODEL_POLICIES
    values = set(re.findall(r'name="ba-policy" value="([a-z_]+)"', HTML.read_text()))
    assert values == set(MODEL_POLICIES)


def test_exactly_one_radio_is_checked_by_default():
    block = re.findall(r'<input type="radio" name="ba-policy"[^>]*>', HTML.read_text())
    assert len(block) == 3
    assert sum("checked" in tag for tag in block) == 1


def test_the_window_defaults_are_the_inline_cards_eight_hundred_frames():
    html = HTML.read_text()
    assert re.search(r'id="ba-before" value="200"', html)
    assert re.search(r'id="ba-after" value="599"', html)


def test_the_panel_is_collapsed_by_default():
    html = HTML.read_text()
    # The enable checkbox must NOT be checked, and the panel must carry
    # `hidden`, or the card grows a large panel nobody asked for.
    assert re.search(r'id="ba-enable"(?![^>]*\bchecked\b)[^>]*>', html)
    assert re.search(r'id="ba-panel" class="hidden"', html)


def test_the_controller_is_registered_in_the_module_loader():
    assert "'./batch_analyze.js'" in MAIN_JS.read_text()


def test_the_pill_classes_the_controller_emits_are_styled():
    src, css = JS.read_text(), CSS.read_text()
    for cls in re.findall(r'className = "([a-z0-9 -]*ba-[a-z0-9-]+[a-z0-9 -]*)"', src):
        for part in cls.split():
            if part.startswith("ba-"):
                assert f".{part}" in css, f"{part} is emitted but never styled"


@pytest.mark.parametrize("route", [
    "/dlc/project/batch-analyze/start",
    "/dlc/project/batch-analyze/status",
    "/dlc/project/batch-analyze/list",
    "/dlc/project/batch-analyze/cancel",
])
def test_every_endpoint_the_controller_calls_is_registered(route):
    assert route in JS.read_text(), "controller no longer calls this route"
    assert f'"{route}"' in (SRC / "dlc" / "batch_analyze.py").read_text(), \
        "the controller calls a route the blueprint does not define"
