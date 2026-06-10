"""
save_cookies.py — Connects to your real Chrome and saves sahibinden cookies.

STEPS:
1. Open a NEW terminal and run:
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
     --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-scraper

2. In that Chrome window, go to sahibinden.com and browse normally
   (pass any challenges, log in if you want)

3. Run this script: python save_cookies.py
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

COOKIES_FILE = Path(__file__).parent / "cookies.json"
CDP_URL = "http://localhost:9222"

# Build TARGET_URL from the first district in cities.json
def _first_district_url() -> str:
    cities_file = Path(__file__).parent / "cities.json"
    with open(cities_file, encoding="utf-8") as f:
        data = json.load(f)
    city = data["cities"][0]["name"].lower()
    d = data["cities"][0]["districts"][0]
    district = (d["name"] if isinstance(d, dict) else d).lower()
    return f"https://www.sahibinden.com/satilik-daire/{city}-{district}?pagingOffset=0&sorting=date_asc"

TARGET_URL = _first_district_url()


async def main():
    print("🔌 Connecting to real Chrome on port 9222...")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
        except Exception:
            print("❌ Could not connect to Chrome!")
            print("   Make sure Chrome is running with --remote-debugging-port=9222")
            print("   Command:")
            print('   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\')
            print('     --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-scraper')
            return

        print("✅ Connected to real Chrome!")

        # Use existing context (already has your session/cookies)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        print(f"📍 Navigating to sahibinden listing page...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        title = await page.title()
        print(f"📄 Page title: {title}")

        rows = await page.query_selector_all("tr.searchResultsItem")
        print(f"📋 Listings found: {len(rows)}")

        if rows:
            # Save cookies from this real Chrome session
            cookies = await context.cookies()
            with open(COOKIES_FILE, "w") as f:
                json.dump(cookies, f, indent=2)
            print(f"\n✅ {len(cookies)} cookies saved to cookies.json")
            print("🎉 You can now run: python clock.py")
        else:
            content = (await page.content()).lower()
            if "basılı tut" in content or "kontrol" in content:
                print("\n⚠️  Cloudflare challenge still showing in Chrome.")
                print("   Solve it manually in the Chrome window, then re-run this script.")
            else:
                print(f"\n⚠️  Page loaded ('{title}') but no listings found.")
                print("   CSS selector 'tr.searchResultsItem' may need updating.")
                snippet = (await page.content())[:800]
                print(f"\nHTML snippet:\n{snippet}")


if __name__ == "__main__":
    asyncio.run(main())
