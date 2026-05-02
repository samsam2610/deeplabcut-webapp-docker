"""Static template + DOM-shape assertions for the post-process card.

Parses the actual template files (no rendering) to verify:
- The new card partial exists and has the expected IDs.
- The trigger button is between the existing two buttons in the right order.
- The new card partial is included in index.html.
- All IDs introduced by the new card are unique across all partials.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
INDEX = ROOT / "src" / "templates" / "index.html"


def test_card_postprocess_partial_exists():
    p = PARTIALS / "card_postprocess.html"
    assert p.is_file()
    txt = p.read_text()
    assert 'id="postprocess-card"' in txt
    assert 'id="btn-close-postprocess"' in txt
    assert 'id="pp-tool"' in txt


def test_index_includes_postprocess_partial():
    txt = INDEX.read_text()
    assert "partials/card_postprocess.html" in txt


def test_button_sits_between_view_and_annotate():
    p = PARTIALS / "card_dlc_project.html"
    txt = p.read_text()
    i_view = txt.index('id="btn-open-view-analyzed"')
    i_post = txt.index('id="btn-open-postprocess"')
    i_annot = txt.index('id="btn-open-annotate-video"')
    assert i_view < i_post < i_annot


def test_new_ids_are_unique_across_partials():
    new_ids = {
        "postprocess-card", "btn-close-postprocess", "btn-open-postprocess",
        "pp-tool", "pp-input-mode-file", "pp-input-mode-folder",
        "pp-params-deeplabcut", "pp-params-refine",
        "pp-run", "pp-cancel", "pp-status", "pp-log", "pp-recent",
    }
    seen: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    for nid in new_ids:
        assert seen.get(nid, 0) == 1, f"id {nid!r} appears {seen.get(nid, 0)} times"
