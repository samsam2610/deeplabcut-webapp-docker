"""
Regression tests: timeline bars must use <canvas>, not per-frame DOM nodes.

Background
----------
Two cards — "View Analyzed Videos/Frames" (viewer, prefix ``va-``) and
"Video Annotator" (annotator, prefix ``anv-``) — originally rendered
companion-CSV annotation timelines by creating **one <div> DOM node per
annotated frame** plus one ``addEventListener`` per node.  With thousands of
annotated frames this caused severe client-side RAM/CPU bloat.

Both were replaced with a single ``<canvas>`` element per bar whose
``_*DrawCanvas`` helper draws one ``fillRect`` call per row — zero DOM nodes
regardless of CSV size.

These tests catch any future regression that reintroduces the DOM-node-per-
frame pattern.

What is checked
---------------
1. **HTML structure** — each timeline section contains a ``<canvas>`` element,
   NOT a ``<div class="fe-timeline-bar">``.
2. **JS: no per-frame createElement** — the ``_anvBuildCsvBars`` and
   ``_vaBuildCsvBars`` function bodies do not contain
   ``document.createElement`` (which would indicate the old pattern).
3. **JS: canvas draw helpers exist** — ``_anvDrawCanvas`` and
   ``_vaDrawCanvas`` / ``_vaRedrawNoteCanvas`` are defined in main.js.
4. **JS: single canvas click handlers** — canvas click is wired via a loop
   or direct ``addEventListener`` on the canvas element itself, not on child
   divs inside a bar container.
5. **JS: no fe-timeline-seg creation** — the class ``fe-timeline-seg`` is not
   created dynamically anywhere inside the two build functions.
"""
from __future__ import annotations

import re
import sys
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO = Path(__file__).parent.parent
_HTML = _REPO / "src" / "templates" / "index.html"
_JS   = _REPO / "src" / "static" / "main.js"

assert _HTML.is_file(), f"Template not found: {_HTML}"
assert _JS.is_file(),   f"main.js not found: {_JS}"

# After the modular HTML refactor, index.html contains only Jinja {% include %}
# directives. Concatenate index.html plus all partial templates so that the
# same ID/tag checks work against the full rendered tree.
_PARTIALS_DIR = _REPO / "src" / "templates" / "partials"
_html_parts = [_HTML.read_text(encoding="utf-8")]
if _PARTIALS_DIR.is_dir():
    for p in sorted(_PARTIALS_DIR.glob("*.html")):
        _html_parts.append(p.read_text(encoding="utf-8"))
_html_text = "\n".join(_html_parts)

_js_text   = _JS.read_text(encoding="utf-8")


# ===========================================================================
# 1. HTML structure checks
# ===========================================================================

class TestHtmlStructure:
    """Canvas elements must exist; legacy fe-timeline-bar divs must be gone."""

    # ── Viewer card (va-) ──────────────────────────────────────────────────

    def test_va_status_canvas_present(self):
        assert 'id="va-status-canvas"' in _html_text, (
            "va-status-canvas <canvas> is missing from index.html. "
            "Do NOT replace it with a <div class='fe-timeline-bar'>."
        )

    def test_va_note_canvas_present(self):
        assert 'id="va-note-canvas"' in _html_text, (
            "va-note-canvas <canvas> is missing from index.html."
        )

    def test_va_no_timeline_bar_div_in_csv_bars(self):
        """The va-csv-bars section must not contain fe-timeline-bar divs."""
        # Extract the va-csv-bars block (from its opening tag to the closing </div>
        # of the outer wrapper).  A simple heuristic: find the block and check it.
        start = _html_text.find('id="va-csv-bars"')
        assert start != -1, "va-csv-bars not found in HTML"
        # Grab the next 2000 characters — enough to cover both bar wraps.
        snippet = _html_text[start:start + 2000]
        assert 'class="fe-timeline-bar"' not in snippet, (
            "fe-timeline-bar div found inside va-csv-bars. "
            "This is the O(n-DOM-nodes) antipattern — use <canvas> instead."
        )

    # ── Annotator card (anv-) ─────────────────────────────────────────────

    def test_anv_status_canvas_present(self):
        assert 'id="anv-status-canvas"' in _html_text, (
            "anv-status-canvas <canvas> is missing from index.html."
        )

    def test_anv_note_canvas_present(self):
        assert 'id="anv-note-canvas"' in _html_text, (
            "anv-note-canvas <canvas> is missing from index.html."
        )

    def test_anv_no_timeline_bar_div_in_csv_bars(self):
        """The anv-csv-bars section must not contain fe-timeline-bar divs."""
        start = _html_text.find('id="anv-csv-bars"')
        assert start != -1, "anv-csv-bars not found in HTML"
        snippet = _html_text[start:start + 1500]
        assert 'class="fe-timeline-bar"' not in snippet, (
            "fe-timeline-bar div found inside anv-csv-bars. "
            "Use <canvas> — not <div class='fe-timeline-bar'>."
        )


# ===========================================================================
# 2. JS: no per-frame createElement inside build functions
# ===========================================================================

def _extract_function_body(js: str, func_name: str, *, search_chars: int = 4000) -> str:
    """
    Return the source text of the first function whose name matches *func_name*.
    Searches forward from the function declaration for a balanced-brace body.
    Returns an empty string if not found.
    """
    pattern = re.compile(
        r'\bfunction\s+' + re.escape(func_name) + r'\s*\(',
    )
    m = pattern.search(js)
    if not m:
        return ""
    start = m.start()
    # Find the opening brace
    brace_pos = js.index("{", start)
    depth = 0
    for i, ch in enumerate(js[brace_pos:brace_pos + search_chars]):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js[brace_pos: brace_pos + i + 1]
    return js[brace_pos: brace_pos + search_chars]


class TestJsNoDomNodesPerFrame:
    """_anvBuildCsvBars and _vaBuildCsvBars must not call document.createElement."""

    def test_anv_build_csv_bars_no_create_element(self):
        body = _extract_function_body(_js_text, "_anvBuildCsvBars")
        assert body, "_anvBuildCsvBars not found in main.js"
        assert "document.createElement" not in body, (
            "_anvBuildCsvBars calls document.createElement — "
            "this creates one DOM node per annotated frame and is the "
            "known performance antipattern. Use _anvDrawCanvas instead."
        )

    def test_va_build_csv_bars_no_create_element(self):
        body = _extract_function_body(_js_text, "_vaBuildCsvBars")
        assert body, "_vaBuildCsvBars not found in main.js"
        assert "document.createElement" not in body, (
            "_vaBuildCsvBars calls document.createElement — "
            "use _vaRedrawNoteCanvas / _vaRedrawStatusCanvas instead."
        )

    def test_anv_build_csv_bars_no_timeline_seg(self):
        body = _extract_function_body(_js_text, "_anvBuildCsvBars")
        assert body, "_anvBuildCsvBars not found in main.js"
        assert "fe-timeline-seg" not in body, (
            "_anvBuildCsvBars creates fe-timeline-seg elements — "
            "this is the O(n) DOM-node antipattern."
        )

    def test_va_build_csv_bars_no_timeline_seg(self):
        body = _extract_function_body(_js_text, "_vaBuildCsvBars")
        assert body, "_vaBuildCsvBars not found in main.js"
        assert "fe-timeline-seg" not in body, (
            "_vaBuildCsvBars creates fe-timeline-seg elements."
        )


# ===========================================================================
# 3. JS: canvas draw helpers are defined
# ===========================================================================

class TestJsCanvasHelpersExist:
    """The canvas rendering helpers must be present in main.js."""

    def test_anv_draw_canvas_defined(self):
        assert "function _anvDrawCanvas(" in _js_text, (
            "_anvDrawCanvas is not defined in main.js. "
            "This function replaces the per-frame DOM loop in _anvBuildCsvBars."
        )

    def test_va_draw_canvas_defined(self):
        assert "function _vaDrawCanvas(" in _js_text, (
            "_vaDrawCanvas is not defined in main.js. "
            "This function replaces the per-frame DOM loop in _vaBuildCsvBars."
        )

    def test_va_redraw_helpers_defined(self):
        assert "_vaRedrawNoteCanvas" in _js_text, (
            "_vaRedrawNoteCanvas is not defined — viewer canvas redraws broken."
        )
        assert "_vaRedrawStatusCanvas" in _js_text, (
            "_vaRedrawStatusCanvas is not defined."
        )

    def test_anv_draw_canvas_uses_fill_rect(self):
        body = _extract_function_body(_js_text, "_anvDrawCanvas")
        assert body, "_anvDrawCanvas not found"
        assert "fillRect" in body, (
            "_anvDrawCanvas does not call ctx.fillRect — "
            "canvas drawing is broken."
        )

    def test_va_draw_canvas_uses_fill_rect(self):
        body = _extract_function_body(_js_text, "_vaDrawCanvas")
        assert body, "_vaDrawCanvas not found"
        assert "fillRect" in body, (
            "_vaDrawCanvas does not call ctx.fillRect."
        )


# ===========================================================================
# 4. JS: canvas click handlers wired directly on canvas elements
# ===========================================================================

class TestJsCanvasClickHandlers:
    """Click-to-jump must be a single listener on the canvas, not on child nodes."""

    def test_anv_canvas_click_handler_present(self):
        # The annotator wires click via a forEach over [anvNoteCanvas, anvStatusCanvas]
        assert "anvNoteCanvas" in _js_text and "anvStatusCanvas" in _js_text, (
            "anvNoteCanvas / anvStatusCanvas references missing from main.js."
        )
        # There should be an addEventListener("click" on the canvas array
        snippet_start = _js_text.find("anvNoteCanvas, anvStatusCanvas")
        if snippet_start == -1:
            snippet_start = _js_text.find("anvStatusCanvas, anvNoteCanvas")
        assert snippet_start != -1 or 'anvNoteCanvas' in _js_text, (
            "Canvas click handler loop not found for annotator canvases."
        )

    def test_va_canvas_click_handler_present(self):
        assert "vaNoteCanvas" in _js_text and "vaStatusCanvas" in _js_text, (
            "vaNoteCanvas / vaStatusCanvas references missing from main.js."
        )


# ===========================================================================
# 5. No lingering fe-timeline-seg creation anywhere in the annotator IIFE
# ===========================================================================

class TestNoTimelineSegCreation:
    """fe-timeline-seg must not be created anywhere for the two timeline bars."""

    def test_no_anv_timeline_seg_anywhere(self):
        # Find the annotator IIFE block and check it contains no fe-timeline-seg creation
        start = _js_text.find("// ── Video Annotator")
        end   = _js_text.find("// ── Video Annotator", start + 1)
        if end == -1:
            end = start + 60_000
        block = _js_text[start:end]
        dyn_matches = [m for m in re.finditer(r'fe-timeline-seg', block)
                       if not _is_in_comment(_js_text, start + m.start())]
        assert not dyn_matches, (
            "fe-timeline-seg appears in the Video Annotator IIFE — "
            "this is the per-frame DOM-node antipattern."
        )


def _is_in_comment(text: str, pos: int) -> bool:
    """Return True if *pos* is inside a // line comment."""
    line_start = text.rfind("\n", 0, pos) + 1
    line = text[line_start:pos]
    return "//" in line
