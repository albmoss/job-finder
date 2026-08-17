"""
Careerjet API Scraper
Free Publisher REST API for job listings.
Docs: https://www.careerjet.com/partners/api/
Registration: Free Publisher account at https://www.careerjet.com/partners/api/
"""

import logging
import time
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List
from base64 import b64encode

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.data_models import Job

logger = logging.getLogger(__name__)

BASE_URL = "https://search.api.careerjet.net/v4/query"


class CareerjetAPIScraper:
    """Scraper using Careerjet Publisher API."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = config.get("careerjet_api_key", "") or os.getenv("CAREERJET_API_KEY", "")
        self.location = config.get("location", "Warszawa")
        self.session = requests.Session()
        
        # Basic Auth: api_key as username, empty password
        if self.api_key:
            auth_str = b64encode(f"{self.api_key}:".encode()).decode()
            self.session.headers.update({
                "Authorization": f"Basic {auth_str}",
            })
        
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "JobScratcher/1.0"
        })

    def get_source_name(self) -> str:
        return "Careerjet"

    def _fetch_page(self, keywords: str, page: int, retries: int = 3) -> dict:
        """Fetch a single page of results."""
        params = {
            "keywords": keywords,
            "location": self.location,
            "locale_code": "pl_PL",
            "user_ip": "127.0.0.1",
            "user_agent": "JobScratcher/1.0",
            "pagesize": 99,
            "page": page,
            "sort": "date",
        }

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(BASE_URL, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = 2 ** attempt * 3
                    logger.warning(f"Careerjet: Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.warning(f"Careerjet page {page}: attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
        return {}

    def _parse_job(self, item: dict) -> Job:
        """Convert Careerjet result to Job dataclass."""
        title = item.get("title", "Unknown")
        company = item.get("company", "Unknown")
        link = item.get("url", "")
        description = item.get("description", item.get("snippet", ""))
        location = item.get("locations", item.get("location", ""))
        posted_date = item.get("date", "")
        
        # Salary
        salary = item.get("salary", "")
        if salary:
            description += f"\nWynagrodzenie: {salary}"

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
        """Fetch job listings from Careerjet."""
        if not self.api_key:
            logger.warning("Careerjet: No API key configured (CAREERJET_API_KEY). "
                         "Register for free at https://www.careerjet.com/partners/api/")
            return []

        logger.info("Careerjet: Starting API scrape")
        all_jobs = []
        seen_links = set()
        
        keyword_sets = [
            "junior",
            "praktykant stażysta",
            "asystent bez doświadczenia",
        ]
        
        for keywords in keyword_sets:
            page = 1
            max_pages = 10
            
            while page <= max_pages:
                data = self._fetch_page(keywords, page)
                jobs_data = data.get("jobs", data.get("results", []))
                
                if not jobs_data:
                    break
                
                for item in jobs_data:
                    try:
                        job = self._parse_job(item)
                        if job.link and job.link not in seen_links:
                            seen_links.add(job.link)
                            all_jobs.append(job)
                    except Exception as e:
                        logger.warning(f"Careerjet: Failed to parse job: {e}")
                
                total = data.get("hits", data.get("total", 0))
                logger.info(f"Careerjet: '{keywords}' page {page} - {len(jobs_data)} results (total: {total})")
                
                if len(jobs_data) < 99:
                    break
                
                page += 1
                time.sleep(2.0)  # Respect rate limits
        
        logger.info(f"Careerjet: Total {len(all_jobs)} unique jobs fetched")
        return all_jobs
