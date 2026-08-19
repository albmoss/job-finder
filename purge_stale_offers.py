"""
Purge Stale Offers
==================
Usuwa stare, nieocenione oferty z jobs_database.json i analyzed_jobs_waterfall.json.

Zachowuje:
  - WSZYSTKIE oferty z decyzją użytkownika (rated/saved/rejected/aspirational)
  - oferty pobrane w ciągu ostatnich 14 dni

Dodatkowo: oferty z RĘCZNĄ OCENĄ trafiają do rated_archive.json razem z wynikiem
analizy AI. Powód: ocena przeżywa w user_decisions.json, ale sama oferta znikała
z bazy - a bez tytułu, opisu i wyniku AI ta ocena jest bezużyteczna do ewaluacji
rankingu i do budowy profilu preferencji. To jedyny zbiór treningowy, jaki mamy.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic

JOBS_DB = "jobs_database.json"
ANALYZED_DB = "analyzed_jobs_waterfall.json"
DECISIONS_FILE = "user_decisions.json"
RATED_ARCHIVE = "rated_archive.json"


def load_json(filepath):
    default = {} if filepath == DECISIONS_FILE else []
    return load_json_safe(filepath, default=default)


def save_json(filepath, data):
    save_json_atomic(filepath, data, backup=True)


def archive_rated(jobs, analyzed, decisions):
    """
    Dopisz do rated_archive.json każdą ofertę z ręczną oceną, wraz z jej analizą AI.
    Archiwum rośnie i nigdy nie jest czyszczone - to nasz zbiór walidacyjny.
    """
    rated_links = {
        canonical_link(link)
        for link, val in decisions.items()
        if isinstance(val, dict) and val.get("rating") is not None
    }
    if not rated_links:
        return 0

    archive = load_json_safe(RATED_ARCHIVE, default=[])
    existing = {canonical_link((a.get("job") or {}).get("link", "")) for a in archive}

    analysis_by_link = {
        canonical_link((a.get("job") or {}).get("link", "")): a for a in analyzed
    }
    jobs_by_link = {canonical_link(j.get("link", "")): j for j in jobs}

    added = 0
    for link in rated_links:
        if link in existing:
            continue
        entry = analysis_by_link.get(link)
        if entry is None:
            job = jobs_by_link.get(link)
            if job is None:
                continue  # ani analizy, ani oferty - nie ma czego archiwizować
            entry = {"job": job, "match_percentage": None, "reason": "brak analizy AI"}
        record = dict(entry)
        record["user_rating"] = decisions[link].get("rating") if link in decisions else None
        record["archived_at"] = datetime.now().isoformat()
        archive.append(record)
        added += 1

    if added:
        save_json_atomic(RATED_ARCHIVE, archive, backup=True)
    return added


def main():
    from datetime import timedelta
    today = datetime.now().date()
    cutoff_date = today - timedelta(days=14)
    
    print(f"PURGE STALE OFFERS (keeping from {cutoff_date.isoformat()} onwards + decided)")
    print("=" * 60)

    # Load data
    jobs = load_json(JOBS_DB)
    decisions = load_json(DECISIONS_FILE) if os.path.exists(DECISIONS_FILE) else {}
    if isinstance(decisions, list):
        decisions = {}

    # Porównujemy na postaci kanonicznej - inaczej ten sam link z parametrem
    # śledzącym nie zostanie rozpoznany jako "oceniony" i oferta zostanie skasowana.
    decided_links = {canonical_link(k) for k in decisions.keys()}
    print(f"Loaded {len(jobs)} jobs from DB")
    print(f"{len(decided_links)} jobs have user decisions (protected)")

    # Zarchiwizuj oceniane oferty ZANIM cokolwiek usuniemy
    analyzed_now = load_json(ANALYZED_DB) if os.path.exists(ANALYZED_DB) else []
    archived = archive_rated(jobs, analyzed_now, decisions)
    if archived:
        print(f"Archived {archived} rated offers -> {RATED_ARCHIVE}")

    # Determine which jobs to keep
    kept_jobs = []
    removed_count = 0
    kept_decided = 0
    kept_recent = 0

    for job in jobs:
        link = canonical_link(job.get("link", ""))
        scraped_at = job.get("scraped_at", "") # format: 2026-03-22T...

        # Keep if user has made a decision on this job
        if link in decided_links:
            kept_jobs.append(job)
            kept_decided += 1
            continue

        # Keep if scraped within the last 14 days
        if scraped_at:
            try:
                scraped_date = datetime.fromisoformat(scraped_at[:10]).date()
                if scraped_date >= cutoff_date:
                    kept_jobs.append(job)
                    kept_recent += 1
                    continue
            except:
                pass

        # Otherwise, purge
        removed_count += 1

    print(f"\nResults:")
    print(f"   Kept (decided):   {kept_decided}")
    print(f"   Kept (last 14d): {kept_recent}")
    print(f"   REMOVED (stale):  {removed_count}")
    print(f"   Final DB size:    {len(kept_jobs)}")

    # Save cleaned jobs DB
    save_json(JOBS_DB, kept_jobs)
    print(f"Saved cleaned {JOBS_DB}")

    # Also clean analyzed_jobs_waterfall.json
    if os.path.exists(ANALYZED_DB):
        analyzed = load_json(ANALYZED_DB)
        kept_links = {canonical_link(j.get("link", "")) for j in kept_jobs}
        original_analyzed = len(analyzed)

        kept_analyzed = [
            item for item in analyzed
            if canonical_link(item.get("job", {}).get("link", "")) in kept_links
            or canonical_link(item.get("job", {}).get("link", "")) in decided_links
        ]
        removed_analyzed = original_analyzed - len(kept_analyzed)

        save_json(ANALYZED_DB, kept_analyzed)
        print(f"Cleaned {ANALYZED_DB}: {original_analyzed} → {len(kept_analyzed)} (removed {removed_analyzed})")

    print("\nPurge complete!")


if __name__ == "__main__":
    main()
