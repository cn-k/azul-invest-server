"""
scraper.py — Scrapes a single page of listings from sahibinden.com.
Uses Playwright Chromium + Chrome cookies to bypass bot protection.
"""

import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path

from playwright.async_api import async_playwright

from db import insert_apartments_batch

logger = logging.getLogger(__name__)

BASE_URLS = {
    "satilik-daire": "https://www.sahibinden.com/satilik-daire",
    "kiralik-daire": "https://www.sahibinden.com/kiralik-daire",
}
COOKIES_FILE = Path(__file__).parent / "cookies.json"
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

# Browser connection — priority order:
#   1. PLAYWRIGHT_WS_URL  → Railway browserless (internal ws://)
#   2. CDP_URL            → Local real Chrome (http://localhost:9222)
#   3. fallback           → Local headless Chromium
PLAYWRIGHT_WS_URL = os.getenv("PLAYWRIGHT_WS_URL", "")  # Railway browserless
CDP_URL           = os.getenv("CDP_URL", "")             # Local real Chrome
PROXY_SERVER      = os.getenv("PROXY_SERVER", "")        # ör: http://proxy.webshare.io:80
PROXY_USER        = os.getenv("PROXY_USER", "")
PROXY_PASS        = os.getenv("PROXY_PASS", "")

MODE = "browserless" if PLAYWRIGHT_WS_URL else ("cdp" if CDP_URL else "headless")
logger_mode = logging.getLogger(__name__)

VALID_SAME_SITE = {"Strict", "Lax", "None"}


async def _get_page(p):
    """
    Returns (browser, context, page).

    Mode is auto-detected from environment variables:

      PLAYWRIGHT_WS_URL set  → Railway Browserless (ws://browserless.railway.internal:...)
      CDP_URL set            → Local real Chrome   (http://localhost:9222)
      neither set            → Local headless Chromium + cookies.json (fallback)
    """
    if MODE == "browserless":
        logger.info("Browser mode: Browserless (%s)", PLAYWRIGHT_WS_URL[:50])
        browser = await p.chromium.connect(PLAYWRIGHT_WS_URL)

        context_opts = dict(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        if PROXY_SERVER:
            context_opts["proxy"] = {
                "server": PROXY_SERVER,
                **({"username": PROXY_USER} if PROXY_USER else {}),
                **({"password": PROXY_PASS} if PROXY_PASS else {}),
            }
            logger.info("Proxy aktif: %s", PROXY_SERVER)

        context = await browser.new_context(**context_opts)
        page = await context.new_page()
        return browser, context, page

    if MODE == "cdp":
        logger.info("Browser mode: Local Chrome CDP (%s)", CDP_URL)
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        return browser, context, page

    # Fallback: local headless Chromium + cookies
    logger.info("Browser mode: Headless Chromium + cookies.json")
    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="tr-TR",
        viewport={"width": 1280, "height": 800},
    )
    cookies = _load_cookies()
    if cookies:
        await context.add_cookies(cookies)
    page = await context.new_page()
    return browser, context, page


async def _close(browser):
    """Browserless/CDP: don't close remote browser. Headless: close."""
    if MODE == "headless":
        await browser.close()


def _load_cookies() -> list:
    if not COOKIES_FILE.exists():
        logger.warning("cookies.json not found — run save_cookies.py first!")
        return []
    with open(COOKIES_FILE) as f:
        raw = json.load(f)

    cookies = []
    for c in raw:
        # Cookie-Editor uses "expirationDate", Playwright uses "expires"
        if "expirationDate" in c and "expires" not in c:
            c["expires"] = c.pop("expirationDate")
        elif "expirationDate" in c:
            c.pop("expirationDate")

        # Normalize sameSite — force to valid value, default Lax
        same_site_map = {
            "no_restriction": "None",
            "none": "None",
            "lax": "Lax",
            "strict": "Strict",
            "unspecified": "Lax",
        }
        raw_ss = str(c.get("sameSite", "")).lower()
        c["sameSite"] = same_site_map.get(raw_ss, "Lax")

        # Remove fields Playwright doesn't accept
        for field in ("hostOnly", "storeId", "id", "session"):
            c.pop(field, None)

        # Must have name, value, domain
        if c.get("name") and c.get("domain"):
            cookies.append(c)

    logger.info("Loaded %d cookies from cookies.json", len(cookies))
    return cookies


async def scrape_page(district: str, offset: int, listing_type: str = "satilik-daire") -> int:
    base = BASE_URLS.get(listing_type, BASE_URLS["satilik-daire"])
    url = (
        f"{base}/{district}"
        f"?viewType=Classic&pagingOffset={offset}&pagingSize=50&sorting=date_asc"
    )
    logger.info("Scraping %s (offset=%d)", district, offset)

    async with async_playwright() as p:
        browser, context, page = await _get_page(p)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for JS to populate real listing links (href="#" means JS not ready)
            try:
                await page.wait_for_selector(
                    "td.searchResultsTitleValue a[href*='/ilan/']",
                    timeout=10000
                )
            except Exception:
                logger.warning("Real links did not appear in 10s — trying anyway")
            await asyncio.sleep(random.uniform(1, 2))

            title = await page.title()
            logger.info("Page title: %s", title)

            if "giriş" in title.lower():
                logger.error("Redirected to login — re-run save_cookies.py")
                await page.close()
                await _close(browser)
                return -1  # error, not end of results

            rows = await page.query_selector_all("tr.searchResultsItem")
            logger.info("Rows found: %d", len(rows))

            if not rows:
                snippet = (await page.content())[:600]
                logger.warning("No rows matched selector. HTML snippet:\n%s", snippet)
                await page.close()
                await _close(browser)
                return 0  # truly empty page → caller should mark done

            # Parse all rows concurrently
            parse_tasks = [_parse_row(row) for row in rows]
            parsed = await asyncio.gather(*parse_tasks, return_exceptions=True)

            batch = []
            for data in parsed:
                if isinstance(data, Exception):
                    logger.warning("Row parse error: %s", data)
                    continue
                data["district"] = district
                data["listing_type"] = listing_type
                if data.get("sahibinden_id"):
                    batch.append(data)
                else:
                    logger.warning("No sahibinden_id for url: %s", data.get("url"))

            # Single DB transaction for the whole page
            saved = insert_apartments_batch(batch)
            logger.info("Page done — %d/%d listings saved (offset=%d)", saved, len(rows), offset)

            # Return metadata for state tracking
            last = batch[-1] if batch else {}
            await page.close()
            return {
                "rows_found": len(rows),
                "saved": saved,
                "last_sahibinden_id": last.get("sahibinden_id"),
                "last_ilan_tarihi": last.get("ilan_tarihi"),
            }

        except Exception as e:
            logger.error("Page scrape failed (offset=%d): %s", offset, e)
            try:
                await page.close()
                await _close(browser)
            except Exception:
                pass
            return -1  # error — do NOT mark done, just skip this tick


async def _parse_row(row) -> dict:
    async def text(selector):
        el = await row.query_selector(selector)
        return (await el.inner_text()).strip() if el else ""

    # Title ve href → td.searchResultsLargeThumbnail > a (title attribute'da)
    thumb_el = await row.query_selector("td.searchResultsLargeThumbnail a")
    title = (await thumb_el.get_attribute("title") or "").strip() if thumb_el else ""
    href  = (await thumb_el.get_attribute("href")  or "").strip() if thumb_el else ""
    full_url = f"https://www.sahibinden.com{href}" if href and href != "#" else None

    # sahibinden_id: önce href'den, sonra data-id'den
    id_match = re.search(r"/(\d{7,12})(?:[/?#]|$)", href or "")
    sahibinden_id = id_match.group(1) if id_match else None

    if not sahibinden_id:
        sahibinden_id = await row.get_attribute("data-id")

    if not sahibinden_id:
        row_id = await row.get_attribute("id") or ""
        id_match2 = re.search(r"(\d{7,12})", row_id)
        sahibinden_id = id_match2.group(1) if id_match2 else None

    attr_cells = await row.query_selector_all("td.searchResultsAttributeValue")
    attrs = [(await cell.inner_text()).strip() for cell in attr_cells]

    ilan_tarihi = await text("td.searchResultsDateValue")

    # Location: üst satır = ilçe/şehir, alt satır = mahalle
    loc_el = await row.query_selector("td.searchResultsLocationValue")
    location_raw = (await loc_el.inner_text()).strip() if loc_el else ""
    loc_parts = [p.strip() for p in location_raw.split("\n") if p.strip()]
    location     = loc_parts[0] if len(loc_parts) > 0 else None  # "Etimesgut, Ankara"
    neighbourhood = loc_parts[1] if len(loc_parts) > 1 else None  # "Atatürk Mah."

    # Sahibinden attribute column order: size_m2 (m²), room_count (3+1), floor, building_age
    return {
        "sahibinden_id": sahibinden_id,
        "title": title or None,
        "price": await text("td.searchResultsPriceValue") or None,
        "location": location,
        "neighbourhood": neighbourhood,
        "size_m2": attrs[0] if len(attrs) > 0 else None,
        "room_count": attrs[1] if len(attrs) > 1 else None,
        "floor": attrs[2] if len(attrs) > 2 else None,
        "building_age": attrs[3] if len(attrs) > 3 else None,
        "ilan_tarihi": ilan_tarihi or None,
        "url": full_url,
    }


async def scrape_district(
    district: str,
    listing_type: str = "satilik-daire",
    max_pages: int = 20,
    initial_load: bool = False,
) -> int:
    """
    Unified scraper — initial load ve delta sync için tek fonksiyon.

    initial_load=True  → Tüm sayfaları çek (max 20), karşılaştırma yapma.
                         Sayfa kayması (race condition) sorununu önler.
    initial_load=False → Her sayfada bilinen ID var mı kontrol et:
                           - Var → yeni olanları ekle, dur.
                           - Yok → hepsini ekle, sonraki sayfaya geç.

    Returns: toplam eklenen ilan sayısı
    """
    from db import Apartment, get_engine
    from sqlalchemy.orm import Session

    base = BASE_URLS.get(listing_type, BASE_URLS["satilik-daire"])
    engine = get_engine()
    total_added = 0

    for page_num in range(max_pages):
        offset = page_num * 50
        url = (
            f"{base}/{district}"
            f"?viewType=Classic&pagingOffset={offset}&pagingSize=50&sorting=date_desc"
        )
        logger.info("[%s] Sayfa %d (offset=%d)", district, page_num + 1, offset)

        async with async_playwright() as p:
            browser, context, page = await _get_page(p)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector(
                        "td.searchResultsLargeThumbnail a[href*='/ilan/']", timeout=10000
                    )
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(1, 2))

                rows = await page.query_selector_all("tr.searchResultsItem")
                if not rows:
                    logger.info("[%s] Boş sayfa — durduruluyor.", district)
                    await page.close()
                    await _close(browser)
                    break

                parse_tasks = [_parse_row(row) for row in rows]
                parsed = await asyncio.gather(*parse_tasks, return_exceptions=True)
                await page.close()
                await _close(browser)

            except Exception as e:
                logger.error("[%s] Sayfa %d hatası: %s", district, page_num + 1, e)
                try:
                    await page.close()
                    await _close(browser)
                except Exception:
                    pass
                break

        if initial_load:
            # Initial load: karşılaştırma yapma, hepsini ekle (ON CONFLICT DO NOTHING)
            all_rows = []
            for data in parsed:
                if isinstance(data, Exception) or not data.get("sahibinden_id"):
                    continue
                data["district"] = district
                data["listing_type"] = listing_type
                all_rows.append(data)
            added = insert_apartments_batch(all_rows)
            total_added += added
            logger.info("[%s] Sayfa %d — %d kayıt eklendi.", district, page_num + 1, added)
        else:
            # Delta: sayfada bilinen ID var mı kontrol et
            with Session(engine) as session:
                has_known = any(
                    session.get(Apartment, d.get("sahibinden_id"))
                    for d in parsed
                    if not isinstance(d, Exception) and d.get("sahibinden_id")
                )

            if has_known:
                # Sadece yeni olanları ekle, dur
                new_rows = []
                with Session(engine) as session:
                    for data in parsed:
                        if isinstance(data, Exception):
                            continue
                        sid = data.get("sahibinden_id")
                        if not sid or session.get(Apartment, sid):
                            continue
                        data["district"] = district
                        data["listing_type"] = listing_type
                        new_rows.append(data)
                added = insert_apartments_batch(new_rows)
                total_added += added
                logger.info("[%s] Bilinen kayıt bulundu — %d yeni eklendi, durduruluyor.", district, added)
                break
            else:
                # Hepsini ekle, sonraki sayfaya geç
                all_rows = []
                for data in parsed:
                    if isinstance(data, Exception) or not data.get("sahibinden_id"):
                        continue
                    data["district"] = district
                    data["listing_type"] = listing_type
                    all_rows.append(data)
                added = insert_apartments_batch(all_rows)
                total_added += added
                logger.info("[%s] Sayfa %d — %d kayıt eklendi, sonraki sayfaya geçiliyor.", district, page_num + 1, added)

        # Her iki modda da sayfalar arası bekleme (son sayfa hariç)
        if page_num < max_pages - 1:
            delay = random.uniform(15, 20)
            logger.info("[%s] %.1f saniye bekleniyor...", district, delay)
            await asyncio.sleep(delay)

    logger.info("[%s] Tamamlandı — toplam %d ilan eklendi.", district, total_added)
    return total_added
