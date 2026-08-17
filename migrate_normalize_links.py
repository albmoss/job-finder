"""
Migracja: normalizacja linków we WSZYSTKICH plikach danych.

Powód: OLX doklejał do linków parametry śledzące (?search_reason=search|organic).
Ten sam link z innym query trafiał do bazy jako osobna oferta, a klucze w
user_decisions.json nie pasowały do linków w bazie po ich znormalizowaniu.

Skrypt sprowadza linki do postaci kanonicznej (bez query stringa) w:
  - jobs_database.json
  - analyzed_jobs_waterfall.json
  - user_decisions.json

Przy kolizjach zachowuje rekord bogatszy (dłuższy opis / decyzja z oceną).
Idempotentny - można uruchamiać wielokrotnie.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.links import canonical_link as normalize
from utils.safe_io import load_json_safe, save_json_atomic

JOBS_DB = "jobs_database.json"
ANALYZED_DB = "analyzed_jobs_waterfall.json"
DECISIONS = "user_decisions.json"


def decision_richness(val) -> int:
    """Im wyższa wartość, tym cenniejszy rekord decyzji (przy kolizji wygrywa)."""
    if isinstance(val, dict):
        score = 10
        if val.get("rating") is not None:
            score += 100          # ręczna ocena to najcenniejsze, co mamy
        if val.get("applied_at"):
            score += 20
        if val.get("status") in ("save", "apply", "aspirational"):
            score += 5
        return score
    return 1                      # legacy string ("reject") - masowe czyszczenie


def migrate_jobs():
    jobs = load_json_safe(JOBS_DB, default=[])
    if not jobs:
        print(f"  {JOBS_DB}: pusty, pomijam")
        return

    by_link = {}
    for job in jobs:
        link = normalize(job.get("link", ""))
        job["link"] = link
        prev = by_link.get(link)
        if prev is None or len(job.get("description") or "") > len(prev.get("description") or ""):
            by_link[link] = job

    merged = list(by_link.values())
    print(f"  {JOBS_DB}: {len(jobs)} -> {len(merged)} (scalono {len(jobs) - len(merged)})")
    save_json_atomic(JOBS_DB, merged, backup=True)


def migrate_analyzed():
    data = load_json_safe(ANALYZED_DB, default=[])
    if not data:
        print(f"  {ANALYZED_DB}: pusty, pomijam")
        return

    by_link = {}
    for item in data:
        job = item.get("job") or {}
        link = normalize(job.get("link", ""))
        job["link"] = link
        # Przy duplikacie zostaw ten z dłuższym uzasadnieniem (zwykle pełniejsza analiza)
        prev = by_link.get(link)
        if prev is None or len(item.get("reason") or "") > len(prev.get("reason") or ""):
            by_link[link] = item

    merged = list(by_link.values())
    print(f"  {ANALYZED_DB}: {len(data)} -> {len(merged)} (scalono {len(data) - len(merged)})")
    save_json_atomic(ANALYZED_DB, merged, backup=True)


def migrate_decisions():
    decisions = load_json_safe(DECISIONS, default={})
    if not decisions:
        print(f"  {DECISIONS}: pusty, pomijam")
        return

    merged = {}
    collisions = 0
    for link, val in decisions.items():
        key = normalize(link)
        if key in merged:
            collisions += 1
            if decision_richness(val) > decision_richness(merged[key]):
                merged[key] = val
        else:
            merged[key] = val

    rated_before = sum(1 for v in decisions.values() if isinstance(v, dict) and v.get("rating") is not None)
    rated_after = sum(1 for v in merged.values() if isinstance(v, dict) and v.get("rating") is not None)

    print(f"  {DECISIONS}: {len(decisions)} -> {len(merged)} (kolizje: {collisions})")
    print(f"     ręczne oceny zachowane: {rated_before} -> {rated_after}")

    if rated_after < rated_before:
        print("     ⚠️  UWAGA: ubyło ręcznych ocen - przerywam zapis dla bezpieczeństwa!")
        return

    save_json_atomic(DECISIONS, merged, backup=True)


def main():
    print("=" * 60)
    print("MIGRACJA: normalizacja linków")
    print("=" * 60)
    migrate_jobs()
    migrate_analyzed()
    migrate_decisions()
    print("=" * 60)
    print("Gotowe. Backupy poprzednich wersji leżą w backups/")
    print("=" * 60)


if __name__ == "__main__":
    main()
