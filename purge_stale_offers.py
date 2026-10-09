"""
Purge Stale Offers
==================
Usuwa z jobs_database.json oferty, których portal od 14 dni nie pokazuje.

Zachowuje:
  - WSZYSTKIE oferty z decyzją użytkownika (zapisane, ukryte, wysłane)
  - oferty widziane na portalu w ciągu ostatnich 14 dni (`last_seen`)

Liczy się `last_seen`, nie data pobrania: każdy przebieg scrapera odświeża go
ofertom, które nadal wiszą na liście portalu. Oferta wystawiona na 30 dni zostaje
więc w bazie przez cały ten czas. Gdyby wypadła po 14 dniach od pobrania,
następny przebieg wziąłby ją za nową - drugi raz pobierał jej stronę i płacił
za ocenę. Usunięta zostaje dopiero oferta, której portal już nie pokazuje.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import candidates, telemetry
from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic
from utils.console import force_utf8

JOBS_DB = "jobs_database.json"


def save_json(filepath, data):
    save_json_atomic(filepath, data, backup=True)


def main():
    from datetime import timedelta
    today = datetime.now().date()
    cutoff_date = today - timedelta(days=14)
    
    print(f"PURGE STALE OFFERS (keeping from {cutoff_date.isoformat()} onwards + decided)")
    print("=" * 60)

    jobs = load_json_safe(JOBS_DB, default=[])
    decided_links = candidates.decided_links()
    print(f"Loaded {len(jobs)} jobs from DB")
    print(f"{len(decided_links)} jobs have user decisions (protected)")

    # Determine which jobs to keep
    kept_jobs = []
    removed_count = 0
    kept_decided = 0
    kept_recent = 0

    for job in jobs:
        link = canonical_link(job.get("link", ""))
        # Starsze rekordy bez last_seen: data pobrania to ostatnia pewna obserwacja.
        seen_at = job.get("last_seen") or job.get("scraped_at") or ""

        # Zostaje, jeśli oferta ma ręczną decyzję
        if link in decided_links:
            kept_jobs.append(job)
            kept_decided += 1
            continue

        # Zostaje, jeśli portal pokazał ją w ciągu ostatnich 14 dni
        if seen_at:
            try:
                seen_date = datetime.fromisoformat(seen_at[:10]).date()
                if seen_date >= cutoff_date:
                    kept_jobs.append(job)
                    kept_recent += 1
                    continue
            except ValueError:
                pass

        # W przeciwnym razie leci z bazy
        removed_count += 1

    print("\nResults:")
    print(f"   Kept (decided):   {kept_decided}")
    print(f"   Kept (seen in last 14d): {kept_recent}")
    print(f"   REMOVED (stale):  {removed_count}")
    telemetry.emit("stage_stats", id="phase0", removed=removed_count)
    print(f"   Final DB size:    {len(kept_jobs)}")

    if removed_count:
        save_json(JOBS_DB, kept_jobs)
        print(f"Saved cleaned {JOBS_DB}")
    else:
        print(f"{JOBS_DB}: nothing stale - file unchanged")

    # match_results.json nie trzeba tu czyścić: matching/run.py usuwa wyniki ofert,
    # których nie ma już w bazie.

    print("\nPurge complete!")


if __name__ == "__main__":
    force_utf8()
    main()
