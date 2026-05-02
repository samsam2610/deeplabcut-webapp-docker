"""Static template + JS-source assertions for the viewer layered overlay."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
VIEWER_HTML = PARTIALS / "card_viewer.html"
VIEWER_JS = ROOT / "src" / "static" / "js" / "viewer.js"

NEW_IDS = {
    "va-overlay-primary-select",
    "va-overlay-compare-list",
    "va-overlay-add-compare",
    "va-overlay-customize-thresholds",
    "va-overlay-edit-disabled-banner",
    "va-overlay-compare-block",
}

RETAINED_IDS = {  # Browse fallback must keep working
    "va-overlay-h5-path",
    "va-overlay-h5-browse",
    "va-overlay-h5-browser",
    "va-overlay-h5-clear",
}


def _ids_in(file: Path) -> dict[str, int]:
    seen: dict[str, int] = {}
    for m in re.finditer(r'id="([^"]+)"', file.read_text()):
        seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    return seen


def test_new_overlay_ids_present_and_unique():
    seen_global: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen_global[m.group(1)] = seen_global.get(m.group(1), 0) + 1
    for nid in NEW_IDS:
        assert seen_global.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen_global.get(nid, 0)} times across partials"
        )


def test_retained_ids_still_present():
    seen = _ids_in(VIEWER_HTML)
    for nid in RETAINED_IDS:
        assert seen.get(nid, 0) >= 1, f"retained id {nid!r} is missing"


def test_viewer_js_references_new_ids():
    js = VIEWER_JS.read_text()
    for nid in (
        "va-overlay-primary-select",
        "va-overlay-add-compare",
        "va-overlay-compare-list",
        "va-overlay-customize-thresholds",
    ):
        assert nid in js, f"viewer.js does not reference {nid!r}"


def test_viewer_js_uses_layer_model():
    """Sanity: the layer abstraction landed."""
    js = VIEWER_JS.read_text()
    assert "_vaLayers" in js
    assert "_vaPrimary" in js
    assert "_vaIsEditable" in js
    assert "/dlc/viewer/h5-variants" in js


def test_viewer_js_dropped_legacy_globals():
    """Regression: the scalar overlay globals are gone."""
    js = VIEWER_JS.read_text()
    # _vaH5Path is allowed to remain ONLY as a property read (e.g. layer.path);
    # the bare global declaration `let _vaH5Path` must not exist.
    assert "let _vaH5Path" not in js, (
        "the legacy scalar `_vaH5Path` declaration should be removed; "
        "use _vaPrimary().path instead"
    )
    assert "let _vaThreshold" not in js, (
        "the legacy scalar `_vaThreshold` declaration should be removed; "
        "use _vaGlobalThreshold + _vaLayerThreshold(layer) instead"
    )
