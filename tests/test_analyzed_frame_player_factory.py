"""Enforce the analyzed-frame-player factory contract.

- The canonical factory lives at src/static/js/components/analyzed_frame_player.js.
- It exports `makeAnalyzedFramePlayer` (named export).
- inline_analysis.js (once it exists) imports it.
- Soft consumer-count check: until viewer.js migrates, only 1 consumer.

Parallel to tests/test_file_browser_policy.py.
"""
import re
from pathlib import Path

import pytest

ROOT       = Path(__file__).parent.parent
AFP_PATH   = ROOT / "src" / "static" / "js" / "components" / "analyzed_frame_player.js"
INLINE     = ROOT / "src" / "static" / "js" / "inline_analysis.js"

_IMPORT_RE = re.compile(
    r"import\s*\{[^}]*\bmakeAnalyzedFramePlayer\b[^}]*\}\s*from\s*"
    r"[\"']\./components/analyzed_frame_player\.js[\"']"
)


def test_canonical_factory_exists():
    assert AFP_PATH.is_file(), f"missing factory at {AFP_PATH}"


def test_factory_exports_make_analyzed_frame_player():
    src = AFP_PATH.read_text()
    assert re.search(r"export\s+function\s+makeAnalyzedFramePlayer\b", src), (
        "analyzed_frame_player.js must export `makeAnalyzedFramePlayer` "
        "as a named export"
    )


def test_consumer_count_soft():
    """Until viewer.js migrates, exactly one consumer (inline_analysis.js).

    This test passes either way today; it documents intent and will be
    tightened when the migration lands.
    """
    if not INLINE.is_file():
        pytest.skip("inline_analysis.js not yet present (Phase 2)")
    src = INLINE.read_text()
    assert _IMPORT_RE.search(src), (
        "inline_analysis.js must import makeAnalyzedFramePlayer from "
        "./components/analyzed_frame_player.js"
    )
