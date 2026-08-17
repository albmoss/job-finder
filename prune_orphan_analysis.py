"""
Usunięcie "ofert widmo" - wyników analizy bez odpowiadającej oferty w bazie.

Aplikacja wyświetla zawartość analyzed_jobs_waterfall.json, a nie jobs_database.json.
Jeśli oferta zniknie z bazy (odświeżenie źródła, wygaśnięcie, czyszczenie), a jej
wynik analizy zostanie, w UI wciąż widać ofertę, której już nie ma - łącznie
z martwym linkiem.

Wyniki ofert z Twoją decyzją są ZAWSZE zachowywane, nawet bez oferty w bazie -
to materiał do ewaluacji rankingu i budowy profilu preferencji.

Użycie:
    python prune_orphan_analysis.py --dry-run
    python prune_orphan_analysis.py
"""

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic

JOBS_DB = "jobs_database.json"
ANALYZED_DB = "analyzed_jobs_waterfall.json"
DECISIONS = "user_decisions.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Tylko pokaż, nic nie zapisuj")
    args = ap.parse_args()

    jobs = load_json_safe(JOBS_DB, default=[])
    analyzed = load_json_safe(ANALYZED_DB, default=[])
    decisions = load_json_safe(DECISIONS, default={})

    if not analyzed:
        print("Brak wyników analizy - nic do zrobienia.")
        return 0

    db_links = {canonical_link(j.get("link", "")) for j in jobs}
    decided = {canonical_link(k) for k in decisions}

    kept, orphans = [], []
    for entry in analyzed:
        link = canonical_link((entry.get("job") or {}).get("link", ""))
        if link in db_links or link in decided:
            kept.append(entry)
        else:
            orphans.append(entry)

    print("=" * 60)
    print(f"Wyników analizy:        {len(analyzed)}")
    print(f"Ofert w bazie:          {len(jobs)}")
    print(f"Widmo (do usunięcia):   {len(orphans)}")
    print(f"Zostaje:                {len(kept)}")

    if orphans:
        by_source = collections.Counter(
            (o.get("job") or {}).get("source", "?") for o in orphans
        )
        print("\nWedług źródła:")
        for src, n in by_source.most_common():
            print(f"   {src:<32} {n:>5}")

    if args.dry_run:
        print("\n(dry-run - nic nie zapisano)")
    elif orphans:
        save_json_atomic(ANALYZED_DB, kept, backup=True)
        print(f"\n✅ Usunięto {len(orphans)} ofert widmo.")
    else:
        print("\nBaza i wyniki analizy są spójne.")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
