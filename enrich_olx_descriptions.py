"""
Uzupełnienie opisów istniejących ofert OLX w bazie.

Problem: OLXScraper zapisywał zaślepkę "Oferta z OLX (kategoria: X)" zamiast opisu,
więc ~25% bazy szło do analizy AI z samym tytułem. Ten skrypt dociąga prawdziwe
opisy dla rekordów, które już są w jobs_database.json - bez ponownego scrapowania.

Efekty uboczne (zamierzone):
  - oferty wygasłe zostają usunięte z bazy (i tak nie da się na nie zaaplikować),
  - linki są normalizowane (ucięcie ?search_reason=...), co usuwa duplikaty,
  - uzupełniana jest nazwa firmy i data publikacji.

Uruchamianie: python enrich_olx_descriptions.py [--limit N] [--all-sources]
Można przerwać (Ctrl+C) i wznowić - postęp zapisywany jest na bieżąco.
"""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from config import JOBS_DATABASE_PATH
from utils.olx_details import fetch_offer_details, normalize_olx_link
from utils.safe_io import load_json_safe, save_json_atomic
from utils.text_cleaner import clean_job_description

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("enrich_olx")

PLACEHOLDER_PREFIX = "Oferta z OLX (kategoria:"
SAVE_EVERY = 50


def needs_enrichment(job: dict) -> bool:
    if job.get("source") != "OLX Praca":
        return False
    desc = (job.get("description") or "").strip()
    return desc.startswith(PLACEHOLDER_PREFIX) or len(desc) < 120


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Przetwórz tylko N ofert (test)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--keep-expired", action="store_true", help="Nie usuwaj wygasłych ofert")
    args = parser.parse_args()

    jobs = load_json_safe(JOBS_DATABASE_PATH, default=[])
    if not jobs:
        logger.error("Database empty or unreadable - aborting.")
        return 1

    logger.info(f"Loaded {len(jobs)} offers from the database.")

    # Normalizacja linków + deduplikacja (ta sama oferta pod różnymi query stringami)
    seen = {}
    deduped = []
    for job in jobs:
        job["link"] = normalize_olx_link(job.get("link", ""))
        key = job["link"]
        if key in seen:
            # Zachowaj wersję z dłuższym opisem
            prev = seen[key]
            if len(job.get("description") or "") > len(prev.get("description") or ""):
                deduped[deduped.index(prev)] = job
                seen[key] = job
            continue
        seen[key] = job
        deduped.append(job)

    removed_dupes = len(jobs) - len(deduped)
    if removed_dupes:
        logger.info(f"Removed {removed_dupes} duplicates after link normalisation.")
    jobs = deduped

    targets = [j for j in jobs if needs_enrichment(j)]
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        logger.info("Nothing to enrich.")
        save_json_atomic(JOBS_DATABASE_PATH, jobs, backup=True)
        return 0

    logger.info(f"To enrich: {len(targets)} OLX offers.")

    session = requests.Session()
    stats = {"ok": 0, "expired": 0, "error": 0}
    expired_links = set()
    done = [0]

    def work(job):
        result = fetch_offer_details(job["link"], session=session)
        status = result.get("status", "error")

        if status == "ok":
            job["description"] = clean_job_description(result["description"])
            if result.get("company"):
                job["company"] = result["company"]
            if result.get("posted_date"):
                job["posted_date"] = result["posted_date"]
            stats["ok"] += 1
        elif status == "expired":
            expired_links.add(job["link"])
            stats["expired"] += 1
        else:
            stats["error"] += 1

        done[0] += 1
        if done[0] % SAVE_EVERY == 0:
            logger.info(
                f"Progress: {done[0]}/{len(targets)} | ok={stats['ok']} "
                f"expired={stats['expired']} errors={stats['error']}"
            )
            save_json_atomic(JOBS_DATABASE_PATH, jobs, backup=False)
        return None

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, targets))
    except KeyboardInterrupt:
        logger.warning("Interrupted - saving progress so far...")
    finally:
        session.close()

    if expired_links and not args.keep_expired:
        before = len(jobs)
        jobs = [j for j in jobs if j.get("link") not in expired_links]
        logger.info(f"Removed {before - len(jobs)} expired offers from the database.")

    save_json_atomic(JOBS_DATABASE_PATH, jobs, backup=True)

    logger.info("=" * 60)
    logger.info(f"DONE. Enriched: {stats['ok']} | Expired: {stats['expired']} | Errors: {stats['error']}")
    logger.info(f"The database now holds {len(jobs)} offers.")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
