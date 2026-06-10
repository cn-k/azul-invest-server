# Azul Investment — Sahibinden Scraper

## Project Overview
Scrapes apartment listings from sahibinden.com and stores them in a Railway PostgreSQL database.
Runs once daily at 02:00 TR time (23:00 UTC). Designed to run on a local Windows/Mac machine
(not Railway cloud) because sahibinden.com uses Cloudflare + PerimeterX bot protection that
blocks headless browsers.

## Architecture
- **scraper.py** — Core scraping logic. Uses Playwright with real Chrome via CDP.
- **clock.py** — APScheduler cron job. Runs `scrape_district()` for each active district.
- **db.py** — SQLAlchemy ORM. Schema: `sahibinden`. Two tables: `apartments`, `districts`.
- **cities.json** — Source of truth for which districts to scrape (`is_active` flag).
- **save_cookies.py** — One-time tool: exports cookies from Chrome into `cookies.json`.

## Browser Mode (auto-detected from .env)
Priority order:
1. `PLAYWRIGHT_WS_URL` set → Browserless (NOT recommended — blocked by bot protection)
2. `CDP_URL` set → Local real Chrome via CDP (**current mode, works**)
3. Neither → Headless Chromium + playwright-stealth (**blocked by Cloudflare/PerimeterX**)

**Always use CDP mode.** Headless Chromium cannot bypass sahibinden.com bot protection.

## Running Chrome for CDP
```bash
# Mac
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=$HOME/.chrome-sahibinden \
  --no-first-run --no-default-browser-check

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir=C:\chrome-sahibinden ^
  --no-first-run --no-default-browser-check
```

## Running the Scraper
```bash
# Test mode — runs immediately, ignores schedule
python clock.py --test

# Production — waits for 02:00 TR cron
python clock.py
```

## Environment Variables (.env)
```
DATABASE_URL=postgresql://...          # Railway PostgreSQL
CDP_URL=http://localhost:9222          # Local Chrome debug port
# PROXY_SERVER=http://...             # Only needed for cloud deployment
# PROXY_USER=...
# PROXY_PASS=...
```

## Database Schema (`sahibinden` schema)
### apartments
| Column | Type | Notes |
|---|---|---|
| sahibinden_id | String (PK) | Extracted from listing URL |
| title | String | From thumbnail `<a>` title attribute |
| price | String | |
| location | String | First line of location cell (e.g. "Etimesgut, Ankara") |
| neighbourhood | String | Second line (e.g. "Atatürk Mah.") |
| room_count | String | e.g. "3+1" |
| size_m2 | String | e.g. "120 m²" |
| floor | String | |
| building_age | String | |
| listing_type | String | "satilik-daire" or "kiralik-daire" |
| district | String | slug e.g. "ankara-etimesgut" |
| url | String | Full sahibinden.com URL |
| ilan_tarihi | String | Listing date as shown on site |
| scraped_at | DateTime | UTC |

### districts
Tracks sync state per district. Seeded from `cities.json` on startup.

Key fields: `slug`, `status` (pending → loading → active), `last_synced_at`.

## Scraping Logic (`scrape_district`)
- **initial load** (`last_synced_at IS NULL`): Fetches up to 20 pages, no ID comparison (avoids race condition from page shifts).
- **delta sync**: Checks each page for known IDs. Stops when found, adds only new listings. Continues up to 20 pages if no known IDs found.
- Sorting: `date_desc`, `pagingSize=50` → max 1000 listings per district per run.
- Inter-page delay: 15–20 seconds (anti-ban).
- Inter-district delay: 30–60 seconds.

## Adding New Districts
Edit `cities.json`, set `is_active: true`. They are auto-seeded into DB on next startup.

## Known Issues / Anti-Bot Notes
- sahibinden.com uses **Cloudflare** (JS challenge) + **PerimeterX** (fingerprinting).
- Headless Chromium is reliably blocked even with playwright-stealth.
- Real Chrome via CDP bypasses both — use a persistent `--user-data-dir` so cookies/session persist.
- If blocked, re-run `save_cookies.py` to refresh cookies from a manual Chrome session.

## Dependencies
- `playwright==1.41.2` — pinned for stability
- `playwright-stealth==1.0.6` — only used in headless fallback mode
- `setuptools==69.5.1` — required by playwright-stealth (pkg_resources)
- `sqlalchemy`, `psycopg2-binary`, `python-dotenv`, `apscheduler`, `pytz`
