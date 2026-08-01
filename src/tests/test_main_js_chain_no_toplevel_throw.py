"""Guard: no module in the main.js import chain may throw at module scope.

main.js imports ~18 modules in sequence. ES module evaluation is sequential, so
if ANY of them throws while evaluating, every module imported after it is
silently skipped — no error surfaces except one console line.

This bit us on 2026-08-01: frame_labeler.js did

    const flCanvas = document.getElementById("fl-canvas");
    const flCtx    = flCanvas.getContext("2d");     // <-- throws when absent

`#fl-canvas` does not exist on /dlc-3d/ (that page ships dlc-3D's own frame
labeler card), so the chain died at import #9 and the nine modules after it —
including tracked_files_panel.js — never ran. Their buttons rendered but were
never wired.

The codebase's own idiom for this is the guarded ternary, e.g.
    test_set_picker.js:17  const tsCtx = tsCanvas ? tsCanvas.getContext("2d") : null;
"""
import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "static" / "js"
MAIN_JS = JS_DIR / "main.js"

# `const x = y.getContext(...)` — y is captured so we can check where it came from.
# Leading indent is capped at 4 because these modules keep their module-scope
# statements at 0–4 spaces (a leftover from their old IIFE wrappers); anything
# more deeply indented is inside a function and therefore only runs on demand,
# when the card it belongs to is actually on the page.
_GETCONTEXT = re.compile(
    r"^[ \t]{0,4}(?:const|let|var)\s+\w+\s*=\s*(\w+)\.getContext\(", re.M)


def _element_consts(src):
    """Names bound directly to a document.getElementById(...) result.

    Only these are a hazard: a function parameter named `canvas` is the
    caller's problem and is typically guarded by an `if (!canvas) return;`.
    """
    return set(re.findall(
        r"(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\(", src))


def _chain_modules():
    """Every module main.js loads, in order.

    main.js lists them in a MODULES array and imports them dynamically — see
    test_main_js_resilient_bootstrap.py for why it is not static imports.
    """
    src = MAIN_JS.read_text()
    names = re.findall(r"['\"]\./([\w./]+\.js)['\"]", src)
    return [JS_DIR / n for n in names]


def test_main_js_chain_is_discoverable():
    mods = _chain_modules()
    assert len(mods) >= 10, f"parsed only {len(mods)} imports from main.js"
    for m in mods:
        assert m.is_file(), f"main.js imports a missing module: {m}"


def test_no_module_in_the_chain_calls_getContext_unguarded_at_module_scope():
    offenders = []
    for mod in _chain_modules():
        src = mod.read_text()
        elements = _element_consts(src)
        for m in _GETCONTEXT.finditer(src):
            receiver, line = m.group(1), m.group(0)
            if receiver not in elements:
                continue          # a parameter or local, not a page element
            # A guarded form reads `x ? x.getContext(...) : null`.
            if "?" not in line:
                offenders.append(f"{mod.name}: {line.strip()}")
    assert not offenders, (
        "module-scope getContext on a possibly-null element kills the whole "
        "main.js import chain on pages lacking that canvas:\n  "
        + "\n  ".join(offenders)
    )
