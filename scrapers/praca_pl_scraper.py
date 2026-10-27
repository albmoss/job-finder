"""
praca.pl - duży portal ogólny (nie-IT), mocno obsadzony w ofertach biurowych,
obsłudze klienta i pracach fizycznych, czyli dokładnie w profilu kandydata.

Metoda: HTTP + schema.org JobPosting w ld+json na stronie oferty.
Listing daje wyłącznie tytuł i link, pełny opis (700-1700 znaków) jest dopiero
na stronie szczegółów - stąd dwustopniowe pobieranie w LdJsonPortalScraper.

Paginacja: /s-warszawa_2.html, /s-warszawa_3.html ... Wariant z parametrem
(?p=2) jest ignorowany przez serwer i oddaje w kółko stronę 1.

Filtry: portal nie udostępnia filtrowania kategorii branżowych w URL-ach
listingu ani w wyszukiwarce (jedynie miasto i fraza tekstowa), więc scraper
działa bez filtra kategorii i przeszukuje listing miejski.
"""

import re

from utils.candidate_scope import all_poland

from .ldjson_scraper_base import LdJsonPortalScraper


class PracaPlScraper(LdJsonPortalScraper):
    SOURCE_NAME = "praca.pl"
    CONFIG_KEY = "praca_pl"

    # Oferty to /{slug}_{id}.html. Negatywny lookahead na "s-" i "oferty-pracy_" odcina linki
    # paginacji (/s-warszawa_2.html, /oferty-pracy_2.html), które mają identyczny kształt.
    OFFER_LINK_RE = re.compile(r"https://www\.praca\.pl/(?!s-|oferty-pracy_)[a-z0-9\-]+_\d+\.html")

    DEFAULT_MAX_PAGES = 15
    DEFAULT_MAX_OFFERS = 400
    REPORTS_LIVENESS = True

    def __init__(self, config: dict):
        super().__init__(config)
        # Miasto w URL-u jest w mianowniku i bez znaków diakrytycznych
        self.city_slug = self.cfg.get("city_slug") or self.city_slug

    def build_listing_url(self, page: int) -> str:
        base = "oferty-pracy" if all_poland() else f"s-{self.city_slug}"
        if page <= 1:
            return f"https://www.praca.pl/{base}.html"
        return f"https://www.praca.pl/{base}_{page}.html"
