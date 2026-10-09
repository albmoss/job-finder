"""
Główny pipeline: profil z CV -> scraping -> czyszczenie -> dopasowanie.

Kolejność ma znaczenie:
  0. Purge               - usuwa oferty niewidziane na portalu od 14 dni
  0.5 Profil z CV        - miasto i poziom z CV wyznaczają zakres scraperów
  1. Scraping            - pomija źródła zescrapowane dziś
  1.5 Normalizacja linków - musi być przed deduplikacją, inaczej ta sama oferta
                            pod dwoma URL-ami przejdzie jako dwie różne
  2. Deduplikacja + czyszczenie opisów
  3. Dopasowanie         - przesiew w kodzie + ocena Jev (matching/run.py)
"""

from datetime import datetime
import logging
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from utils.console import force_utf8

force_utf8()

from main_scraper import run_all_scrapers
from utils.data_models import JobDatabase
from utils.safe_io import load_json_safe, save_json_atomic
from config import JOBS_DATABASE_PATH
from utils import candidates, stop, telemetry
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
        telemetry.emit("pipeline", result="stopped")
        stop.clear()
        return False
    failed = [name for name, ok in phase_results.items() if not ok]
    complete = not failed

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE" if complete else "PIPELINE INCOMPLETE")
    print("=" * 60)
    telemetry.emit("pipeline", result="complete" if complete else "incomplete")

    if failed:
        logger.error("Failed phases: %s", ", ".join(failed))

    return complete

def run_pipeline(skip_scraping=False, rescore_all=False, resume=False):
    print("\n" + "=" * 60)
    print("PIPELINE: CV profile -> scraping -> matching")
    print("=" * 60)

    options = {
        "skip_scraping": skip_scraping,
        "rescore_all": rescore_all,
        "candidate": candidates.active_id(),
    }

    checkpoint = load_checkpoint() if resume else {}
    if resume and checkpoint.get("options", {}).get("candidate", candidates.FIRST_ID) != options["candidate"]:
        logger.info("Checkpoint belongs to another candidate - starting from the beginning.")
        resume = False
    if resume:
        completed_stages = set(checkpoint.get("completed_stages", []))
        saved_options = checkpoint.get("options", {})
        if "skip_scraping" in saved_options:
            skip_scraping = saved_options["skip_scraping"]
        if "rescore_all" in saved_options:
            rescore_all = saved_options["rescore_all"]
        options.update(saved_options)
        logger.info(f"Resuming pipeline from checkpoint (completed stages: {', '.join(sorted(completed_stages)) or 'none'}).")
    else:
        completed_stages = set()
        clear_checkpoint()
        save_checkpoint(completed_stages, options, current_stage="phase0")

    # Do not clear the stop flag here: the manager unlinks stale tokens before Popen,
    # so any existing token represents an immediate user stop request for this run.
    phase_results = {}

    def _execute_stage(stage_id, stage_name, fn, critical=False):
        if stop.requested():
            logger.info(f"Stop requested before {stage_name} - halting.")
            save_checkpoint(completed_stages, options, current_stage=stage_id, stopped=True)
            return False, True

        if stage_id in completed_stages:
            logger.info(f"── {stage_name} (skipped: already completed in checkpoint)")
            telemetry.emit("stage", id=stage_id, state="cached")
            phase_results[stage_name] = True
            return True, False

        telemetry.emit("stage", id=stage_id, state="running")
        try:
            ok = _phase(stage_name, fn, critical=critical)
        except Exception:
            telemetry.emit("stage", id=stage_id, state="failed")
            raise
        phase_results[stage_name] = ok
        stopped = stop.requested()
        if ok and not stopped:
            completed_stages.add(stage_id)
            save_checkpoint(completed_stages, options, current_stage=stage_id)
            telemetry.emit("stage", id=stage_id, state="done")
            return True, False
        save_checkpoint(completed_stages, options, current_stage=stage_id,
                        failed_stage=None if ok else stage_id, stopped=stopped)
        if not ok:
            telemetry.emit("stage", id=stage_id, state="failed")
        return False, stopped

    # 0. Usuń przeterminowane oferty
    ok, stopped = _execute_stage(
        "phase0",
        "PHASE 0: Drop offers older than 14 days",
        purge_stale_offers.main,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    # 0.5 Profil kandydata z CV - scrapery biorą z niego miasto i poziomy
    def _profile():
        from utils.cv_profile import ensure_profile
        profile = ensure_profile()
        if profile is None:
            print("No CV found - upload a CV in the app before running the pipeline.")
            return False
        print(f"CV profile: {profile.get('seniority')} | {profile.get('city')} | "
              f"{len(profile.get('skills') or [])} skills")
        return True

    ok, stopped = _execute_stage(
        "phase0_5",
        "PHASE 0.5: Candidate profile from CV",
        _profile,
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
            last_run = candidates.path(candidates.LAST_SCRAPE_RUN)
            if not (resume and last_run.exists()):
                save_json_atomic(
                    last_run,
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

    # Walidacja bazy przed dopasowaniem
    if stop.requested():
        save_checkpoint(completed_stages, options, stopped=True)
        return _finish(phase_results, stopped=True)

    jobs = JobDatabase(str(JOBS_DATABASE_PATH)).load_jobs()
    logger.info(f"The database holds {len(jobs)} offers.")
    if not jobs:
        logger.error("Database empty - aborting before matching.")
        phase_results["Database validation"] = False
        save_checkpoint(completed_stages, options, failed_stage="phase3")
        return _finish(phase_results)

    # 3. Dopasowanie: przesiew w kodzie + ocena Jev
    def _match():
        from matching import run as matching_run
        return matching_run.main(["--rescore-all"] if rescore_all else [])
    ok, stopped = _execute_stage(
        "phase3",
        "PHASE 3: Matching (prefilter + Jev)",
        _match,
    )
    if not ok:
        return _finish(phase_results, stopped=stopped)

    clear_checkpoint()
    return _finish(phase_results, stopped=False)


def run_in_manager(argv) -> int:
    """
    Przebieg z terminala idzie przez PipelineProcessManager jako proces potomny.
    Stan trafia wtedy do pipeline_run_state.json i interfejs pokazuje go tak samo
    jak przebieg uruchomiony przyciskiem; wyjście procesu drukuje się w konsoli.
    """
    from pipeline_manager import PipelineProcessManager

    mgr = PipelineProcessManager()
    mode = "skip_scraping" if "--skip-scraping" in argv else "full"
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), *argv]
    ok, msg = mgr.start_pipeline(mode=mode, cmd=cmd, echo=lambda line: print(line, flush=True))
    if not ok:
        print(msg)
        return 1
    try:
        code = mgr.wait()
    except KeyboardInterrupt:
        # Ctrl+C trafia też do procesu potomnego (wspólna konsola); czekamy, aż zapisze stan.
        try:
            code = mgr.wait()
        except KeyboardInterrupt:
            return 130
    return 1 if code is None else code


if __name__ == "__main__":
    if not os.environ.get("PIPELINE_MANAGED"):
        sys.exit(run_in_manager(sys.argv[1:]))
    skip_scraping = "--skip-scraping" in sys.argv
    rescore_all = "--rescore-all" in sys.argv
    resume = "--resume" in sys.argv
    sys.exit(0 if run_pipeline(skip_scraping=skip_scraping, rescore_all=rescore_all, resume=resume) else 1)
