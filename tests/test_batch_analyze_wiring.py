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


def test_the_panel_is_the_card_now():
    # The gating checkbox is gone: one queue, one parameter set, several
    # actions. The panel is always rendered (the CARD is still hidden until
    # opened, and the controller boots on that reveal).
    html = HTML.read_text()
    assert "ba-enable" not in html
    assert 'id="ba-panel"' in html and 'id="ba-panel" class="hidden"' not in html
    assert "ba-enable" not in JS.read_text()


def test_the_single_run_workflow_is_gone():
    # "Analyze all" over a one-file queue replaces it. Leaving the old widgets
    # behind would mean two buttons that both analyse and two ways to pick a
    # file, which is exactly the confusion this removed.
    html, js = HTML.read_text(), (SRC / "static" / "js" / "analyze.js").read_text()
    for dead in ("av-target-path", "av-browse-btn", "av-browse-up", "av-browser",
                 "av-batch-add-btn", "av-batch-clear-btn", "av-batch-list",
                 "btn-run-analyze", "btn-stop-analyze", "av-run-status"):
        assert dead not in html, f"{dead} still in the markup"
        assert dead not in js, f"{dead} still wired in analyze.js"


def test_create_labeled_video_survives_and_reads_the_queue():
    html, js = HTML.read_text(), (SRC / "static" / "js" / "analyze.js").read_text()
    assert 'id="btn-create-labeled-video"' in html
    assert 'id="av-progress"' in html, "Create Labeled Video reports into this"
    assert "state.baQueue" in js, "its target must come from the queue now"
    assert "state.baQueue" in JS.read_text(), "batch_analyze.js must publish it"


def test_the_snapshot_dropdown_has_a_pin_list():
    html = HTML.read_text()
    assert 'id="av-snapshot"' in html
    assert 'id="av-refresh-snapshots"' in html
    assert 'id="av-snapshot-pin-list"' in html


def test_the_dropdown_beats_the_persisted_pin():
    # Otherwise a dropdown showing one snapshot while the pin names another
    # silently runs the pin.
    assert "snapshot_rel: _snapshotRel()" in JS.read_text()
    py = (SRC / "dlc" / "batch_analyze.py").read_text()
    assert 'body.get("snapshot_rel")' in py
    assert "is_relative_to" in py, "an explicit snapshot must stay path-checked"


def test_gpu_and_output_live_in_run_options_above_the_buttons():
    # Ordering, not just presence: these must read as part of the run
    # parameters, not as strays after the actions.
    html = HTML.read_text()
    for el in ('id="av-gputouse"', 'name="av-output-mode"'):
        assert html.index(el) < html.index('id="ba-run-all"'), f"{el} sits after the buttons"
    assert len(re.findall(r'name="av-output-mode"', html)) == 2, "default + custom"
    assert re.search(r'id="av-output-default"[^>]*checked', html), "default must win"


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


# ── Wide layout (2026-08-06) ──────────────────────────────────────────────

def test_the_analyze_card_is_full_width():
    # .card caps at 560px; cards sit directly in <main class="cards"> which has
    # no cap, so overriding max-width is the whole mechanism — same as
    # #inline-analysis-3d-card.
    css = CSS.read_text()
    assert re.search(r"#analyze-card\s*\{[^}]*max-width:\s*none", css)
    assert re.search(r"#analyze-card\s*\{[^}]*width:\s*100%", css)


def test_single_run_params_are_gridded_not_stretched():
    # Without this the seven parameter rows each span the full monitor.
    html, css = HTML.read_text(), CSS.read_text()
    assert html.count('class="av-param-grid"') == 3, \
        "Model half, Run options half, and the labeled-video params each grid"
    assert re.search(r"\.av-param-grid\s*\{[^}]*repeat\(auto-fit,\s*minmax\(", css), \
        "auto-fit keeps it responsive without hand-picked breakpoints"


def test_the_panel_splits_into_halves_and_collapses_when_narrow():
    html, css = HTML.read_text(), CSS.read_text()
    assert 'class="ba-split"' in html
    assert re.search(r"\.ba-split\s*\{[^}]*grid-template-columns:\s*1fr 1fr", css)
    assert re.search(r"@media \(max-width: 900px\)\s*\{\s*#ba-panel \.ba-split\s*\{"
                     r"\s*grid-template-columns:\s*1fr", css), \
        "two columns must collapse to one on a narrow screen"


def test_analyze_for_tag_is_disabled_in_the_markup():
    # Not just by the controller: if batch_analyze.js fails to load, the button
    # must still be inert rather than submitting a run with no tags.
    tag_btn = re.search(r'<button id="ba-run-tag"[^>]*>', HTML.read_text()).group(0)
    assert "disabled" in tag_btn
    # "Analyze all" ignores TAGS, but it is still gated on the queue — see
    # test_both_run_buttons_start_disabled.
    run_all = re.search(r'<button id="ba-run-all"[^>]*>', HTML.read_text()).group(0)
    assert "Queue at least one video" in run_all, \
        '"Analyze all" must be gated on the queue, not on tags'


def test_the_tag_field_no_longer_advertises_comma_separation():
    # Chips are the selection now; the field mints exactly one tag per Add.
    field = re.search(r'<input type="text" id="ba-tag-input"[^>]*>', HTML.read_text()).group(0)
    assert "comma" not in field.lower()


def test_the_controller_submits_the_selection_not_the_field():
    src = JS.read_text()
    # The exact submission line, not just any mention: submittedTags is also
    # called by _syncTagEnablement, and a looser check passes even when the
    # payload is built from something else entirely.
    assert "const tags = submittedTags(_tags, _selected);" in src, \
        "a run must carry the selected chips, not whatever is typed in the box"
    assert "_parseTags" not in src, "the old field-parsing path must be gone"


def test_the_pure_tag_rules_live_in_a_testable_module():
    assert (SRC / "static" / "js" / "internal" / "batch_tags.mjs").is_file()
    assert "from './internal/batch_tags.mjs'" in JS.read_text()


# ── Run-button gating (2026-08-06) ────────────────────────────────────────

def test_both_run_buttons_start_disabled():
    # An empty queue means there is nothing to run. Disabled in the MARKUP so
    # the buttons are inert even if batch_analyze.js fails to load.
    html = HTML.read_text()
    for bid in ("ba-run-all", "ba-run-tag"):
        tag = re.search(rf'<button id="{bid}"[^>]*>', html).group(0)
        assert "disabled" in tag, f"{bid} must start disabled"


def test_cancel_is_disabled_not_hidden():
    # A button that appears and vanishes is harder to find than one that greys
    # out, and "where did Cancel go" is a worse question than "why is it grey".
    tag = re.search(r'<button id="ba-cancel"[^>]*>', HTML.read_text()).group(0)
    assert "disabled" in tag
    assert "hidden" not in tag, "cancel should grey out, not disappear"
    js = JS.read_text()
    assert 'classList.remove("hidden")' not in js.split("_startPolling")[1][:400]


def test_the_queue_drives_run_enablement():
    js = JS.read_text()
    assert "const queued = _queue.length > 0;" in js
    # Re-running enablement on every queue change is what keeps the buttons
    # honest; without it they stay stale until a tag is clicked.
    render = js.split("function _renderQueue()")[1][:400]
    assert "_syncTagEnablement()" in render
