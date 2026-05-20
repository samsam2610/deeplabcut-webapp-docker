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


def test_frame_labeler_untouched_by_this_commit():
    """Sanity: this task should NOT have modified frame_labeler.js."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        cwd="/home/sam/docker-images/deeplabcut-webapp-docker",
        capture_output=True, text=True, check=True,
    )
    changed = set(result.stdout.strip().split("\n"))
    assert "src/static/js/frame_labeler.js" not in changed, (
        f"frame_labeler.js was modified — Task 6 should leave it alone. "
        f"Changed: {changed}"
    )
