"""
OPTIMIZED Pracuj.pl Scraper with Urllib + JSON parsing
- Bypass Playwright entirely
- Downloads AI summaries natively
- Multi-threaded pagination
"""

import logging
import json
import random
import threading
import time
import urllib.error
import urllib.request
from typing import List
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

from utils.data_models import Job

logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0'
]

class PracujOptimizedScraper:
    """Ultra-optimized scraper using urllib + JSON parsing (NO Playwright).
    Does NOT inherit BaseScraper since it doesn't need browser automation.
    Implements same interface: get_source_name() and run()."""
    
    BASE_URL = "https://www.pracuj.pl"
    
    def __init__(self, config: dict):
        self.config = config
        # 8 wątków bez opóźnień generowało ścianę HTTP 429 od ~75 strony i gubiło
        # ogon wyników. 3 wątki + throttling przechodzą całość bez blokad.
        self.parallel_threads = config.get("pracuj_pl", {}).get("parallel_threads", 3)
        self.min_request_interval = 0.7   # sekundy między żądaniami (globalnie)
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0

    def _throttle(self):
        """Globalny throttle - odstęp między żądaniami niezależnie od liczby wątków."""
        with self._rate_lock:
            now = time.monotonic()

            # Po serii 429 wszystkie wątki czekają na wspólny cooldown
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                time.sleep(wait)
                now = time.monotonic()

            delta = now - self._last_request_at
            if delta < self.min_request_interval:
                time.sleep(self.min_request_interval - delta)

            self._last_request_at = time.monotonic()

    def _trigger_cooldown(self, seconds: float):
        """Zatrzymaj wszystkie wątki po napotkaniu 429."""
        with self._rate_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def get_source_name(self) -> str:
       # Nazwa źródła trafia na kartę oferty, więc jest to nazwa PORTALU,
       # nie opis techniki pobierania. Wcześniejsze "Pracuj.pl (Optimized APIs)"
       # wyciekało do interfejsu. Zmiana wymagała migracji istniejących rekordów
       # (migrate_source_names.py) - inaczej w bazie żyłyby dwa osobne źródła
       # o tej samej treści.
       return "Pracuj.pl"
    
    def build_search_url(self, page: int = 1) -> str:
        """Build search URL"""
        import random
        from config import SCRAPER_CONFIG
        
        experience_levels = SCRAPER_CONFIG["pracuj_pl"]["experience_levels"]
        days = SCRAPER_CONFIG["pracuj_pl"]["days_param"]
        levels_param = ",".join(str(level) for level in experience_levels)
        
        url = f"{self.BASE_URL}/praca/warszawa;wp?et={levels_param}&itth={days}&pn={page}"
        return url
        
    def fetch_page_html(self, page_num: int) -> str:
        url = self.build_search_url(page_num)
        max_attempts = 4

        for attempt in range(max_attempts):
            self._throttle()

            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.8',
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    return response.read().decode('utf-8', errors='ignore')

            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Respektuj Retry-After jeśli serwer go poda, inaczej backoff wykładniczy
                    retry_after = e.headers.get('Retry-After') if e.headers else None
                    try:
                        wait = float(retry_after) if retry_after else None
                    except (TypeError, ValueError):
                        wait = None
                    if wait is None:
                        wait = (5 * (2 ** attempt)) + random.random() * 2

                    wait = min(wait, 120)
                    logger.warning(f"Page {page_num}: HTTP 429 - cooldown {wait:.0f}s (attempt {attempt+1}/{max_attempts})")
                    self._trigger_cooldown(wait)
                    continue

                logger.warning(f"Page {page_num} attempt {attempt+1}/{max_attempts} failed: HTTP {e.code}")

            except Exception as e:
                logger.warning(f"Page {page_num} attempt {attempt+1}/{max_attempts} failed: {e}")

            if attempt < max_attempts - 1:
                time.sleep((2 ** attempt) + random.random())

        logger.error(f"All {max_attempts} attempts failed for page {page_num}")
        return ""

    def fetch_job_listings_page(self, page_num: int) -> List[Job]:
        """Fetch job listings AND their AI summaries from one page synchronously"""
        jobs = []
        html = self.fetch_page_html(page_num)
        if not html:
            return []
            
        soup = BeautifulSoup(html, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        
        if not script or not script.string:
            logger.warning(f"No __NEXT_DATA__ found on page {page_num}")
            return []
            
        try:
            data = json.loads(script.string)
            
            found_offers = []
            def search_all_keys(d):
                if isinstance(d, dict):
                    if 'jobTitle' in d and 'companyName' in d:
                        found_offers.append(d)
                        return
                    for k, v in d.items():
                        if isinstance(v, (dict, list)):
                            search_all_keys(v)
                elif isinstance(d, list):
                    for item in d:
                        if isinstance(item, (dict, list)):
                            search_all_keys(item)
                            
            search_all_keys(data)
            
            seen_links = set()
            for offer in found_offers:
                title = offer.get('jobTitle', 'Unknown')
                company = offer.get('companyName', 'Unknown')
                # Search deeply for the uri inside this specific job offer structure
                def find_uri(d):
                    if isinstance(d, dict):
                        if 'offerAbsoluteUri' in d and d['offerAbsoluteUri']: return d['offerAbsoluteUri']
                        if 'offerUrl' in d and d['offerUrl']: return d['offerUrl']
                        for v in d.values():
                            res = find_uri(v)
                            if res: return res
                    elif isinstance(d, list):
                        for i in d:
                            res = find_uri(i)
                            if res: return res
                    return ''
                    
                link = find_uri(offer)
                
                # Fallback parsing
                if not link or link in seen_links: continue
                seen_links.add(link)
                
                if not link.startswith('http'):
                    link = self.BASE_URL + link
                    
                # Handle location
                locations = offer.get('displayWorkplaces', [])
                location = locations[0] if locations else "Warszawa"
                
                # THE SECRET SAUCE: Getting the description WITHOUT hitting the subpage
                description = "Brak opisu"
                if offer.get('aiSummary'):
                    # The aiSummary is usually HTML bullets
                    description = BeautifulSoup(offer['aiSummary'], "html.parser").get_text(separator="\n", strip=True)
                    
                job = Job(
                    title=title.strip(),
                    company=company.strip(),
                    link=link,
                    description=description,
                    source=self.get_source_name(),
                    location=location.strip()
                )
                jobs.append(job)
                
            logger.info(f"Page {page_num}: {len(jobs)} jobs found and processed instantly")
            
        except Exception as e:
            logger.error(f"Failed to parse __NEXT_DATA__ JSON on page {page_num}: {e}")
            
        return jobs
    
    def fetch_all_listings(self) -> List[Job]:
        """Fetch all job listings using ThreadPoolExecutor"""
        all_jobs = []
        
        # 1. Fetch first page to find pagination
        logger.info(f"Fetching page 1 to discover pagination...")
        first_page_jobs = self.fetch_job_listings_page(1)
        all_jobs.extend(first_page_jobs)
        
        if not first_page_jobs:
            logger.warning("No jobs found on first page")
            return []
            
        # Hard cap to 50 pages if we can't extract total_pages easily.
        # Next.js pagination parsing is fragile, but since HTTP requests are instant,
        # we can just try up to a reasonable cap and break on empty pages.
        total_pages = 100 
        
        logger.info(f"Scanning up to {total_pages} pages with {self.parallel_threads} threads...")
        
        consecutive_empty = 0
        current_page = 2
        
        with ThreadPoolExecutor(max_workers=self.parallel_threads) as executor:
            while current_page <= total_pages and consecutive_empty < 3:
                # Create batch
                batch_size = self.parallel_threads
                page_batch = list(range(current_page, min(current_page + batch_size, total_pages + 1)))
                
                # Execute batch
                results = list(executor.map(self.fetch_job_listings_page, page_batch))
                
                batch_found_jobs = False
                for result in results:
                    if result and len(result) > 0:
                        all_jobs.extend(result)
                        batch_found_jobs = True
                        consecutive_empty = 0
                
                if not batch_found_jobs:
                    consecutive_empty += 1
                    
                current_page += batch_size
        
        # Deduplicate final global list because Pracuj repeating offers sometimes happens on bounds
        unique_jobs = {job.link: job for job in all_jobs}.values()
        
        logger.info(f"Total Unique listings fetched: {len(unique_jobs)}")
        return list(unique_jobs)
    
    def run(self) -> List[Job]:
        """Sync entrypoint — no async needed for urllib-based scraper."""
        logger.info(f"{self.get_source_name()}: Starting optimized scrape")
        return self.fetch_all_listings()

