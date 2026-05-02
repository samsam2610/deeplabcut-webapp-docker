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


NEW_FLOW_IDS = {
    "va-browse-hide-no-h5",
    "va-overlay-add-compare-empty-hint",
    "va-play-step",
}


def test_new_flow_ids_present_and_unique():
    seen_global: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen_global[m.group(1)] = seen_global.get(m.group(1), 0) + 1
    for nid in NEW_FLOW_IDS:
        assert seen_global.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen_global.get(nid, 0)} times across partials"
        )


def test_viewer_js_uses_dir_with_h5_route():
    js = VIEWER_JS.read_text()
    assert "/dlc/viewer/dir-with-h5" in js, (
        "viewer.js must consume /dir-with-h5 instead of /fs/ls for the Browse list"
    )


def test_viewer_js_pick_best_primary_helper_present():
    js = VIEWER_JS.read_text()
    assert "_vaPickBestPrimary" in js, (
        "auto-latest selection helper _vaPickBestPrimary must be defined"
    )


def test_viewer_js_primary_swap_clears_layers():
    """Regression: primary swap must reset _vaLayers, not just push a new primary."""
    js = VIEWER_JS.read_text()
    # The new _vaApplyPrimaryFromSelect contains "_vaLayers.length = 0".
    assert "_vaLayers.length = 0" in js, (
        "_vaApplyPrimaryFromSelect must explicitly empty _vaLayers before "
        "pushing the new primary"
    )


def test_viewer_js_paint_barrier_present():
    """Regression: _vaLoadFrame must await an rAF barrier so the play loop
    never advances mid-render."""
    js = VIEWER_JS.read_text()
    assert "new Promise(requestAnimationFrame)" in js, (
        "_vaLoadFrame must await `new Promise(requestAnimationFrame)` before "
        "the prefetch step"
    )


def test_viewer_js_play_step_helper_present():
    js = VIEWER_JS.read_text()
    assert "_vaPlayStep" in js, "_vaPlayStep helper must exist"
    assert "va-play-step" in js, "viewer.js must read the va-play-step input"


NEW_TWEAK_IDS = {
    "va-overlay-primary-row",
    "va-overlay-primary-visible",
    "va-overlay-primary-shape",
    "va-overlay-primary-label",
    "va-play-fps",
    "va-curation-toggle",
    "va-curation-controls",
}


def test_new_tweak_ids_present_and_unique():
    seen_global: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen_global[m.group(1)] = seen_global.get(m.group(1), 0) + 1
    for nid in NEW_TWEAK_IDS:
        assert seen_global.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen_global.get(nid, 0)} times across partials"
        )


def test_viewer_js_diamond_replaces_circle_open():
    js = VIEWER_JS.read_text()
    assert "_drawDiamond" in js, "filled diamond primitive must be defined"
    assert '"diamond"' in js, '_SHAPE_ORDER / _SHAPE_FN must include "diamond"'
    assert "_drawCircleOpen" not in js, "_drawCircleOpen must be removed"
    assert '"circle-open"' not in js, '"circle-open" slot must be gone from _SHAPE_ORDER'


def test_viewer_js_playback_fps_helpers_present():
    js = VIEWER_JS.read_text()
    assert "_vaPlaybackFps" in js, "_vaPlaybackFps helper must exist"
    assert "_vaPlayDelayMs" in js, "_vaPlayDelayMs helper must exist"
    assert "va-play-fps" in js, "viewer.js must read the va-play-fps input"
    # The play loop MUST consume _vaPlayDelayMs(); the legacy `1000 / _vaFps`
    # arithmetic in the play loop is gone.
    assert "1000 / _vaFps) - elapsed" not in js, (
        "play loop must use _vaPlayDelayMs(), not 1000 / _vaFps"
    )


def test_viewer_js_atomic_swap_pattern_present():
    """Regression: _vaLoadFrame must preload the image and pose-fetch in
    parallel and only commit both atomically."""
    js = VIEWER_JS.read_text()
    assert "Promise.all([imgReady, posesReady])" in js, (
        "_vaLoadFrame must await Promise.all([imgReady, posesReady]) before "
        "swapping the visible image"
    )


def test_viewer_js_curation_toggle_handler_present():
    js = VIEWER_JS.read_text()
    assert "va-curation-toggle" in js
    assert "va-curation-controls" in js


def test_viewer_js_primary_visibility_handler_present():
    js = VIEWER_JS.read_text()
    assert "va-overlay-primary-visible" in js
    assert "_vaSyncPrimaryRow" in js, "_vaSyncPrimaryRow helper must exist"
