"""Source-guard test: jobs.js `_renderRail` renders the aggregate-batch
`j.stage` sub-label (Contract B). No JS runtime — a plain source assertion."""
from __future__ import annotations

from pathlib import Path

_JOBS_JS = Path(__file__).parent.parent / "src" / "static" / "js" / "jobs.js"


def _render_rail_body() -> str:
    src = _JOBS_JS.read_text()
    start = src.index("function _renderRail")
    # slice to the next top-level function decl after _renderRail
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


def test_render_rail_references_stage():
    body = _render_rail_body()
    assert "j.stage" in body, "_renderRail must reference j.stage"
    assert ".stage" in body


def test_render_rail_escapes_stage():
    body = _render_rail_body()
    assert "_escapeHtml(j.stage)" in body, \
        "stage must be rendered through _escapeHtml"
