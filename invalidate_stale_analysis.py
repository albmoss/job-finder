"""
Unieważnienie wyników analizy dla ofert, których opis się zmienił.

Każdy wpis w analyzed_jobs_waterfall.json przechowuje własną kopię oferty
(łącznie z opisem, jaki miała w chwili analizy). Porównując tę kopię z aktualną
bazą, można wykryć oceny wystawione na podstawie nieaktualnych (albo pustych)
danych - nawet dla starych wpisów, które nie mają stempla `_description_hash`.

Typowy przypadek: oferty OLX oceniane po samym tytule (opis był zaślepką
"Oferta z OLX (kategoria: X)"), którym potem dociągnięto prawdziwy opis.

Unieważnione wpisy są usuwane, więc waterfall_analysis.py przeliczy je przy
następnym uruchomieniu.

Użycie:
    python invalidate_stale_analysis.py --dry-run     # pokaż co by usunęło
    python invalidate_stale_analysis.py
    python invalidate_stale_analysis.py --source "OLX Praca"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic

JOBS_DB = "jobs_database.json"
ANALYZED_DB = "analyzed_jobs_waterfall.json"

# Poniżej tylu znaków opis uznajemy za nieinformacyjny (zgodne z progiem w promptcie)
THIN_DESC = 150
# O tyle znaków opis musi urosnąć, żeby uznać zmianę za istotną
GROWTH_THRESHOLD = 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Tylko pokaż, nic nie zapisuj")
    ap.add_argument("--source", help="Ogranicz do jednego źródła, np. \"OLX Praca\"")
    args = ap.parse_args()

    jobs = load_json_safe(JOBS_DB, default=[])
    analyzed = load_json_safe(ANALYZED_DB, default=[])

    if not analyzed:
        print("No analysis results - nothing to do.")
        return 0

    current = {canonical_link(j.get("link", "")): (j.get("description") or "") for j in jobs}

    keep, drop = [], []
    for entry in analyzed:
        job = entry.get("job") or {}
        link = canonical_link(job.get("link", ""))

        if args.source and job.get("source") != args.source:
            keep.append(entry)
            continue

        old_desc = job.get("description") or ""
        new_desc = current.get(link)

        # Oferty, których nie ma już w bazie, zostawiamy w spokoju
        if new_desc is None:
            keep.append(entry)
            continue

        analyzed_on_nothing = len(old_desc.strip()) < THIN_DESC
        now_has_content = len(new_desc.strip()) >= THIN_DESC
        grew_a_lot = len(new_desc) - len(old_desc) > GROWTH_THRESHOLD

        if (analyzed_on_nothing and now_has_content) or grew_a_lot:
            drop.append((entry, len(old_desc), len(new_desc)))
        else:
            keep.append(entry)

    print("=" * 64)
    print(f"Entries in analysis:      {len(analyzed)}")
    print(f"To invalidate:            {len(drop)}")
    print(f"Remaining:                {len(keep)}")

    if drop:
        by_source = {}
        for entry, _, _ in drop:
            src = (entry.get("job") or {}).get("source", "?")
            by_source[src] = by_source.get(src, 0) + 1
        print("\nBy source:")
        for src, n in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"   {src:<32} {n:>5}")

        print("\nExamples (description before -> after):")
        for entry, old_len, new_len in drop[:5]:
            job = entry.get("job") or {}
            print(f"   [{entry.get('match_percentage')}%] {job.get('title', '?')[:44]:<44} "
                  f"{old_len:>5} -> {new_len:<6} zn.")

    if args.dry_run:
        print("\n(dry run - nothing was written)")
    elif drop:
        save_json_atomic(ANALYZED_DB, keep, backup=True)
        print(f"\nSaved. Run waterfall_analysis.py to rescore {len(drop)} offers.")
    else:
        print("\nNothing to invalidate.")

    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
