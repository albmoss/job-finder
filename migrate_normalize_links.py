"""
Migracja: normalizacja linków we WSZYSTKICH plikach danych.

Powód: OLX doklejał do linków parametry śledzące (?search_reason=search|organic).
Ten sam link z innym query trafiał do bazy jako osobna oferta, a klucze w
user_decisions.json nie pasowały do linków w bazie po ich znormalizowaniu.

Skrypt sprowadza linki do postaci kanonicznej (bez query stringa) w:
  - jobs_database.json
  - user_decisions.json
match_results.json ma klucze kanoniczne od początku (matching/run.py).

Przy kolizjach zachowuje rekord bogatszy (dłuższy opis / decyzja z datą wysłania).
Idempotentny - można uruchamiać wielokrotnie.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import candidates
from utils.links import canonical_link as normalize
from utils.safe_io import load_json_safe, save_json_atomic

JOBS_DB = "jobs_database.json"


def decision_richness(val) -> int:
    """Im wyższa wartość, tym cenniejszy rekord decyzji (przy kolizji wygrywa)."""
    if isinstance(val, dict):
        score = 10
        if val.get("applied_at"):
            score += 20
        if val.get("status") in ("save", "apply", "aspirational"):
            score += 5
        return score
    return 1                      # legacy string ("reject") - masowe czyszczenie


def migrate_jobs():
    jobs = load_json_safe(JOBS_DB, default=[])
    if not jobs:
        print(f"  {JOBS_DB}: empty, skipping")
        return

    by_link = {}
    zmienione = 0
    for job in jobs:
        link = normalize(job.get("link", ""))
        if link != job.get("link"):
            zmienione += 1
        job["link"] = link
        prev = by_link.get(link)
        if prev is None or len(job.get("description") or "") > len(prev.get("description") or ""):
            by_link[link] = job

    merged = list(by_link.values())
    print(f"  {JOBS_DB}: {len(jobs)} -> {len(merged)} (scalono {len(jobs) - len(merged)})")
    # Zapis tylko przy realnej zmianie: przy juz znormalizowanej bazie
    # ten etap przepisywal caly plik razem z kopia zapasowa, zeby
    # odtworzyc go bajt w bajt.
    if zmienione or len(merged) != len(jobs):
        save_json_atomic(JOBS_DB, merged, backup=True)


def migrate_decisions():
    for directory in candidates.dirs():
        _migrate_decisions(directory / candidates.DECISIONS)


def _migrate_decisions(path):
    label = f"{path.parent.name}/{path.name}"
    decisions = load_json_safe(path, default={})
    if not decisions:
        print(f"  {label}: empty, skipping")
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

    print(f"  {label}: {len(decisions)} -> {len(merged)} (kolizje: {collisions})")
    save_json_atomic(path, merged, backup=True)


def main():
    print("=" * 60)
    print("MIGRATION: link normalisation")
    print("=" * 60)
    migrate_jobs()
    migrate_decisions()
    print("=" * 60)
    print("Done. Backups of the previous versions are in backups/")
    print("=" * 60)


if __name__ == "__main__":
    main()
