"""
Scraper SOLID.Jobs - w pełni publiczne REST API, bez uwierzytelniania.

Dokumentacja: https://solid.jobs (sekcja public-api).
Limit: 300 zapytań na minutę na adres IP.
"""

import logging
import time
import sys
from pathlib import Path
from datetime import datetime
from typing import List

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils import stop
from utils.candidate_scope import scope_city, scope_levels
from utils.portal_categories import pick_categories
from utils.data_models import Job
from utils.links import logo_url
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

DIVISIONS = ["it", "engineering", "marketing", "sales", "hr", "logistics", "finances", "other"]

# Katalog działów / kategorii portalu - źródło: https://solid.jobs (sprawdzone 2026-09-30)
SOLID_DIVISIONS_CATALOG = {
    "engineering": "Inżynieria",
    "finances": "Finanse",
    "hr": "HR",
    "it": "IT",
    "logistics": "Logistyka",
    "marketing": "Marketing",
    "other": "Pozostałe",
    "sales": "Sprzedaż",
}
BASE_URL = "https://solid.jobs/public-api/offers"
CAMPAIGN_ID = "job-scratcher"  # Parametr wymagany: małe litery, cyfry, myślniki
# Mniejsze strony są stabilniejsze - przy pageSize=500 API bywa niekonsekwentne
PAGE_SIZE = 200



# SOLID.Jobs akceptuje / zwraca: Intern, Junior, Regular, Senior
SOLID_LEVEL_MAP = {
    "intern": "Intern",
    "junior": "Junior",
    "mid": "Regular",
    "senior": "Senior",
    "lead": "Senior",
    "manager": "Senior",
}
class SolidJobsAPIScraper:
    """Scraper na publicznym REST API SOLID.Jobs - klucz niepotrzebny."""

    def __init__(self, config: dict):
        self.config = config
        cfg = config.get("solid_jobs", {}) or {}
        default_city = cfg.get("location") or config.get("location", "Warszawa")
        self.location_filter = scope_city(default_city).lower()
        raw_levels = scope_levels()
        self.allowed_levels = {
            SOLID_LEVEL_MAP[lv].lower() for lv in raw_levels if lv in SOLID_LEVEL_MAP
        } or {"junior", "intern"}
        self.keep_unknown_level = cfg.get("keep_unknown_level", True)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "JobScratcher/1.0"
        })

    def _fetch_category_catalog(self) -> dict[str, str]:
        """Pobierz katalog działów/kategorii z portalu w czasie działania lub użyj słownika zapasowego."""
        try:
            resp = self.session.get("https://solid.jobs", timeout=10)
            if resp.status_code == 200:
                import re
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                cats = {}
                for a in soup.find_all("a", href=True):
                    m = re.match(r"^/offers/([a-zA-Z0-9_-]+)$", a["href"])
                    if m:
                        slug = m.group(1)
                        raw = a.get_text(strip=True)
                        clean = re.sub(r"[\d\s\xa0]+$", "", raw)
                        if clean and not clean.startswith("Przeglądaj") and slug in DIVISIONS:
                            cats[slug] = clean
                if cats:
                    return cats
        except Exception as e:
            logger.debug(f"SOLID.Jobs: nie udało się pobrać działów z HTML ({e}), używam słownika zapasowego")
        return dict(SOLID_DIVISIONS_CATALOG)

    def get_source_name(self) -> str:
        return "SOLID.Jobs"

    def _is_relevant(self, offer: dict) -> bool:
        """
        Odsiej oferty spoza zasięgu kandydata.

        Wcześniej `location_filter` był ustawiany, ale NIGDY nieużywany, a poziom
        doświadczenia nie był sprawdzany w ogóle. Skutek: do bazy trafiało ~750 ofert
        z całej Polski, z czego 31% wprost seniorskich, a tylko ~2% juniorskich.
        Przy medianie opisu ~3800 znaków to był największy pożeracz tokenów w systemie.
        """
        level = (offer.get("experienceLevel") or "").strip().lower()
        if level:
            if level not in self.allowed_levels:
                return False
        elif not self.keep_unknown_level:
            return False

        # Praca zdalna jest niezależna od miasta i mieści się w preferencjach
        if offer.get("isRemote"):
            return True

        locations = offer.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        for loc in locations:
            if self.location_filter in str(loc).lower():
                return True

        return False

    def _fetch_division(self, division: str, retries: int = 3) -> list:
        """
        Pobierz wszystkie oferty z jednego działu.

        UWAGA: pageIndex jest 0-INDEKSOWANY. Poprzednia wersja zaczynała od 1
        i przez to gubiła pierwszą stronę każdego działu. Przy pageSize=500
        oznaczało to, że działy mniejsze niż 500 ofert (engineering, marketing,
        sales, hr, logistics, finances, other) zwracały ZERO wyników - do bazy
        trafiały wyłącznie oferty IT. Sprawdzone: dla działu hr (totalCount=85)
        pageIndex=0 daje 50 ofert, pageIndex=1 daje 35, razem dokładnie 85.
        """
        all_offers = []
        page = 0
        max_pages = 20  # Bezpiecznik - twardy limit stron

        while page < max_pages and not stop.requested():
            url = f"{BASE_URL}/{division}?campaign={CAMPAIGN_ID}&pageIndex={page}&pageSize={PAGE_SIZE}"
            data = None
            
            for attempt in range(1, retries + 1):
                try:
                    resp = self.session.get(url, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except requests.RequestException as e:
                    logger.warning(f"SOLID.Jobs [{division}]: attempt {attempt}/{retries} failed: {e}")
                    if attempt < retries:
                        time.sleep(2 ** attempt)
            
            if data is None:
                break  # Wszystkie próby nieudane
            
            offers = data.get("jobs", [])
            total_count = data.get("totalCount", 0)

            all_offers.extend(offers)
            logger.info(
                f"SOLID.Jobs [{division}]: page {page} - {len(offers)} offers "
                f"({len(all_offers)}/{total_count})"
            )

            # Pusta strona = koniec danych. Nie ufamy totalPages - API podaje je
            # niespójnie z faktyczną liczbą dostępnych rekordów.
            if not offers or len(all_offers) >= total_count:
                break

            page += 1
            time.sleep(0.5)

        return all_offers

    def _parse_offer(self, offer: dict) -> Job:
        """Convert API offer dict to Job dataclass with structured fields."""
        title = offer.get("title", offer.get("name", "Unknown"))
        
        company_raw = offer.get("company", offer.get("companyName", "Unknown"))
        if isinstance(company_raw, dict):
            company = company_raw.get("name", company_raw.get("display_name", "Unknown"))
        else:
            company = str(company_raw) if company_raw else "Unknown"
        
        link = offer.get("url", "")
        if not link:
            slug = offer.get("slug", offer.get("jobOfferKey", ""))
            link = f"https://solid.jobs/offer/{slug}"
        
        # Opis z portalu bez doklejanych metadanych
        desc = (offer.get("description") or "").strip()
        description = desc if desc else f"Oferta z SOLID.Jobs: {title}"

        # Poziom doświadczenia
        seniority = norm_seniority(offer.get("experienceLevel"))

        # Tryb pracy
        modes = []
        if offer.get("isRemote"):
            modes.append("remote")
        if offer.get("isHybrid"):
            modes.append("hybrid")
        if not modes and offer.get("locations"):
            modes.append("onsite")
        work_modes = norm_work_modes(modes)

        # Wymiar czasu pracy
        schedules = norm_schedules(offer.get("contractTime"))

        # Umiejętności: rozdzielenie wymagane / mile widziane wg level != 'NiceToHave'
        skills = offer.get("skills") or []
        req_skills = []
        nice_skills = []
        if isinstance(skills, list):
            for s in skills:
                if isinstance(s, dict):
                    if s.get("level") == "NiceToHave":
                        nice_skills.append(s.get("name", ""))
                    else:
                        req_skills.append(s.get("name", ""))
                elif isinstance(s, str):
                    req_skills.append(s)
        skills_required = norm_skills(req_skills) or norm_skills(offer.get("technologies"))
        skills_nice = norm_skills(nice_skills)

        # Języki
        langs = []
        for l in offer.get("languages") or []:
            if isinstance(l, dict):
                name = l.get("name")
                lvl = l.get("level")
                parsed = make_language(name, level=lvl, required=True)
                if parsed:
                    langs.append(parsed)
        languages = langs or None

        # Wynagrodzenie i typ umowy
        sal = offer.get("salary")
        salary = None
        contract_types = None
        if isinstance(sal, dict):
            low = sal.get("from")
            high = sal.get("to")
            currency = sal.get("currency", "PLN")
            period = sal.get("period")
            emp_type = sal.get("employmentType")
            contract = norm_contracts(emp_type)
            contract_code = contract[0] if contract else None
            gross = False if contract_code == "b2b" else (True if contract_code == "uop" else None)
            salary = make_salary(low, high, currency=currency, period=period, gross=gross, contract=contract_code)
            contract_types = contract
        elif offer.get("salaryFrom") or offer.get("salaryTo"):
            salary = make_salary(
                min_value=offer.get("salaryFrom"),
                max_value=offer.get("salaryTo"),
                currency=offer.get("salaryCurrency", "PLN"),
            )

        # Daty
        posted_date = offer.get("validFrom") or offer.get("updatedAt") or None
        valid_through = offer.get("validTo") or None

        # Kategoria
        category = offer.get("category") or offer.get("division") or None

        # Lokalizacja
        locations = offer.get("locations", [])
        if isinstance(locations, list) and locations:
            location = str(locations[0])
        else:
            loc = offer.get("city", offer.get("location", ""))
            if isinstance(loc, dict):
                location = loc.get("name", loc.get("city", ""))
            else:
                location = str(loc) if loc else None
        location = location or scope_city(self.config.get("location", "Warszawa"))

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
            schedules=schedules,
            salary=salary,
            skills_required=skills_required,
            skills_nice=skills_nice,
            languages=languages,
            category=category,
            logo_url=logo_url(offer.get("companyLogoUrl")),
        )
    def run(self) -> List[Job]:
        """Pobierz oferty ze wszystkich działów i odfiltruj wg lokalizacji i poziomu."""
        logger.info(
            f"SOLID.Jobs: start (lokalizacja: {self.location_filter}, "
            f"poziomy: {', '.join(sorted(self.allowed_levels))})"
        )
        all_jobs = []
        seen_links = set()
        fetched = 0
        skipped = 0

        catalog = self._fetch_category_catalog()
        picked_divisions = pick_categories("solid_jobs", catalog) if catalog else None
        if picked_divisions is not None:
            logger.info(f"SOLID.Jobs: {len(picked_divisions)} kategorii z CV")
            divisions = [d for d in picked_divisions if d in DIVISIONS]
        else:
            divisions = DIVISIONS

        for division in divisions:
            offers = self._fetch_division(division)
            fetched += len(offers)
            for offer in offers:
                if not self._is_relevant(offer):
                    skipped += 1
                    continue
                try:
                    job = self._parse_offer(offer)
                    if job.link not in seen_links:
                        seen_links.add(job.link)
                        all_jobs.append(job)
                except Exception as e:
                    logger.warning(f"SOLID.Jobs: Failed to parse offer: {e}")
            time.sleep(0.3)  # Nie dobijamy serwera, mimo limitu 300 zapytań na minutę

        logger.info(
            f"SOLID.Jobs: fetched {fetched} offers, dropped {skipped} "
            f"(level/location) -> {len(all_jobs)} unique"
        )
        return all_jobs
