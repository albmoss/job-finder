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

from utils.console import force_utf8
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('scraper.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# Nazwa źródła -> jego sekcja w SCRAPER_CONFIG. Potrzebne tylko po to, żeby
# dało się wyłączyć źródło konfiguracją ("enabled": false), bez komentowania
# linijek w kodzie.
SOURCE_CONFIG_KEYS = {
    "Indeed": "indeed",
    "OLX Praca": "olx_praca",
    "LinkedIn": "linkedin",
    "Pracuj.pl": "pracuj_pl",
    "RocketJobs": "rocketjobs",
    "JustJoinIT": "justjoinit",
    "NoFluffJobs": "nofluffjobs",
    "SOLID.Jobs": "solid_jobs",
    "praca.pl": "praca_pl",
    "aplikuj.pl": "aplikuj",
    "GoWork.pl": "gowork",
}


def refresh_sources(source_names):
    """
    Usuń istniejące oferty wskazanych źródeł, żeby scraper zapisał je od nowa.

    Potrzebne, bo JobDatabase.record_scrape deduplikuje po linku i NIGDY nie aktualizuje
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
    
    # Konfiguracja scrapera razem z pulą user-agentów
    scraper_config = SCRAPER_CONFIG.copy()
    scraper_config['user_agents'] = USER_AGENTS
    
    # === Etap 1: scrapery API - szybkie, bez przeglądarki ===
    api_scrapers = [
        SolidJobsAPIScraper(scraper_config),  # publiczne API - bez klucza
        PracujOptimizedScraper(scraper_config),  # HTTP + __NEXT_DATA__
        NoFluffScraper(scraper_config),  # wewnętrzne API
        JustJoinScraper(scraper_config),  # candidate-api
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
    # Źródła pominięte z braku klucza trafiają do raportu na końcu przebiegu:
    # świadoma rezygnacja i awaria portalu wyglądają w logu tak samo - brakiem ofert.
    skipped_no_key = []
    for scraper_cls, required_keys in optional_scrapers:
        if all(scraper_config.get(k) for k in required_keys):
            api_scrapers.append(scraper_cls(scraper_config))
            logger.info(f"Enabled optional scraper: {scraper_cls.__name__}")
        else:
            skipped_no_key.append(scraper_cls.__name__.replace("APIScraper", ""))
            logger.debug(f"Skipped {scraper_cls.__name__} - missing keys: {', '.join(required_keys)}")

    # === Etap 2: scrapery przeglądarkowe - wolniejsze, wymagają Playwrighta ===
    browser_scrapers = [
        OLXScraper(scraper_config),
        LinkedInScraper(scraper_config),
        # Indeed sam robi sobie długie przerwy między słowami kluczowymi
        # (zabezpieczenia portalu), więc jest najwolniejszym źródłem w przebiegu.
        # Domyślnie wyłączony - patrz "enabled" w SCRAPER_CONFIG["indeed"].
        IndeedScraper(scraper_config),
    ]

    # Źródła wyłączone w konfiguracji. Scraper zostaje w kodzie razem z całym
    # rozpoznaniem portalu, ale nie startuje - `--only` nadal go uruchomi,
    # więc sprawdzenie, czy portal znowu przepuszcza, to jedno polecenie.
    disabled = {
        name for name, key in SOURCE_CONFIG_KEYS.items()
        if scraper_config.get(key, {}).get("enabled", True) is False
    }
    # Do raportu trafiają tylko te, których faktycznie nie uruchomiono; przy --only
    # wyłączenie z konfiguracji nie obowiązuje i wypisanie go byłoby nieprawdą.
    disabled_reported = []
    if disabled and not only:
        skipped = [s for s in api_scrapers + browser_scrapers
                   if s.get_source_name() in disabled]
        if skipped:
            disabled_reported = sorted(s.get_source_name() for s in skipped)
            api_scrapers = [s for s in api_scrapers if s.get_source_name() not in disabled]
            browser_scrapers = [s for s in browser_scrapers if s.get_source_name() not in disabled]
            logger.info(
                f"Disabled in the config, not running: "
                f"{', '.join(s.get_source_name() for s in skipped)}"
            )

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
    
    status_manager = ScraperStatusManager()
    
    db = JobDatabase(str(JOBS_DATABASE_PATH))

    def _scrape_source(scraper):
        source_name = scraper.get_source_name()
        
        if not force and status_manager.is_scraped_today(source_name):
            logger.info(f"\n{'='*60}")
            logger.info(f"Skipping {source_name} - Already scraped SUCCESSFULLY today")
            return {
                'source': source_name,
                'success': True,
                'jobs': [],
                'status': 'skipped'
            }
            
        # --force ma znaczyc "puszczaj od nowa". OLX trzyma wlasny checkpoint
        # kategorii (zeby przerwany bieg wznawial sie od miejsca zatrzymania),
        # ktorego flaga wczesniej nie dotykala - po naprawie scrapera tego
        # samego dnia przebieg konczyl sie "Successfully scraped 0 jobs",
        # bo pomijal wszystkie 37 kategorii.
        scraper.force = force

        logger.info(f"\n{'='*60}")
        logger.info(f"Running {source_name} scraper...")
        logger.info(f"{'='*60}")
        
        try:
            jobs = scraper.run()
            
            # Za sukces uznajemy dopiero sensowną liczbę ofert, nie sam brak wyjątku
            MIN_JOBS_THRESHOLD = 5
            
            # Normę tego źródła czytamy PRZED dopisaniem dzisiejszego wyniku -
            # inaczej chudy przebieg sam sobie obniża medianę, z którą go
            # porównujemy, i przy kilku takich pod rząd awaria staje się normą.
            history_before = status_manager.get_history(source_name)
            # Historia idzie do pliku ZAWSZE, także przy zerze - to właśnie zero
            # jest wpisem, którego szuka później diagnoza (utils/scraper_health.py).
            status_manager.record_yield(source_name, len(jobs))

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
                'status': 'scraped',
                'history_before': history_before,
            }
            
        except Exception as e:
            logger.error(f"✗ {source_name}: Failed - {e}")
            return {
                'source': source_name,
                'success': False,
                'error': str(e),
                'status': 'failed'
            }

    def _collect(scrapers, max_workers, label):
        """Uruchom grupę scraperów równolegle i zapisuj wyniki na bieżąco."""
        logger.info(f"{label}: running {len(scrapers)} scrapers (max {max_workers} parallel)...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_scrape_source, s): s for s in scrapers}

            for future in concurrent.futures.as_completed(futures):
                scraper = futures[future]
                source = scraper.get_source_name()

                try:
                    result = future.result()
                except Exception as e:
                    logger.error(f"✗ {source}: Thread crashed - {e}")
                    scraper_results[source] = {'success': False, 'error': str(e), 'status': 'crashed'}
                    continue

                if not result['success']:
                    scraper_results[source] = {
                        'success': False,
                        'error': result.get('error', 'Unknown'),
                        'status': 'failed',
                    }
                    continue

                new_jobs = result['jobs']
                all_jobs.extend(new_jobs)
                scraper_results[source] = {
                    'success': True,
                    'jobs_count': len(new_jobs),
                    'status': result['status'],
                    # Próbka wystarczy: objawy, których szukamy, dotyczą całych
                    # pól naraz, więc widać je na pięćdziesięciu rekordach tak
                    # samo dobrze jak na pięciu tysiącach.
                    'sample': new_jobs[:50],
                    'history_before': result.get('history_before') or [],
                }

                # Zapis przyrostowy - przerwany przebieg nie traci tego, co zebrał.
                # Nowe oferty i te, których szczegółów nie pobierano (bo już je
                # mamy), idą jednym zapisem: bez odnotowania tych drugich sygnał
                # "wisi od X dni" nigdy by nie ruszył.
                seen_again = getattr(scraper, "seen_again_links", None) or ()
                # Źródło, które pobrało zero NOWYCH ofert, ale rozpoznało setki
                # znanych, działa poprawnie - po prostu nic nowego nie wisi.
                # Bez tej liczby diagnoza uznałaby je za martwe.
                scraper_results[source]['also_seen'] = len(seen_again)
                # Statystyki dociagania opisow. Bez nich przebieg, w ktorym
                # KAZDA podstrona odmowila, konczyl sie napisem "Successfully
                # scraped" - liczba ofert byla w porzadku, bo braklo tylko
                # tresci. Tak przez tydzien wchodzilo do bazy 1272 ofert
                # z zaslepka zamiast opisu.
                scraper_results[source]['enrich'] = getattr(scraper, "enrich_stats", None)
                if new_jobs or seen_again:
                    added, touched = db.record_scrape(new_jobs, seen_again)
                    logger.info(f"{source}: saved {added} new offers, "
                                f"{touched} still listed")

    _collect(api_scrapers, 5, "Phase 1 (API/HTTP)")
    # Playwright nie znosi wielu instancji naraz - stąd ostrzejszy limit
    _collect(browser_scrapers, 2, "Phase 2 (browser)")

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

    findings = _report_health(scraper_results, db, skipped_no_key, disabled_reported)
    blocking_sources = _blocking_scrape_sources(scraper_results, findings)
    if blocking_sources:
        raise RuntimeError(
            "Scraping incomplete for: " + ", ".join(blocking_sources)
        )

    return all_jobs


def _blocking_scrape_sources(scraper_results, findings):
    """Źródła, przez które cały etap nie może udawać pełnego sukcesu."""
    blocking = {
        source
        for source, result in scraper_results.items()
        if result.get("status") != "skipped" and not result.get("success")
    }
    blocking.update(
        finding["source"]
        for finding in findings
        if finding.get("verdict") in {"broken", "degraded"}
    )
    return sorted(blocking)


def _report_health(scraper_results, db, skipped_no_key, disabled):
    """
    Diagnoza źródeł na podstawie tego, co przebieg już ma - bez dodatkowych zapytań.

    Wynik idzie na stdout razem z podsumowaniem, a nie do logu: log czyta się po
    awarii, a o cichej awarii trzeba się dowiedzieć, zanim się jej poszuka.
    """
    from utils import scraper_health

    known_jobs = db.load_jobs()
    findings = []

    for source, result in scraper_results.items():
        if result.get('status') == 'skipped':
            continue  # nie było przebiegu, nie ma czego oceniać

        if not result.get('success'):
            finding = scraper_health.check(source, 0, error=result.get('error', ''))
        else:
            count = result.get('jobs_count', 0)
            if count == 0 and result.get('also_seen'):
                continue  # portal odpowiedział, tyle że samymi znanymi ofertami
            finding = scraper_health.check(
                source,
                count,
                jobs=result.get('sample', ()),
                history=result.get('history_before', ()),
                domain=scraper_health.expected_domain(known_jobs, source),
                enrich=result.get('enrich'),
            )

        if finding:
            findings.append(finding)

    report = scraper_health.format_report(findings, skipped_no_key, disabled)
    if report:
        print(report)
    return findings


if __name__ == "__main__":
    force_utf8()
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
