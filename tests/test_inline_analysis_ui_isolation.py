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


def test_new_ids_are_unique_across_partials():
    new_ids = {
        "inline-analysis-card", "btn-close-inline-analysis", "btn-open-inline-analysis",
        "ia-snapshot", "ia-batch-size",
        "ia-frames-per-click", "ia-keep-warm-seconds",
        "ia-warm-indicator", "ia-btn-analyze-range", "ia-last-run-status",
        "ia-file-browser-pane", "ia-hide-no-h5",
        "ia-frame-img", "ia-overlay-canvas", "ia-btn-play",
        "ia-btn-prev", "ia-btn-next", "ia-seek", "ia-frame-counter",
        "ia-zoom", "ia-zoom-val", "ia-skip-n", "ia-frame-spinner",
        "ia-overlay-toggle", "ia-overlay-primary-select",
        "ia-overlay-add-compare", "ia-overlay-compare-list",
        "ia-overlay-threshold", "ia-overlay-marker-size",
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
