"""
gowork.pl - portal ogólny z bardzo wysokim udziałem ofert entry-level
(magazyn, ochrona, opieka, obsługa klienta, rejestracja medyczna).
Profilowo najbliższy kandydatowi razem z aplikuj.pl.

Metoda: HTTP + schema.org JobPosting w ld+json na stronie oferty
(opisy rzędu 1400 znaków, datePosted, employmentType, branża).

Paginacja jest w postaci /praca/{miasto};l/{N};pg - segmenty z sufiksami
(`;l` = lokalizacja, `;pg` = strona) zamiast query stringu.
Strona 1 nie ma segmentu numeru.
"""

import re

from .ldjson_scraper_base import LdJsonPortalScraper


class GoWorkScraper(LdJsonPortalScraper):
    SOURCE_NAME = "GoWork.pl"
    CONFIG_KEY = "gowork"

    # Oferty to /oferta/{slug},{id},{miasto} - linki są względne, więc
    # uzupełniamy je o domenę w extract_offer_links().
    OFFER_LINK_RE = re.compile(r'href="(/oferta/[^"]+)"')

    DEFAULT_MAX_PAGES = 10
    DEFAULT_MAX_OFFERS = 400

    # GoWork tnie ostrzej niż praca.pl i aplikuj.pl: przy 0.6 s i 3 wątkach sypał
    # HTTP 429 kilkanaście razy na 400 pobrań. Sondowanie krótkimi seriami nie
    # pokazuje tego limitu (18 żądań bez odstępu przechodzi bez jednego 429),
    # więc jest to limit w oknie czasowym, widoczny dopiero przy dłuższej pracy.
    # 0.8 s to kompromis: pojedynczy 429 kosztuje 10 s cooldownu i jest
    # obsłużony, więc lepiej jechać szybciej i czasem odczekać, niż zwolnić
    # cały przebieg o godzinę.
    MIN_REQUEST_INTERVAL = 0.8
    DETAIL_THREADS = 2

    def __init__(self, config: dict):
        super().__init__(config)
        self.city_slug = self.cfg.get("city_slug", self.location_filter)

    def build_listing_url(self, page: int) -> str:
        base = f"https://www.gowork.pl/praca/{self.city_slug};l"
        return base if page <= 1 else f"{base}/{page};pg"

    def extract_offer_links(self, html: str):
        return [
            "https://www.gowork.pl" + path
            for path in self.OFFER_LINK_RE.findall(html)
        ]
