"""Script to capture high quality screenshots using Playwright and system chromium."""
import os
import shutil
import time
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = os.path.abspath("screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def main():
    print("Launching Playwright Chromium...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium",
            args=["--no-sandbox", "--disable-gpu"]
        )

        # 1. VOD Screen (Desktop 1440x900)
        print("Capturing VOD...")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://localhost:5000/vod", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "vod.png"))
        print("Saved vod.png")
        page.close()

        # 2. Sports Screen (Desktop 1440x900)
        print("Capturing Sports Dashboard...")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://localhost:5000/sports", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "sports.png"))
        print("Saved sports.png")

        # 3. Sports Game Center Modal (Desktop 1440x900)
        print("Capturing Sports Game Modal...")
        game_card = page.query_selector(".sports-game-card, .sports-billboard-card")
        if game_card:
            game_card.click()
            page.wait_for_timeout(2000)
            page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "sports-game.png"))
            print("Saved sports-game.png")
        page.close()

        # 4. Weather Page (Desktop 1440x900)
        print("Capturing Weather Page...")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://localhost:5000/weather", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "weather.png"))
        print("Saved weather.png")
        page.close()

        # 5. Remote (Mobile 414x896)
        print("Capturing Mobile Remote...")
        page = browser.new_page(viewport={"width": 414, "height": 896}, is_mobile=True)
        page.goto("http://localhost:5000/remote", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "remote.png"))
        print("Saved remote.png")
        page.close()

        # 6. Copy / ensure sports channel card
        sports_card = "data/live_content/sports/card_01.png"
        if os.path.exists(sports_card):
            shutil.copyfile(sports_card, os.path.join(SCREENSHOTS_DIR, "sports-channel.png"))
            print("Copied sports channel broadcast card to screenshots/sports-channel.png")

        browser.close()
    print("All screenshots successfully captured!")

if __name__ == "__main__":
    main()
