"""
Scraper Adzuny - oficjalne REST API, darmowy próg dla Polski (kod kraju: pl).

Dokumentacja i rejestracja: https://developer.adzuna.com/
Limit darmowego progu: 25 zapytań na minutę.
"""

import logging
import time
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List

import requests

from utils.candidate_scope import scope_city, scope_levels
from utils.data_models import Job
from utils.offer_fields import (
    make_salary,
    norm_contracts,
    norm_schedules,
    norm_seniority,
    norm_work_modes,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/pl/search"

class AdzunaAPIScraper:
    """Scraper na oficjalnym REST API Adzuny (darmowy próg)."""

    def __init__(self, config: dict):
        self.config = config
        self.app_id = config.get("adzuna_app_id", "") or os.getenv("ADZUNA_APP_ID", "")
        self.app_key = config.get("adzuna_app_key", "") or os.getenv("ADZUNA_APP_KEY", "")
        self.location = scope_city(default=config.get("location", "Warszawa"))
        self.keywords = self._keywords_from_levels(scope_levels())
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "JobScratcher/1.0"
        })

    @staticmethod
    def _keywords_from_levels(levels: list[str]) -> str:
        level_words = {
            "intern": ["praktykant", "stażysta", "trainee", "intern"],
            "junior": ["junior", "asystent", "młodszy"],
            "mid": ["specjalista", "mid", "regular"],
            "senior": ["senior", "starszy"],
            "lead": ["lead", "lider"],
            "manager": ["manager", "kierownik"],
        }
        words = []
        for lvl in levels:
            words.extend(level_words.get(lvl, []))
        return " OR ".join(dict.fromkeys(words)) if words else "junior OR praktykant OR stażysta OR asystent OR trainee"
    def get_source_name(self) -> str:
        return "Adzuna"

    def _fetch_page(self, page: int, retries: int = 3) -> dict:
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": self.keywords,
            "where": self.location,
            "results_per_page": 50,
            "content-type": "application/json",
        }
        url = f"{BASE_URL}/{page}"
        
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = 2 ** attempt * 3
                    logger.warning(f"Adzuna: Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.warning(f"Adzuna page {page}: attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
        return {}

    def _parse_result(self, result: dict) -> Job:
        """Convert Adzuna API result to Job dataclass."""
        title = result.get("title", "Unknown")
        company = result.get("company", {}).get("display_name", "Unknown")
        link = result.get("redirect_url", result.get("adref", ""))
        
        description = (result.get("description") or "").strip()

        salary_obj = None
        min_sal = result.get("salary_min")
        max_sal = result.get("salary_max")
        if min_sal or max_sal:
            salary_obj = make_salary(
                min_value=min_sal,
                max_value=max_sal,
                currency="PLN",
                period="year",
            )

        category = result.get("category", {}).get("label") or None
        location_data = result.get("location", {})
        location = location_data.get("display_name") or None
        posted_date = result.get("created", "")

        contract_types = norm_contracts(result.get("contract_type"))
        schedules = norm_schedules(result.get("contract_time"))
        work_modes = norm_work_modes(title) or norm_work_modes(location)
        seniority = norm_seniority(title)

        return Job(
            title=title,
            company=company,
            link=link,
            description=description,
            source=self.get_source_name(),
            location=location,
            posted_date=posted_date,
            salary=salary_obj,
            category=category,
            contract_types=contract_types,
            schedules=schedules,
            work_modes=work_modes,
            seniority=seniority,
            scraped_at=datetime.now().isoformat(),
        )

    def run(self) -> List[Job]:
        if not self.app_id or not self.app_key:
            logger.warning("Adzuna: No API credentials configured (ADZUNA_APP_ID / ADZUNA_APP_KEY). "
                         "Register for free at https://developer.adzuna.com/")
            return []

        logger.info("Adzuna: Starting API scrape for Poland")
        all_jobs = []
        seen_links = set()
        page = 1
        max_pages = 20  # Bezpiecznik - twardy limit stron

        while page <= max_pages:
            data = self._fetch_page(page)
            results = data.get("results", [])
            
            if not results:
                logger.info(f"Adzuna: No more results at page {page}")
                break

            for result in results:
                try:
                    job = self._parse_result(result)
                    if job.link and job.link not in seen_links:
                        seen_links.add(job.link)
                        all_jobs.append(job)
                except Exception as e:
                    logger.warning(f"Adzuna: Failed to parse result: {e}")

            total_count = data.get("count", 0)
            logger.info(f"Adzuna: Page {page} - {len(results)} results (total available: {total_count})")

            if len(all_jobs) >= total_count:
                break

            page += 1
            time.sleep(2.5)  # Limit portalu: 25 zapytań na minutę

        logger.info(f"Adzuna: Total {len(all_jobs)} unique jobs fetched")
        return all_jobs
