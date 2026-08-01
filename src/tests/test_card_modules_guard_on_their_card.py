"""Guard: a card module must not wire itself when its card is not on the page.

main.js now isolates module failures (see test_main_js_resilient_bootstrap.py),
but a module that throws still logs an error and leaves its card half-wired.
The three 2D-only modules below run on /dlc-3d/, where their cards are absent,
and each threw on a null element.

The fix is one presence check per module rather than ~50 null guards: the whole
body is wrapped in `if (<element on this card>) { ... }`. The body was already
indented (a leftover from these modules' old IIFE wrappers), so the wrap costs
two lines and no re-indentation.

Note frame_labeler.js keys on `#fl-canvas`, NOT `#frame-labeler-card`: the 3D
page ships dlc-3D's own frame-labeler card which REUSES the card id but has
none of the 2D inner elements. Keying on the card id there would not guard.
"""
import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "static" / "js"

# module -> the element id whose absence means "this card is not on this page"
GUARDED = {
    "frame_labeler.js": "fl-canvas",
    "viewer.js": "view-analyzed-card",
    "inline_analysis_player.js": "inline-analysis-card",
}


def test_each_module_wraps_its_body_in_a_presence_check():
    for name, element_id in GUARDED.items():
        src = (JS_DIR / name).read_text()
        assert re.search(
            rf'if\s*\(\s*document\.getElementById\(\s*["\']{re.escape(element_id)}["\']\s*\)\s*\)\s*\{{',
            src,
        ), f"{name} must wrap its body in a presence check on #{element_id}"


def test_the_guard_sits_before_any_element_lookup():
    """A lookup outside the guard is fine, but a DEREFERENCE outside it is the
    bug we are fixing — the guard has to come first."""
    for name in GUARDED:
        src = (JS_DIR / name).read_text()
        guard = re.search(r"if\s*\(\s*document\.getElementById\(", src)
        assert guard, f"{name} has no guard"
        head = src[: guard.start()]
        # Only imports, "use strict" and comments may precede the guard.
        for line in head.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*", "*/")):
                continue
            assert stripped.startswith(("import ", '"use strict"', "'use strict'")), \
                f"{name}: code runs before the guard: {stripped[:70]}"


def test_braces_stay_balanced_after_the_wrap():
    """Cheap structural check that the closing brace was actually added."""
    for name in GUARDED:
        src = (JS_DIR / name).read_text()
        assert src.count("{") == src.count("}"), f"{name}: unbalanced braces"
