"""Regression guards for the View-Analyzed card after compare-layer removal.

Mirrors tests/test_inline_analysis_ui_isolation.py's compare-absent
section but for src/templates/partials/card_viewer.html and
src/static/js/viewer.js.

See docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md.
"""
from __future__ import annotations

from pathlib import Path

ROOT        = Path(__file__).resolve().parents[1]
PARTIALS    = ROOT / "src" / "templates" / "partials"
CARD        = PARTIALS / "card_viewer.html"
VIEWER_JS   = ROOT / "src" / "static" / "js" / "viewer.js"


_FORBIDDEN_HTML_FRAGMENTS = (
    "va-overlay-compare-block",
    "va-overlay-add-compare",
    "va-overlay-add-compare-empty-hint",
    "va-overlay-compare-list",
    "va-overlay-edit-disabled-banner",
    "va-overlay-customize-thresholds",
    "va-overlay-primary-threshold-slot",
    "va-overlay-primary-row",
    "va-overlay-primary-visible",
    "va-overlay-primary-shape",
    "va-overlay-primary-label",
    "Comparison layers",
    "Customize threshold per layer",
)


_FORBIDDEN_JS_SYMBOLS = (
    "_vaCompare(",
    "_vaIsEditable",
    "_vaPerLayerThresholds",
    "_vaLayerThreshold",
    "_vaRenderCompareRows",
    "_vaAddCompare",
    "_vaRemoveCompare",
    "_vaRefreshAddComparisonOptions",
    "_vaRenderPrimaryThresholdInline",
    "_vaUpdateEditDisabledBanner",
    "_vaSyncPrimaryRow",
)


# ─── markup invariants ───────────────────────────────────────────────


def test_card_partial_has_no_compare_layer_markup():
    html = CARD.read_text()
    for frag in _FORBIDDEN_HTML_FRAGMENTS:
        assert frag not in html, (
            f"forbidden compare-layer markup reintroduced: {frag!r}"
        )


def test_card_partial_keeps_primary_select_and_threshold():
    """Primary-layer dropdown + global threshold slider + marker-size +
    body-part chips MUST stay — these are the surfaces the user still
    relies on after compare removal.
    """
    html = CARD.read_text()
    for needed in (
        'id="va-overlay-primary-select"',
        'id="va-overlay-h5-path"',
        'id="va-overlay-h5-browse"',
        'id="va-overlay-threshold"',
        'id="va-overlay-marker-size"',
        'id="va-bp-chips"',
        'id="va-marker-edit-banner"',
    ):
        assert needed in html, f"required surface missing from card_viewer: {needed!r}"


# ─── JS invariants ───────────────────────────────────────────────────


def test_viewer_js_has_no_compare_layer_symbols():
    src = VIEWER_JS.read_text()
    for sym in _FORBIDDEN_JS_SYMBOLS:
        assert sym not in src, (
            f"forbidden compare-layer symbol reintroduced: {sym!r}"
        )


def test_viewer_js_uses_global_threshold_directly():
    src = VIEWER_JS.read_text()
    assert "_vaGlobalThreshold" in src
    # state init + 3 builders (_vaPoseCacheKey, _vaFetchPosesForFrame, _vaPrefetchOne)
    assert src.count("_vaGlobalThreshold") >= 4, (
        "expected >=4 uses of _vaGlobalThreshold; if fewer, the "
        "_vaLayerThreshold->_vaGlobalThreshold collapse missed a call site"
    )


def test_viewer_js_keeps_primary_apply_path():
    """_vaApplyPrimaryFromSelect must still:
      - clear _vaLayers and push a fresh primary
      - load layer info + edit cache
      - trigger _vaLoadFrame when overlay enabled
    """
    src = VIEWER_JS.read_text()
    assert "_vaApplyPrimaryFromSelect" in src
    assert "_vaLayers.length = 0" in src
    assert "_vaSetPrimaryLayer" in src
    assert "_vaLoadLayerInfo" in src
    assert "_vaLoadEditCacheForPrimary" in src


def test_viewer_js_keeps_marker_edit_save_path():
    """Save/Discard/Clear-Frame editing surfaces stay wired up."""
    src = VIEWER_JS.read_text()
    assert "va-save-adjustments-btn" in src
    assert "va-discard-adjustments-btn" in src
    assert "va-clear-frame-btn" in src
    assert "/dlc/viewer/save-marker-edits" in src
