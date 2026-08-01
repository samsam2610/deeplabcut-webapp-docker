"""Guards for the progress-bar editor's multi-column layout.

The editor stacked every segment in one tall column and let its text inputs
stretch to the full card width (~1200px), so five segments filled the screen
while most of each row was empty space. Segments now flow into a responsive
grid and inputs are capped, so the freed width becomes more columns.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
CSS = SRC / "static" / "css" / "components.css"
PANEL_JS = SRC / "static" / "js" / "tracked_files_panel.js"


def test_segments_container_is_a_responsive_multi_column_grid():
    css = CSS.read_text()
    block = re.search(r"#tf-segments\s*\{[^}]*\}", css)
    assert block, "no #tf-segments rule in components.css"
    rule = block.group(0)
    assert "display: grid" in rule or "display:grid" in rule
    assert "auto-fill" in rule or "auto-fit" in rule, \
        "columns must be responsive, not a fixed count"
    assert "minmax(" in rule, "needs a minmax() track so narrow cards fall back to one column"


def test_text_inputs_are_capped_around_40_50_characters():
    css = CSS.read_text()
    m = re.search(r"#tf-segments\s+input\[type=[\"']text[\"']\]\s*\{[^}]*\}", css)
    assert m, "no width cap on the editor's text inputs"
    ch = re.search(r"max-width:\s*(\d+)ch", m.group(0))
    assert ch, "cap should be expressed in ch so it tracks the font"
    assert 40 <= int(ch.group(1)) <= 50, f"cap should be 40–50ch, got {ch.group(1)}ch"


def test_segment_box_does_not_hardcode_a_bottom_margin():
    """Grid `gap` owns the spacing now; a leftover margin doubles it."""
    js = PANEL_JS.read_text()
    box = re.search(r'box\.style\.cssText\s*=\s*\n?\s*"([^"]*)"', js)
    assert box, "could not find the segment box style in tracked_files_panel.js"
    assert "margin-bottom" not in box.group(1), \
        "segment box must not set margin-bottom; the grid gap handles spacing"
