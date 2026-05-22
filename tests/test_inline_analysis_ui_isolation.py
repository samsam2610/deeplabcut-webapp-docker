"""Static-template + JS-source assertions for the Inline Analysis card.

After multiple iterations of fix-on-fix, the card's player was rebuilt
as a verbatim clone of viewer.js with `va-*` → `ia-*` renamed (see
src/static/js/inline_analysis_player.js). The cloned player gives us
the same proven marker-render path View-Analyzed uses; this test file
guards the clone's invariants.

No JS runtime here — these tests parse the template + JS files directly.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT      = Path(__file__).resolve().parents[1]
PARTIALS  = ROOT / "src" / "templates" / "partials"
JS_ROOT   = ROOT / "src" / "static" / "js"
CARD      = PARTIALS / "card_inline_analysis.html"
PLAYER_JS = JS_ROOT / "inline_analysis_player.js"


# ─── basic existence / wiring ────────────────────────────────────────


def test_card_partial_exists():
    assert CARD.is_file()
    txt = CARD.read_text()
    assert 'id="inline-analysis-card"' in txt
    assert 'id="btn-close-inline-analysis"' in txt
    # Analysis params block
    for needed_id in [
        "ia-shuffle", "ia-trainingsetindex", "ia-snapshot",
        "ia-batch-size", "ia-frames-per-click", "ia-keep-warm-seconds",
        "ia-save-csv", "ia-btn-analyze-range", "ia-last-run-status",
        "ia-warm-indicator", "ia-refresh-snapshots",
    ]:
        assert f'id="{needed_id}"' in txt, f"missing analysis-params id {needed_id!r}"
    # Player + overlay + marker-edit + curation surfaces inherited from viewer
    for needed_id in [
        "ia-player-section", "ia-frame-img", "ia-overlay-canvas",
        "ia-overlay-toggle", "ia-overlay-threshold", "ia-overlay-marker-size",
        "ia-marker-edit-controls",
        "ia-extract-frame-btn", "ia-add-to-dataset-btn", "ia-batch-add-btn",
    ]:
        assert f'id="{needed_id}"' in txt, f"missing inherited id {needed_id!r}"


def test_index_includes_partial():
    idx = (ROOT / "src" / "templates" / "index.html").read_text()
    assert "partials/card_inline_analysis.html" in idx


def test_main_js_loads_new_player_module():
    main_js = (JS_ROOT / "main.js").read_text()
    assert "./inline_analysis_player.js" in main_js, (
        "main.js must import the cloned player module"
    )
    # The legacy modules MUST be gone — they're the source of the bugs.
    assert "./inline_analysis.js" not in main_js, (
        "main.js must NOT import the old inline_analysis.js controller; "
        "all logic is now in inline_analysis_player.js"
    )
    assert "analyzed_frame_player" not in main_js


# ─── proof that we cloned viewer.js verbatim ─────────────────────────


def test_cloned_player_is_viewer_js_with_prefix_renamed():
    """The cloned player must use the EXACT identifier patterns viewer.js
    uses, just with va- → ia- rename. If someone tries to "improve" the
    clone away from viewer.js's proven path, this guard catches it.
    """
    src = PLAYER_JS.read_text()
    # Canonical viewer-pattern symbols that must appear in the clone
    for sym in [
        "iaFrameImg",          # frame display
        "iaOverlayCanvas",     # marker canvas
        "iaOverlayToggle",     # overlay enable checkbox
        "_iaCurrentFrame",     # frame state
        "_iaCurrentPoses",     # the working draw buffer viewer uses
        "_iaDiscoverVariants", # h5 variant scanner
        "_iaOverlayEnabled",
    ]:
        assert sym in src, f"clone missing viewer-pattern symbol {sym!r}"
    # No va- prefix should leak (other than acceptable strings like 'var')
    leaks = re.findall(r"\bva[A-Z][A-Za-z0-9_]*", src)
    leaks = [s for s in leaks if s not in {"valArg"}]  # whitelist if needed
    assert not leaks, f"unrenamed va* identifiers found in clone: {set(leaks)}"


def test_player_uses_canonical_server_endpoints():
    """The cloned player MUST hit the same endpoints viewer.js hits:
    /annotate/video-frame/<n>, /dlc/viewer/frame-poses-batch,
    /dlc/viewer/h5-variants. Earlier hand-coded paths invented bogus
    URLs that 404'd silently — this guard prevents that regression.
    """
    src = PLAYER_JS.read_text()
    assert "/dlc/viewer/frame-poses-batch" in src
    assert "/dlc/viewer/h5-variants" in src
    # Made-up endpoints that previous drafts used:
    for bad in [
        "/annotate/frame?",
        "/dlc/viewer/h5-pose-window",
    ]:
        assert bad not in src, f"clone references known-bad endpoint {bad!r}"


# ─── analysis dispatch behaviour ─────────────────────────────────────


def test_player_has_analyze_dispatch_block():
    """The bottom of inline_analysis_player.js must contain the analysis
    dispatch IIFE — Analyze button click, /session/start, /range submit,
    /range/status polling, and the done-handler that re-discovers h5
    variants + ticks the overlay toggle so markers appear automatically.
    """
    src = PLAYER_JS.read_text()
    assert "ia-btn-analyze-range" in src
    assert "/dlc/project/inline-analysis/range" in src
    assert "/dlc/project/inline-analysis/session/start" in src
    assert "/dlc/project/inline-analysis/range/status" in src
    # On done: re-discover variants AND tick overlay-toggle. Both are required.
    assert "_iaDiscoverVariants" in src
    assert (
        'iaOverlayToggle.checked = true' in src
        or 'iaOverlayToggle.checked=true' in src
    ), "done-handler must auto-tick ia-overlay-toggle"
    assert 'new Event("change"' in src, (
        "done-handler must dispatch a change event so the overlay-toggle "
        "listener actually fires"
    )


def test_player_snapshot_picker_mirrors_analyze_card_format():
    """Snapshot dropdown must show the same iter/sh format as Analyze."""
    src = PLAYER_JS.read_text()
    assert "/dlc/project/snapshots" in src
    assert "latest_rel_path" in src   # default-Latest option
    assert "iter " in src             # iter N · sh M formatting


# ─── card-open behavior ──────────────────────────────────────────────


def test_card_opens_without_hiding_other_cards():
    """Cloned from viewer.js — should toggle its own .hidden only."""
    src = PLAYER_JS.read_text()
    assert "hideAllOtherCards" not in src
    # Must use scrollIntoView (viewer's pattern)
    assert "scrollIntoView" in src


# ─── canvas + spinner regressions ────────────────────────────────────


def test_overlay_canvas_does_not_intercept_pointer_events():
    """Regression guard from a prior fix — the canvas must declare
    pointer-events:none AND width/height 100%. Cloned from card_viewer
    which always set these correctly.
    """
    html = CARD.read_text()
    m = re.search(r'<canvas[^>]*id="ia-overlay-canvas"[^>]*>', html)
    assert m, "ia-overlay-canvas element must exist"
    tag = m.group(0)
    assert "pointer-events:none" in tag
    assert "width:100%" in tag and "height:100%" in tag


def test_no_top_level_frame_spinner_pushing_layout():
    """A previous draft had `<div id="ia-frame-spinner">Loading…</div>`
    in normal flow that displaced everything below it on every frame
    load. The cloned viewer uses the same id but with absolute positioning
    inside the video wrap (no layout shift). Just sanity-check that any
    spinner is INSIDE the player-img wrap.
    """
    html = CARD.read_text()
    if 'id="ia-frame-spinner"' in html:
        # If present, it must be inside ia-video-wrap (viewer's pattern)
        wrap_pos = html.find('id="ia-video-wrap"')
        spin_pos = html.find('id="ia-frame-spinner"')
        wrap_end = html.find("</div>", wrap_pos)
        assert wrap_pos < spin_pos < wrap_end, (
            "ia-frame-spinner, if present, must live inside ia-video-wrap"
        )


# ─── worker-side dense-ify (the real symptom from session 2026-05-20) ─


def test_worker_dense_ifies_h5_for_positional_consumers():
    """Server side: _run_range must write a DENSE h5 (rows 0..max with
    NaN for unanalyzed) so /dlc/viewer/frame-poses-batch's positional
    `poses_np[fn]` lookup works. Sparse h5 = silent zero markers.
    """
    tasks_py = (ROOT / "src" / "dlc" / "tasks.py").read_text()
    assert "df_merge.reindex(_ia_pd.RangeIndex(" in tasks_py
    assert "dense = existing.reindex" in tasks_py, (
        "_run_range's full-skip branch must self-heal pre-existing sparse h5s"
    )


# ─── inline-analysis-specific UX rules ───────────────────────────────


def test_hide_no_h5_unchecked_by_default():
    """Unlike View-Analyzed (which defaults to hide-no-h5 checked), the
    Inline Analysis card must default UNCHECKED — the whole point of
    the card is to PRODUCE an h5 from a video that doesn't have one yet.
    """
    html = CARD.read_text()
    m = re.search(r'<input[^>]*id="ia-browse-hide-no-h5"[^>]*>', html)
    assert m, "ia-browse-hide-no-h5 checkbox must exist"
    tag = m.group(0)
    assert " checked" not in tag, (
        "ia-browse-hide-no-h5 must default UNCHECKED (opposite of View-Analyzed)"
    )


# ─── compare-layer + per-layer-threshold regression guards ───────────


_FORBIDDEN_HTML_FRAGMENTS = (
    "ia-overlay-compare-block",
    "ia-overlay-add-compare",
    "ia-overlay-add-compare-empty-hint",
    "ia-overlay-compare-list",
    "ia-overlay-edit-disabled-banner",
    "ia-overlay-customize-thresholds",
    "ia-overlay-primary-threshold-slot",
    "ia-overlay-primary-row",
    "ia-overlay-primary-visible",
    "ia-overlay-primary-shape",
    "ia-overlay-primary-label",
    "Comparison layers",
    "Customize threshold per layer",
)


_FORBIDDEN_JS_SYMBOLS = (
    "_iaCompare(",
    "_iaIsEditable",
    "_iaPerLayerThresholds",
    "_iaLayerThreshold",
    "_iaRenderCompareRows",
    "_iaAddCompare",
    "_iaRemoveCompare",
    "_iaRefreshAddComparisonOptions",
    "_iaRenderPrimaryThresholdInline",
    "_iaUpdateEditDisabledBanner",
    "_iaSyncPrimaryRow",
)


def test_card_partial_has_no_compare_layer_markup():
    """Compare-layer + customize-threshold DOM ids must NOT reappear in
    the inline-analysis partial. See
    docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md.
    """
    html = CARD.read_text()
    for frag in _FORBIDDEN_HTML_FRAGMENTS:
        assert frag not in html, (
            f"forbidden compare-layer markup reintroduced: {frag!r}"
        )


def test_player_js_has_no_compare_layer_symbols():
    """Compare-layer JS functions + per-layer-threshold state must NOT
    reappear in inline_analysis_player.js.
    """
    src = PLAYER_JS.read_text()
    for sym in _FORBIDDEN_JS_SYMBOLS:
        assert sym not in src, (
            f"forbidden compare-layer symbol reintroduced: {sym!r}"
        )


def test_player_js_uses_global_threshold_directly():
    """After collapse of _iaLayerThreshold(layer), every pose-fetch
    URL builder must read _iaGlobalThreshold directly. No per-layer
    threshold getter calls remain.
    """
    src = PLAYER_JS.read_text()
    assert "_iaGlobalThreshold" in src, (
        "_iaGlobalThreshold must still drive the threshold query parameter"
    )
    # Sanity: the three pose-cache / fetch / prefetch builders should all
    # reference _iaGlobalThreshold rather than the removed helper.
    assert src.count("_iaGlobalThreshold") >= 4, (
        "expected >=4 uses of _iaGlobalThreshold (state init + 3 builders); "
        "if fewer, the helper-collapse step probably missed a call site"
    )


def test_section_order_browser_then_params_then_player():
    """Layout order (user request 2026-05-21): file browser (Source tabs) →
    Analysis Parameters → Player section."""
    html = CARD.read_text()
    i_tabs   = html.find("<!-- Source tabs -->")
    i_params = html.find("Analysis Parameters")
    i_player = html.find("Player section")
    assert 0 < i_tabs < i_params < i_player, (
        "order must be file browser → analysis params → player viewer"
    )


def test_analyze_button_matches_start_analysis_style():
    """The inline Analyze button mirrors the Analyze-card 'Start Analysis'
    button: btn-create class, outline play-triangle SVG, static label."""
    html = CARD.read_text()
    m = re.search(r'<button[^>]*id="ia-btn-analyze-range".*?</button>', html, re.S)
    assert m, "ia-btn-analyze-range button not found"
    btn = m.group(0)
    assert "btn-create" in btn, "must adopt the btn-create accent style"
    assert "<svg" in btn and 'points="5 3 19 12 5 21 5 3"' in btn, \
        "must include the outline play-triangle icon"
    assert "Start Analysis" in btn, "label must read 'Start Analysis'"
    assert "width:100%" not in btn, "drop full-width so it matches the compact reference"


def test_player_does_not_rewrite_analyze_button_text():
    """After the restyle, nothing may overwrite the Analyze button's
    textContent (it would wipe the SVG icon). The old dynamic label is gone."""
    src = PLAYER_JS.read_text()
    assert "_iaSyncLabel" not in src, "dynamic label fn must be removed"
    assert "iaBtnAnalyze.textContent" not in src, \
        "nothing may set the Analyze button textContent"


def test_init_button_shows_file_exists_note_when_initialized():
    """When the analysis file already exists the Init button is disabled and
    its label reads 'Analysis file exists'."""
    src = PLAYER_JS.read_text()
    assert "Analysis file exists" in src, "disabled-state label must be 'Analysis file exists'"
    assert "Analysis file ready" not in src, "old 'ready' wording must be replaced"


def test_finalize_minicard_present_after_curation():
    html = CARD.read_text()
    for needed in ["ia-finalize-toggle", "ia-finalize-controls", "ia-finalize-start",
                   "ia-finalize-count", "ia-finalize-add-btn", "ia-finalize-status"]:
        assert f'id="{needed}"' in html, f"missing {needed!r}"
    assert html.find('id="ia-curation-panel"') < html.find('id="ia-finalize-toggle"'), \
        "finalize panel must come after the curation panel"


def test_marker_edit_controls_moved_below_marker_list():
    html = CARD.read_text()
    assert 'id="ia-marker-edit-banner"' not in html, "old top banner must be removed"
    assert 'id="ia-marker-edit-controls"' in html, "relocated controls row must exist"
    pos_list  = html.find('id="ia-bp-list-wrap"')
    pos_ctrls = html.find('id="ia-marker-edit-controls"')
    pos_curil = html.find('id="ia-curation-panel"')
    assert 0 < pos_list < pos_ctrls < pos_curil, \
        "controls must sit after the marker list and before the curation panel"
    for needed in ["ia-marker-edit-count", "ia-save-adjustments-btn",
                   "ia-discard-adjustments-btn", "ia-clear-frame-btn"]:
        assert f'id="{needed}"' in html, f"missing {needed!r}"


def test_js_edit_gating_uses_finalize_flag():
    src = PLAYER_JS.read_text()
    assert "_iaFinalizeEnabled" in src, "finalize gating flag must exist"
    assert "_iaEditingAllowed" in src, "editing-allowed helper must exist"
    assert 'getElementById("ia-marker-edit-controls")' in src
    assert 'getElementById("ia-marker-edit-banner")' not in src
