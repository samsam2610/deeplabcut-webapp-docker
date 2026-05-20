"""Static-source guards for the "Lock body-part selection" feature.

The lock is a UI checkbox (`fl-lock-bp`) plus a `_flAutoAdvanceBp` early-return
and an `L` keyboard shortcut. These tests scan the template + JS source so
they run without the browser/fixture stack.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
FRAME_LABELER_JS = REPO_ROOT / "src" / "static" / "js" / "frame_labeler.js"
FRAME_LABELER_HTML = (
    REPO_ROOT / "src" / "templates" / "partials" / "card_frame_labeler.html"
)
LOCK_ID = "fl-lock-bp"


def _extract_function_body(src: str, fn_name: str) -> str:
    """Return the body (between the outermost { }) of `function fn_name(...)`.

    Walks braces from the opening { to its matching close. Raises AssertionError
    if the function isn't found or the braces don't balance.

    NOTE: the brace walk does not skip string, template, or regex literals.
    Safe for `_flAutoAdvanceBp`, whose body contains none. Revisit if the
    target function ever grows literals that contain `{` or `}`.
    """
    m = re.search(r"function\s+" + re.escape(fn_name) + r"\s*\([^)]*\)\s*\{", src)
    assert m, f"function {fn_name} not found in source"
    start = m.end()
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
    return src[start : i - 1]


def test_lock_bp_checkbox_in_template():
    html = FRAME_LABELER_HTML.read_text()
    # Look for an <input type="checkbox" ... id="fl-lock-bp"> (any attribute order).
    pattern = re.compile(
        r"<input\b[^>]*\btype\s*=\s*\"checkbox\"[^>]*\bid\s*=\s*\""
        + re.escape(LOCK_ID)
        + r"\"|<input\b[^>]*\bid\s*=\s*\""
        + re.escape(LOCK_ID)
        + r"\"[^>]*\btype\s*=\s*\"checkbox\"",
        re.IGNORECASE,
    )
    assert pattern.search(html), (
        f"Expected <input type=\"checkbox\" id=\"{LOCK_ID}\"> in "
        f"card_frame_labeler.html (the Lock BP toggle row)."
    )


def test_auto_advance_bp_respects_lock():
    src = FRAME_LABELER_JS.read_text()
    body = _extract_function_body(src, "_flAutoAdvanceBp")
    # The function must reference the lock element variable (flLockBp) — the
    # JS lookup uses getElementById("fl-lock-bp") and stashes it in flLockBp.
    assert "flLockBp" in body, (
        "_flAutoAdvanceBp must reference the lock checkbox (flLockBp) so it "
        "can short-circuit auto-advance when the lock is on."
    )
    assert "return" in body, (
        "_flAutoAdvanceBp must contain a `return` early-exit so the cycle "
        "loop is bypassed when the lock is on."
    )


def test_keybinding_l_toggles_lock():
    src = FRAME_LABELER_JS.read_text()
    # Assert the exact form used in the implementation.
    assert 'e.key.toLowerCase() === "l"' in src, (
        "Expected a keydown branch matching the L key case-insensitively "
        "via `e.key.toLowerCase() === \"l\"`."
    )
    # And that the branch toggles `.checked` on the lock element.
    assert "flLockBp.checked = !flLockBp.checked" in src, (
        "Expected the L-key branch to toggle flLockBp.checked."
    )
