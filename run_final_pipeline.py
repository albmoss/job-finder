"""
Główny pipeline: scraping -> czyszczenie -> analiza AI -> ewaluacja.

Kolejność ma znaczenie:
  0. Archiwizacja+purge  - chroni ręczne oceny PRZED usunięciem starych ofert
  1. Scraping            - pomija źródła zescrapowane dziś
  1.5 Normalizacja linków - musi być przed deduplikacją, inaczej ta sama oferta
                            pod dwoma URL-ami przejdzie jako dwie różne
  2. Deduplikacja + czyszczenie tokenów
  3. Analiza AI (kaskada modeli + rotacja kluczy)
  4. Ewaluacja rankingu  - jedyny sposób, żeby stwierdzić czy cokolwiek się poprawiło
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.console import force_utf8

force_utf8()

from main_scraper import run_all_scrapers
from utils.data_models import JobDatabase
from config import JOBS_DATABASE_PATH
import clean_db
import purge_stale_offers
import deduplicate_db
import migrate_normalize_links

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Pipeline")


def _phase(name, fn, critical=False):
    """Uruchom etap; niekrytyczny błąd nie zatrzymuje pipeline'u."""
    logger.info(f"── {name}")
    try:
        fn()
        return True
    except Exception as e:
        logger.error(f"{name} failed: {e}")
        if critical:
            raise
        return False


def run_pipeline(skip_scraping=False):
    print("\n" + "=" * 60)
    print("PIPELINE: scraping -> analysis -> evaluation")
    print("=" * 60)

    # 0. Archiwizuj oceny i usuń przeterminowane oferty
    _phase("PHASE 0: Archive ratings + drop offers older than 14 days", purge_stale_offers.main)

    # 1. Scraping
    if skip_scraping:
        logger.info("PHASE 1: skipped (--skip-scraping)")
    else:
        _phase("PHASE 1: Scrape sources", run_all_scrapers, critical=True)

    # 1.5 Normalizacja linków (musi poprzedzać deduplikację)
    _phase("PHASE 1.5: Link normalisation", migrate_normalize_links.main)

    # 2. Deduplikacja i czyszczenie
    _phase("PHASE 2: Database deduplication", deduplicate_db.run)
    _phase("PHASE 2.5: Description cleanup (token diet)", clean_db.run)

    jobs = JobDatabase(str(JOBS_DATABASE_PATH)).load_jobs()
    logger.info(f"The database holds {len(jobs)} offers.")
    if not jobs:
        logger.error("Database empty - aborting before AI analysis.")
        return

    # 3. Analiza AI
    def _analyze():
        import waterfall_analysis
        waterfall_analysis.main()

    _phase("PHASE 3: AI analysis (waterfall)", _analyze)

    # 4. Ewaluacja - czy ranking faktycznie działa?
    def _eval():
        import eval_ranking
        eval_ranking.main([])

    _phase("PHASE 4: Ranking evaluation", _eval)

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    run_pipeline(skip_scraping="--skip-scraping" in sys.argv)
