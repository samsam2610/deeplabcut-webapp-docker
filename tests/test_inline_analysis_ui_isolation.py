"""Static-template + DOM-shape assertions for the Inline Analysis card.

No JS runtime here — these tests parse the template files directly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT     = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
INDEX    = ROOT / "src" / "templates" / "index.html"


def test_card_inline_analysis_partial_exists():
    p = PARTIALS / "card_inline_analysis.html"
    assert p.is_file()
    txt = p.read_text()
    assert 'id="inline-analysis-card"' in txt
    assert 'id="btn-close-inline-analysis"' in txt
    # Player elements with the "ia-" prefix the factory expects.
    assert 'id="ia-frame-img"' in txt
    assert 'id="ia-overlay-canvas"' in txt
    assert 'id="ia-btn-play"' in txt
    # Params block.
    assert 'id="ia-snapshot"' in txt
    assert 'id="ia-batch-size"' in txt
    assert 'id="ia-frames-per-click"' in txt
    assert 'id="ia-keep-warm-seconds"' in txt
    assert 'id="ia-btn-analyze-range"' in txt
    # No ia-disable-banner — project-type errors surface via the existing
    # "Last run" status line, sourced from /session/start's 409 body.
    assert 'id="ia-disable-banner"' not in txt


def test_index_includes_inline_analysis_partial():
    txt = INDEX.read_text()
    assert "partials/card_inline_analysis.html" in txt


def test_button_sits_between_analyze_and_view_analyzed():
    p = PARTIALS / "card_dlc_project.html"
    txt = p.read_text()
    i_analyze = txt.index('id="btn-open-analyze"')
    i_inline  = txt.index('id="btn-open-inline-analysis"')
    i_view    = txt.index('id="btn-open-view-analyzed"')
    assert i_analyze < i_inline < i_view, (
        "Inline Analysis button must sit between Analyze and View-Analyzed"
    )


def test_hide_no_h5_is_unchecked_by_default():
    """Spec §1: default UNCHECKED (opposite of View-Analyzed)."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    m = re.search(r'<input[^>]*id="ia-hide-no-h5"[^>]*>', txt)
    assert m, "ia-hide-no-h5 checkbox must exist"
    assert "checked" not in m.group(0), (
        "ia-hide-no-h5 must NOT have `checked` — default unchecked per spec §1"
    )


def test_no_create_labeled_controls_in_card():
    """Spec §1: 'Create labeled video / frame' is explicitly omitted."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert "ia-create-labeled" not in txt
    assert "Create labeled video" not in txt
    assert "Create labeled frame" not in txt


def test_inline_analysis_js_does_not_hide_other_cards():
    """Spec §1.1: openCard must NOT call hideAllOtherCards or otherwise
    iterate over section.card and toggle .hidden — that collapses every
    other open dashboard card. The card just shows itself and scrolls
    into view.
    """
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "hideAllOtherCards" not in js, (
        "inline_analysis.js must not define or call hideAllOtherCards "
        "— see polish spec §1.1"
    )
    assert "section.card" not in js, (
        "inline_analysis.js must not query `section.card` (which would "
        "let it mass-toggle other cards' visibility)"
    )


def test_shuffle_and_trainingsetindex_inputs_exist():
    """Polish spec §1.3: full Analyze-card parity for the snapshot row."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-shuffle"' in txt
    assert 'id="ia-trainingsetindex"' in txt
    assert 'id="ia-snapshot"' in txt
    assert 'id="ia-refresh-snapshots"' in txt


def test_inline_analysis_js_uses_latest_rel_path_and_iter_format():
    """The snapshot picker must mirror analyze.js's format —
    use data.latest_rel_path for the default and render
    `<label> · iter N · shM` per option."""
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "latest_rel_path" in js, "must use the Latest-default pattern"
    assert "iter" in js, "must format the iteration count in option text"
    # Shuffle change reloads snapshots (indices are per-shuffle).
    assert "ia-shuffle" in js


def test_inline_analysis_js_sends_trainingsetindex_in_range():
    """Polish spec §1.3 last bullet: /range POST body must include
    shuffle + trainingsetindex from the new inputs."""
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "ia-trainingsetindex" in js
    # Sanity: still sends shuffle (it did before, but now from the input).
    assert "shuffle" in js


def test_overlay_comparison_widgets_removed():
    """Polish spec §1.4: drop the multi-h5 comparison UI; the card now
    shows ONLY the just-produced h5."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-overlay-primary-select"' not in txt, (
        "primary-select dropdown must be removed per polish spec §1.4"
    )
    assert 'id="ia-overlay-add-compare"' not in txt
    assert 'id="ia-overlay-compare-list"' not in txt
    # Keep these — they remain useful in single-layer mode:
    assert 'id="ia-overlay-toggle"' in txt
    assert 'id="ia-overlay-threshold"' in txt
    assert 'id="ia-overlay-marker-size"' in txt


def test_bp_chips_container_present():
    """Polish spec §1.4: body-part chips container is newly added."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-bp-chips"' in txt


def test_full_curation_panel_mirrored_in_inline_analysis_partial():
    """Polish spec §1.5: every va-* curation ID has an ia-* counterpart
    in the inline-analysis partial."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    required_ia_ids = [
        # Toggle + master area
        "ia-curation-panel", "ia-curation-toggle", "ia-curation-controls",
        "ia-curation-status",
        # Row 1: Extract + Add
        "ia-extract-frame-btn", "ia-add-to-dataset-btn",
        # Row 2: Batch
        "ia-batch-count", "ia-batch-step", "ia-batch-add-btn",
        # Row 3: CSV section
        "ia-csv-section", "ia-csv-none", "ia-csv-loaded",
        "ia-csv-path-display", "ia-create-csv-btn", "ia-csv-create-status",
        # Row 3b: Timelines
        "ia-csv-bars", "ia-status-bar-wrap", "ia-note-bar-wrap",
        "ia-status-canvas", "ia-note-canvas",
        "ia-status-chips", "ia-note-chips",
        "ia-status-prev-btn", "ia-status-next-btn",
        "ia-note-prev-btn", "ia-note-next-btn",
        # Row 4: Annotation panel
        "ia-annot-panel", "ia-annot-frame-num",
        "ia-status-input", "ia-save-status-btn",
        "ia-note-input", "ia-save-note-btn",
        "ia-annot-save-status",
        "ia-new-tag-input", "ia-add-tag-btn",
    ]
    missing = [i for i in required_ia_ids if f'id="{i}"' not in txt]
    assert not missing, f"missing IDs in curation panel: {missing}"


def test_no_va_ids_leaked_into_inline_partial():
    """Sanity: ensure the rename from va- to ia- was complete."""
    import re as _re
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    leaked = _re.findall(r'id="(va-[^"]+)"', txt)
    assert not leaked, f"va- IDs leaked into inline-analysis partial: {leaked}"


def test_new_ids_are_unique_across_partials():
    new_ids = {
        "inline-analysis-card", "btn-close-inline-analysis", "btn-open-inline-analysis",
        "ia-snapshot", "ia-shuffle", "ia-trainingsetindex", "ia-batch-size",
        "ia-frames-per-click", "ia-keep-warm-seconds",
        "ia-warm-indicator", "ia-btn-analyze-range", "ia-last-run-status",
        "ia-file-browser-pane", "ia-hide-no-h5",
        "ia-frame-img", "ia-overlay-canvas", "ia-btn-play",
        "ia-btn-prev", "ia-btn-next", "ia-seek", "ia-frame-counter",
        "ia-zoom", "ia-zoom-val", "ia-skip-n", "ia-frame-spinner",
        "ia-overlay-toggle",
        "ia-overlay-threshold", "ia-overlay-marker-size",
        "ia-bp-list-wrap", "ia-bp-chips",
        "ia-marker-edit-banner",
    }
    seen: dict = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    for nid in new_ids:
        assert seen.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen.get(nid, 0)} times across partials"
        )
