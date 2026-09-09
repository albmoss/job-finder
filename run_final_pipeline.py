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
        result = fn()
        failed_result = result is False or (
            isinstance(result, int) and not isinstance(result, bool) and result != 0
        )
        if failed_result:
            raise RuntimeError(f"stage returned failure status {result!r}")
        return True
    except Exception as e:
        logger.error(f"{name} failed: {e}")
        if critical:
            raise
        return False


def _finish(phase_results):
    """Wypisz prawdziwy stan calego przebiegu i zwroc kod sukcesu."""
    failed = [name for name, ok in phase_results.items() if not ok]
    complete = not failed

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE" if complete else "PIPELINE INCOMPLETE")
    print("=" * 60)

    if failed:
        logger.error("Failed phases: %s", ", ".join(failed))

    return complete


def run_pipeline(skip_scraping=False):
    print("\n" + "=" * 60)
    print("PIPELINE: scraping -> analysis -> evaluation")
    print("=" * 60)

    # 0. Archiwizuj oceny i usuń przeterminowane oferty
    phase_results = {}
    phase_results["PHASE 0: Archive ratings + drop offers older than 14 days"] = _phase(
        "PHASE 0: Archive ratings + drop offers older than 14 days",
        purge_stale_offers.main,
    )

    # 1. Scraping
    if skip_scraping:
        logger.info("PHASE 1: skipped (--skip-scraping)")
        phase_results["PHASE 1: Scrape sources"] = True
    else:
        phase_results["PHASE 1: Scrape sources"] = _phase(
            "PHASE 1: Scrape sources", run_all_scrapers, critical=True
        )

    # 1.5 Normalizacja linków (musi poprzedzać deduplikację)
    phase_results["PHASE 1.5: Link normalisation"] = _phase(
        "PHASE 1.5: Link normalisation", migrate_normalize_links.main
    )

    # 2. Deduplikacja i czyszczenie
    phase_results["PHASE 2: Database deduplication"] = _phase(
        "PHASE 2: Database deduplication", deduplicate_db.run
    )
    phase_results["PHASE 2.5: Description cleanup (token diet)"] = _phase(
        "PHASE 2.5: Description cleanup (token diet)", clean_db.run
    )

    jobs = JobDatabase(str(JOBS_DATABASE_PATH)).load_jobs()
    logger.info(f"The database holds {len(jobs)} offers.")
    if not jobs:
        logger.error("Database empty - aborting before AI analysis.")
        phase_results["Database validation"] = False
        return _finish(phase_results)

    # 3. Analiza AI
    def _analyze():
        import waterfall_analysis
        return waterfall_analysis.main()

    phase_results["PHASE 3: AI analysis (waterfall)"] = _phase(
        "PHASE 3: AI analysis (waterfall)", _analyze
    )

    # 4. Ewaluacja - czy ranking faktycznie działa?
    def _eval():
        import eval_ranking
        return eval_ranking.main([])

    phase_results["PHASE 4: Ranking evaluation"] = _phase(
        "PHASE 4: Ranking evaluation", _eval
    )

    return _finish(phase_results)

if __name__ == "__main__":
    sys.exit(0 if run_pipeline(skip_scraping="--skip-scraping" in sys.argv) else 1)
