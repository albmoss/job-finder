"""
Klasa bazowa scraperów przeglądarkowych - wspólna obsługa Playwrighta.
"""

import logging
import random
import time
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
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if hasattr(self, 'playwright'):
                self.playwright.stop()
            
            logger.info(f"{self.get_source_name()}: Browser closed")
        except Exception as e:
            logger.error(f"{self.get_source_name()}: Error closing browser: {e}")
    
    def navigate_with_retry(self, url: str) -> bool:
        """
        Navigate to URL with retry logic
        
        Args:
            url: URL to navigate to
            
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"{self.get_source_name()}: Navigating to {url} (attempt {attempt}/{self.max_retries})")
                self.page.goto(url, wait_until='domcontentloaded')
                time.sleep(self.delay)
                return True
            except Exception as e:
                logger.warning(f"{self.get_source_name()}: Navigation attempt {attempt} failed: {e}")
                if attempt < self.max_retries:
                    time.sleep(self.delay * attempt)  # Wykładniczo rosnąca przerwa między próbami
                else:
                    logger.error(f"{self.get_source_name()}: All navigation attempts failed")
                    return False
    
    def wait_for_element(self, selector: str, timeout: Optional[int] = None) -> bool:
        """
        Wait for element to appear
        
        Args:
            selector: CSS selector
            timeout: Optional custom timeout in ms
            
        Returns:
            True if element found, False otherwise
        """
        try:
            self.page.wait_for_selector(selector, timeout=timeout or self.timeout)
            return True
        except Exception as e:
            logger.warning(f"{self.get_source_name()}: Element not found: {selector} - {e}")
            return False
    
    def extract_text_safe(self, selector: str, default: str = "") -> str:
        try:
            element = self.page.query_selector(selector)
            if element:
                return element.inner_text().strip()
            return default
        except Exception as e:
            logger.debug(f"{self.get_source_name()}: Failed to extract text from {selector}: {e}")
            return default
    
    def extract_attribute_safe(self, selector: str, attribute: str, default: str = "") -> str:
        try:
            element = self.page.query_selector(selector)
            if element:
                value = element.get_attribute(attribute)
                return value if value else default
            return default
        except Exception as e:
            logger.debug(f"{self.get_source_name()}: Failed to extract {attribute} from {selector}: {e}")
            return default
    
    def run(self) -> List[Job]:
        jobs = []
        try:
            logger.info(f"{self.get_source_name()}: Starting scrape")
            self.setup_browser()
            jobs = self.scrape_jobs()
            logger.info(f"{self.get_source_name()}: Scraped {len(jobs)} jobs")
        except Exception as e:
            logger.error(f"{self.get_source_name()}: Scraping failed: {e}")
        finally:
            self.close_browser()
        
        return jobs
