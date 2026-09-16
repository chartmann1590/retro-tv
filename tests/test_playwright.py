"""Playwright end-to-end browser test for Retro TV.
Tests all pages, button interactions on /remote, console errors, and API responses.
Run with: venv/bin/python tests/test_playwright.py
"""
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:5000"


def run_e2e_tests():
    print("=== Starting Playwright End-to-End Tests ===")
    errors_found = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium",
            args=["--no-sandbox", "--disable-gpu"],
            headless=True
        )
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Capture console messages and errors
        console_logs = []
        page_errors = []
        failed_responses = []

        def on_console(msg):
            text = msg.text
            if msg.type in ("error", "warning"):
                console_logs.append(f"[{msg.type.upper()}] {text}")
                if msg.type == "error":
                    # Filter out expected harmless browser noise (e.g. favicon or hls autoplay policies)
                    if not any(ign in text.lower() for ign in ["favicon", "mediaelement"]):
                        errors_found.append(f"Console error: {text}")

        def on_pageerror(err):
            page_errors.append(str(err))
            errors_found.append(f"Unhandled Page Exception: {err}")

        def on_response(res):
            if res.status >= 400:
                # Some 404s like favicon.ico are benign, but API 4xx/5xx are errors
                url = res.url
                if not url.endswith("/favicon.ico"):
                    failed_responses.append(f"{res.status} on {url}")
                    errors_found.append(f"HTTP {res.status} on {url}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)

        # ------------------------------------------------------------------
        # 1. Test /remote page & Handset Interactions
        # ------------------------------------------------------------------
        print("\n--- 1. Testing /remote ---")
        page.goto(f"{BASE_URL}/remote", wait_until="domcontentloaded")
        time.sleep(1)

        # Verify handset elements
        vod_btn = page.locator("#vodButton")
        guide_btn = page.locator("#guideButton")
        dpad_up = page.locator("button[aria-label='Up']")
        dpad_down = page.locator("button[aria-label='Down']")
        dpad_left = page.locator("button[aria-label='Left']")
        dpad_right = page.locator("button[aria-label='Right']")
        dpad_ok = page.locator("button[aria-label='Select / OK']")
        nc_ch = page.locator("#ncCh")
        nc_title = page.locator("#ncTitle")

        assert vod_btn.is_visible(), "VOD button not visible on remote!"
        assert guide_btn.is_visible(), "Guide button not visible on remote!"
        print("✓ Handset remote controls visible")

        # Verify NO embedded catalog or modals exist on the remote page
        assert page.locator("#remoteVod").count() == 0, "Error: #remoteVod still in remote.html DOM!"
        assert page.locator("#rvodShowModal").count() == 0, "Error: #rvodShowModal still in remote.html DOM!"
        print("✓ Confirmed: NO embedded catalog or modals on remote page (solely on TV)")

        # Test ON DEMAND button press
        print("Clicking ON DEMAND button...")
        vod_btn.click()
        page.wait_for_timeout(1000)

        btn_text = vod_btn.inner_text().strip()
        print(f"  VOD button text after click: '{btn_text}'")
        assert "EXIT" in btn_text or "VOD" in btn_text, f"Unexpected button label: {btn_text}"

        ch_text = nc_ch.inner_text().strip()
        print(f"  Handset LCD Channel: '{ch_text}'")
        assert ch_text == "OD", f"Expected LCD channel 'OD', got '{ch_text}'"

        title_text = nc_title.inner_text().strip()
        print(f"  Handset LCD Title: '{title_text}'")
        assert len(title_text) > 0, "LCD title is empty!"

        # Test D-Pad Navigation while in VOD mode
        print("Testing D-Pad Right...")
        dpad_right.click()
        page.wait_for_timeout(500)
        print(f"  LCD Title after Right: '{nc_title.inner_text().strip()}'")

        print("Testing D-Pad Down (next category)...")
        dpad_down.click()
        page.wait_for_timeout(500)
        print(f"  LCD Title after Down: '{nc_title.inner_text().strip()}'")

        print("Testing D-Pad Left...")
        dpad_left.click()
        page.wait_for_timeout(500)

        print("Testing D-Pad Up...")
        dpad_up.click()
        page.wait_for_timeout(500)

        # Test Exit VOD
        print("Clicking EXIT VOD button...")
        vod_btn.click()
        page.wait_for_timeout(1000)
        print(f"  VOD button text after exit: '{vod_btn.inner_text().strip()}'")
        assert vod_btn.inner_text().strip() == "ON DEMAND", "VOD button did not revert to ON DEMAND"

        # Test Guide Open & Close
        print("Clicking GUIDE button...")
        guide_btn.click()
        page.wait_for_timeout(1500)
        print(f"  Guide button text: '{guide_btn.inner_text().strip()}'")
        assert "EXIT" in guide_btn.inner_text().strip(), f"Guide button did not toggle to EXIT GUIDE (got: {guide_btn.inner_text().strip()})"

        guide_btn.click()
        page.wait_for_timeout(1500)
        print(f"  Guide button text after close: '{guide_btn.inner_text().strip()}'")
        assert guide_btn.inner_text().strip() == "GUIDE", f"Guide button did not revert to GUIDE (got: {guide_btn.inner_text().strip()})"
        print("✓ Guide open & close passed")

        # Test Volume buttons
        vol_up = page.locator("button[aria-label='Volume up']")
        vol_down = page.locator("button[aria-label='Volume down']")
        vol_up.click()
        page.wait_for_timeout(200)
        vol_down.click()
        page.wait_for_timeout(200)
        print("✓ Volume controls passed")

        # Test Live TV button
        live_btn = page.locator("button:has-text('LIVE TV')")
        live_btn.click()
        page.wait_for_timeout(500)
        print("✓ LIVE TV button passed")

        # ------------------------------------------------------------------
        # 2. Test Home Page (/)
        # ------------------------------------------------------------------
        print("\n--- 2. Testing / (Home) ---")
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        assert page.locator("text=Your living room").count() > 0 or page.locator("text=CABLE").count() > 0
        print("✓ Home page loaded successfully")

        # ------------------------------------------------------------------
        # 3. Test Guide Page (/guide)
        # ------------------------------------------------------------------
        print("\n--- 3. Testing /guide ---")
        page.goto(f"{BASE_URL}/guide", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        assert page.locator("text=TV GUIDE").count() > 0
        print("✓ Guide page loaded successfully")

        # ------------------------------------------------------------------
        # 4. Test Watch Page (/watch)
        # ------------------------------------------------------------------
        print("\n--- 4. Testing /watch ---")
        page.goto(f"{BASE_URL}/watch", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        assert page.locator("video").count() > 0
        print("✓ Watch player page loaded successfully")

        # ------------------------------------------------------------------
        # 5. Test VOD Page (/vod)
        # ------------------------------------------------------------------
        print("\n--- 5. Testing /vod ---")
        page.goto(f"{BASE_URL}/vod", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        assert page.locator(".vod-brand").count() > 0 or page.locator(".vod-logo").count() > 0
        print("✓ VOD catalog page loaded successfully")

        # ------------------------------------------------------------------
        # 6. Test Admin Page (/admin)
        # ------------------------------------------------------------------
        print("\n--- 6. Testing /admin ---")
        page.goto(f"{BASE_URL}/admin", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        assert page.locator("text=System Status").count() > 0 or page.locator("text=Channels").count() > 0
        print("✓ Admin page loaded successfully")

        browser.close()

    print("\n=== Test Results Summary ===")
    if console_logs:
        print(f"Console warnings/errors ({len(console_logs)}):")
        for l in console_logs[:10]:
            print(f"  {l}")
    if failed_responses:
        print(f"Failed HTTP responses ({len(failed_responses)}):")
        for r in failed_responses:
            print(f"  {r}")
    if page_errors:
        print(f"Page errors ({len(page_errors)}):")
        for e in page_errors:
            print(f"  {e}")

    if errors_found:
        print(f"\n❌ FAILED with {len(errors_found)} error(s)!")
        for err in errors_found:
            print(f"  - {err}")
        return 1
    else:
        print("\n✅ ALL PLAYWRIGHT TESTS PASSED CLEANLY WITH ZERO ERRORS!")
        return 0


if __name__ == "__main__":
    sys.exit(run_e2e_tests())
