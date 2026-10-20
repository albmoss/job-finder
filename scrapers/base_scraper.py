"""
Klasa bazowa scraperów przeglądarkowych - wspólna obsługa Playwrighta.
"""

import logging
import random
from abc import ABC, abstractmethod
from typing import List, Optional
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

from utils.data_models import Job

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Wspólna baza dla scraperów korzystających z przeglądarki."""
    
    def __init__(self, config: dict):
        self.config = config
        self.headless = config.get("headless", True)
        self.timeout = config.get("timeout_ms", 30000)
        self.max_retries = config.get("max_retries", 3)
        self.delay = config.get("delay_between_requests", 2.0)
        self.user_agents = config.get("user_agents", [])
        
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    @abstractmethod
    def get_source_name(self) -> str:
        pass
    
    @abstractmethod
    def scrape_jobs(self) -> List[Job]:
        pass
    
    def setup_browser(self):
        try:
            self.playwright = sync_playwright().start()
            
            # Losowy user-agent - utrudnia wykrycie bota
            user_agent = random.choice(self.user_agents) if self.user_agents else None
            
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
            }
            if user_agent:
                context_options['user_agent'] = user_agent
            
            self.context = self.browser.new_context(**context_options)
            
            # Skrypty maskujące automatyzację
            self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            self.page = self.context.new_page()
            self.page.set_default_timeout(self.timeout)
            
            logger.info(f"{self.get_source_name()}: Browser setup complete")
            
        except Exception as e:
            logger.error(f"{self.get_source_name()}: Browser setup failed: {e}")
            raise
    
    def close_browser(self):
        for nazwa in ("page", "context", "browser"):
            obiekt = getattr(self, nazwa, None)
            if not obiekt:
                continue
            try:
                obiekt.close()
            except Exception as e:
                logger.warning(f"{self.get_source_name()}: closing {nazwa} failed: {e}")
        if getattr(self, "playwright", None):
            try:
                self.playwright.stop()
            except Exception as e:
                logger.error(f"{self.get_source_name()}: Error closing browser: {e}")
        logger.info(f"{self.get_source_name()}: Browser closed")
    
    def run(self) -> List[Job]:
        jobs = []
        self.zebrane = []
        try:
            logger.info(f"{self.get_source_name()}: Starting scrape")
            self.setup_browser()
            jobs = self.scrape_jobs()
            logger.info(f"{self.get_source_name()}: Scraped {len(jobs)} jobs")
        except Exception as e:
            logger.error(f"{self.get_source_name()}: Scraping failed: {e}")
            jobs = list(self.zebrane)
            if jobs:
                logger.warning(f"{self.get_source_name()}: keeping {len(jobs)} offers collected before the failure")
        finally:
            self.close_browser()
        
        return jobs
