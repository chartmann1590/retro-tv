"""Exercise the running receiver (controls the TV) using real library media.

venv/bin/python tests/test_playback_e2e.py --movie Sinners --browser-media-id 620 --browser-channel 4
Requires Playwright and Chromium; screenshots are written outside the repository.
"""
import argparse
import json
from playwright.sync_api import sync_playwright, expect


def run(movie, browser_media_id, browser_channel, base_url):
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                                    args=["--no-sandbox", "--disable-gpu", "--autoplay-policy=no-user-gesture-required"])
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: errors.append(f"{response.status}: {response.url}")
                if response.status >= 400 and "/api/" in response.url else None)
        request = context.request

        def get(path):
            response = request.get(base_url + path)
            assert response.ok, response.text()
            return response.json()

        def live():
            with page.expect_response("**/api/vod/stop") as response:
                page.get_by_role("button", name="● LIVE TV", exact=True).click()
            result = response.value
            assert result.request.method == "POST"
            assert result.ok and result.json()["ok"], result.text()
            expect(page.locator("#ncLive")).to_have_text("LIVE")
            state = get("/api/hdmi")
            assert not state["is_vod"] and state["mpv_alive"] and not state["paused"], state

        page.goto(base_url + "/vod", wait_until="domcontentloaded")
        page.locator("#vodSearch").fill(movie)
        page.locator(".search-card").filter(has=page.get_by_role("heading", name=movie, exact=True)).first.click()
        with page.expect_response("**/api/vod/play") as response:
            page.locator("#modalMoviePlayTV").click()
        assert response.value.json()["ok"]
        media_id = response.value.json()["vod_info"]["media_id"]
        page.goto(base_url + "/remote", wait_until="domcontentloaded")
        expect(page.locator("#ncTitle")).to_have_text(movie)
        expect(page.locator("#ncLive")).to_have_text("ON DEMAND")
        # Allow startup to settle; compare drops throughout a sustained sample.
        page.wait_for_timeout(5000)
        before = get("/api/system")["playback_health"]
        page.wait_for_timeout(30000)
        after = get("/api/system")["playback_health"]
        assert get("/api/hdmi")["is_vod"]
        assert after["alive"] and after["fps"] > 0, after
        assert after["dropped_frames"] - before["dropped_frames"] <= 2, (before, after)
        assert abs(after["avsync_ms"]) < 100, after
        print("HDMI performance:", json.dumps({"before": before, "after": after}), flush=True)
        page.screenshot(path="/tmp/retro-tv-vod-playing.png", full_page=True)
        live()
        # An already-live remote must also resume paused live playback.
        page.locator("#pauseButton").click()
        expect(page.locator("#ncLive")).to_have_text("PAUSED")
        live()
        # Opening the catalog/guide must not leave an overlay after Live TV.
        for button, endpoint in [("#vodButton", "/api/tv-vod/status"), ("#guideButton", None)]:
            page.locator(button).click()
            expect(page.locator(button)).to_contain_text("EXIT")
            live()
            if endpoint:
                assert not get(endpoint)["visible"]
            expect(page.locator(button)).not_to_contain_text("EXIT")
        # Simulate a second remote starting VOD before this handset has polled.
        assert request.post(base_url + "/api/vod/play", data={"media_id": media_id}).json()["ok"]
        page.evaluate("IS_VOD_PLAYING=false; CUR_CH=null")
        live()
        page.screenshot(path="/tmp/retro-tv-live-restored.png", full_page=True)
        # Verify browser playback advances, including after a seek.
        for path in [f"/watch?vod={browser_media_id}", f"/watch/{browser_channel}"]:
            page.goto(base_url + path, wait_until="domcontentloaded")
            page.wait_for_function("document.querySelector('video').currentTime > 0 && !document.querySelector('video').paused", timeout=150000)
            before = page.locator("video").evaluate("v => v.currentTime")
            page.wait_for_timeout(4000)
            after = page.locator("video").evaluate("v => ({time:v.currentTime,error:v.error,quality:v.getVideoPlaybackQuality().droppedVideoFrames})")
            assert after["error"] is None and after["time"] > before + 2, after
            print(path, after, flush=True)
            if "vod=" in path:
                page.locator("video").evaluate("v => {v.currentTime=60}")
                page.wait_for_function("document.querySelector('video').currentTime > 61")
        assert not errors, errors
        browser.close()
    print("Playback and Live TV regression checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--movie", required=True)
    parser.add_argument("--browser-media-id", required=True, type=int)
    parser.add_argument("--browser-channel", required=True, type=int)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    args = parser.parse_args()
    run(args.movie, args.browser_media_id, args.browser_channel, args.base_url)
