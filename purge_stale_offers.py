"""
Purge Stale Offers
==================
Usuwa stare, nieocenione oferty z jobs_database.json.

Zachowuje:
  - WSZYSTKIE oferty z decyzją użytkownika (rated/saved/rejected/aspirational)
  - oferty pobrane w ciągu ostatnich 14 dni

Dodatkowo: oferty z RĘCZNĄ OCENĄ trafiają do rated_archive.json razem z procentem
dopasowania. Powód: ocena przeżywa w user_decisions.json, ale sama oferta znikała
z bazy - a bez tytułu, opisu i wyniku ta ocena jest bezużyteczna do ewaluacji
rankingu. To jedyny zbiór walidacyjny, jaki mamy.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic
from utils.console import force_utf8

JOBS_DB = "jobs_database.json"
MATCH_RESULTS = "match_results.json"
DECISIONS_FILE = "user_decisions.json"
RATED_ARCHIVE = "rated_archive.json"


def load_json(filepath):
    default = {} if filepath == DECISIONS_FILE else []
    return load_json_safe(filepath, default=default)


def save_json(filepath, data):
    save_json_atomic(filepath, data, backup=True)


def archive_rated(jobs, results, decisions):
    """
    Dopisz do rated_archive.json każdą ofertę z ręczną oceną, wraz z procentem
    dopasowania z match_results.json. Archiwum rośnie i nigdy nie jest
    czyszczone - to nasz zbiór walidacyjny.
    """
    # Mapuj link kanoniczny -> dane decyzji (obsługa formatu słownikowego oraz niekanonicznych kluczy)
    rated_by_canon = {}
    for link, val in decisions.items():
        if isinstance(val, dict) and val.get("rating") is not None:
            rated_by_canon[canonical_link(link)] = val
    if not rated_by_canon:
        return 0

    archive = load_json_safe(RATED_ARCHIVE, default=[])
    existing = {canonical_link((a.get("job") or {}).get("link", "")) for a in archive}
    jobs_by_link = {canonical_link(j.get("link", "")): j for j in jobs}

    added = 0
    for link, decision_val in rated_by_canon.items():
        if link in existing:
            continue
        job = jobs_by_link.get(link)
        if job is None:
            continue  # oferty już nie ma w bazie - nie ma czego archiwizować
        entry = {"job": job, "match_percentage": (results.get(link) or {}).get("percent")}
        record = dict(entry)
        record["user_rating"] = decision_val.get("rating") if isinstance(decision_val, dict) else None
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
    results = load_json_safe(MATCH_RESULTS, default={}) if os.path.exists(MATCH_RESULTS) else {}
    archived = archive_rated(jobs, results if isinstance(results, dict) else {}, decisions)
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

        # Zostaje, jeśli oferta ma ręczną decyzję
        if link in decided_links:
            kept_jobs.append(job)
            kept_decided += 1
            continue

        # Zostaje, jeśli zescrapowana w ciągu ostatnich 14 dni
        if scraped_at:
            try:
                scraped_date = datetime.fromisoformat(scraped_at[:10]).date()
                if scraped_date >= cutoff_date:
                    kept_jobs.append(job)
                    kept_recent += 1
                    continue
            except:
                pass

        # W przeciwnym razie leci z bazy
        removed_count += 1

    print("\nResults:")
    print(f"   Kept (decided):   {kept_decided}")
    print(f"   Kept (last 14d): {kept_recent}")
    print(f"   REMOVED (stale):  {removed_count}")
    print(f"   Final DB size:    {len(kept_jobs)}")

    save_json(JOBS_DB, kept_jobs)
    print(f"Saved cleaned {JOBS_DB}")

    # match_results.json nie trzeba tu czyścić: matching/run.py usuwa wyniki ofert,
    # których nie ma już w bazie.

    print("\nPurge complete!")


if __name__ == "__main__":
    force_utf8()
    main()
