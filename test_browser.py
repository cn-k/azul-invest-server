"""Minimal browser test — stealth olmadan ve stealth ile"""
import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        print("1) Browser başlatılıyor...")
        browser = await p.chromium.launch(headless=True)
        print("2) Context oluşturuluyor...")
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
        )
        print("3) Sayfa açılıyor...")
        page = await context.new_page()

        try:
            from playwright_stealth import stealth_async
            print("4) Stealth uygulanıyor...")
            await stealth_async(page)
            print("   Stealth OK")
        except Exception as e:
            print(f"   Stealth HATA: {e}")

        print("5) sahibinden.com'a gidiliyor...")
        try:
            resp = await page.goto(
                "https://www.sahibinden.com/satilik-daire/ankara-akyurt?viewType=Classic&pagingOffset=0&pagingSize=50&sorting=date_desc",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            print(f"6) Yanıt status: {resp.status if resp else 'None'}")
            title = await page.title()
            print(f"7) Sayfa başlığı: {title}")
            rows = await page.query_selector_all("tr.searchResultsItem")
            print(f"8) Satır sayısı: {len(rows)}")
            if not rows:
                snippet = (await page.content())[:800]
                print(f"   HTML snippet:\n{snippet}")
        except Exception as e:
            print(f"   HATA: {type(e).__name__}: {e}")

        await browser.close()
        print("TAMAMLANDI")

asyncio.run(test())
