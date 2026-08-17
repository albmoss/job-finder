"""
aplikuj.pl - portal ogólny z ~35 tys. ofert, wyraźnie przechylony w stronę
entry-level: praca fizyczna, magazyn, ochrona, obsługa klienta, staże.
Najlepsze dopasowanie do profilu kandydata spośród dodanych źródeł.

Metoda: HTTP + schema.org JobPosting w ld+json na stronie oferty (pełny opis,
datePosted, validThrough, employmentType, branża).

Paginacja: /praca/{miasto}/strona-2 - i tylko ta forma działa. Warianty
?strona=2, ?page=2 i /praca/{miasto}/2 zwracają z powrotem stronę 1, co bez
kontroli "brak nowych linków" dawałoby scraper mielący w kółko te same oferty.

Poza listingiem miejskim portal wystawia kategorie tematyczne
(/praca/warszawa/staz, /praca/warszawa/przy-komputerze) - można je dorzucić
przez `extra_paths` w konfiguracji.
"""

import re
from typing import List

from .ldjson_scraper_base import LdJsonPortalScraper


class AplikujScraper(LdJsonPortalScraper):
    SOURCE_NAME = "aplikuj.pl"
    CONFIG_KEY = "aplikuj"

    OFFER_LINK_RE = re.compile(r"https://www\.aplikuj\.pl/oferta/\d+/[a-z0-9\-]+")

    DEFAULT_MAX_PAGES = 12
    DEFAULT_MAX_OFFERS = 400

    def __init__(self, config: dict):
        super().__init__(config)
        self.city_slug = self.cfg.get("city_slug", self.location_filter)
        # Ścieżki kategorii doklejane po wyczerpaniu listingu głównego,
        # np. ["staz", "przy-komputerze", "umowa-zlecenie"]
        self.extra_paths: List[str] = self.cfg.get("extra_paths", ["staz"])

    def build_listing_url(self, page: int) -> str:
        base = f"https://www.aplikuj.pl/praca/{self.city_slug}"

        # Pierwsze `max_pages` stron to listing miejski, kolejne strony
        # to pierwsze strony kategorii dodatkowych.
        if page <= self.max_pages - len(self.extra_paths):
            return base if page <= 1 else f"{base}/strona-{page}"

        idx = page - (self.max_pages - len(self.extra_paths)) - 1
        if 0 <= idx < len(self.extra_paths):
            return f"{base}/{self.extra_paths[idx]}"
        return base
