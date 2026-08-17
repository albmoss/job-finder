"""
Ujednolicenie nazw źródeł w istniejących danych.

Nazwa źródła jest pokazywana wprost na karcie oferty, więc musi być nazwą
portalu, a nie opisem sposobu pobierania. "Pracuj.pl (Optimized APIs)" było
nazwą klasy scrapera, która wyciekła do interfejsu.

Sama zmiana `get_source_name()` nie wystarcza: rekordy pobrane wcześniej
zostają ze starą nazwą i w bazie żyją dwa źródła o tej samej treści -
rozjeżdżają się statystyki, filtr po źródle i `--refresh`.

Skrypt jest idempotentny: uruchomiony ponownie nie ma nic do roboty.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.safe_io import load_json_safe, save_json_atomic

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MigracjaNazwZrodel")

# stara nazwa -> nowa
RENAMES = {
    "Pracuj.pl (Optimized APIs)": "Pracuj.pl",
    "Manual (justjoin.it)": "Dodane ręcznie",
}

FILES = [
    ("jobs_database.json", False),
    ("analyzed_jobs_waterfall.json", True),
    ("rated_archive.json", True),
]


def migrate_file(filename: str, nested: bool) -> int:
    path = Path(__file__).parent / filename
    if not path.exists():
        logger.info(f"{filename}: brak pliku - pomijam")
        return 0

    data = load_json_safe(path, default=[])
    if not data:
        return 0

    changed = 0
    for item in data:
        job = item.get("job") if nested else item
        if not isinstance(job, dict):
            continue
        new_name = RENAMES.get(job.get("source"))
        if new_name:
            job["source"] = new_name
            changed += 1

    if changed:
        save_json_atomic(path, data, backup=True)
        logger.info(f"✅ {filename}: zmieniono {changed} rekordów")
    else:
        logger.info(f"{filename}: nic do zmiany")
    return changed


def main():
    logger.info("Ujednolicanie nazw źródeł...")
    total = sum(migrate_file(name, nested) for name, nested in FILES)
    logger.info(f"Gotowe. Łącznie zmienionych rekordów: {total}")


if __name__ == "__main__":
    main()
