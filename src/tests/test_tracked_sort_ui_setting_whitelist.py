"""Every ui-setting key the tracked-files component sends must be whitelisted.

`/dlc/project/ui-setting` rejects any key absent from `_UI_SETTING_KEYS` with
400 "unknown key" — silently, from the client's point of view. On 2026-07-31
eight reprojection keys were missing and nothing that card saved was ever
stored. dlc-3D guards its own card this way; this is the equivalent guard for
the shared tracked-files component, which lives in this repo.

Both sides are extracted from source rather than hand-copied, so the test
cannot drift out of sync with either.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
JS = SRC / "static" / "js" / "components" / "tracked_files_tab.js"
INLINE_ANALYSIS = SRC / "dlc" / "inline_analysis.py"


def _whitelist():
    text = INLINE_ANALYSIS.read_text()
    block = re.search(r"_UI_SETTING_KEYS\s*=\s*\{(.*?)\}", text, re.S)
    assert block, "could not find _UI_SETTING_KEYS in inline_analysis.py"
    return set(re.findall(r'"([\w-]+)"', block.group(1)))


def _keys_sent_by_the_component():
    text = JS.read_text()
    keys = set(re.findall(r'key:\s*"([\w-]+)"', text))
    keys |= set(re.findall(r'\?key=([\w-]+)', text))
    # `const SORT_KEY = "tracked_sort";` then used as `key: SORT_KEY`
    for const, value in re.findall(r'const\s+(\w+)\s*=\s*"([\w-]+)"\s*;', text):
        if re.search(rf'key:\s*{const}\b', text) or re.search(rf'\?key=\$\{{{const}\}}', text):
            keys.add(value)
    return keys


def test_the_component_sends_at_least_one_ui_setting_key():
    """Guards the extractor itself — if this regresses to zero the test below
    would pass vacuously."""
    assert _keys_sent_by_the_component(), \
        "extracted no ui-setting keys from tracked_files_tab.js"


def test_every_key_the_component_sends_is_whitelisted():
    missing = _keys_sent_by_the_component() - _whitelist()
    assert not missing, (
        f"these keys would 400 'unknown key' and silently never persist: {sorted(missing)}"
    )
