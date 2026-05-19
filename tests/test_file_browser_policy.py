"""Enforce the file-browser component policy:

- A single canonical factory lives at src/static/js/components/file_browser.js.
- It exports `makeFileBrowser` (named export).
- Its double-click handler does NOT hide the pane (no classList.add("hidden")
  inside the dblclick listener block) — double-clicking adds to queue and
  shows transient feedback while keeping the browser open.
- Each consumer card (analyze.js, viewer.js, annotator.js, postprocess.js)
  imports the canonical factory; none redefines an inline equivalent.

These are deliberately static-analysis (regex over source) because the project
has no JS unit-test runner. The checks are tight enough to catch a future
divergent picker introduced by accident.

Parallel guard to dlc-3D's `tests/test_file_browser_policy.py`.
"""
import re
from pathlib import Path

import pytest

ROOT       = Path(__file__).parent.parent
FB_PATH    = ROOT / "src" / "static" / "js" / "components" / "file_browser.js"
ANALYZE    = ROOT / "src" / "static" / "js" / "analyze.js"
VIEWER     = ROOT / "src" / "static" / "js" / "viewer.js"
ANNOTATOR  = ROOT / "src" / "static" / "js" / "annotator.js"
POSTPROC   = ROOT / "src" / "static" / "js" / "postprocess.js"
POLICY_DOC = ROOT / "docs" / "policies" / "file-browser-component.md"


# Accept BOTH direct calls (`makeFileBrowser(`) and aliased calls
# (`const fb = makeFileBrowser; fb(`).
_IMPORT_RE = re.compile(
    r"import\s*\{[^}]*\bmakeFileBrowser\b[^}]*\}\s*from\s*"
    r"[\"']\./components/file_browser\.js[\"']"
)


def _assert_imports_factory(path: Path):
    src = path.read_text()
    assert _IMPORT_RE.search(src), (
        f"{path.name} must import {{ makeFileBrowser }} from "
        f"./components/file_browser.js"
    )
    # And actually use it (either directly or via a one-line alias).
    used_directly = re.search(r"\bmakeFileBrowser\s*\(", src)
    aliased = re.search(
        r"(?:const|let|var)\s+(\w+)\s*=\s*makeFileBrowser\b", src
    )
    if used_directly:
        return
    if aliased:
        alias = aliased.group(1)
        assert re.search(rf"\b{re.escape(alias)}\s*\(", src), (
            f"{path.name} aliases makeFileBrowser as `{alias}` but never calls it"
        )
        return
    pytest.fail(
        f"{path.name} imports makeFileBrowser but doesn't call it "
        f"(directly or via alias)"
    )


def _assert_no_inline_factory(path: Path, *bad_names: str):
    src = path.read_text()
    assert not re.search(r"\bfunction\s+makeFileBrowser\b", src), (
        f"{path.name} must not define makeFileBrowser locally"
    )
    for bn in bad_names:
        assert f"function {bn}(" not in src, (
            f"{path.name} must not redefine the inline factory `{bn}` — "
            f"use makeFileBrowser from ./components/file_browser.js"
        )


# ─────────────────────────────────────────────────────────────────────
# 1) component exists
# ─────────────────────────────────────────────────────────────────────
def test_canonical_component_exists():
    assert FB_PATH.is_file(), f"missing canonical file browser at {FB_PATH}"


# ─────────────────────────────────────────────────────────────────────
# 2) factory exported
# ─────────────────────────────────────────────────────────────────────
def test_factory_exported():
    src = FB_PATH.read_text()
    assert re.search(r"\bexport\s+function\s+makeFileBrowser\b", src) or \
           re.search(r"\bexport\s*\{[^}]*\bmakeFileBrowser\b[^}]*\}", src), \
        "file_browser.js must export `makeFileBrowser`"


# ─────────────────────────────────────────────────────────────────────
# 3) dblclick handler must not hide the pane (regression guard for the
#    user-reported bug)
# ─────────────────────────────────────────────────────────────────────
def test_dblclick_does_not_hide_pane():
    src = FB_PATH.read_text()
    m = re.search(
        r"addEventListener\(\s*[\"']dblclick[\"']\s*,\s*[^{]*\{(?P<body>.*?)^\s*\}\s*\)",
        src,
        re.DOTALL | re.MULTILINE,
    )
    assert m, "no dblclick listener found in file_browser.js"
    body = m.group("body")
    assert 'classList.add("hidden")' not in body and \
           "classList.add('hidden')" not in body, (
        "dblclick handler must NOT hide the browser pane — double-click should "
        "keep the browser open with a transient 'Added' badge instead"
    )


# ─────────────────────────────────────────────────────────────────────
# 4 + 5) per-consumer imports + no inline factory
#
# Each consumer slice lands in its own refactor commit. Until that commit
# lands, the slice is skipped with a reason that points at the missing
# refactor. The component-level tests (1-3, 6) stay green from commit 1.
# ─────────────────────────────────────────────────────────────────────

def test_analyze_uses_canonical_factory():
    _assert_imports_factory(ANALYZE)
    _assert_no_inline_factory(ANALYZE, "_avMakeEntry")


def test_viewer_uses_canonical_factory():
    _assert_imports_factory(VIEWER)
    _assert_no_inline_factory(VIEWER, "_vaH5MakeEntry")


@pytest.mark.skip(reason="lands with refactor(annotator): use canonical file_browser component")
def test_annotator_uses_canonical_factory():
    _assert_imports_factory(ANNOTATOR)
    _assert_no_inline_factory(ANNOTATOR, "_anvMakeEntry", "_anvClipMakeEntry")


@pytest.mark.skip(reason="lands with refactor(postprocess): use canonical file_browser component")
def test_postprocess_uses_canonical_factory():
    _assert_imports_factory(POSTPROC)
    _assert_no_inline_factory(POSTPROC, "_ppMakeEntry")


# ─────────────────────────────────────────────────────────────────────
# 6) policy doc exists
# ─────────────────────────────────────────────────────────────────────
def test_policy_doc_exists():
    assert POLICY_DOC.is_file(), f"missing policy doc at {POLICY_DOC}"
    text = POLICY_DOC.read_text().lower()
    assert "makefilebrowser" in text or "file_browser.js" in text, \
        "policy doc should reference the canonical component"
