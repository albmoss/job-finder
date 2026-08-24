"""
Zbiór linków, które są już w bazie ofert - wspólny dla wszystkich scraperów.

Powód istnienia: `JobDatabase.record_scrape` nigdy nie nadpisuje istniejącego
rekordu (od tego jest --refresh), więc pobranie strony oferty, którą już mamy,
jest czystym kosztem czasu. Przy pełnym pokryciu portalu to 60-90% listingu.

Dlaczego jeden wspólny snapshot, a nie cache per scraper: `jobs_database.json`
ma ~27 MB, a scrapery startują równolegle - każdy wczytywał go osobno. Snapshot
robimy raz na proces i celowo go NIE odświeżamy w trakcie przebiegu: gdyby
scraper A dopisał ofertę, którą chwilę później zobaczy scraper B, chcemy żeby
B i tak ją pobrał (cross-posting bywa jedynym źródłem pełnego opisu).
"""

import logging
import threading

from utils.links import canonical_link

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_snapshot = None


def known_links() -> set:
    """Kanoniczne linki obecne w bazie. Liczone raz na proces."""
    global _snapshot
    with _lock:
        if _snapshot is not None:
            return _snapshot

        links = set()
        try:
            from config import JOBS_DATABASE_PATH
            from utils.safe_io import load_json_safe

            for record in load_json_safe(str(JOBS_DATABASE_PATH), default=[]):
                link = canonical_link(record.get("link", ""))
                if link:
                    links.add(link)
        except Exception as e:
            # Pusty zbiór = scrapery pobiorą wszystko. Wolniej, ale poprawnie.
            logger.warning(f"Could not read the known links ({e}) - fetching every offer")

        _snapshot = links
        return _snapshot


def reset_cache():
    """Wymuś ponowne wczytanie - używane w testach i po --refresh."""
    global _snapshot
    with _lock:
        _snapshot = None


def split_known(links):
    """Rozdziel listę linków na (nowe, już znane) - zachowuje kolejność."""
    known = known_links()
    fresh, seen = [], []
    for link in links:
        (seen if canonical_link(link) in known else fresh).append(link)
    return fresh, seen
