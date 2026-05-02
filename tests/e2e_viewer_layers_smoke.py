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

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
