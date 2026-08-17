"""
Jooble API Scraper
Free aggregator REST API for job listings.
Docs: https://jooble.org/api/about
Registration: Free at https://jooble.org/api/about
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

BASE_URL = "https://jooble.org/api"


class JoobleAPIScraper:
    """Scraper using Jooble free aggregator API."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = config.get("jooble_api_key", "") or os.getenv("JOOBLE_API_KEY", "")
        self.location = config.get("location", "Warszawa")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "JobScratcher/1.0"
        })

    def get_source_name(self) -> str:
        return "Jooble"

    def _fetch_page(self, keywords: str, page: int, retries: int = 3) -> dict:
        """Fetch a single page of results via POST."""
        url = f"{BASE_URL}/{self.api_key}"
        payload = {
            "keywords": keywords,
            "location": self.location,
            "page": str(page),
        }

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=30)
                if resp.status_code == 429:
                    wait = 2 ** attempt * 3
                    logger.warning(f"Jooble: Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.warning(f"Jooble page {page} '{keywords}': attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
        return {}

    def _parse_job(self, item: dict) -> Job:
        """Convert Jooble API job to Job dataclass."""
        title = item.get("title", "Unknown")
        company = item.get("company", "Unknown")
        link = item.get("link", "")
        
        description = item.get("snippet", item.get("description", ""))
        
        # Salary
        salary = item.get("salary", "")
        if salary:
            description += f"\nWynagrodzenie: {salary}"
        
        # Type
        job_type = item.get("type", "")
        if job_type:
            description += f"\nTyp: {job_type}"
        
        location = item.get("location", "")
        posted_date = item.get("updated", item.get("created", ""))

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
        """Fetch job listings from Jooble aggregator."""
        if not self.api_key:
            logger.warning("Jooble: No API key configured (JOOBLE_API_KEY). "
                         "Register for free at https://jooble.org/api/about")
            return []

        logger.info("Jooble: Starting API scrape")
        all_jobs = []
        seen_links = set()
        
        # Search with multiple keyword sets to maximize coverage
        keyword_sets = [
            "junior praktykant stażysta",
            "asystent trainee bez doświadczenia",
        ]
        
        for keywords in keyword_sets:
            page = 1
            max_pages = 10  # Safety limit per keyword set
            
            while page <= max_pages:
                data = self._fetch_page(keywords, page)
                jobs_data = data.get("jobs", [])
                
                if not jobs_data:
                    logger.info(f"Jooble: No more results for '{keywords}' at page {page}")
                    break
                
                for item in jobs_data:
                    try:
                        job = self._parse_job(item)
                        if job.link and job.link not in seen_links:
                            seen_links.add(job.link)
                            all_jobs.append(job)
                    except Exception as e:
                        logger.warning(f"Jooble: Failed to parse job: {e}")
                
                total = data.get("totalCount", 0)
                logger.info(f"Jooble: '{keywords}' page {page} - {len(jobs_data)} results (total: {total})")
                
                if len(jobs_data) < 20:  # Jooble returns fewer items on last page
                    break
                
                page += 1
                time.sleep(1.5)  # Respect rate limits
        
        logger.info(f"Jooble: Total {len(all_jobs)} unique jobs fetched")
        return all_jobs
