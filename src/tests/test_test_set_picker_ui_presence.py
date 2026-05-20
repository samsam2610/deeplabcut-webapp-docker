"""Sanity tests that the picker card files exist and are wired into templates."""
from pathlib import Path


SRC = Path(__file__).parents[1]


def test_card_partial_exists():
    assert (SRC / "templates" / "partials" / "card_test_set_picker.html").is_file()


def test_card_included_in_index():
    text = (SRC / "templates" / "index.html").read_text()
    assert "partials/card_test_set_picker.html" in text


def test_opener_button_in_project_card():
    text = (SRC / "templates" / "partials" / "card_dlc_project.html").read_text()
    assert "btn-open-test-set-picker" in text
    # Order: must appear after Frame Labeler button and before Create Training Dataset button
    fl = text.find("btn-open-frame-labeler")
    ts = text.find("btn-open-test-set-picker")
    ctd = text.find("btn-open-create-training-dataset")
    assert fl < ts < ctd, f"Button order wrong: fl={fl}, ts={ts}, ctd={ctd}"


def test_picker_js_exists():
    assert (SRC / "static" / "js" / "test_set_picker.js").is_file()


def test_picker_js_imports_overlay():
    text = (SRC / "static" / "js" / "test_set_picker.js").read_text()
    assert "frame_overlay" in text


def test_picker_js_loaded_somewhere():
    """Sanity that something in templates references the picker JS."""
    found = False
    for tpl in (SRC / "templates").rglob("*.html"):
        if "test_set_picker.js" in tpl.read_text() or "test_set_picker" in tpl.read_text():
            found = True; break
    # Also check static JS entry point (main.js imports it)
    if not found:
        main_js = SRC / "static" / "js" / "main.js"
        if main_js.exists() and "test_set_picker" in main_js.read_text():
            found = True
    assert found, "test_set_picker.js is not loaded by any template"


def test_inspect_dialog_present():
    text = (SRC / "templates" / "partials" / "card_test_set_picker.html").read_text()
    assert "ts-inspect-dialog" in text
    assert "ts-inspect-banner" in text


def test_picker_js_has_inspect_logic():
    text = (SRC / "static" / "js" / "test_set_picker.js").read_text()
    assert "/dlc/project/training-dataset/inspect" in text
    assert "_tsInspect" in text


def test_ctd_card_has_split_mode_selector():
    text = (SRC / "templates" / "partials" / "card_training_dataset.html").read_text()
    assert 'name="ctd-split-mode"' in text
    assert 'value="random"' in text
    assert 'value="hybrid"' in text
    assert 'value="manual"' in text


def test_main_js_sends_split_mode():
    text = (SRC / "static" / "main.js").read_text()
    assert "split_mode" in text
    assert "ctd-split-mode" in text


def test_frame_overlay_exports_default_palette():
    text = (SRC / "static" / "js" / "frame_overlay.js").read_text()
    assert "export const DEFAULT_PALETTE" in text, (
        "frame_overlay.js should export a DEFAULT_PALETTE shared with the picker"
    )
    # Palette is napari-inspired; cross-check a few canonical entries
    for hex_color in ("#f87171", "#fb923c", "#fbbf24"):
        assert hex_color in text, f"missing {hex_color} from DEFAULT_PALETTE"


def test_picker_js_imports_default_palette():
    text = (SRC / "static" / "js" / "test_set_picker.js").read_text()
    assert "DEFAULT_PALETTE" in text, (
        "test_set_picker.js should import DEFAULT_PALETTE from frame_overlay.js"
    )
    # And should NOT keep the old TS_DEFAULT_PALETTE local array
    assert "TS_DEFAULT_PALETTE" not in text, (
        "TS_DEFAULT_PALETTE local array should have been removed in favor of "
        "the shared DEFAULT_PALETTE"
    )


def test_inspect_dialog_uses_dropdown():
    text = (SRC / "templates" / "partials" / "card_test_set_picker.html").read_text()
    # New IDs
    assert 'id="ts-inspect-select"' in text, (
        "inspect dialog should have a ts-inspect-select dropdown"
    )
    assert 'id="ts-inspect-refresh"' in text, (
        "inspect dialog should have a ts-inspect-refresh button"
    )
    # Old IDs gone
    assert 'id="ts-inspect-iter"' not in text, (
        "inspect dialog should no longer use ts-inspect-iter number input"
    )
    assert 'id="ts-inspect-shuffle"' not in text, (
        "inspect dialog should no longer use ts-inspect-shuffle number input"
    )


def test_picker_js_calls_splits_endpoint():
    text = (SRC / "static" / "js" / "test_set_picker.js").read_text()
    assert "/dlc/project/training-dataset/splits" in text, (
        "test_set_picker.js should fetch /dlc/project/training-dataset/splits "
        "to populate the inspect dropdown"
    )
    assert "_loadInspectSplits" in text, (
        "picker JS should have a _loadInspectSplits helper"
    )


def test_picker_js_renders_folder_counter():
    text = (SRC / "static" / "js" / "test_set_picker.js").read_text()
    assert "_stemOptionLabel" in text, (
        "picker JS should have a _stemOptionLabel helper that builds 'stem — xx/yy' option text"
    )
    assert "_refreshStemOptionLabel" in text, (
        "picker JS should have a _refreshStemOptionLabel helper to update one option in place after a toggle"
    )
