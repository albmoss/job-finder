"""
aplikuj.pl - portal ogólny z ~35 tys. ofert, wyraźnie przechylony w stronę
entry-level: praca fizyczna, magazyn, ochrona, obsługa klienta, staże.
Najlepsze dopasowanie do profilu kandydata spośród dodanych źródeł.

Metoda: HTTP + schema.org JobPosting w ld+json na stronie oferty (pełny opis,
datePosted, validThrough, employmentType, branża).

Paginacja: /praca/{miasto}/strona-2 - i tylko ta forma działa. Warianty
?strona=2, ?page=2 i /praca/{miasto}/2 zwracają z powrotem stronę 1, co bez
kontroli "brak nowych linków" dawałoby scraper mielący w kółko te same oferty.

Kategorie: /praca/{miasto}/{kategoria} oraz /praca/{miasto}/{kategoria}/strona-{N}.
Kategorie dobierane są przez utils.portal_categories.pick_categories do profilu CV.
"""

import logging

import re
from html import unescape
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from utils.links import canonical_link, logo_url

logger = logging.getLogger(__name__)

_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_ATTR_RE = re.compile(r'(?<![\w-])(src|alt)="([^"]*)"')

# Źródło: https://www.aplikuj.pl/zawody (kategorie zawodowe, sprawdzono: 2026-09-30)
APLIKUJ_CATEGORIES: dict[str, str] = {
    'agencja-reklamowa': 'Agencja reklamowa',
    'badania-i-rozwoj': 'Badania i rozwój',
    'bankowosc-finanse': 'Bankowość / Finanse',
    'bhp-ochrona-srodowiska': 'BHP / Ochrona środowiska',
    'budownictwo-architektura-geodezja': 'Budownictwo / Architektura / Geodezja',
    'doradztwo-konsulting-audyt': 'Doradztwo / Konsulting / Audyt',
    'edukacja-badania-naukowe-szkolenia-tlumaczenia': 'Edukacja / Badania naukowe / Szkolenia / Tłumaczenia',
    'energia-odnawialna': 'Energia odnawialna',
    'fizyczna': 'Praca fizyczna',
    'franczyza-wlasny-biznes': 'Franczyza / Własny biznes',
    'grafika-i-fotografia': 'Grafika i fotografia',
    'hotelarstwo-gastronomia-turystyka': 'Hotelarstwo / Gastronomia / Turystyka',
    'hr-kadry': 'HR / Kadry',
    'informatyk': 'Informatyk',
    'internet-e-commerce-nowe-media': 'Internet / E-commerce / Nowe media',
    'inzynieria-technologia-technika': 'Inżynieria / Technologia / Technika',
    'it': 'IT / Nowe technologie',
    'ksiegowy': 'Księgowy',
    'logistyka-spedycja-transport': 'Logistyka / Spedycja / Transport',
    'magazynier': 'Magazynier',
    'medycyna-farmacja-zdrowie': 'Medycyna / Farmacja / Zdrowie',
    'motoryzacja': 'Motoryzacja',
    'nieruchomosci': 'Nieruchomości',
    'obsluga-klienta-call-center': 'Obsługa klienta / Call center',
    'pracownik-biurowy': 'Pracownik biurowy',
    'praktyki-staze': 'Praktyki / Staże',
    'prawo-i-administracja-panstwowa': 'Prawo i administracja państwowa',
    'produkcja-przemysl': 'Produkcja / Przemysł',
    'przy-komputerze': 'Praca przy komputerze',
    'rekreacja-i-sport': 'Rekreacja i Sport',
    'rolnictwo': 'Rolnictwo',
    'sektor-publiczny-sluzby-mundurowe': 'Sektor publiczny / Służby mundurowe',
    'serwis-montaz': 'Serwis / Montaż',
    'sprzedaz-zakupy': 'Sprzedaż / Zakupy',
    'staz': 'Staż',
    'sztuka-rozrywka-kreacja-projektowanie': 'Sztuka / Rozrywka / Kreacja / Projektowanie',
    'telekomunikacja': 'Telekomunikacja',
    'ubezpieczenia': 'Ubezpieczenia',
    'uroda-pielegnacja-dietetyka': 'Uroda / Pielęgnacja / Dietetyka',
    'wytworstwo-rzemioslo': 'Wytwórstwo / Rzemiosło',
    'zarzadzanie-dyrekcja': 'Zarządzanie / Dyrekcja',
}


def fetch_aplikuj_categories() -> dict[str, str]:
    """Pobiera kategorie z https://www.aplikuj.pl/zawody w czasie działania, z fallbackiem do słownika statycznego."""
    url = "https://www.aplikuj.pl/zawody"
    try:
        resp = requests.get(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
                'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.8',
            },
            timeout=8,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            h_kat = soup.find(lambda t: t.name in ['h2', 'h3'] and 'Praca w kategoriach zawodowych' in t.get_text())
            if h_kat:
                container = h_kat.find_next_sibling() or h_kat.parent.find_next_sibling()
                if container:
                    cats = {}
                    for a in container.find_all('a', href=True):
                        slug = a['href'].rstrip('/').split('/')[-1]
                        name = a.get_text(strip=True)
                        if name.startswith("Praca "):
                            name = name[6:].strip()
                        cats[slug] = name
                    if 'it' not in cats:
                        cats['it'] = 'IT / Nowe technologie'
                    if 'przy-komputerze' not in cats:
                        cats['przy-komputerze'] = 'Praca przy komputerze'
                    if 'staz' not in cats:
                        cats['staz'] = 'Staż'
                    return cats
    except Exception as e:
        logger.debug(f"aplikuj.pl: nie udało się pobrać kategorii dynamicznie ({e}) - używam słownika statycznego")
    return APLIKUJ_CATEGORIES

from .ldjson_scraper_base import LdJsonPortalScraper


class AplikujScraper(LdJsonPortalScraper):
    SOURCE_NAME = "aplikuj.pl"
    CONFIG_KEY = "aplikuj"

    OFFER_LINK_RE = re.compile(r"https://www\.aplikuj\.pl/oferta/\d+/[a-z0-9\-]+")

    DEFAULT_MAX_PAGES = 12
    DEFAULT_MAX_OFFERS = 400

    def __init__(self, config: dict):
        super().__init__(config)
        self.city_slug = self.cfg.get("city_slug") or self.city_slug
        self.extra_paths: List[str] = self.cfg.get("extra_paths", [])

        catalog = fetch_aplikuj_categories()
        from utils.portal_categories import pick_categories
        picked = pick_categories("aplikuj", catalog)
        if picked is not None:
            self.categories: Optional[List[str]] = picked
            logger.info(f"aplikuj.pl: {len(picked)} categories from CV")
        else:
            self.categories = None

    def build_listing_url(self, page: int) -> str:
        base = f"https://www.aplikuj.pl/praca/{self.city_slug}"
        if self.categories and len(self.categories) == 1:
            cat_base = f"{base}/{self.categories[0]}"
            return cat_base if page <= 1 else f"{cat_base}/strona-{page}"

        if self.extra_paths:
            if page <= self.max_pages - len(self.extra_paths):
                return base if page <= 1 else f"{base}/strona-{page}"
            idx = page - (self.max_pages - len(self.extra_paths)) - 1
            if 0 <= idx < len(self.extra_paths):
                return f"{base}/{self.extra_paths[idx]}"

        return base if page <= 1 else f"{base}/strona-{page}"

    def build_category_url(self, category: str, page: int = 1) -> str:
        base = f"https://www.aplikuj.pl/praca/{self.city_slug}/{category}"
        return base if page <= 1 else f"{base}/strona-{page}"

    def _collect_links(self) -> List[str]:
        if not self.categories:
            return super()._collect_links()

        ordered, seen = [], set()
        for cat in self.categories:
            if len(ordered) >= self.max_offers:
                break
            for page in range(1, self.max_pages + 1):
                url = self.build_category_url(cat, page)
                html = self._fetch(url)
                if not html:
                    break
                found = [canonical_link(u) for u in self.extract_offer_links(html)]
                new_in_page = [u for u in found if u and u not in seen]
                for u in new_in_page:
                    seen.add(u)
                    ordered.append(u)

                logger.info(
                    f"{self.SOURCE_NAME} [{cat}]: strona {page} - {len(found)} linków, "
                    f"{len(new_in_page)} nowych (łącznie {len(ordered)})"
                )

                if not new_in_page:
                    break

                if len(ordered) >= self.max_offers:
                    logger.info(f"{self.SOURCE_NAME}: reached the limit of {self.max_offers} offers")
                    break

        return ordered[: self.max_offers]

    def _logo_from_html(self, html: str, job) -> Optional[str]:
        for tag in _IMG_RE.findall(html):
            attrs = dict(_IMG_ATTR_RE.findall(tag))
            src = unescape(attrs.get("src", ""))
            if src.startswith("/media/") and unescape(attrs.get("alt", "")).strip() == job.company:
                return logo_url(src, base="https://www.aplikuj.pl/")
        return None
