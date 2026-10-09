"""
Zbiór linków, które są już w bazie ofert - wspólny dla wszystkich scraperów.

Powód istnienia: `JobDatabase.record_scrape` nigdy nie nadpisuje istniejącego
rekordu (od tego jest --refresh), więc pobranie strony oferty, którą już mamy,
jest czystym kosztem czasu. Przy pełnym pokryciu portalu to 60-90% listingu.

Dlaczego jeden wspólny snapshot, a nie cache per scraper: baza ma dziesiątki tysięcy ofert,
a scrapery startują równolegle - każdy wczytywał ją osobno. Snapshot
robimy raz na proces i celowo go NIE odświeżamy w trakcie przebiegu: gdyby
scraper A dopisał ofertę, którą chwilę później zobaczy scraper B, chcemy żeby
B i tak ją pobrał (cross-posting bywa jedynym źródłem pełnego opisu).

Oferta z zaślepką zamiast opisu (portal nie oddał treści albo scraper doszedł do
limitu stron szczegółów) NIE jest znana: scraper pobiera ją jak nową, a
`record_scrape` wstawia treść w miejsce zaślepki. Inaczej zostałaby bez opisu na stałe.
"""

import logging
import threading

from utils.links import canonical_link

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_snapshot = None
_stored = None


def _load():
    global _snapshot, _stored
    described, stored = set(), set()
    try:
        from config import JOBS_DATABASE_PATH
        from utils.data_models import JobDatabase, is_placeholder_description

        for record in JobDatabase(JOBS_DATABASE_PATH).load_records():
            link = canonical_link(record.get("link", ""))
            if not link:
                continue
            stored.add(link)
            if not is_placeholder_description(record.get("description")):
                described.add(link)
    except Exception as e:
        # Pusty zbiór = scrapery pobiorą wszystko. Wolniej, ale poprawnie.
        logger.warning(f"Could not read the known links ({e}) - fetching every offer")
    _snapshot, _stored = described, stored


def known_links() -> set:
    """Kanoniczne linki obecne w bazie z prawdziwym opisem. Liczone raz na proces."""
    with _lock:
        if _snapshot is None:
            _load()
        return _snapshot


def stored_links() -> set:
    with _lock:
        if _stored is None:
            _load()
        return _stored


def reset_cache():
    """Wymuś ponowne wczytanie - używane w testach i po --refresh."""
    global _snapshot, _stored
    with _lock:
        _snapshot = _stored = None


def split_known(links):
    """Rozdziel listę linków na (nowe, już znane) - zachowuje kolejność."""
    known = known_links()
    fresh, seen = [], []
    for link in links:
        (seen if canonical_link(link) in known else fresh).append(link)
    return fresh, seen
