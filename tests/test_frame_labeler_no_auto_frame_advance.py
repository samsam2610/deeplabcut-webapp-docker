"""Regression guard: _flAutoAdvanceBp must not advance the frame when every
BP on the current frame is already labeled. The user owns frame navigation.

This test reads the source of frame_labeler.js and asserts the body of the
_flAutoAdvanceBp function contains no call to _flShowFrame. It's a cheap
static guard against the deleted fall-through being reintroduced.
"""
import re
from pathlib import Path

FRAME_LABELER_JS = (
    Path(__file__).parent.parent / "src" / "static" / "js" / "frame_labeler.js"
)


def _extract_function_body(src: str, fn_name: str) -> str:
    """Return the body (between the outermost { }) of `function fn_name(...)`.

    Walks braces from the opening { to its matching close. Raises AssertionError
    if the function isn't found or the braces don't balance.
    """
    m = re.search(r"function\s+" + re.escape(fn_name) + r"\s*\([^)]*\)\s*\{", src)
    assert m, f"function {fn_name} not found in source"
    start = m.end()              # first char after the opening {
    depth = 1
    i = start
    while i < len(src) and depth:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"unbalanced braces while scanning {fn_name}"
    return src[start : i - 1]    # exclude the closing }


def test_auto_advance_bp_does_not_call_show_frame():
    src = FRAME_LABELER_JS.read_text()
    body = _extract_function_body(src, "_flAutoAdvanceBp")
    assert "_flShowFrame" not in body, (
        "_flAutoAdvanceBp must not call _flShowFrame — the auto-frame-advance "
        "fall-through was reintroduced. Frame navigation belongs to the user."
    )
