"""
clock.py — Long-running Railway worker.

Günde 1 kez saat 02:00 TR (23:00 UTC) çalışır.
Tüm aktif district'ler için unified scraper çalıştırır:
  - DB'de bilinen kayıt bulunana kadar sayfaları gezer (max 20 sayfa)
  - Bilinen kayıt bulununca sadece yeni olanları ekler, durur
"""

import asyncio
import logging
import random
import sys
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import (
    get_all_districts,
    mark_synced,
    seed_districts_from_json,
    setup_tables,
)
from scraper import scrape_district

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TR_TZ = pytz.timezone("Europe/Istanbul")
scheduler = AsyncIOScheduler(timezone=TR_TZ)


@scheduler.scheduled_job("cron", hour=23, minute=0, timezone=pytz.UTC)  # 02:00 TR
async def run_scraper():
    """Günde bir kez çalışır — 02:00 TR saati (23:00 UTC)."""
    logger.info("=" * 60)
    logger.info("Günlük scraper başlıyor — %s", datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M TR"))
    logger.info("=" * 60)

    districts = get_all_districts()
    if not districts:
        logger.info("Hiç district bulunamadı.")
        return

    logger.info("%d district işlenecek.", len(districts))

    for i, d in enumerate(districts):
        today = datetime.now(timezone.utc).date()
        if d.last_synced_at and d.last_synced_at.date() >= today:
            logger.info("[%s] Bugün zaten çalıştırıldı, atlanıyor.", d.slug)
            continue

        is_initial = d.last_synced_at is None
        mode = "initial load" if is_initial else "delta sync"
        logger.info("[%s] Başlıyor (%d/%d) — %s", d.slug, i + 1, len(districts), mode)
        await scrape_district(d.slug, listing_type=d.listing_type, initial_load=is_initial)
        mark_synced(d.slug)

        # District'ler arası bekleme
        if i < len(districts) - 1:
            delay = random.uniform(30, 60)
            logger.info("Sonraki district için %.0f saniye bekleniyor...", delay)
            await asyncio.sleep(delay)

    logger.info("Tüm district'ler tamamlandı.")


async def main():
    logger.info("Veritabanı tabloları hazırlanıyor...")
    setup_tables()

    new_count = seed_districts_from_json()
    if new_count:
        logger.info("%d yeni district eklendi (cities.json).", new_count)

    districts = get_all_districts()
    logger.info("Toplam district: %d", len(districts))
    logger.info("Sonraki çalışma: her gün 02:00 TR saatinde (23:00 UTC)")

    scheduler.start()
    logger.info("Scheduler başlatıldı.")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Kapatılıyor...")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    if "--test" in sys.argv:
        async def test_run():
            logger.info("TEST MODU — job hemen çalıştırılıyor.")
            setup_tables()
            new_count = seed_districts_from_json()
            if new_count:
                logger.info("%d yeni district eklendi.", new_count)
            await run_scraper()
        asyncio.run(test_run())
    else:
        asyncio.run(main())
