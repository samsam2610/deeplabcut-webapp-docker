"""End-to-end smoke for the viewer's layered overlay.

Runs against the live app (http://localhost:5000). Drives the browser:
1. Force-unhides the dlc project sidebar so the viewer card is reachable.
2. Opens the View Analyzed Videos / Frames card.
3. Browses to one of the OM-2 RatBox videos and loads it.
4. Toggles the kinematic overlay on; verifies the Primary <select> is populated.
5. Adds a comparison layer (if any postproc variant exists on disk);
   verifies the "Edit disabled (compare mode)" banner appears.
6. Removes the comparison; verifies the banner is gone.
7. Toggles "Customize threshold per layer"; verifies a per-layer slider
   appears on the compare row.

Manual:
    python tests/e2e_viewer_layers_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

APP_URL = "http://localhost:5000/?token=deeplabcut"
OM2_HOST = Path(
    "/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/"
    "tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10"
)
HOST_TO_CONTAINER = (
    "/home/sam/synology/Parra-Lab-Data",
    "/user-data/Parra-Data/Cloud",
)


def _container_path(host_path: Path) -> str:
    s = str(host_path)
    return s.replace(*HOST_TO_CONTAINER)


def main() -> int:
    if not OM2_HOST.is_dir():
        print(f"FATAL: OM-2 folder not on host: {OM2_HOST}", file=sys.stderr)
        return 2
    avis = sorted(OM2_HOST.glob("*.avi"))
    if not avis:
        print("FATAL: no .avi in OM-2 folder", file=sys.stderr)
        return 2
    video_path_container = _container_path(avis[0])
    video_dir_container  = video_path_container.rsplit("/", 1)[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 1100})
        page = ctx.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.set_default_timeout(15_000)
        page.goto(APP_URL)
        page.wait_for_load_state("networkidle")

        # The dlc-project-card and view-analyzed-card both start hidden until
        # a project is loaded; force-unhide them for the test (we are testing
        # the viewer card's behaviour, not the project picker).
        page.evaluate(
            "() => { const ids=['dlc-project-card','view-analyzed-card','dlc-frame-extract-launch'];"
            "ids.forEach(id => { const el=document.getElementById(id); if (el) el.classList.remove('hidden'); }); }"
        )

        # Activate a DLC project so /dlc/project/video-frame-ext succeeds.
        # Without an active project, frame fetches return "No active DLC project"
        # and the play loop can't advance frames (Phase H).
        proj_resp = page.evaluate(
            "async () => { const r = await fetch('/dlc/project', {method:'POST', "
            "  headers:{'Content-Type':'application/json'},"
            "  body: JSON.stringify({path: '/user-data/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07'})});"
            "  return {status: r.status, body: (await r.text()).slice(0,200)}; }"
        )
        print(f"[A] activate DLC project → {proj_resp['status']}")

        # Open the viewer card.
        page.click("#btn-open-view-analyzed")
        page.wait_for_selector("#view-analyzed-card", state="visible")
        print("[A] viewer card opened")

        # Switch to Browse tab and navigate to the OM-2 folder.
        page.click("#va-tab-browse")
        page.fill("#va-browse-breadcrumb", video_dir_container)
        page.keyboard.press("Enter")
        time.sleep(1.0)

        # Click the first video file row.
        page.wait_for_selector(f"text={avis[0].name}", timeout=15_000)
        page.click(f"text={avis[0].name}")
        # Player section appears.
        page.wait_for_selector("#va-player-section", state="visible", timeout=10_000)
        print(f"[A] loaded video: {avis[0].name}")
        # Wait for the frame-info fetch to populate the counter (needed for Phase H).
        for _ in range(20):
            counter_txt = page.text_content("#va-frame-counter") or ""
            if "/ 0" not in counter_txt and "Frame" in counter_txt:
                break
            time.sleep(0.25)
        print(f"[A] frame counter: {counter_txt!r}")

        # Toggle overlay on.
        page.click("#va-overlay-toggle")
        page.wait_for_selector("#va-overlay-controls", state="visible")
        # Give variant discovery + h5-info a moment.
        time.sleep(2.0)

        primary_options = page.evaluate(
            "() => Array.from(document.querySelectorAll('#va-overlay-primary-select option')).map(o => o.value).filter(v => v)"
        )
        print(f"[B] primary options ({len(primary_options)}): {primary_options[:3]}…")
        if not primary_options:
            print("WARN: no h5 variants discovered for this video", file=sys.stderr)
            # Not fatal — the route may legitimately return zero if no companion h5
            # is present on disk. Subsequent compare tests are skipped below.

        compare_options = page.evaluate(
            "() => Array.from(document.querySelectorAll('#va-overlay-add-compare option')).slice(1).map(o => o.value).filter(v => v)"
        )
        print(f"[B] add-compare options ({len(compare_options)}): {compare_options[:3]}…")

        if compare_options:
            # Add a comparison.
            page.select_option("#va-overlay-add-compare", value=compare_options[0])
            page.wait_for_selector("#va-overlay-edit-disabled-banner", state="visible",
                                   timeout=5_000)
            print("[C] comparison added; edit-disabled banner visible")
            # Remove the row.
            page.click("#va-overlay-compare-list button")
            page.wait_for_selector("#va-overlay-edit-disabled-banner", state="hidden",
                                   timeout=3_000)
            print("[C] comparison removed; banner hidden")
        else:
            print("[C] no comparison variants on disk — skipping compare-mode test")

        # Customize threshold toggle.
        page.click("#va-overlay-customize-thresholds")
        time.sleep(0.3)
        # Re-add a comparison (if available) to see the per-layer slider.
        if compare_options:
            page.select_option("#va-overlay-add-compare", value=compare_options[0])
            time.sleep(0.3)
            slider_present = page.evaluate(
                "() => !!document.querySelector('#va-overlay-compare-list input[type=range]')"
            )
            print(f"[D] per-layer slider present in compare row: {slider_present}")
            if not slider_present:
                print("FAIL: customize-per-layer slider missing", file=sys.stderr)
                return 1

        # Primary inline slider should also be present after Customize toggle.
        primary_slot_present = page.evaluate(
            "() => !!document.getElementById('va-overlay-primary-threshold-slot')"
        )
        print(f"[D] primary inline threshold slot: {primary_slot_present}")

        # ── Phase E: Browse filter toggle ────────────────────────────
        page.click("#va-tab-browse")
        time.sleep(0.5)
        # Untick "Hide videos without h5" → at least one row may appear with
        # data-has-h5="false" (if the dir has any h5-less videos).
        page.click("#va-browse-hide-no-h5")  # untick
        time.sleep(1.5)
        no_h5_rows = page.evaluate(
            "() => document.querySelectorAll('#va-browse-list [data-has-h5=\"false\"]').length"
        )
        with_h5_rows = page.evaluate(
            "() => document.querySelectorAll('#va-browse-list [data-has-h5=\"true\"]').length"
        )
        print(f"[E] rows with h5: {with_h5_rows}, without h5: {no_h5_rows}")
        # At least one row with has_h5=true must exist (the OM-2 dir has analyzed videos).
        assert with_h5_rows >= 1, "expected at least one video with h5"
        # Re-tick to restore default.
        page.click("#va-browse-hide-no-h5")
        time.sleep(0.5)

        # ── Phase F: Auto-latest primary ─────────────────────────────
        # Primary <select>'s default selected option's text should prefer the
        # newest dated variant over Raw, when one exists.
        primary_default_label = page.evaluate(
            "() => { const s = document.getElementById('va-overlay-primary-select');"
            "  return s ? s.options[s.selectedIndex]?.textContent : null; }"
        )
        print(f"[F] primary default label: {primary_default_label!r}")
        # If at least 2 primary options exist (raw + filtered), the default
        # MUST be the dated one (label starts with 'filtered @' or 'refine_').
        if primary_options and len(primary_options) >= 2 and primary_default_label:
            assert (primary_default_label.startswith("filtered @") or
                    primary_default_label.startswith("refine_")), (
                f"expected newest dated variant as default, got {primary_default_label!r}"
            )

        # ── Phase G: Add-compare empty hint ──────────────────────────
        # Add the only available comparison; the dropdown should now be hidden
        # and the empty-hint span should be visible.
        if compare_options:
            # If a compare row is already present (Phase D may have re-added
            # it), remove it first so we can drive a clean add → hide flow.
            existing_compare_btn = page.query_selector(
                "#va-overlay-compare-list button"
            )
            if existing_compare_btn:
                existing_compare_btn.click()
                time.sleep(0.3)
            page.select_option("#va-overlay-add-compare", value=compare_options[0])
            time.sleep(0.4)
            hint_visible = page.evaluate(
                "() => { const e = document.getElementById('va-overlay-add-compare-empty-hint');"
                "  return e && !e.classList.contains('hidden'); }"
            )
            select_hidden = page.evaluate(
                "() => document.getElementById('va-overlay-add-compare').classList.contains('hidden')"
            )
            print(f"[G] empty-hint visible: {hint_visible}, dropdown hidden: {select_hidden}")
            assert hint_visible and select_hidden, (
                "after taking the only compare option, hint must show + dropdown must hide"
            )

        # ── Phase H: Frame-step (play every N frames) ────────────────
        # Set step=5, play briefly, pause, verify the visible frame counter
        # advanced by more than 1.
        before = page.text_content("#va-frame-counter") or ""
        page.fill("#va-play-step", "5")
        page.click("#va-btn-play")
        time.sleep(1.2)
        page.click("#va-btn-play")  # pause
        after = page.text_content("#va-frame-counter") or ""
        print(f"[H] frame counter: before={before!r} after={after!r}")
        # The counter looks like "Frame N / M". Parse N out of each.
        import re as _re
        m1 = _re.search(r"Frame\s+(\d+)", before or "")
        m2 = _re.search(r"Frame\s+(\d+)", after  or "")
        if m1 and m2:
            advanced = int(m2.group(1)) - int(m1.group(1))
            print(f"[H] frames advanced: {advanced}")
            assert advanced >= 4, (
                f"with step=5 and ~1.2s of playback, expected ≥ 4 frame advance, got {advanced}"
            )

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
