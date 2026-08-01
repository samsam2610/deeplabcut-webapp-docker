"""Guard: main.js must load its modules resiliently, not via static imports.

Static ES imports evaluate sequentially and share fate: the first module that
throws while evaluating silently prevents every module imported after it from
running at all. Only one console line appears, and the affected cards' buttons
render but are never wired.

That is exactly what happened on /dlc-3d/. The page ships dlc-3D's own frame
labeler card, so `#fl-canvas` is absent; frame_labeler.js dereferenced it at
module scope, threw, and the eleven modules imported after it — including
tracked_files_panel.js — never executed. The button existed and did nothing.

A survey on 2026-08-01 counted ~208 module-scope dereferences of possibly-null
getElementById results across the chain, so guarding them individually is not a
fix. Instead main.js isolates each module: one failure is logged loudly and the
rest still load.
"""
import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "static" / "js"
MAIN_JS = JS_DIR / "main.js"


def _src():
    return MAIN_JS.read_text()


def test_main_js_has_no_bare_static_imports_of_card_modules():
    """A single static import re-introduces shared fate for that module."""
    statics = re.findall(r"^import\s+'\./([\w./]+)';", _src(), re.M)
    assert statics == [], (
        "these are statically imported and will silently kill the rest of the "
        f"chain if they throw: {statics}"
    )


def test_each_module_is_loaded_inside_its_own_try_catch():
    s = _src()
    assert "await import(" in s, "modules must load via dynamic import()"
    assert "try {" in s and "catch" in s, "each import must be isolated"
    assert "console.error" in s, "a failed module must be reported, not swallowed"


def test_every_module_file_listed_actually_exists():
    s = _src()
    mods = re.findall(r"['\"](\./[\w./]+\.js)['\"]", s)
    assert len(mods) >= 15, f"expected the full card-module list, found {len(mods)}"
    for m in mods:
        assert (JS_DIR / m[2:]).is_file(), f"main.js lists a missing module: {m}"


def test_load_order_dependencies_are_preserved():
    """log_stream.js must still come before gpu_monitor.js, and state/api first."""
    mods = re.findall(r"['\"]\./([\w./]+\.js)['\"]", _src())
    assert mods.index("log_stream.js") < mods.index("gpu_monitor.js")
    assert mods.index("state.js") < mods.index("dlc_project.js")
    assert mods.index("dlc_project.js") < mods.index("anipose.js")
