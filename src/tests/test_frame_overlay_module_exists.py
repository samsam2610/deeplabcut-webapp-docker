"""Sanity tests that the shared overlay module exists with the expected exports."""
from pathlib import Path


SRC = Path(__file__).parents[1]


def test_frame_overlay_module_exists():
    p = SRC / "static" / "js" / "frame_overlay.js"
    assert p.is_file(), f"{p} is missing — the picker depends on it"


def test_frame_overlay_exports_drawframe_and_drawbodyparts():
    p = SRC / "static" / "js" / "frame_overlay.js"
    text = p.read_text()
    assert "export function drawFrame" in text
    assert "export function drawBodyparts" in text


def test_frame_overlay_is_imported_by_the_picker():
    """The overlay module exists to be shared — verify it still has a consumer.

    Replaces an earlier `test_frame_labeler_untouched_by_this_commit`, which
    shelled out to `git diff HEAD~1 HEAD` and asserted that whatever commit
    happened to be at HEAD had not touched frame_labeler.js. That is a property
    of git history, not of the code: it passed only while no one had a
    legitimate reason to edit that file, and fired on 2026-08-01 when
    frame_labeler.js was correctly guarded so it stops throwing on /dlc-3d/.

    The 2026-05-19 plan intended a durable check that the labeler imported
    frame_overlay.js, but the real consumer turned out to be the test-set
    picker, so that is what this asserts.
    """
    text = (SRC / "static" / "js" / "test_set_picker.js").read_text()
    assert "frame_overlay.js" in text, (
        "frame_overlay.js has no importer — it was extracted to be shared with "
        "the test-set picker; if the picker no longer uses it, delete it."
    )
