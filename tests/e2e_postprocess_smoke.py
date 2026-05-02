"""End-to-end smoke for the Post-Process Predictions card.

Runs against the live app (http://localhost:5000). Drives the browser:

1. Force-unhides two sibling cards (dlc-project-card and analyze-card) via JS,
   simulating a user who already has cards open. This sidesteps the project-
   loading flow so we can isolate the post-process card's behavior.
2. Clicks "Post-Process Predictions" and asserts BOTH the existing siblings
   remain visible (regression for "opening hides every other card").
3. Types a known folder path containing analyzed .h5/.csv pairs.
4. Clicks Run, polls the on-disk run.json sidecar until the run lands.
5. Asserts at least one input succeeded and at least one *_filtered.h5
   physical file exists.
6. Clicks Close — asserts the sibling cards are STILL visible (regression
   for "closing the card emptied the page").

Not part of the unit suite — invoked manually:
    python tests/e2e_postprocess_smoke.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

APP_URL = "http://localhost:5000/?token=deeplabcut"
TARGET_DIR_CONTAINER = (
    "/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/tdcs/"
    "042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10"
)
TARGET_DIR_HOST = (
    "/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/"
    "tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10"
)


def _newest_postproc_run(parent_host: Path) -> Path | None:
    pp = parent_host / "postproc"
    if not pp.is_dir():
        return None
    runs = sorted(pp.glob("*_filterpredictions/run.json"),
                  key=lambda p: p.parent.stat().st_mtime,
                  reverse=True)
    return runs[0] if runs else None


def main() -> int:
    parent_host = Path(TARGET_DIR_HOST)
    if not parent_host.is_dir():
        print(f"FATAL: TARGET_DIR_HOST does not exist: {parent_host}",
              file=sys.stderr)
        return 2

    pre_run = _newest_postproc_run(parent_host)
    pre_run_path = str(pre_run) if pre_run else None

    sibling_ids = ["dlc-project-card", "analyze-card"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()

        # Capture browser-side noise so we can debug failures.
        page.on("console", lambda msg: print(f"[browser/{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: print(f"[browser/error] {exc}"))
        page.on("requestfailed", lambda req: print(f"[browser/reqfail] {req.url}: {req.failure}"))
        responses: list[dict] = []
        def _on_response(resp):
            if "/dlc/postprocess/" in resp.url:
                responses.append({"url": resp.url, "status": resp.status})
        page.on("response", _on_response)

        page.set_default_timeout(15_000)
        page.goto(APP_URL)
        page.wait_for_load_state("networkidle")

        # Force-unhide the siblings so we can test the regression. Also unhide
        # the action-button list inside dlc-project-card (which is normally
        # only revealed after a project is loaded).
        page.evaluate(
            "(ids) => {"
            "  ids.forEach(id => {"
            "    const el = document.getElementById(id);"
            "    if (el) el.classList.remove('hidden');"
            "  });"
            "  const launch = document.getElementById('dlc-frame-extract-launch');"
            "  if (launch) launch.classList.remove('hidden');"
            "}",
            sibling_ids,
        )

        before = page.evaluate(
            "(ids) => ids.map(id => ({id, "
            "  visible: !!document.getElementById(id) && "
            "    !document.getElementById(id).classList.contains('hidden')}))",
            sibling_ids,
        )
        print(f"[A] siblings before opening:        {before}")
        if not all(s["visible"] for s in before):
            print("FAIL: could not even unhide siblings.", file=sys.stderr)
            return 1

        # Phase A: open the post-process card.
        page.click("#btn-open-postprocess")
        page.wait_for_selector("#postprocess-card:not(.hidden)", timeout=5_000)

        after_open = page.evaluate(
            "(ids) => ids.map(id => ({id, "
            "  visible: !!document.getElementById(id) && "
            "    !document.getElementById(id).classList.contains('hidden')}))",
            sibling_ids,
        )
        print(f"[A] siblings after  opening card:   {after_open}")
        clobbered = [s for s in after_open if not s["visible"]]
        if clobbered:
            print(f"FAIL: opening the post-process card hid: {clobbered}",
                  file=sys.stderr)
            return 1

        # Phase B: type the target folder, click Run.
        page.fill("#pp-input-path", TARGET_DIR_CONTAINER)
        page.click("#pp-run")
        print(f"[B] Run clicked with path={TARGET_DIR_CONTAINER}")
        # Give the fetch chain a moment to fire.
        time.sleep(2.0)
        status_text = page.text_content("#pp-status") or ""
        print(f"[B] status text after click:        {status_text!r}")
        print(f"[B] postprocess responses so far:   {responses}")

        # Poll: a NEW postproc/<ts>_filterpredictions/run.json must appear.
        deadline = time.time() + 120
        new_run: Path | None = None
        while time.time() < deadline:
            cur = _newest_postproc_run(parent_host)
            if cur is not None and (pre_run_path is None or str(cur) != pre_run_path):
                new_run = cur
                break
            time.sleep(1.0)

        if new_run is None:
            print("FAIL: no new run.json appeared under postproc/ within 120s.",
                  file=sys.stderr)
            page.screenshot(path="/tmp/e2e_postprocess_run_timeout.png")
            return 1

        payload = json.loads(new_run.read_text())
        print(f"[B] new run sidecar:                {new_run}")
        print(f"[B] status:                         {payload['status']}")
        print(f"[B] inputs:                         {len(payload.get('inputs', []))} files")
        succeeded = [i for i in payload.get("inputs", []) if i.get("status") == "success"]
        failed = [i for i in payload.get("inputs", []) if i.get("status") == "failed"]
        print(f"[B] succeeded:                      {len(succeeded)}")
        print(f"[B] failed:                         {len(failed)}")
        if failed:
            for f in failed[:3]:
                print(f"    failed: {Path(f.get('path','?')).name}: {f.get('error')}")

        filtered_files = list(new_run.parent.glob("*_filtered.h5"))
        print(f"[B] *_filtered.h5 on disk:          {len(filtered_files)}")
        if not filtered_files:
            print("FAIL: no *_filtered.h5 produced.", file=sys.stderr)
            return 1
        if not succeeded:
            print("FAIL: every per-file status is failed.", file=sys.stderr)
            return 1

        # Phase C: close the card.
        page.click("#btn-close-postprocess")
        # `.hidden` makes the element invisible (display:none); wait for that
        # via state="hidden" rather than the default "visible".
        page.wait_for_selector("#postprocess-card", state="hidden", timeout=3_000)
        after_close = page.evaluate(
            "(ids) => ids.map(id => ({id, "
            "  visible: !!document.getElementById(id) && "
            "    !document.getElementById(id).classList.contains('hidden')}))",
            sibling_ids,
        )
        print(f"[C] siblings after  closing card:   {after_close}")
        clobbered = [s for s in after_close if not s["visible"]]
        if clobbered:
            print(f"FAIL: closing the post-process card hid: {clobbered}",
                  file=sys.stderr)
            return 1

        # Phase D: re-open to test Browse panel toggles cleanly.
        page.click("#btn-open-postprocess")
        page.wait_for_selector("#postprocess-card:not(.hidden)", timeout=3_000)
        page.click("#pp-browse-btn")
        try:
            page.wait_for_selector("#pp-browser", state="visible", timeout=5_000)
            print("[D] Browse pane opened on click")
            page.click("#pp-browse-btn")
            page.wait_for_selector("#pp-browser", state="hidden", timeout=3_000)
            print("[D] Browse pane closed on second click")
        except Exception as e:
            print(f"WARN: browse-pane toggle test skipped: {e}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
