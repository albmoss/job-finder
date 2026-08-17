"""
Adzuna API Scraper
Official REST API with free tier for Poland (country code: pl).
Docs: https://developer.adzuna.com/
Rate limit: 25 requests/minute (free tier)
Registration: Free at https://developer.adzuna.com/
"""

import logging
import time
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.data_models import Job

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/pl/search"
SEARCH_KEYWORDS = "junior OR praktykant OR stażysta OR asystent OR trainee"


class AdzunaAPIScraper:
    """Scraper using Adzuna official REST API (free tier)."""

    def __init__(self, config: dict):
        self.config = config
        self.app_id = config.get("adzuna_app_id", "") or os.getenv("ADZUNA_APP_ID", "")
        self.app_key = config.get("adzuna_app_key", "") or os.getenv("ADZUNA_APP_KEY", "")
        self.location = config.get("location", "Warszawa")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "JobScratcher/1.0"
        })

    def get_source_name(self) -> str:
        return "Adzuna"

    def _fetch_page(self, page: int, retries: int = 3) -> dict:
        """Fetch a single page of results."""
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": SEARCH_KEYWORDS,
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
        
        # Description
        description = result.get("description", "")
        
        # Salary info
        salary_parts = []
        if result.get("salary_min"):
            salary_parts.append(f"od {result['salary_min']:.0f}")
        if result.get("salary_max"):
            salary_parts.append(f"do {result['salary_max']:.0f}")
        if salary_parts:
            description += f"\nWynagrodzenie: {' '.join(salary_parts)} PLN"
        
        # Category
        category = result.get("category", {}).get("label", "")
        if category:
            description += f"\nKategoria: {category}"
        
        # Location
        location_data = result.get("location", {})
        location = location_data.get("display_name", "")
        
        # Date
        posted_date = result.get("created", "")

        return Job(
            title=title,
            company=company,
            link=link,
            description=description,
            source=self.get_source_name(),
            location=location if location else None,
            posted_date=posted_date,
            scraped_at=datetime.now().isoformat()
        )

    def run(self) -> List[Job]:
        """Fetch all available job listings from Adzuna Poland."""
        if not self.app_id or not self.app_key:
            logger.warning("Adzuna: No API credentials configured (ADZUNA_APP_ID / ADZUNA_APP_KEY). "
                         "Register for free at https://developer.adzuna.com/")
            return []

        logger.info("Adzuna: Starting API scrape for Poland")
        all_jobs = []
        seen_links = set()
        page = 1
        max_pages = 20  # Safety limit

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
            time.sleep(2.5)  # Respect 25 req/min rate limit

        logger.info(f"Adzuna: Total {len(all_jobs)} unique jobs fetched")
        return all_jobs
