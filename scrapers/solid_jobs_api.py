"""
SOLID.Jobs API Scraper
Fully public REST API - no authentication required.
API docs: https://solid.jobs (public-api section)
Rate limit: 300 requests/minute per IP
"""

import logging
import time
import sys
from pathlib import Path
from datetime import datetime
from typing import List

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.data_models import Job

logger = logging.getLogger(__name__)

DIVISIONS = ["it", "engineering", "marketing", "sales", "hr", "logistics", "finances", "other"]
BASE_URL = "https://solid.jobs/public-api/offers"
CAMPAIGN_ID = "job-scratcher"  # Required param (lowercase, letters/numbers/dashes)
# Mniejsze strony są stabilniejsze - przy pageSize=500 API bywa niekonsekwentne
PAGE_SIZE = 200


class SolidJobsAPIScraper:
    """Scraper using SOLID.Jobs fully public REST API (no auth needed)."""

    def __init__(self, config: dict):
        self.config = config
        cfg = config.get("solid_jobs", {}) or {}
        self.location_filter = (cfg.get("location") or config.get("location", "Warszawa")).lower()
        # API zwraca experienceLevel: Junior / Regular / Senior
        self.allowed_levels = {lv.lower() for lv in cfg.get("experience_levels", ["Junior"])}
        self.keep_unknown_level = cfg.get("keep_unknown_level", True)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "JobScratcher/1.0"
        })

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
        max_pages = 20  # Safety limit

        while page < max_pages:
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
                break  # All retries failed
            
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
        """Convert API offer dict to Job dataclass."""
        title = offer.get("title", offer.get("name", "Unknown"))
        
        # Company can be a string or dict depending on endpoint
        company_raw = offer.get("company", offer.get("companyName", "Unknown"))
        if isinstance(company_raw, dict):
            company = company_raw.get("name", company_raw.get("display_name", "Unknown"))
        else:
            company = str(company_raw) if company_raw else "Unknown"
        
        # Build link
        # SOLID.Jobs returns full URL with campaign tracking
        link = offer.get("url", "")
        if not link:
            slug = offer.get("slug", offer.get("jobOfferKey", ""))
            link = f"https://solid.jobs/offer/{slug}"
        
        # Description: combine what's available
        desc_parts = []
        if offer.get("description"):
            desc_parts.append(offer["description"])
        if offer.get("skills"):
            skills = offer["skills"]
            if isinstance(skills, list):
                skill_strs = []
                for s in skills:
                    if isinstance(s, dict):
                        name = s.get("name", "")
                        level = s.get("level", "")
                        skill_strs.append(f"{name} ({level})" if level else name)
                    else:
                        skill_strs.append(str(s))
                if skill_strs:
                    desc_parts.append("Umiejętności: " + ", ".join(skill_strs))
        if offer.get("technologies"):
            techs = offer["technologies"]
            if isinstance(techs, list):
                desc_parts.append("Technologie: " + ", ".join(t if isinstance(t, str) else t.get("name", "") for t in techs))
        
        # UWAGA: opis jest sklejany dopiero na końcu. Wcześniej robiono to tutaj,
        # a benefity i tryb pracy dopisywano do desc_parts JUŻ PO sklejeniu -
        # przez co nigdy nie trafiały do opisu oferty.

        # Poziom doświadczenia - istotny sygnał dla oceny dopasowania
        if offer.get("experienceLevel"):
            desc_parts.append(f"Poziom: {offer['experienceLevel']}")

        # Location
        locations = offer.get("locations", [])
        if isinstance(locations, list) and locations:
            location = locations[0]
        else:
            location = offer.get("city", offer.get("location", ""))
            if isinstance(location, dict):
                location = location.get("name", location.get("city", ""))
        
        # Remote/Hybrid info
        if offer.get("isRemote"):
            desc_parts.append("Tryb pracy: Zdalnie")
        elif offer.get("isHybrid"):
            desc_parts.append("Tryb pracy: Hybrydowo")

        # Benefits
        benefits = offer.get("benefits", [])
        if benefits and isinstance(benefits, list):
            desc_parts.append("Benefity: " + ", ".join(str(b) for b in benefits))

        # Salary info from structured salary object
        salary = offer.get("salary", {})
        salary_parts = []
        if isinstance(salary, dict):
            if salary.get("from"):
                salary_parts.append(f"od {salary['from']:.0f}")
            if salary.get("to"):
                salary_parts.append(f"do {salary['to']:.0f}")
            if salary.get("currency"):
                salary_parts.append(salary["currency"])
            if salary.get("period"):
                salary_parts.append(f"/{salary['period']}")
            if salary.get("employmentType"):
                salary_parts.append(f"({salary['employmentType']})")
        elif offer.get("salaryFrom"):
            salary_parts.append(f"od {offer['salaryFrom']}")
        if offer.get("salaryTo"):
            salary_parts.append(f"do {offer['salaryTo']}")
        if offer.get("salaryCurrency"):
            salary_parts.append(offer["salaryCurrency"])
        if salary_parts:
            desc_parts.append(f"Wynagrodzenie: {' '.join(salary_parts)}")

        # Sklejenie DOPIERO teraz, gdy wszystkie fragmenty są zebrane
        description = "\n".join(p for p in desc_parts if p) or f"Oferta z SOLID.Jobs: {title}"

        return Job(
            title=title,
            company=company,
            link=link,
            description=description,
            source=self.get_source_name(),
            location=str(location) if location else None,
            scraped_at=datetime.now().isoformat()
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

        for division in DIVISIONS:
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
            time.sleep(0.3)  # Be polite even with 300/min limit

        logger.info(
            f"SOLID.Jobs: fetched {fetched} offers, dropped {skipped} "
            f"(level/location) -> {len(all_jobs)} unique"
        )
        return all_jobs
