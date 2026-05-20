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
