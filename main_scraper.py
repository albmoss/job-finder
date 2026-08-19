"""
Main Scraper Orchestrator
Runs all job scrapers and saves results to jobs_database.json
"""

import logging
import sys
import concurrent.futures
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent))

from config import SCRAPER_CONFIG, JOBS_DATABASE_PATH, USER_AGENTS
from scrapers import (
    PracujOptimizedScraper,  # HTTP + __NEXT_DATA__ (bez przeglądarki)
    OLXScraper,
    RocketJobsScraper,    # candidate-api (bez przeglądarki)
    LinkedInScraper,
    NoFluffScraper,       # internal API (bez przeglądarki)
    JustJoinScraper,      # candidate-api (bez przeglądarki)
    SolidJobsAPIScraper,  # publiczne API - bez klucza
    AdzunaAPIScraper,
    JoobleAPIScraper,
    CareerjetAPIScraper,
    PracaPlScraper,       # HTTP + ld+json (bez przeglądarki)
    AplikujScraper,       # HTTP + ld+json (bez przeglądarki)
    GoWorkScraper,        # HTTP + ld+json (bez przeglądarki)
    IndeedScraper,        # Playwright - Cloudflare
)

from utils.data_models import JobDatabase, ScraperStatusManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('scraper.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


def refresh_sources(source_names):
    """
    Usuń istniejące oferty wskazanych źródeł, żeby scraper zapisał je od nowa.

    Potrzebne, bo JobDatabase.append_jobs deduplikuje po linku i NIGDY nie aktualizuje
    istniejącego rekordu. Po poprawce scrapera (np. lepsze wydobywanie opisu) samo
    ponowne uruchomienie nic by nie dało - stare, ubogie rekordy zostałyby na stałe.

    Czyści OBA pliki: jobs_database.json i analyzed_jobs_waterfall.json. Pominięcie
    tego drugiego zostawiało "oferty widmo" - usunięte z bazy, ale wciąż widoczne
    w aplikacji, bo UI czyta wyniki analizy, nie bazę surową.

    Oferty z decyzją użytkownika są zachowywane - nie kasujemy tego, co oceniłeś.
    """
    from utils.links import canonical_link
    from utils.safe_io import load_json_safe, save_json_atomic

    decisions = load_json_safe("user_decisions.json", default={})
    decided = {canonical_link(k) for k in decisions}
    targets = {s.lower() for s in source_names}

    def matches(record_source: str) -> bool:
        src = (record_source or "").lower()
        return any(t in src for t in targets)

    # 1. Baza surowa
    jobs = load_json_safe(str(JOBS_DATABASE_PATH), default=[])
    kept_jobs, removed, protected = [], 0, 0
    for job in jobs:
        if matches(job.get("source")):
            if canonical_link(job.get("link", "")) in decided:
                kept_jobs.append(job)
                protected += 1
            else:
                removed += 1
            continue
        kept_jobs.append(job)

    if removed:
        save_json_atomic(str(JOBS_DATABASE_PATH), kept_jobs, backup=True)

    # 2. Wyniki analizy - inaczej UI dalej pokazuje usunięte oferty
    analyzed = load_json_safe("analyzed_jobs_waterfall.json", default=[])
    kept_analyzed, removed_analyzed = [], 0
    for entry in analyzed:
        job = entry.get("job") or {}
        if matches(job.get("source")) and canonical_link(job.get("link", "")) not in decided:
            removed_analyzed += 1
            continue
        kept_analyzed.append(entry)

    if removed_analyzed:
        save_json_atomic("analyzed_jobs_waterfall.json", kept_analyzed, backup=True)

    if removed or removed_analyzed:
        logger.info(
            f"Refresh: removed {removed} offers and {removed_analyzed} "
            f"analysis results ({protected} carrying a decision were kept)"
        )
    return removed


def run_all_scrapers(only=None, force=False, refresh=False):
    """
    Uruchom scrapery i zapisz wyniki do bazy.

    only    - lista fragmentów nazw źródeł do uruchomienia (np. ["rocket", "nofluff"]).
              Przydatne po naprawie jednego portalu - nie trzeba przepuszczać całości.
    force   - ignoruj oznaczenie "już zescrapowane dzisiaj".
    refresh - usuń istniejące oferty tych źródeł przed scrapowaniem (patrz refresh_sources).
    """

    logger.info("=" * 60)
    logger.info("Starting Job Search Scraper")
    logger.info("=" * 60)
    
    # Prepare scraper config with user agents
    scraper_config = SCRAPER_CONFIG.copy()
    scraper_config['user_agents'] = USER_AGENTS
    
    # === Phase 1: API scrapers (fast, no browser needed) ===
    api_scrapers = [
        SolidJobsAPIScraper(scraper_config),      # 🌐 Public API (no key!)
        PracujOptimizedScraper(scraper_config),   # 🚀 HTTP + __NEXT_DATA__
        NoFluffScraper(scraper_config),           # 🚀 Internal API
        JustJoinScraper(scraper_config),          # 🚀 candidate-api
        RocketJobsScraper(scraper_config),        # 🚀 candidate-api (był browser + 0 ofert)
        PracaPlScraper(scraper_config),           # 📄 ld+json - portal ogólny
        AplikujScraper(scraper_config),           # 📄 ld+json - dużo entry-level
        GoWorkScraper(scraper_config),            # 📄 ld+json - dużo entry-level
    ]

    # Scrapery wymagające kluczy API - dołączane tylko gdy klucz jest ustawiony,
    # inaczej co uruchomienie generowały ciche porażki.
    optional_scrapers = [
        (AdzunaAPIScraper, ("adzuna_app_id", "adzuna_app_key")),
        (JoobleAPIScraper, ("jooble_api_key",)),
        (CareerjetAPIScraper, ("careerjet_api_key",)),
    ]
    for scraper_cls, required_keys in optional_scrapers:
        if all(scraper_config.get(k) for k in required_keys):
            api_scrapers.append(scraper_cls(scraper_config))
            logger.info(f"Enabled optional scraper: {scraper_cls.__name__}")
        else:
            logger.debug(f"Skipped {scraper_cls.__name__} - missing keys: {', '.join(required_keys)}")

    # === Phase 2: Browser scrapers (slower, need Playwright) ===
    browser_scrapers = [
        OLXScraper(scraper_config),
        LinkedInScraper(scraper_config),
        # Indeed sam robi sobie długie przerwy między słowami kluczowymi
        # (zabezpieczenia portalu), więc jest najwolniejszym źródłem w przebiegu.
        IndeedScraper(scraper_config),
    ]

    if only:
        patterns = [p.lower() for p in only]

        def wanted(scraper):
            name = scraper.get_source_name().lower()
            return any(p in name for p in patterns)

        api_scrapers = [s for s in api_scrapers if wanted(s)]
        browser_scrapers = [s for s in browser_scrapers if wanted(s)]
        selected = [s.get_source_name() for s in api_scrapers + browser_scrapers]

        if not selected:
            logger.error(f"No scraper matches: {only}")
            return []
        logger.info(f"Ograniczono do: {', '.join(selected)}")

    all_scrapers = api_scrapers + browser_scrapers

    if refresh:
        refresh_sources([s.get_source_name() for s in all_scrapers])
        force = True  # odświeżanie bez ponownego scrapowania nie miałoby sensu

    all_jobs = []
    scraper_results = {}
    
    # Initialize status manager
    status_manager = ScraperStatusManager()
    
    # Initialize database
    db = JobDatabase(str(JOBS_DATABASE_PATH))

    # Define inner function for parallel execution
    def _scrape_source(scraper):
        source_name = scraper.get_source_name()
        
        # Check explicit status
        if not force and status_manager.is_scraped_today(source_name):
            logger.info(f"\n{'='*60}")
            logger.info(f"Skipping {source_name} - Already scraped SUCCESSFULLY today")
            return {
                'source': source_name,
                'success': True,
                'jobs': [],
                'status': 'skipped'
            }
            
        logger.info(f"\n{'='*60}")
        logger.info(f"Running {source_name} scraper...")
        logger.info(f"{'='*60}")
        
        try:
            jobs = scraper.run()
            
            # SMART SUCCESS CHECK: Only mark as completed if we got a meaningful number of jobs
            MIN_JOBS_THRESHOLD = 5
            
            if len(jobs) >= MIN_JOBS_THRESHOLD:
                status_manager.mark_as_completed(source_name, len(jobs))
                logger.info(f"✓ {source_name}: Scrape COMPLETE (Threshold {MIN_JOBS_THRESHOLD} met)")
            else:
                logger.warning(f"{source_name}: Only {len(jobs)} jobs found (Threshold: {MIN_JOBS_THRESHOLD}) - NOT marking as completed")
            
            logger.info(f"✓ {source_name}: Successfully scraped {len(jobs)} jobs")
            
            return {
                'source': source_name,
                'success': True,
                'jobs': jobs,
                'status': 'scraped'
            }
            
        except Exception as e:
            logger.error(f"✗ {source_name}: Failed - {e}")
            return {
                'source': source_name,
                'success': False,
                'error': str(e),
                'status': 'failed'
            }

    # === Phase 1: Run API scrapers in parallel (safe, no browser conflicts) ===
    logger.info(f"\nPhase 1: Running {len(api_scrapers)} API/HTTP scrapers in parallel...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_scraper = {executor.submit(_scrape_source, s): s for s in api_scrapers}
        
        for future in concurrent.futures.as_completed(future_to_scraper):
            scraper = future_to_scraper[future]
            try:
                result = future.result()
            except Exception as e:
                source_name = scraper.get_source_name()
                logger.error(f"✗ {source_name}: Thread crashed - {e}")
                scraper_results[source_name] = {
                    'success': False,
                    'error': str(e),
                    'status': 'crashed'
                }
                continue
            
            source = result['source']
            
            if result['success']:
                new_jobs = result['jobs']
                all_jobs.extend(new_jobs)
                
                scraper_results[source] = {
                    'success': True,
                    'jobs_count': len(new_jobs),
                    'status': result['status']
                }
                
                # Incremental Save
                if new_jobs:
                    saved = db.append_jobs(new_jobs)
                    logger.info(f"{source}: Saved {saved} new jobs to DB.")

                # Oferty, których szczegółów nie pobierano (są już w bazie) też
                # trzeba odnotować - inaczej sygnał "wisi od X dni" nigdy nie ruszy.
                seen_again = getattr(scraper, "seen_again_links", None)
                if seen_again:
                    touched = db.touch_seen(seen_again)
                    logger.info(f"{source}: {touched} offers still listed")
            else:
                 scraper_results[source] = {
                    'success': False,
                    'error': result.get('error', 'Unknown'),
                    'status': 'failed'
                }

    # === Phase 2: Run browser scrapers (limited parallelism to avoid Playwright conflicts) ===
    logger.info(f"\nPhase 2: Running {len(browser_scrapers)} browser scrapers (max 2 parallel)...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_to_scraper = {executor.submit(_scrape_source, s): s for s in browser_scrapers}
        
        for future in concurrent.futures.as_completed(future_to_scraper):
            scraper = future_to_scraper[future]
            try:
                result = future.result()
            except Exception as e:
                source_name = scraper.get_source_name()
                logger.error(f"✗ {source_name}: Thread crashed - {e}")
                scraper_results[source_name] = {
                    'success': False,
                    'error': str(e),
                    'status': 'crashed'
                }
                continue
            
            source = result['source']
            
            if result['success']:
                new_jobs = result['jobs']
                all_jobs.extend(new_jobs)
                
                scraper_results[source] = {
                    'success': True,
                    'jobs_count': len(new_jobs),
                    'status': result['status']
                }
                
                if new_jobs:
                    saved = db.append_jobs(new_jobs)
                    logger.info(f"{source}: Saved {saved} new jobs to DB.")
            else:
                 scraper_results[source] = {
                    'success': False,
                    'error': result.get('error', 'Unknown'),
                    'status': 'failed'
                }
    
    # Print summary
    print("\n" + "="*60)
    print("SCRAPING SUMMARY")
    print("="*60)
    
    total_success = sum(1 for r in scraper_results.values() if r['success'])
    total_failed = len(all_scrapers) - total_success
    total_jobs = len(all_jobs)
    
    print(f"\nScrapers run: {len(all_scrapers)}")
    print(f"Successful: {total_success}")
    print(f"Failed: {total_failed}")
    print(f"Total jobs scraped: {total_jobs}")
    
    print("\nDetails by source:")
    for source, result in scraper_results.items():
        if result['success']:
            status = result.get('status', 'scraped')
            if status == 'skipped':
                print(f"  ✓ {source}: SKIPPED (Already done today)")
            else:
                print(f"  ✓ {source}: {result['jobs_count']} jobs")
        else:
            print(f"  ✗ {source}: FAILED - {result.get('error', 'Unknown error')}")
    
    print(f"\nJobs database: {JOBS_DATABASE_PATH}")
    print("="*60)
    
    return all_jobs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Uruchom scrapery ofert pracy")
    parser.add_argument("--only", nargs="+", metavar="NAZWA",
                        help="Uruchom tylko wskazane źródła, np. --only rocket nofluff")
    parser.add_argument("--force", action="store_true",
                        help="Uruchom nawet jeśli źródło było już zescrapowane dzisiaj")
    parser.add_argument("--refresh", action="store_true",
                        help="Usuń stare oferty tych źródeł i pobierz od nowa "
                             "(po poprawce scrapera; oferty z Twoją decyzją zostają)")
    args = parser.parse_args()

    try:
        jobs = run_all_scrapers(only=args.only, force=args.force, refresh=args.refresh)
        logger.info("\n✓ Scraping completed successfully!")
        sys.exit(0)
    except KeyboardInterrupt:
        logger.warning("\nScraping interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n✗ Scraping failed with error: {e}")
        sys.exit(1)
