"""
Wspólna baza dla portali korzystających z API "candidate-api".

JustJoin.it i RocketJobs.pl należą do tej samej grupy i wystawiają identyczne
API pod /api/candidate-api/offers:
  - lista:      GET /api/candidate-api/offers?city=&experienceLevels=&from=<cursor>
  - szczegóły:  GET /api/candidate-api/offers/<slug>   -> pole `body` z pełnym opisem HTML

Wcześniej każdy z tych scraperów miał własną implementację (RocketJobs dodatkowo
sniffował ruch przez Playwright i zwracał 0 ofert). Tutaj jest jedna logika.
"""

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from utils.candidate_scope import scope_city, scope_levels
from utils.portal_categories import pick_categories
from utils.data_models import Job
from utils.links import canonical_link
from utils.offer_fields import (
    norm_seniority,
    norm_work_modes,
    norm_contracts,
    norm_schedules,
    norm_skills,
    make_salary,
    make_language,
)
logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
}

# Candidate-api (JustJoin.it i RocketJobs.pl) akceptuje wyłącznie: intern, junior, mid, senior.
CANDIDATE_API_LEVEL_MAP = {
    "intern": "intern",
    "junior": "junior",
    "mid": "mid",
    "senior": "senior",
    "lead": "senior",
    "manager": "senior",
}

# Kategorie portali - źródło: https://justjoin.it/job-offers/all-locations (sprawdzone 2026-09-30)
JUSTJOIN_CATEGORIES = {
    "admin": "Admin",
    "ai": "AI/ML",
    "analytics": "Analytics",
    "architecture": "Architecture",
    "c": "C",
    "data": "Data",
    "devops": "DevOps",
    "erp": "ERP",
    "game": "Game",
    "go": "Go",
    "html": "HTML",
    "java": "Java",
    "javascript": "JavaScript",
    "mobile": "Mobile",
    "net": "Net",
    "other": "Other",
    "php": "PHP",
    "pm": "PM",
    "python": "Python",
    "ruby": "Ruby",
    "scala": "Scala",
    "security": "Security",
    "support": "Support",
    "testing": "Testing",
    "ux": "UX/UI",
}

# Kategorie portali - źródło: https://rocketjobs.pl/oferty-pracy/warszawa (sprawdzone 2026-09-30)
ROCKETJOBS_CATEGORIES = {
    "bankowosc": "Bankowość",
    "bi-data": "BI & Data",
    "budownictwo": "Budownictwo",
    "consulting": "Consulting",
    "design": "Design",
    "edukacja": "Edukacja",
    "finanse": "Finanse",
    "gastronomia": "Gastronomia",
    "hr": "HR",
    "inne": "Inne",
    "inzynieria": "Inżynieria",
    "logistyka": "Logistyka",
    "marketing": "Marketing",
    "media": "Media",
    "nieruchomosci": "Nieruchomości",
    "pm": "Zarządzanie",
    "praca-biurowa": "Praca biurowa",
    "praca-w-sklepie": "Praca w sklepie",
    "prawo": "Prawo",
    "produkcja": "Produkcja",
    "sales": "Sprzedaż",
    "support": "Obsługa klienta",
    "turystyka": "Turystyka",
    "zdrowie": "Zdrowie i uroda",
}


class CandidateAPIScraper(BaseScraper):
    """Baza dla scraperów opartych o candidate-api. Nie używa przeglądarki."""

    # Do nadpisania w podklasach
    PORTAL_URL = ""          # np. "https://justjoin.it"
    SOURCE_NAME = ""
    CONFIG_KEY = ""          # klucz w SCRAPER_CONFIG
    PORTAL_KEY = ""

    # Ile ofert maksymalnie dociągać ze szczegółami (opis) w jednym przebiegu - najdroższa
    # część. Oferty ponad limit zapisują się z zaślepką; `known_links` nie liczy ich jako
    # znanych, więc następny przebieg pobiera ich opisy. Tempo zmierzone 30.09.2026 przy
    # 3 wątkach: JustJoin ~310 opisów/min, RocketJobs ~195/min (przy limicie 1200 zostało
    # wtedy 1408 + 1948 ofert bez opisu). 4000 to u RocketJobs ~20 min, mniej niż cały etap pobierania.
    MAX_DETAIL_FETCH = 4000
    DETAIL_WORKERS = 3

    def __init__(self, config: dict):
        super().__init__(config)
        portal_cfg = config.get(self.CONFIG_KEY, {}) or {}
        # Oferty już obecne w bazie pomijamy w całości: `record_scrape` i tak nie
        # nadpisuje istniejącego rekordu, więc pobranie ich opisu było czystym
        # kosztem. Przy RocketJobs to ~1000 zapytań na przebieg (11 minut).
        self.skip_known_details = portal_cfg.get("skip_known_details", True)
        self.seen_again_links = []
        self._rate_lock = threading.Lock()
        self._cooldown_until = 0.0
        self._detail_failures = 0
        self._throttled = 0

    @property
    def api_url(self) -> str:
        return f"{self.PORTAL_URL}/api/candidate-api/offers"

    def get_source_name(self) -> str:
        return self.SOURCE_NAME

    def scrape_jobs(self) -> List[Job]:
        """Nieużywane - run() jest nadpisane, żeby pominąć przeglądarkę."""
        return []

    def _headers(self) -> dict:
        h = dict(DEFAULT_HEADERS)
        h["Referer"] = f"{self.PORTAL_URL}/"
        h["Origin"] = self.PORTAL_URL
        return h

    def _fetch_category_catalog(self, session: requests.Session) -> dict[str, str]:
        """Pobierz katalog kategorii z portalu w czasie działania lub użyj słownika zapasowego."""
        portal_key = self.PORTAL_KEY or self.CONFIG_KEY
        fallback = JUSTJOIN_CATEGORIES if portal_key == "justjoinit" else ROCKETJOBS_CATEGORIES
        url_path = "/job-offers/all-locations" if portal_key == "justjoinit" else "/oferty-pracy/warszawa"
        pattern = r"^/job-offers/[^/]+/([a-zA-Z0-9_-]+)$" if portal_key == "justjoinit" else r"^/oferty-pracy/[^/]+/([a-zA-Z0-9_-]+)$"
        try:
            resp = session.get(f"{self.PORTAL_URL}{url_path}", headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                import re
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                cats = {}
                for a in soup.find_all("a", href=True):
                    m = re.match(pattern, a["href"])
                    if m:
                        slug = m.group(1)
                        raw = a.get_text(strip=True)
                        clean = re.sub(r"[\d\s\xa0]+$", "", raw)
                        if clean:
                            cats[slug] = clean
                if cats:
                    return cats
        except Exception as e:
            logger.debug(f"{self.SOURCE_NAME}: nie udało się pobrać kategorii z HTML ({e}), używam słownika zapasowego")
        return dict(fallback)

    def _fetch_listing(self, session: requests.Session) -> List[dict]:
        """Pobierz wszystkie oferty z listy (paginacja kursorowa, miasto + zdalne)."""
        portal_cfg = self.config.get(self.CONFIG_KEY, {}) or {}
        default_city = portal_cfg.get("location") or self.config.get("location", "Warszawa")
        city = scope_city(default_city).capitalize()

        raw_levels = scope_levels()
        levels = list(dict.fromkeys(CANDIDATE_API_LEVEL_MAP[lv] for lv in raw_levels if lv in CANDIDATE_API_LEVEL_MAP)) or ["junior", "intern"]

        catalog = self._fetch_category_catalog(session)
        portal_key = self.PORTAL_KEY or self.CONFIG_KEY
        picked_categories = pick_categories(portal_key, catalog) if catalog else None
        if picked_categories is not None:
            logger.info(f"{self.SOURCE_NAME}: {len(picked_categories)} kategorii z CV")
            cat_params = [("categories", c) for c in picked_categories]
        else:
            cat_params = []

        queries = [
            ("city", [("city", city)] + [("experienceLevels", lv) for lv in levels] + cat_params),
            ("remote", [("isRemote", "true")] + [("experienceLevels", lv) for lv in levels] + cat_params),
        ]

        offers_by_slug = {}
        for q_label, base_params in queries:
            cursor = 0
            seen_cursors = set()
            query_fetched = 0
            while True:
                params = base_params + [("from", cursor)]
                try:
                    resp = session.get(self.api_url, params=params, headers=self._headers(), timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.error(f"{self.SOURCE_NAME}: listing failed ({q_label}) at cursor {cursor}: {e}")
                    break

                page = data.get("data", [])
                if not page:
                    break
                for off in page:
                    slug = off.get("slug")
                    if slug:
                        offers_by_slug[slug] = off

                query_fetched += len(page)
                meta = data.get("meta", {})
                total = meta.get("totalItems", 0)
                next_cursor = (meta.get("next") or {}).get("cursor")

                logger.info(f"{self.SOURCE_NAME} [{q_label}]: cursor {cursor} -> +{len(page)} offers ({query_fetched}/{total})")

                if next_cursor is None or next_cursor in seen_cursors or query_fetched >= total:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                time.sleep(0.3 + random.random() * 0.3)

        logger.info(f"{self.SOURCE_NAME}: fetched {len(offers_by_slug)} unique offers from the listing")
        return list(offers_by_slug.values())

    def _matches_location(self, offer: dict, target_city: str) -> bool:
        """
        API zwraca ~10% ofert spoza wskazanego miasta (Gdańsk, Wrocław, Kraków).
        Zostawiamy tylko oferty z docelowego miasta ORAZ wszystkie zdalne -
        praca zdalna jest niezależna od lokalizacji i jest w preferencjach.
        """
        if str(offer.get("workplaceType", "")).lower() == "remote":
            return True

        target = target_city.lower()
        if target in str(offer.get("city", "")).lower():
            return True

        for loc in (offer.get("locations") or []):
            if isinstance(loc, dict) and target in str(loc.get("city", "")).lower():
                return True

        return False

    def _cooldown(self, seconds: float):
        """Wstrzymaj wszystkie wątki po napotkaniu limitu."""
        with self._rate_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def _wait_if_cooling(self):
        with self._rate_lock:
            remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def _fetch_detail(self, session: requests.Session, slug: str) -> dict:
        """
        Pobierz szczegóły oferty z ponawianiem.

        Bez retry przy 960 ofertach ~77% pobrań kończyło się porażką (limit
        włącza się przy większym wolumenie), a oferty zostawały z samymi
        metadanymi z listingu zamiast treści ogłoszenia. Błędy szły do
        logger.debug, więc problem był niewidoczny w logach.
        """
        for attempt in range(3):
            self._wait_if_cooling()
            try:
                resp = session.get(f"{self.api_url}/{slug}", headers=self._headers(), timeout=25)

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code in (429, 503):
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else 0
                    except (TypeError, ValueError):
                        wait = 0
                    wait = min(wait or (3 * (2 ** attempt) + random.random() * 2), 60)
                    self._cooldown(wait)
                    with self._rate_lock:
                        self._throttled += 1
                    continue

                if resp.status_code == 404:
                    return {}  # oferta zniknęła - nie ma sensu ponawiać

                logger.debug(f"{self.SOURCE_NAME}: detail {slug} -> HTTP {resp.status_code}")

            except Exception as e:
                logger.debug(f"{self.SOURCE_NAME}: detail {slug} failed: {e}")

            if attempt < 2:
                time.sleep(1.5 * (attempt + 1) + random.random())

        with self._rate_lock:
            self._detail_failures += 1
        return {}

    def build_link(self, slug: str) -> str:
        return f"{self.PORTAL_URL}/offers/{slug}"

    def _parse(self, offer: dict, detail: dict) -> Job:
        """Zbuduj Job z listy + szczegółów. Pełny opis w body, reszta jako pola strukturalne."""
        title = offer.get("title", "Unknown")
        company = offer.get("companyName") or detail.get("companyName") or "Unknown"
        slug = offer.get("slug", "")

        # Pełny opis oferty - czysty tekst/HTML bez doklejanych metadanych
        body = (detail.get("body") or "").strip()
        description = body if body else f"Oferta z {self.SOURCE_NAME}: {title}"

        # Poziom doświadczenia
        seniority = norm_seniority(offer.get("experienceLevel") or detail.get("experienceLevel"))

        # Tryb pracy
        work_modes = norm_work_modes(offer.get("workplaceType") or detail.get("workplaceType"))

        # Wymiar czasu pracy
        schedules = norm_schedules(offer.get("workingTime") or detail.get("workingTime"))

        # Umiejętności
        req_raw = detail.get("requiredSkills") or offer.get("requiredSkills") or []
        nice_raw = detail.get("niceToHaveSkills") or offer.get("niceToHaveSkills") or []
        skills_required = norm_skills(req_raw)
        skills_nice = norm_skills(nice_raw)

        # Typy umów i wynagrodzenie
        emp_types = detail.get("employmentTypes") or offer.get("employmentTypes") or []
        raw_contracts = [e.get("type") for e in emp_types if isinstance(e, dict) and e.get("type")]
        contract_types = norm_contracts(raw_contracts)

        salary = None
        for emp in emp_types:
            if not isinstance(emp, dict):
                continue
            unit = (emp.get("unit") or "month").lower()
            if unit == "hour" and (emp.get("fromPerUnit") or emp.get("toPerUnit")):
                low = emp.get("fromPerUnit")
                high = emp.get("toPerUnit")
            else:
                low = emp.get("from")
                high = emp.get("to")

            if low or high:
                currency = emp.get("currency", "PLN")
                gross = emp.get("gross")
                emp_type = emp.get("type")
                c_norm = norm_contracts(emp_type)
                contract_code = c_norm[0] if c_norm else None
                s = make_salary(
                    min_value=low,
                    max_value=high,
                    currency=currency,
                    period=unit,
                    gross=gross,
                    contract=contract_code,
                )
                if s:
                    if emp.get("currencySource") == "original" or currency == "PLN":
                        salary = s
                        break
                    if salary is None:
                        salary = s

        # Języki z poziomami
        raw_langs = detail.get("languages") or offer.get("languages") or []
        languages = []
        for l in raw_langs:
            if isinstance(l, dict):
                name = l.get("code") or l.get("name")
                lvl = l.get("level")
                parsed = make_language(name, level=lvl, required=True)
                if parsed:
                    languages.append(parsed)
        languages = languages or None

        # Kategoria
        cat_obj = detail.get("category") or offer.get("category")
        category = None
        if isinstance(cat_obj, dict):
            category = cat_obj.get("key") or cat_obj.get("name")
        elif isinstance(cat_obj, str):
            category = cat_obj

        # Daty publikacji i wygaśnięcia
        posted_date = offer.get("publishedAt") or detail.get("publishedAt") or None
        valid_through = offer.get("expiredAt") or detail.get("expiredAt") or None

        # Lokalizacja
        portal_cfg = self.config.get(self.CONFIG_KEY, {}) or {}
        default_city = portal_cfg.get("location") or self.config.get("location", "Warszawa")
        location = offer.get("city") or detail.get("city") or scope_city(default_city)

        return Job(
            title=title,
            company=company,
            link=canonical_link(self.build_link(slug)),
            description=description,
            source=self.SOURCE_NAME,
            location=location,
            posted_date=posted_date,
            valid_through=valid_through,
            scraped_at=datetime.now().isoformat(),
            seniority=seniority,
            work_modes=work_modes,
            contract_types=contract_types,
            schedules=schedules,
            salary=salary,
            skills_required=skills_required,
            skills_nice=skills_nice,
            languages=languages,
            category=category,
        )
    def run(self) -> List[Job]:
        """Wejście - HTTP zamiast przeglądarki."""
        logger.info(f"{self.SOURCE_NAME}: start (candidate-api, no browser)")
        jobs = []
        session = requests.Session()

        try:
            offers = self._fetch_listing(session)
            if not offers:
                return []

            portal_cfg = self.config.get(self.CONFIG_KEY, {}) or {}
            target_city = scope_city(portal_cfg.get("location") or self.config.get("location", "Warszawa"))
            before = len(offers)
            offers = [o for o in offers if self._matches_location(o, target_city)]
            if before != len(offers):
                logger.info(f"{self.SOURCE_NAME}: filtered out {before - len(offers)} offers outside {target_city}")

            if self.skip_known_details:
                from utils.known_links import known_links

                known = known_links()
                fresh = []
                for offer in offers:
                    link = canonical_link(self.build_link(offer.get("slug", "")))
                    if link in known:
                        self.seen_again_links.append(link)
                    else:
                        fresh.append(offer)
                logger.info(
                    f"{self.SOURCE_NAME}: {len(fresh)} new offers, "
                    f"{len(self.seen_again_links)} already in the database (skipped)"
                )
                offers = fresh
                if not offers:
                    return []

            # Szczegóły równolegle, ale delikatnie - to najwolniejsza faza.
            with_slug = [o for o in offers if o.get("slug")]
            to_detail = with_slug[: self.MAX_DETAIL_FETCH]
            logger.info(f"{self.SOURCE_NAME}: fetching descriptions for {len(to_detail)} offers...")
            if len(with_slug) > len(to_detail):
                # Podsumowanie niżej liczy procent tylko z pobieranych ofert, więc limit widać tylko tutaj.
                logger.warning(
                    f"{self.SOURCE_NAME}: {len(with_slug) - len(to_detail)} offers over the "
                    f"{self.MAX_DETAIL_FETCH} detail limit saved without a description - "
                    f"the next run fetches them"
                )

            details = {}

            def worker(off):
                slug = off["slug"]
                d = self._fetch_detail(session, slug)
                time.sleep(0.35 + random.random() * 0.25)
                return slug, d

            with ThreadPoolExecutor(max_workers=self.DETAIL_WORKERS) as ex:
                for slug, d in ex.map(worker, to_detail):
                    if d:
                        details[slug] = d

            rate = len(details) / len(to_detail) if to_detail else 0
            msg = (f"{self.SOURCE_NAME}: pobrano {len(details)}/{len(to_detail)} pełnych opisów "
                   f"({rate:.0%})")
            if self._throttled:
                msg += f" | limit portalu: {self._throttled}x"
            if rate < 0.8:
                # Widoczne ostrzeżenie - wcześniej porażki szły do debug i ginęły
                logger.warning(msg + " low success rate - offers will be left without a description")
            else:
                logger.info(msg)

            for offer in offers:
                try:
                    jobs.append(self._parse(offer, details.get(offer.get("slug", ""), {})))
                except Exception as e:
                    logger.warning(f"{self.SOURCE_NAME}: parse failed for {offer.get('slug')}: {e}")

        except Exception as e:
            logger.error(f"{self.SOURCE_NAME}: scrape crashed: {e}")
        finally:
            session.close()

        logger.info(f"{self.SOURCE_NAME}: {len(jobs)} offers total")
        return jobs
