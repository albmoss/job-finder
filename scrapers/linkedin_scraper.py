"""
LinkedIn Scraper
"""

import logging
from typing import List
from scrapers.base_scraper import BaseScraper
from utils.data_models import Job

logger = logging.getLogger(__name__)


class LinkedInScraper(BaseScraper):
    """Scraper for LinkedIn Jobs (public listings, no auth)"""
    
    BASE_URL = "https://www.linkedin.com"
    
    def get_source_name(self) -> str:
        return "LinkedIn"
    
    def build_search_url(self) -> str:
        """Build search URL with entry-level filters"""
        from config import SCRAPER_CONFIG
        
        # LinkedIn experience level filters
        experience_levels = SCRAPER_CONFIG["linkedin"]["experience_levels"]
        job_types = SCRAPER_CONFIG["linkedin"]["job_types"]
        
        # f_E=1,2 (Internship + Entry level)
        # f_JT=F,P (Full-time + Part-time)
        # f_TPR=r2592000 (Past month)
        exp_param = ",".join(experience_levels)
        jt_param = ",".join(job_types)
        
        # Make location dynamic and URL-encoded
        location = SCRAPER_CONFIG.get("location", "Warszawa")
        import urllib.parse
        loc_param = urllib.parse.quote(location)
        
        url = (f"{self.BASE_URL}/jobs/search?keywords=&location={loc_param}"
               f"&f_E={exp_param}&f_JT={jt_param}&f_TPR=r2592000")
        
        logger.info(f"LinkedIn using experience levels: {exp_param}, job types: {jt_param}, location: {location}")
        return url
    
    def scrape_jobs(self) -> List[Job]:
        """Scrape jobs from LinkedIn"""
        jobs = []
        
        search_url = self.build_search_url()
        
        if not self.navigate_with_retry(search_url):
            logger.error(f"{self.get_source_name()}: Failed to navigate to search page")
            return jobs
        
        # Wait for job listings
        if not self.wait_for_element('.jobs-search__results-list, .job-search-card', timeout=15000):
            logger.warning(f"{self.get_source_name()}: Job listings not found")
            return jobs
        
        # Scroll to load more jobs
        self.scroll_to_load_jobs()
        
        # Scrape current page
        page_jobs = self.scrape_current_page()
        jobs.extend(page_jobs)
        
        return jobs
    
    def scroll_to_load_jobs(self):
        """Scroll page to trigger lazy loading"""
        try:
            for _ in range(3):  # Scroll 3 times
                self.page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                self.page.wait_for_timeout(2000)
        except Exception as e:
            logger.debug(f"{self.get_source_name()}: Scrolling failed: {e}")
    
    def scrape_current_page(self) -> List[Job]:
        """Scrape all jobs from current page"""
        jobs = []
        
        # Get all job cards
        job_cards = self.page.query_selector_all('.job-search-card, .base-card')
        
        logger.info(f"{self.get_source_name()}: Found {len(job_cards)} job cards")
        
        for card in job_cards:
            try:
                # Extract info
                title_elem = card.query_selector('.base-search-card__title, h3')
                company_elem = card.query_selector('.base-search-card__subtitle, h4')
                link_elem = card.query_selector('a.base-card__full-link')
                location_elem = card.query_selector('.job-search-card__location')
                
                if not (title_elem and link_elem):
                    continue
                
                title = title_elem.inner_text().strip()
                company = company_elem.inner_text().strip() if company_elem else "Unknown"
                link = link_elem.get_attribute('href')
                location = location_elem.inner_text().strip() if location_elem else "Warsaw"
                
                # LinkedIn links are already absolute
                if not link:
                    continue
                
                # Get description
                # FAST MODE: User requested to skip detailed description fetching due to slowness
                # description = self.get_job_description(link) 
                description = f"Pełny opis oferty dostępny pod adresem: {link}"
                
                job = Job(
                    title=title,
                    company=company,
                    link=link,
                    description=description,
                    source=self.get_source_name(),
                    location=location
                )
                
                jobs.append(job)
                logger.debug(f"{self.get_source_name()}: Scraped: {title} at {company}")
                
            except Exception as e:
                logger.warning(f"{self.get_source_name()}: Failed to scrape job card: {e}")
                continue
        
        return jobs
    
    def get_job_description(self, job_url: str) -> str:
        """Visit job page and extract description"""
        try:
            job_page = self.context.new_page()
            job_page.goto(job_url, wait_until='domcontentloaded', timeout=15000)
            job_page.wait_for_timeout(2000)
            
            # Click "Show more" if present
            try:
                show_more = job_page.query_selector('button.show-more-less-html__button')
                if show_more:
                    show_more.click()
                    job_page.wait_for_timeout(500)
            except Exception:
                pass
            
            # Extract description
            description_elem = job_page.query_selector('.show-more-less-html__markup, .description__text')
            
            description = description_elem.inner_text().strip() if description_elem else "Brak opisu"
            
            job_page.close()
            return description
            
        except Exception as e:
            logger.warning(f"{self.get_source_name()}: Failed to get description: {e}")
            return "Brak opisu"
