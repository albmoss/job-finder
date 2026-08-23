"""
NoFluffJobs Scraper (Internal API Strategy)
Uses NoFluffJobs' internal search API instead of Playwright for speed and reliability.

NOTE: This uses an undocumented internal API (visible in browser DevTools).
It may change without notice. If it breaks, revert to the Playwright version
in _archive/ or check DevTools for updated endpoints.
"""

import logging
import time
import requests
from typing import List
from datetime import datetime

from scrapers.base_scraper import BaseScraper
from utils.data_models import Job

logger = logging.getLogger(__name__)

SEARCH_URL = "https://nofluffjobs.com/api/search/posting?salaryCurrency=PLN&salaryPeriod=month&region=pl"
DETAIL_URL = "https://nofluffjobs.com/api/posting"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Referer": "https://nofluffjobs.com/pl",
    "Origin": "https://nofluffjobs.com",
}


class NoFluffScraper(BaseScraper):
    """Scraper NoFluffJobs na wewnętrznym API - bez przeglądarki."""

    def get_source_name(self) -> str:
        return "NoFluffJobs"

    def scrape_jobs(self) -> List[Job]:
        """Not used — run() is overridden to use API instead of browser."""
        return []

    def _search(self, session: requests.Session, criteria: dict, label: str) -> list:
        """Pobierz wszystkie strony wyników dla jednego zestawu kryteriów."""
        all_postings = []
        page = 1
        page_size = 50

        while True:
            payload = {"criteriaSearch": criteria, "pageFrom": page, "pageSize": page_size}

            try:
                resp = session.post(SEARCH_URL, json=payload, headers=HEADERS, timeout=30)
                if resp.status_code == 403:
                    logger.warning("NFJ API: 403 Forbidden — API may have changed or is blocking")
                    break
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                logger.error(f"NFJ API: Search request failed ({label}): {e}")
                break

            postings = data.get("postings", data.get("items", []))
            if not postings:
                break

            all_postings.extend(postings)
            total = data.get("totalCount", data.get("total", 0))
            logger.info(f"NFJ API [{label}]: page {page} - {len(postings)} offers (total: {total})")

            if len(all_postings) >= total or len(postings) < page_size:
                break

            page += 1
            time.sleep(0.5)

        return all_postings

    @staticmethod
    def _in_scope(posting: dict, target_city: str) -> bool:
        """Oferta z docelowego miasta albo w pełni zdalna."""
        if posting.get("fullyRemote"):
            return True

        target = target_city.lower()
        location = posting.get("location") or {}
        for place in (location.get("places") or []):
            if isinstance(place, dict):
                city = str(place.get("city", "")).lower()
                if target in city or "remote" in city or place.get("remote"):
                    return True
        return False

    def _fetch_listings(self, session: requests.Session) -> list:
        """
        Pobierz wszystkie oferty junior/trainee w Polsce i przefiltruj lokalnie.

        Serwerowy filtr zdalnych jest niewiarygodny: `fullyRemote: true` zwraca
        dokładnie tyle samo wyników co brak filtra (335), czyli jest ignorowany.
        Filtr `city` z kolei ograniczał wynik do ~38 ofert i wycinał oferty zdalne,
        które są niezależne od lokalizacji i mieszczą się w preferencjach kandydata.

        Dlatego bierzemy pełną listę dla kraju i decydujemy po stronie klienta.
        """
        target_city = self.config.get("location", "Warszawa")
        seniority = ["trainee", "junior"]

        postings = self._search(
            session,
            {"seniority": seniority, "country": ["poland"]},
            "PL junior/trainee",
        )

        unique = {}
        for posting in postings:
            key = posting.get("url") or posting.get("id")
            if key and key not in unique and self._in_scope(posting, target_city):
                unique[key] = posting

        logger.info(
            f"NFJ API: {len(postings)} offers in PL -> {len(unique)} matching "
            f"({target_city} lub zdalne)"
        )
        return list(unique.values())

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
        """Convert API posting + detail to Job dataclass."""
        # W API NoFluffJobs `title` to stanowisko, a `name` to NAZWA FIRMY.
        # Poprzednia wersja szukała firmy w posting["company"]["name"], którego
        # nie ma - stąd wszystkie oferty miały company="Unknown".
        title = posting.get("title") or "Unknown"
        company = (
            posting.get("name")
            or (posting.get("company") or {}).get("name")
            or posting.get("companyName")
            or "Unknown"
        )
        slug = posting.get("url", posting.get("slug", posting.get("id", "")))
        link = f"https://nofluffjobs.com/pl/job/{slug}"

        # Opis budujemy ze szczegółów oferty, jeśli są
        desc_parts = []

        if detail:
            # Właściwa treść ogłoszenia. UWAGA: nie ma jej pod detail["body"] ani
            # detail["description"] (tak szukała poprzednia wersja i dlatego opisy
            # ograniczały się do listy technologii). Realne pola to:
            #   details.description       - opis stanowiska
            #   requirements.description  - wymagania opisowe
            #   specs.dailyTasks          - lista codziennych zadań
            main_desc = (detail.get("details") or {}).get("description")
            if isinstance(main_desc, str) and main_desc.strip():
                desc_parts.append(main_desc.strip())

            daily = (detail.get("specs") or {}).get("dailyTasks") or []
            tasks = [str(t).strip() for t in daily if str(t).strip()]
            if tasks:
                desc_parts.append("Zadania:\n" + "\n".join(f"- {t}" for t in tasks))

            requirements = detail.get("requirements") or {}

            req_desc = requirements.get("description")
            if isinstance(req_desc, str) and req_desc.strip():
                desc_parts.append("Wymagania:\n" + req_desc.strip())

            musts = requirements.get("musts", [])
            if musts:
                must_names = [m.get("value", m.get("name", "")) for m in musts if isinstance(m, dict)]
                if must_names:
                    desc_parts.append("Wymagane: " + ", ".join(must_names))

            nices = requirements.get("nices", [])
            if nices:
                nice_names = [n.get("value", n.get("name", "")) for n in nices if isinstance(n, dict)]
                if nice_names:
                    desc_parts.append("Mile widziane: " + ", ".join(nice_names))

            # Świadomie POMIJAMY detail["consents"] - to klauzule RODO
            # (potrafią mieć 7000 znaków) i sam koszt tokenów bez wartości.

        # Zapas: tagi technologii z listy wyników
        if not desc_parts:
            tiles = posting.get("tiles", {})
            technologies = tiles.get("technologies", [])
            if technologies:
                tech_names = [t.get("value", str(t)) if isinstance(t, dict) else str(t) for t in technologies]
                desc_parts.append("Technologie: " + ", ".join(tech_names))
            desc_parts.append(f"Oferta z NoFluffJobs: {title}")

        salary = posting.get("salary", {})
        if salary:
            sal_from = salary.get("from", salary.get("min", ""))
            sal_to = salary.get("to", salary.get("max", ""))
            currency = salary.get("currency", "PLN")
            sal_type = salary.get("type", "")
            if sal_from or sal_to:
                sal_str = f"Wynagrodzenie: {sal_from}-{sal_to} {currency}"
                if sal_type:
                    sal_str += f" ({sal_type})"
                desc_parts.append(sal_str)

        description = "\n".join(desc_parts)

        location_data = posting.get("location", {})
        if isinstance(location_data, dict):
            places = location_data.get("places", [])
            if places:
                location = places[0].get("city", "Warszawa")
            else:
                location = location_data.get("city", "Warszawa")
        else:
            location = "Warszawa"

        seniority = posting.get("seniority", [])
        if seniority and isinstance(seniority, list):
            description += f"\nPoziom: {', '.join(seniority)}"

        return Job(
            title=title,
            company=company,
            link=link,
            description=description,
            source=self.get_source_name(),
            location=location,
            scraped_at=datetime.now().isoformat()
        )

    def run(self) -> List[Job]:
        """Override BaseScraper.run() to skip browser — uses HTTP API instead."""
        logger.info("NoFluffJobs: Starting API-based scrape (no browser)")
        jobs = []

        session = requests.Session()

        try:
            postings = self._fetch_listings(session)
            logger.info(f"NFJ API: Fetched {len(postings)} listing summaries")

            for i, posting in enumerate(postings):
                slug = posting.get("url", posting.get("slug", posting.get("id", "")))
                if not slug:
                    continue

                # Dociągamy szczegóły dla pełniejszych opisów - z ograniczeniem tempa
                detail = {}
                if i < 200:  # Limit detail fetches to avoid excessive requests
                    detail = self._fetch_detail(session, slug)
                    time.sleep(0.8)  # Limit: ok. 1 zapytanie na sekundę

                try:
                    job = self._parse_posting(posting, detail)
                    jobs.append(job)
                except Exception as e:
                    logger.warning(f"NFJ API: Failed to parse posting {slug}: {e}")

        except Exception as e:
            logger.error(f"NFJ API: Scraping failed: {e}")
        finally:
            session.close()

        logger.info(f"NoFluffJobs: Total {len(jobs)} jobs scraped via API")
        return jobs
