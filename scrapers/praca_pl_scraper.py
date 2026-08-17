"""
praca.pl - duży portal ogólny (nie-IT), mocno obsadzony w ofertach biurowych,
obsłudze klienta i pracach fizycznych, czyli dokładnie w profilu kandydata.

Metoda: HTTP + schema.org JobPosting w ld+json na stronie oferty.
Listing daje wyłącznie tytuł i link, pełny opis (700-1700 znaków) jest dopiero
na stronie szczegółów - stąd dwustopniowe pobieranie w LdJsonPortalScraper.

Paginacja: /s-warszawa_2.html, /s-warszawa_3.html ... Wariant z parametrem
(?p=2) jest ignorowany przez serwer i oddaje w kółko stronę 1.
"""

import re

from .ldjson_scraper_base import LdJsonPortalScraper


class PracaPlScraper(LdJsonPortalScraper):
    SOURCE_NAME = "praca.pl"
    CONFIG_KEY = "praca_pl"

    # Oferty to /{slug}_{id}.html. Negatywny lookahead na "s-" odcina linki
    # paginacji (/s-warszawa_2.html), które mają identyczny kształt.
    OFFER_LINK_RE = re.compile(r"https://www\.praca\.pl/(?!s-)[a-z0-9\-]+_\d+\.html")

    DEFAULT_MAX_PAGES = 15
    DEFAULT_MAX_OFFERS = 400

    def __init__(self, config: dict):
        super().__init__(config)
        # Miasto w URL-u jest w mianowniku i bez znaków diakrytycznych
        self.city_slug = self.cfg.get("city_slug", self.location_filter)

    def build_listing_url(self, page: int) -> str:
        if page <= 1:
            return f"https://www.praca.pl/s-{self.city_slug}.html"
        return f"https://www.praca.pl/s-{self.city_slug}_{page}.html"
