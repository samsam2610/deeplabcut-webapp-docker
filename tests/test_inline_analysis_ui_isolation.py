"""Static-template + DOM-shape assertions for the Inline Analysis card.

No JS runtime here — these tests parse the template files directly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT     = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
INDEX    = ROOT / "src" / "templates" / "index.html"


def test_card_inline_analysis_partial_exists():
    p = PARTIALS / "card_inline_analysis.html"
    assert p.is_file()
    txt = p.read_text()
    assert 'id="inline-analysis-card"' in txt
    assert 'id="btn-close-inline-analysis"' in txt
    # Player elements with the "ia-" prefix the factory expects.
    assert 'id="ia-frame-img"' in txt
    assert 'id="ia-overlay-canvas"' in txt
    assert 'id="ia-btn-play"' in txt
    # Params block.
    assert 'id="ia-snapshot"' in txt
    assert 'id="ia-batch-size"' in txt
    assert 'id="ia-frames-per-click"' in txt
    assert 'id="ia-keep-warm-seconds"' in txt
    assert 'id="ia-btn-analyze-range"' in txt
    # No ia-disable-banner — project-type errors surface via the existing
    # "Last run" status line, sourced from /session/start's 409 body.
    assert 'id="ia-disable-banner"' not in txt


def test_index_includes_inline_analysis_partial():
    txt = INDEX.read_text()
    assert "partials/card_inline_analysis.html" in txt


def test_button_sits_between_analyze_and_view_analyzed():
    p = PARTIALS / "card_dlc_project.html"
    txt = p.read_text()
    i_analyze = txt.index('id="btn-open-analyze"')
    i_inline  = txt.index('id="btn-open-inline-analysis"')
    i_view    = txt.index('id="btn-open-view-analyzed"')
    assert i_analyze < i_inline < i_view, (
        "Inline Analysis button must sit between Analyze and View-Analyzed"
    )


def test_hide_no_h5_is_unchecked_by_default():
    """Spec §1: default UNCHECKED (opposite of View-Analyzed)."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    m = re.search(r'<input[^>]*id="ia-hide-no-h5"[^>]*>', txt)
    assert m, "ia-hide-no-h5 checkbox must exist"
    assert "checked" not in m.group(0), (
        "ia-hide-no-h5 must NOT have `checked` — default unchecked per spec §1"
    )


def test_no_create_labeled_controls_in_card():
    """Spec §1: 'Create labeled video / frame' is explicitly omitted."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert "ia-create-labeled" not in txt
    assert "Create labeled video" not in txt
    assert "Create labeled frame" not in txt


def test_inline_analysis_js_does_not_hide_other_cards():
    """Spec §1.1: openCard must NOT call hideAllOtherCards or otherwise
    iterate over section.card and toggle .hidden — that collapses every
    other open dashboard card. The card just shows itself and scrolls
    into view.
    """
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "hideAllOtherCards" not in js, (
        "inline_analysis.js must not define or call hideAllOtherCards "
        "— see polish spec §1.1"
    )
    assert "section.card" not in js, (
        "inline_analysis.js must not query `section.card` (which would "
        "let it mass-toggle other cards' visibility)"
    )


def test_shuffle_and_trainingsetindex_inputs_exist():
    """Polish spec §1.3: full Analyze-card parity for the snapshot row."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-shuffle"' in txt
    assert 'id="ia-trainingsetindex"' in txt
    assert 'id="ia-snapshot"' in txt
    assert 'id="ia-refresh-snapshots"' in txt


def test_inline_analysis_js_uses_latest_rel_path_and_iter_format():
    """The snapshot picker must mirror analyze.js's format —
    use data.latest_rel_path for the default and render
    `<label> · iter N · shM` per option."""
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "latest_rel_path" in js, "must use the Latest-default pattern"
    assert "iter" in js, "must format the iteration count in option text"
    # Shuffle change reloads snapshots (indices are per-shuffle).
    assert "ia-shuffle" in js


def test_inline_analysis_js_sends_trainingsetindex_in_range():
    """Polish spec §1.3 last bullet: /range POST body must include
    shuffle + trainingsetindex from the new inputs."""
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "ia-trainingsetindex" in js
    # Sanity: still sends shuffle (it did before, but now from the input).
    assert "shuffle" in js


def test_overlay_comparison_widgets_removed():
    """Polish spec §1.4: drop the multi-h5 comparison UI; the card now
    shows ONLY the just-produced h5."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-overlay-primary-select"' not in txt, (
        "primary-select dropdown must be removed per polish spec §1.4"
    )
    assert 'id="ia-overlay-add-compare"' not in txt
    assert 'id="ia-overlay-compare-list"' not in txt
    # Keep these — they remain useful in single-layer mode:
    assert 'id="ia-overlay-toggle"' in txt
    assert 'id="ia-overlay-threshold"' in txt
    assert 'id="ia-overlay-marker-size"' in txt


def test_bp_chips_container_present():
    """Polish spec §1.4: body-part chips container is newly added."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-bp-chips"' in txt


def test_full_curation_panel_mirrored_in_inline_analysis_partial():
    """Polish spec §1.5: every va-* curation ID has an ia-* counterpart
    in the inline-analysis partial."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    required_ia_ids = [
        # Toggle + master area
        "ia-curation-panel", "ia-curation-toggle", "ia-curation-controls",
        "ia-curation-status",
        # Row 1: Extract + Add
        "ia-extract-frame-btn", "ia-add-to-dataset-btn",
        # Row 2: Batch
        "ia-batch-count", "ia-batch-step", "ia-batch-add-btn",
        # Row 3: CSV section
        "ia-csv-section", "ia-csv-none", "ia-csv-loaded",
        "ia-csv-path-display", "ia-create-csv-btn", "ia-csv-create-status",
        # Row 3b: Timelines
        "ia-csv-bars", "ia-status-bar-wrap", "ia-note-bar-wrap",
        "ia-status-canvas", "ia-note-canvas",
        "ia-status-chips", "ia-note-chips",
        "ia-status-prev-btn", "ia-status-next-btn",
        "ia-note-prev-btn", "ia-note-next-btn",
        # Row 4: Annotation panel
        "ia-annot-panel", "ia-annot-frame-num",
        "ia-status-input", "ia-save-status-btn",
        "ia-note-input", "ia-save-note-btn",
        "ia-annot-save-status",
        "ia-new-tag-input", "ia-add-tag-btn",
    ]
    missing = [i for i in required_ia_ids if f'id="{i}"' not in txt]
    assert not missing, f"missing IDs in curation panel: {missing}"


def test_no_va_ids_leaked_into_inline_partial():
    """Sanity: ensure the rename from va- to ia- was complete."""
    import re as _re
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    leaked = _re.findall(r'id="(va-[^"]+)"', txt)
    assert not leaked, f"va- IDs leaked into inline-analysis partial: {leaked}"


def test_new_ids_are_unique_across_partials():
    new_ids = {
        "inline-analysis-card", "btn-close-inline-analysis", "btn-open-inline-analysis",
        "ia-snapshot", "ia-shuffle", "ia-trainingsetindex", "ia-batch-size",
        "ia-frames-per-click", "ia-keep-warm-seconds",
        "ia-warm-indicator", "ia-btn-analyze-range", "ia-last-run-status",
        "ia-file-browser-pane", "ia-hide-no-h5",
        "ia-frame-img", "ia-overlay-canvas", "ia-btn-play",
        "ia-btn-prev", "ia-btn-next", "ia-seek", "ia-frame-counter",
        "ia-zoom", "ia-zoom-val", "ia-skip-n",
        "ia-overlay-toggle",
        "ia-overlay-threshold", "ia-overlay-marker-size",
        "ia-bp-list-wrap", "ia-bp-chips",
        "ia-marker-edit-banner",
    }
    seen: dict = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    for nid in new_ids:
        assert seen.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen.get(nid, 0)} times across partials"
        )


def test_poseUrlFn_uses_canonical_frame_poses_batch_endpoint():
    """Regression guard: inline_analysis.js must point the player's
    poseUrlFn at /dlc/viewer/frame-poses-batch?h5=...&start=...&count=...
    — the canonical batched-pose endpoint that viewer.js uses.

    An earlier draft used a made-up /dlc/viewer/h5-pose-window?h5=...&
    start=...&n=... — that route doesn't exist and 404'd every pose
    fetch, so the overlay canvas stayed blank no matter what the user
    did. See systematic-debugging session 2026-05-20.
    """
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "/dlc/viewer/h5-pose-window" not in js, (
        "broken /dlc/viewer/h5-pose-window endpoint must not appear"
    )
    assert "/dlc/viewer/frame-poses-batch" in js, (
        "must use canonical /dlc/viewer/frame-poses-batch endpoint"
    )


def test_factory_parses_canonical_frame_poses_batch_shape():
    """Regression guard: the analyzed_frame_player factory must handle
    the canonical {frames: {<n>: {poses: [...]}}} response shape AND
    normalize each pose's `lh` to `likelihood` so the threshold filter
    in _drawCurrentFrame works (it reads .likelihood, server sends .lh).
    """
    js = (ROOT / "src" / "static" / "js" / "components" /
          "analyzed_frame_player.js").read_text()
    # Branch handles {frames: {...}}
    assert "data.frames" in js, (
        "factory _prefetchPoseWindow must recognize the canonical "
        "{frames: {...}} shape returned by /frame-poses-batch"
    )
    # `lh` → `likelihood` normalization
    assert "p.lh" in js, (
        "factory must normalize p.lh (server) → p.likelihood (drawing path)"
    )


def test_worker_dense_ifies_h5_for_positional_consumers():
    """Regression guard: the warm-worker's _run_range must reindex the
    merged DataFrame to a CONTIGUOUS 0..max range before writing the
    canonical h5. Without this, an analysis from a non-zero start frame
    (e.g. user scrubs to frame 24015 and analyzes 500 from there)
    writes rows ONLY at indices 24015..24514 — pandas-sparse but
    positionally-disjoint.

    Downstream DLC tools — /dlc/viewer/frame-poses-batch,
    filterpredictions, create_labeled_video — read h5 rows
    POSITIONALLY (`poses_np[frame_number]`), so a sparse h5 silently
    returns nothing for the analyzed frames. Symptom: user runs
    Inline Analysis, h5 lands on disk, but the player shows no markers.

    See systematic-debugging session 2026-05-20.
    """
    tasks_py = (ROOT / "src" / "dlc" / "tasks.py").read_text()
    # The dense-ify step on the merge path
    assert "df_merge.reindex(_ia_pd.RangeIndex(" in tasks_py, (
        "_run_range must reindex df_merge to a contiguous 0..max range "
        "before writing (DLC canonical analyze_videos h5 is dense)"
    )
    # The self-healing branch for "all skipped" — must also dense-ify
    # so existing sparse h5s get fixed on the next user click.
    assert "dense = existing.reindex" in tasks_py, (
        "_run_range's full-skip branch must self-heal existing sparse "
        "h5s by reindexing them dense"
    )


def test_done_handler_auto_enables_overlay_toggle():
    """Regression guard: after /range/status reports `done`, inline_
    analysis.js must auto-check the `ia-overlay-toggle` and dispatch a
    change event so the factory's overlay-enabled flag flips. Without
    this, the user finishes an analysis and sees no markers until they
    manually tick a checkbox they probably didn't notice.
    """
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    # Find the done branch.
    done_idx = js.find('d.status === "done"')
    assert done_idx > 0, "done-branch must exist in the polling handler"
    done_block = js[done_idx:done_idx + 2200]
    assert 'ia-overlay-toggle' in done_block, (
        "done handler must reference ia-overlay-toggle"
    )
    assert 'tgl.checked = true' in done_block or 'tgl.checked=true' in done_block, (
        "done handler must check the overlay toggle"
    )
    assert "new Event(\"change\"" in done_block or "new Event('change'" in done_block, (
        "done handler must dispatch a change event so the factory's "
        "change listener runs (just setting .checked doesn't fire events)"
    )


def test_frameUrlFn_uses_canonical_annotate_video_frame_endpoint():
    """Regression guard: inline_analysis.js must point the player at
    /annotate/video-frame/<n>?path=... (the canonical browse-mode frame
    endpoint used by frame_labeler / annotator). An earlier draft used a
    made-up /annotate/frame?path=...&frame=N — that route doesn't exist
    and 404'd every frame fetch, leaving the player blank and the seek
    slider visually inert. See systematic-debugging session 2026-05-20.
    """
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "/annotate/frame?" not in js, (
        "broken /annotate/frame?path=...&frame=... endpoint must not appear"
    )
    assert "/annotate/video-frame/" in js, (
        "must use canonical /annotate/video-frame/<n>?path=... endpoint"
    )


def test_overlay_canvas_does_not_intercept_pointer_events():
    """Regression guard: the inline card's overlay canvas must declare
    pointer-events:none AND size to width/height 100% of its wrap.

    A prior draft set pointer-events:auto with no width/height — the
    canvas then grew to its intrinsic 800x600 pixel size and covered the
    slider + prev/next/play buttons, eating every click. Symptoms were
    "the timeline is nonresponsive" and the transport buttons doing
    nothing. See systematic-debugging session 2026-05-20.
    """
    html = (PARTIALS / "card_inline_analysis.html").read_text()
    m = re.search(r'<canvas[^>]*id="ia-overlay-canvas"[^>]*>', html)
    assert m, "ia-overlay-canvas element must exist"
    tag = m.group(0)
    assert "pointer-events:none" in tag, (
        "overlay canvas must declare pointer-events:none "
        "(otherwise it eats clicks meant for the slider + transport buttons)"
    )
    assert "width:100%" in tag and "height:100%" in tag, (
        "overlay canvas must size to width:100%;height:100% of its wrap "
        "(otherwise it grows to intrinsic 800x600 px and overflows)"
    )


def test_seek_slider_sits_above_controls_row_in_markup():
    """Regression guard: the seek slider must appear in the partial
    BEFORE the controls row (prev/play/next/...) — standard video-player
    layout. The first draft put the slider below the buttons.
    """
    html = (PARTIALS / "card_inline_analysis.html").read_text()
    i_seek = html.find('id="ia-seek"')
    i_prev = html.find('id="ia-btn-prev"')
    assert i_seek > 0 and i_prev > 0, "expected both ia-seek and ia-btn-prev"
    assert i_seek < i_prev, (
        "ia-seek must appear before ia-btn-prev in the partial markup "
        "(slider above controls is the canonical video-player layout)"
    )


def test_frame_spinner_removed_from_inline_card():
    """Regression guard: the inline card must NOT carry a frame-loading
    spinner. The original draft used `<div id="ia-frame-spinner">Loading…
    </div>` with no `position:absolute`, so every show/hide shifted the
    seek slider + controls + curation panel up and down — the user
    called the experience "clunky" and asked us to remove it.

    If we ever want loading feedback back, build it as a
    position:absolute overlay so it doesn't displace the document flow.
    """
    html = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-frame-spinner"' not in html, (
        "ia-frame-spinner must be removed from the inline card "
        "(it shifts the UI below the player on every frame load)"
    )


def test_frame_img_uses_width_not_max_width_so_zoom_works():
    """Regression guard: the frame img must use `width:100%`, NOT
    `max-width:100%`. The factory's zoom handler sets `img.style.width
    = "${zoom}%"`. With `max-width:100%`, the inline width is clamped
    and zoom does nothing.
    """
    html = (PARTIALS / "card_inline_analysis.html").read_text()
    m = re.search(r'<img[^>]*id="ia-frame-img"[^>]*>', html)
    assert m, "ia-frame-img must exist"
    tag = m.group(0)
    assert "width:100%" in tag, "ia-frame-img must declare width:100%"
    assert "max-width:100%" not in tag, (
        "ia-frame-img must NOT declare max-width:100% — it clamps the "
        "factory's zoom handler"
    )
