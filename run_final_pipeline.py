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

from datetime import datetime
import logging
import os
import sys
from pathlib import Path
import time
sys.path.insert(0, str(Path(__file__).parent))

from utils.console import force_utf8

force_utf8()

from main_scraper import run_all_scrapers
from utils.data_models import JobDatabase
from utils.safe_io import load_json_safe, save_json_atomic
from config import JOBS_DATABASE_PATH, LAST_SCRAPE_RUN_PATH
import clean_db
import purge_stale_offers
import deduplicate_db
import migrate_normalize_links

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Pipeline")

CHECKPOINT_FILE = Path(__file__).resolve().parent / "pipeline_checkpoint.json"
STOP_FLAG_FILE = Path(__file__).resolve().parent / "pipeline_stop_requested.flag"


def is_stop_requested() -> bool:
    return STOP_FLAG_FILE.exists()


def clear_stop_flag():
    try:
        if STOP_FLAG_FILE.exists():
            STOP_FLAG_FILE.unlink()
    except Exception:
        pass


def load_checkpoint() -> dict:
    return load_json_safe(CHECKPOINT_FILE, default={}) or {}


def save_checkpoint(completed_stages, options, current_stage=None, failed_stage=None, stopped=False):
    data = {
        "completed_stages": list(completed_stages),
        "options": options,
        "current_stage": current_stage,
        "failed_stage": failed_stage,
        "stopped": stopped,
        "updated_at": datetime.now().isoformat(),
    }
    save_json_atomic(CHECKPOINT_FILE, data, backup=False)


def clear_checkpoint():
    try:
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
    except Exception:
        pass


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


def _finish(phase_results, stopped=False):
    """Wypisz prawdziwy stan calego przebiegu i zwroc kod sukcesu."""
    if stopped:
        print("\n" + "=" * 60)
        print("PIPELINE STOPPED")
        print("=" * 60)
        logger.info("Pipeline stopped by user request.")
        clear_stop_flag()
        return False
    failed = [name for name, ok in phase_results.items() if not ok]
    complete = not failed

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE" if complete else "PIPELINE INCOMPLETE")
    print("=" * 60)

    if failed:
        logger.error("Failed phases: %s", ", ".join(failed))

    return complete

def run_pipeline(skip_scraping=False, rescore_all=False, rescore_changed=False, resume=False):
    print("\n" + "=" * 60)
    print("PIPELINE: scraping -> analysis -> evaluation")
    print("=" * 60)

    options = {
        "skip_scraping": skip_scraping,
        "rescore_all": rescore_all,
        "rescore_changed": rescore_changed,
    }

    if resume:
        checkpoint = load_checkpoint()
        completed_stages = set(checkpoint.get("completed_stages", []))
        saved_options = checkpoint.get("options", {})
        if "skip_scraping" in saved_options:
            skip_scraping = saved_options["skip_scraping"]
        if "rescore_all" in saved_options:
            rescore_all = saved_options["rescore_all"]
        if "rescore_changed" in saved_options:
            rescore_changed = saved_options["rescore_changed"]
        options.update(saved_options)
        logger.info(f"Resuming pipeline from checkpoint (completed stages: {', '.join(sorted(completed_stages)) or 'none'}).")
    else:
        completed_stages = set()
        clear_checkpoint()
        save_checkpoint(completed_stages, options, current_stage="phase0")

    # Do not clear STOP_FLAG_FILE here: the manager unlinks stale tokens before Popen,
    # so any existing token represents an immediate user stop request for this run.
    phase_results = {}

    def _execute_stage(stage_id, stage_name, fn, critical=False):
        if is_stop_requested():
            logger.info(f"Stop requested before {stage_name} - halting.")
            save_checkpoint(completed_stages, options, current_stage=stage_id, stopped=True)
            return False, True

        if stage_id in completed_stages:
            logger.info(f"── {stage_name} (skipped: already completed in checkpoint)")
            phase_results[stage_name] = True
            return True, False

        ok = _phase(stage_name, fn, critical=critical)
        phase_results[stage_name] = ok
        if ok:
            completed_stages.add(stage_id)
            save_checkpoint(completed_stages, options, current_stage=stage_id)
            return True, False
        else:
            stopped = is_stop_requested()
            save_checkpoint(completed_stages, options, failed_stage=stage_id, stopped=stopped)
            return False, stopped

    # 0. Archiwizuj oceny i usuń przeterminowane oferty
    ok, stopped = _execute_stage(
        "phase0",
        "PHASE 0: Archive ratings + drop offers older than 14 days",
        purge_stale_offers.main,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    # 1. Scraping
    if skip_scraping:
        logger.info("PHASE 1: skipped (--skip-scraping)")
        phase_results["PHASE 1: Scrape sources"] = True
        completed_stages.add("phase1")
        save_checkpoint(completed_stages, options, current_stage="phase1")
    else:
        def _scrape():
            # Granica „nowych” ofert na liście. Wznowienie zostawia start przerwanego
            # pobierania, żeby oferty z pierwszej części nie straciły oznaczenia.
            if not (resume and LAST_SCRAPE_RUN_PATH.exists()):
                save_json_atomic(
                    LAST_SCRAPE_RUN_PATH,
                    {"started_at": datetime.now().isoformat(timespec="seconds")},
                    backup=False,
                )
            return run_all_scrapers()

        ok, stopped = _execute_stage(
            "phase1",
            "PHASE 1: Scrape sources",
            _scrape,
            critical=True,
        )
        if not ok:
            return _finish(phase_results, stopped=stopped)

    # 1.5 Normalizacja linków (musi poprzedzać deduplikację)
    ok, stopped = _execute_stage(
        "phase1_5",
        "PHASE 1.5: Link normalisation",
        migrate_normalize_links.main,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    # 2. Deduplikacja i czyszczenie
    ok, stopped = _execute_stage(
        "phase2",
        "PHASE 2: Database deduplication",
        deduplicate_db.run,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    ok, stopped = _execute_stage(
        "phase2_5",
        "PHASE 2.5: Description cleanup (token diet)",
        clean_db.run,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    # Walidacja bazy przed AI
    if is_stop_requested():
        save_checkpoint(completed_stages, options, stopped=True)
        return _finish(phase_results, stopped=True)

    jobs = JobDatabase(str(JOBS_DATABASE_PATH)).load_jobs()
    logger.info(f"The database holds {len(jobs)} offers.")
    if not jobs:
        logger.error("Database empty - aborting before AI analysis.")
        phase_results["Database validation"] = False
        save_checkpoint(completed_stages, options, failed_stage="phase3")
        return _finish(phase_results)

    # 3. Analiza AI
    def _analyze():
        import waterfall_analysis
        return waterfall_analysis.main(rescore_all=rescore_all, rescore_changed=rescore_changed, resume=resume)
    ok, stopped = _execute_stage(
        "phase3",
        "PHASE 3: AI analysis (waterfall)",
        _analyze,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    # 4. Ewaluacja - czy ranking faktycznie działa?
    def _eval():
        import eval_ranking
        return eval_ranking.main([])

    ok, stopped = _execute_stage(
        "phase4",
        "PHASE 4: Ranking evaluation",
        _eval,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    clear_checkpoint()
    return _finish(phase_results, stopped=False)

if __name__ == "__main__":
    if not os.environ.get("PIPELINE_MANAGED"):
        clear_stop_flag()
    skip_scraping = "--skip-scraping" in sys.argv
    rescore_all = "--rescore-all" in sys.argv
    rescore_changed = "--rescore-changed" in sys.argv
    resume = "--resume" in sys.argv
    sys.exit(0 if run_pipeline(skip_scraping=skip_scraping, rescore_all=rescore_all, rescore_changed=rescore_changed, resume=resume) else 1)
