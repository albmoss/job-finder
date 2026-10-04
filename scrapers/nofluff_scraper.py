"""
NoFluffJobs Scraper (Internal API Strategy)
Uses NoFluffJobs' internal search API instead of Playwright for speed and reliability.

NOTE: This uses an undocumented internal API (visible in browser DevTools).
It may change without notice. If it breaks, check DevTools for updated endpoints.
"""

import html
import logging
import time
import requests
from typing import List
from datetime import datetime

from scrapers.base_scraper import BaseScraper
from utils.candidate_scope import scope_city, scope_levels
from utils.portal_categories import pick_categories
from utils.data_models import Job
from utils.links import logo_url
from utils.offer_fields import (
    norm_seniority,
    norm_work_modes,
    norm_contracts,
    norm_skills,
    make_salary,
    make_language,
)
logger = logging.getLogger(__name__)

SEARCH_URL = "https://nofluffjobs.com/api/search/posting?salaryCurrency=PLN&salaryPeriod=month&region=pl"
DETAIL_URL = "https://nofluffjobs.com/api/posting"
LOGO_HOST = "https://static.nofluffjobs.com/"
LOGO_SIZES = ("jobs_details", "jobs_details_2x", "jobs_listing_2x", "original")

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Referer": "https://nofluffjobs.com/pl",
    "Origin": "https://nofluffjobs.com",
}

# NoFluffJobs akceptuje w criteriaSearch: trainee, junior, mid, senior, expert, lead
NFJ_LEVEL_MAP = {
    "intern": "trainee",
    "junior": "junior",
    "mid": "mid",
    "senior": "senior",
    "lead": "lead",
    "manager": "lead",
}

# Kategorie portalu - źródło: https://nofluffjobs.com/pl (sprawdzone 2026-09-30)
NOFLUFF_CATEGORIES = {
    "agile": "Agile",
    "architecture": "Architecture",
    "artificial-intelligence": "AI/ML",
    "automation": "Automatyka",
    "backend": "Backend",
    "business-analyst": "Business Analysis",
    "business-intelligence": "Business Intelligence",
    "consulting": "Doradztwo",
    "customer-service": "Obsługa klienta",
    "data": "Data",
    "devops": "DevOps",
    "electrical-eng": "Inżynieria elektryczna",
    "electronics": "Elektronika",
    "embedded": "Embedded",
    "erp": "ERP",
    "finance": "Finanse",
    "frontend": "Frontend",
    "fullstack": "Fullstack",
    "game-dev": "GameDev",
    "hr": "HR",
    "law": "Prawo",
    "logistics": "Logistyka",
    "marketing": "Marketing",
    "mechanics": "Mechanika",
    "mobile": "Mobile",
    "office-administration": "Administracja biurowa",
    "other": "Inne IT",
    "pm": "PM",
    "product-management": "Product Management",
    "project-manager": "Project Manager",
    "sales": "Sprzedaż",
    "security": "Security",
    "support": "Support",
    "sys-administrator": "Sys. Administrator",
    "telecommunication": "Telekomunikacja",
    "testing": "Testing",
    "ux": "Design",
}


class NoFluffScraper(BaseScraper):
    """Scraper NoFluffJobs na wewnętrznym API - bez przeglądarki."""
    DEFAULT_MAX_DETAILS = 1500

    def __init__(self, config: dict):
        super().__init__(config)
        portal_cfg = config.get("nofluffjobs", {}) or {}
        self.skip_known_details = portal_cfg.get("skip_known_details", True)
        self.max_details = portal_cfg.get("max_details", self.DEFAULT_MAX_DETAILS)
        self.seen_again_links = []
        self.liveness_scope = None


    def get_source_name(self) -> str:
        return "NoFluffJobs"

    def scrape_jobs(self) -> List[Job]:
        """Not used — run() is overridden to use API instead of browser."""
        return []

    def _fetch_category_catalog(self, session: requests.Session) -> dict[str, str]:
        """Pobierz katalog kategorii z portalu w czasie działania lub użyj słownika zapasowego."""
        try:
            resp = session.get(
                "https://nofluffjobs.com/pl",
                headers={"User-Agent": HEADERS["User-Agent"], "Accept-Language": "pl"},
                timeout=10,
            )
            if resp.status_code == 200:
                import re
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                cats = {}
                for a in soup.find_all("a", href=True):
                    m = re.match(r"^/pl/([a-zA-Z0-9_-]+)$", a["href"])
                    if m:
                        slug = m.group(1)
                        text = a.get_text(strip=True)
                        if slug not in ("companies", "kalkulator-wynagrodzen", "insights", "blog", "wizard", "praca") and text:
                            cats[slug] = text
                if cats:
                    return cats
        except Exception as e:
            logger.debug(f"NFJ API: nie udało się pobrać kategorii z HTML ({e}), używam słownika zapasowego")
        return dict(NOFLUFF_CATEGORIES)

    def _search(self, session: requests.Session, criteria: dict, label: str) -> tuple[list, bool]:
        """Pobierz wszystkie strony wyników dla jednego zestawu kryteriów; drugi element: bez błędu."""
        postings_by_id = {}
        page = 1
        complete = True

        while True:
            try:
                resp = session.post(f"{SEARCH_URL}&page={page}", json={"criteriaSearch": criteria},
                                    headers=HEADERS, timeout=30)
                if resp.status_code == 403:
                    logger.warning("NFJ API: 403 Forbidden — API may have changed or is blocking")
                    complete = False
                    break
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                logger.error(f"NFJ API: Search request failed ({label}): {e}")
                complete = False
                break

            postings = data.get("postings", data.get("items", []))
            before = len(postings_by_id)
            for posting in postings:
                postings_by_id.setdefault(posting.get("id") or posting.get("url"), posting)
            total_pages = int(data.get("totalPages") or page)
            logger.info(f"NFJ API [{label}]: page {page}/{total_pages} - {len(postings)} offers, "
                        f"{len(postings_by_id) - before} new (total: {data.get('totalCount')})")

            if not postings or len(postings_by_id) == before or page >= total_pages:
                break

            page += 1
            time.sleep(0.5)

        return list(postings_by_id.values()), complete

    @staticmethod
    def _places(data: dict) -> list:
        location = (data or {}).get("location") or {}
        if not isinstance(location, dict):
            return []
        return [p for p in (location.get("places") or []) if isinstance(p, dict)]

    @staticmethod
    def _is_remote_place(place: dict) -> bool:
        return str(place.get("city") or "").strip().lower() == "remote" or bool(place.get("remote"))

    @classmethod
    def _remote_possible(cls, posting: dict, detail: dict | None = None) -> bool:
        for data in (posting, detail or {}):
            location = data.get("location") or {}
            if data.get("fullyRemote") or (isinstance(location, dict) and location.get("fullyRemote")):
                return True
            if any(cls._is_remote_place(p) for p in cls._places(data)):
                return True
        return False

    @classmethod
    def _in_scope(cls, posting: dict, target_city: str) -> bool:
        """Oferta z docelowego miasta albo z możliwością pracy zdalnej."""
        if cls._remote_possible(posting):
            return True
        target = target_city.lower()
        location = posting.get("location") or {}
        cities = [str(p.get("city") or "") for p in cls._places(posting)]
        if isinstance(location, dict):
            cities.append(str(location.get("city") or ""))
        return any(target in city.lower() for city in cities)

    @classmethod
    def _variant_slugs(cls, posting: dict) -> list:
        own = posting.get("url") or posting.get("slug") or posting.get("id") or ""
        slugs = [own] + [p.get("url") for p in cls._places(posting) if p.get("url")]
        return [s for s in dict.fromkeys(slugs) if s]

    @classmethod
    def _pick_variant(cls, group: list, target_city: str) -> dict:
        target = target_city.lower()

        def own_place(posting):
            url = posting.get("url")
            return next((p for p in cls._places(posting) if p.get("url") == url), {})

        for wanted in (lambda p: target in str(p.get("city") or "").lower(), cls._is_remote_place):
            for posting in group:
                if wanted(own_place(posting)):
                    return posting
        return group[0]

    def _fetch_listings(self, session: requests.Session) -> list:
        """
        Pobierz oferty w Polsce wg poziomów z CV i przefiltruj lokalnie.
        """
        cfg = getattr(self, "config", {})
        portal_cfg = cfg.get("nofluffjobs", {}) or {}
        target_city = scope_city(portal_cfg.get("location") or cfg.get("location", "Warszawa"))

        raw_levels = scope_levels()
        seniority = list(dict.fromkeys(NFJ_LEVEL_MAP[lv] for lv in raw_levels if lv in NFJ_LEVEL_MAP)) or ["trainee", "junior"]

        criteria = {"seniority": seniority, "country": ["poland"]}
        catalog = self._fetch_category_catalog(session)
        picked_categories = pick_categories("nofluffjobs", catalog) if catalog else None
        if picked_categories is not None:
            logger.info(f"NoFluffJobs: {len(picked_categories)} kategorii z CV")
            criteria["category"] = picked_categories

        label_cats = f" [{len(picked_categories)} cats]" if picked_categories else ""
        postings, complete = self._search(
            session,
            criteria,
            f"PL {','.join(seniority)}{label_cats}",
        )
        if complete:
            self.liveness_scope = "|".join([target_city, ",".join(seniority), ",".join(sorted(picked_categories or []))])

        groups = {}
        in_scope = 0
        for posting in postings:
            if not self._in_scope(posting, target_city):
                continue
            in_scope += 1
            key = posting.get("reference") or posting.get("url") or posting.get("id")
            if key:
                groups.setdefault(key, []).append(posting)
        unique = [self._pick_variant(group, target_city) for group in groups.values()]

        logger.info(
            f"NFJ API: {len(postings)} offers in PL -> {in_scope} matching "
            f"({target_city} lub zdalne) -> {len(unique)} unique offers"
        )
        return unique

    def _fetch_detail(self, session: requests.Session, slug: str) -> dict:
        url = f"{DETAIL_URL}/{slug}"
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.debug(f"NFJ API: Detail for {slug} returned {resp.status_code}")
        except requests.RequestException as e:
            logger.debug(f"NFJ API: Detail fetch failed for {slug}: {e}")
        return {}

    def _parse_posting(self, posting: dict, detail: dict) -> Job:
        """Convert API posting + detail to Job dataclass with structured fields."""
        title = html.unescape(posting.get("title") or "Unknown")
        company = html.unescape(
            posting.get("name")
            or (posting.get("company") or {}).get("name")
            or posting.get("companyName")
            or "Unknown"
        )
        slug = posting.get("url", posting.get("slug", posting.get("id", "")))
        link = f"https://nofluffjobs.com/pl/job/{slug}"

        # Czysty opis: właściwy opis + zadania + wymagania tekstowe (bez metadanych)
        desc_parts = []
        if detail:
            main_desc = (detail.get("details") or {}).get("description")
            if isinstance(main_desc, str) and main_desc.strip():
                desc_parts.append(main_desc.strip())

            daily = (detail.get("specs") or {}).get("dailyTasks") or []
            tasks = [str(t).strip() for t in daily if str(t).strip()]
            if tasks:
                desc_parts.append("Zadania:\n" + "\n".join(f"- {t}" for t in tasks))

            req_desc = (detail.get("requirements") or {}).get("description")
            if isinstance(req_desc, str) and req_desc.strip():
                desc_parts.append("Wymagania:\n" + req_desc.strip())

        if not desc_parts:
            desc_parts.append(f"Oferta z NoFluffJobs: {title}")

        description = "\n\n".join(desc_parts)

        # Poziom doświadczenia
        seniority = norm_seniority(posting.get("seniority") or (detail.get("basics") or {}).get("seniority"))

        # Tryb pracy
        modes = []
        if self._remote_possible(posting, detail):
            modes.append("remote")
        loc_data = detail.get("location") or posting.get("location") or {}
        places = self._places(detail) or self._places(posting)
        cities = [str(p.get("city")).strip() for p in places
                  if p.get("city") and not self._is_remote_place(p)]
        if isinstance(loc_data, dict) and loc_data.get("hybridDesc"):
            modes.append("hybrid")
        elif not modes and cities:
            modes.append("onsite")
        work_modes = norm_work_modes(modes)

        # Umiejętności: wymagane i mile widziane
        musts = (detail.get("requirements") or {}).get("musts") or []
        nices = (detail.get("requirements") or {}).get("nices") or []
        skills_required = norm_skills(musts)
        if not skills_required:
            tiles = posting.get("tiles") or {}
            req_tiles = [v for v in tiles.get("values", []) if isinstance(v, dict) and v.get("type") == "requirement"]
            skills_required = norm_skills(req_tiles) or norm_skills(tiles.get("technologies"))
        skills_nice = norm_skills(nices)

        # Wynagrodzenie i typ umowy
        sal = posting.get("salary") or {}
        salary = None
        contract_types = None
        if sal:
            low = sal.get("from") or sal.get("min")
            high = sal.get("to") or sal.get("max")
            currency = sal.get("currency", "PLN")
            period = sal.get("period")
            sal_type = sal.get("type")
            contract = norm_contracts(sal_type)
            contract_code = contract[0] if contract else None
            gross = False if contract_code == "b2b" else (True if contract_code == "uop" else None)
            salary = make_salary(low, high, currency=currency, period=period, gross=gross, contract=contract_code)
            contract_types = contract

        # Języki
        langs = []
        for l in (detail.get("requirements") or {}).get("languages") or []:
            if isinstance(l, dict):
                code = l.get("code")
                req = str(l.get("type", "MUST")).upper() == "MUST"
                parsed = make_language(code, required=req)
                if parsed:
                    langs.append(parsed)
        languages = langs or None

        # Kategoria
        category = posting.get("category") or (detail.get("basics") or {}).get("category")

        # Daty
        posted_raw = posting.get("posted") or detail.get("posted")
        posted_date = None
        if isinstance(posted_raw, (int, float)):
            posted_date = datetime.fromtimestamp(posted_raw / 1000.0).isoformat()
        elif isinstance(posted_raw, str):
            posted_date = posted_raw
        valid_through = detail.get("expiresAt") or None

        # Lokalizacja
        cfg = getattr(self, "config", {})
        default_city = (cfg.get("nofluffjobs", {}) or {}).get("location") or cfg.get("location", "Warszawa")
        target = scope_city(default_city).lower()
        location = next((c for c in cities if target in c.lower()), None) or (cities[0] if cities else None)
        if not location and "remote" in (work_modes or ()):
            location = "Polska"

        logos = posting.get("logo") or {}
        logo_raw = next((logos[k] for k in LOGO_SIZES if logos.get(k)), None) if isinstance(logos, dict) else None

        return Job(
            title=title,
            company=company,
            link=link,
            description=description,
            source=self.get_source_name(),
            location=location,
            posted_date=posted_date,
            valid_through=valid_through,
            scraped_at=datetime.now().isoformat(),
            seniority=seniority,
            work_modes=work_modes,
            contract_types=contract_types,
            salary=salary,
            skills_required=skills_required,
            skills_nice=skills_nice,
            languages=languages,
            category=category,
            logo_url=logo_url(logo_raw, base=LOGO_HOST),
        )

    def run(self) -> List[Job]:
        """Override BaseScraper.run() to skip browser — uses HTTP API instead."""
        logger.info("NoFluffJobs: Starting API-based scrape (no browser)")
        jobs = []

        session = requests.Session()

        try:
            postings = self._fetch_listings(session)
            logger.info(f"NFJ API: Fetched {len(postings)} listing summaries")

            if getattr(self, "skip_known_details", True):
                from utils.known_links import known_links, stored_links
                known, stored = known_links(), stored_links()
                fresh = []
                for posting in postings:
                    links = [f"https://nofluffjobs.com/pl/job/{s}" for s in self._variant_slugs(posting)]
                    seen = [link for link in links if link in known]
                    if seen:
                        self.seen_again_links.extend(seen)
                        continue
                    placeholder = next((link for link in links if link in stored), None)
                    if placeholder:
                        posting = dict(posting, url=placeholder.rsplit("/", 1)[-1])
                    fresh.append(posting)
                logger.info(
                    f"NFJ API: {len(fresh)} new offers, "
                    f"{len(self.seen_again_links)} already in the database (skipped)"
                )
                postings = fresh

            max_details = getattr(self, "max_details", self.DEFAULT_MAX_DETAILS)
            if len(postings) > max_details:
                logger.warning(
                    f"NoFluffJobs: {len(postings) - max_details} offers over the {max_details} "
                    f"detail limit saved without a description - the next run fetches them"
                )
            fetched = 0
            for i, posting in enumerate(postings):
                slug = posting.get("url", posting.get("slug", posting.get("id", "")))
                if not slug:
                    continue

                detail = {}
                if i < max_details:
                    detail = self._fetch_detail(session, slug)
                    fetched += bool(detail)
                    time.sleep(0.5)
                    if (i + 1) % 100 == 0:
                        logger.info(f"NoFluffJobs: {i + 1}/{min(len(postings), max_details)} descriptions")

                try:
                    job = self._parse_posting(posting, detail)
                    jobs.append(job)
                except Exception as e:
                    logger.warning(f"NFJ API: Failed to parse posting {slug}: {e}")

            logger.info(f"NoFluffJobs: fetched {fetched}/{min(len(postings), max_details)} descriptions")
        except Exception as e:
            logger.error(f"NFJ API: Scraping failed: {e}")
        finally:
            session.close()

        logger.info(f"NoFluffJobs: Total {len(jobs)} jobs scraped via API")
        return jobs
