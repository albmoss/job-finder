"""
OLX Praca Scraper

Listing zbierany przez Playwright (OLX renderuje wyniki po stronie klienta),
ale opisy dociągane są zwykłym HTTP z podstron ofert - patrz utils/olx_details.
Wcześniej opis był zaślepką "Oferta z OLX (kategoria: X)", przez co AI oceniało
te oferty wyłącznie po tytule.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from utils.data_models import Job
from utils.olx_details import fetch_offer_details, normalize_olx_link

logger = logging.getLogger(__name__)


class OLXScraper(BaseScraper):
    """Scraper portalu OLX Praca."""
    
    BASE_URL = "https://www.olx.pl"
    
    def __init__(self, config: dict):
        super().__init__(config)
        # Linki ofert, których stron nie pobierano - main_scraper odnotuje na
        # nich last_seen/times_seen (patrz JobDatabase.record_scrape).
        self.seen_again_links = []

    def get_source_name(self) -> str:
        return "OLX Praca"
    
    def build_search_url(self) -> str:
        """Build search URL with 'bez doświadczenia' filter using configured location and radius"""
        from config import SCRAPER_CONFIG
        
        # OLX filter: bez-doswiadczenia (without experience) - gets ALL entry-level jobs
        experience = SCRAPER_CONFIG["olx_praca"]["experience_filter"]
        location = SCRAPER_CONFIG.get("location", "Warszawa").lower()
        radius = SCRAPER_CONFIG.get("radius_km", 15)
        
        # OLX URL structure: /praca/bez-doswiadczenia/warszawa/?search[dist]=15
        url = f"{self.BASE_URL}/praca/{experience}/{location}/?search[dist]={radius}"
        
        logger.info(f"OLX Praca: no experience required, {location.capitalize()} +{radius}km")
        return url
    
    def scrape_jobs(self) -> List[Job]:
        """Scrape jobs using optimized OLX filter URL splitting by categories to bypass 25-page limit"""
        jobs = []
        seen_links = set()
        
        categories = [
            "administracja-biurowa", "badania-i-rozwoj", "bankowosc", "bhp-ochrona-srodowiska",
            "budowa-remonty", "dostawca-kurier-miejski", "e-commerce-handel-internetowy",
            "edukacja", "finanse-ksiegowosc", "franczyza-wlasna-firma", 
            "fryzjerstwo-kosmetyka", "gastronomia", "hr", "hostessa-roznoszenie-ulotek",
            "hotelarstwo", "inzynieria", "it-telekomunikacja", "kierowca", "logistyka-zakupy-spedycja",
            "marketing-pr", "mechanika-lakiernictwo", "montaz-serwis", "obsluga-klienta-call-center",
            "ochrona", "opieka", "organizacja-obsluga-imprez", "praca-magazynowe", "pracownik-sklepu",
            "produkcja", "rolnictwo-ogrodnictwo", "sprzatanie", "sprzedaz", "ubezpieczenia",
            "wykladanie-ekspozycja-towaru", "zdrowie", "praktyki-staze", "pozostale-oferty-pracy"
        ]
        
        import os, json
        state_file = "olx_category_state.json"
        
        completed_categories = []
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    # Stan kasowany raz na dobę
                    import datetime
                    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                    if state.get("date") == today_str:
                        completed_categories = state.get("completed", [])
                    else:
                        completed_categories = []
            except Exception:
                pass

        logger.info(f"Loaded {len(completed_categories)} completed categories from previous runs.")

        for category in categories:
            if category in completed_categories:
                logger.info(f"Skipping already scraped category: {category}")
                continue

            # Lokalizacja i promień z configu
            from config import SCRAPER_CONFIG
            location = SCRAPER_CONFIG.get("location", "Warszawa").lower()
            radius = SCRAPER_CONFIG.get("radius_km", 15)
            search_url = f"https://www.olx.pl/praca/{category}/{location}/?search%5Border%5D=created_at%3Adesc&search%5Bdist%5D={radius}"
            logger.info(f"Navigating to OLX Category URL (Sorted by Newest): {search_url}")
            
            if not self.navigate_with_retry(search_url):
                logger.error(f"{self.get_source_name()}: Failed to navigate to {category}")
                continue
                
            # Zgoda na ciasteczka przy pierwszym wejściu - bez długiego czekania
            try:
                cookie_button = self.page.locator('button[id="onetrust-accept-btn-handler"]')
                if cookie_button.count() > 0 and cookie_button.is_visible(timeout=1000):
                    cookie_button.click(timeout=1000)
                    self.page.wait_for_timeout(500)
            except Exception as e:
                pass

            try:
                empty_state = self.page.query_selector('h3[data-testid="no-results-header"]')
                if empty_state:
                    logger.info(f"No results for {category}, skipping.")
                    completed_categories.append(category)
                    continue
            except Exception:
                pass
                
            page_num = 1
            max_pages = 5 # Reduced to 5 as requested to focus on most recent offers
            
            while page_num <= max_pages:
                logger.info(f"{self.get_source_name()} | {category}: Scraping page {page_num}")
                
                try:
                    self.page.wait_for_selector('a[href*="/oferta/"]', timeout=3000)
                except Exception:
                    break  # Zwykle znaczy koniec ofert albo timeout
                
                cards = self.page.query_selector_all('div[data-cy="l-card"]')
                page_found = 0
                
                for card in cards:
                    try:
                        link_elem = card.query_selector('a[href*="/oferta/"]')
                        if not link_elem: continue
                        
                        title_elem = link_elem.query_selector('h6')
                        title = title_elem.inner_text().strip() if title_elem else link_elem.inner_text().strip()
                        href = link_elem.get_attribute('href')
                        
                        # Kilka selektorów naraz - klasy CSS-modules zmieniają się przy każdym wdrożeniu
                        company_elem = (
                            card.query_selector('p.css-w5qju7') or
                            card.query_selector('[data-testid="company-name"]') or
                            card.query_selector('div[data-cy="l-card"] p:nth-of-type(2)')
                        )
                        company = company_elem.inner_text().strip() if company_elem else "OLX"
                        
                        if not href or not title: continue
                        if not href.startswith('http'): href = self.BASE_URL + href

                        # Utnij parametry śledzące - inaczej ta sama oferta trafia
                        # do bazy wielokrotnie pod różnymi URL-ami
                        href = normalize_olx_link(href)

                        if href in seen_links: continue
                        seen_links.add(href)

                        # Opis dociągany jest w drugiej fazie (enrich_descriptions)
                        job = Job(
                            title=title,
                            company=company,
                            link=href,
                            description=f"Oferta z OLX (kategoria: {category})",
                            source=self.get_source_name(),
                            location="Warszawa"
                        )
                        jobs.append(job)
                        page_found += 1
                    except Exception:
                        pass
                
                logger.info(f"{category} | Page {page_num}: Collected {page_found} new jobs")
                
                if page_found == 0: break
                    
                try:
                    next_btn = self.page.query_selector('a[data-cy="pagination-forward"]')
                    if next_btn:
                        next_btn.click()
                        self.page.wait_for_timeout(1500)
                        page_num += 1
                    else:
                        break  # Koniec listy
                except Exception:
                    break
                    
            # Kategoria przerobiona do końca
            completed_categories.append(category)
            logger.info(f"Completed category {category}. Total cumulative jobs: {len(jobs)}")
            
            # Stan zapisywany po każdej kategorii
            try:
                import datetime
                with open(state_file, 'w') as f:
                    json.dump({
                        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                        "completed": completed_categories
                    }, f)
            except Exception as e:
                logger.error(f"Could not save OLX category state: {e}")
                
        logger.info(f"{self.get_source_name()}: Total jobs collected across all categories: {len(jobs)}")

        jobs = self.enrich_descriptions(jobs)
        return jobs

    def enrich_descriptions(self, jobs: List[Job]) -> List[Job]:
        """
        Dociągnij pełne opisy z podstron ofert (HTTP, bez przeglądarki).
        Oferty wygasłe są odrzucane - i tak nie da się na nie zaaplikować.

        Oferty, które są już w bazie, pomijamy w całości: `record_scrape` nie
        nadpisuje istniejących rekordów, więc pobranie ich opisu (0.4 s każde,
        1272 oferty w przebiegu z 23.08) nie zmieniało niczego poza czasem.
        Ich linki idą do `seen_again_links` - main_scraper odnotowuje na nich
        `last_seen`, czyli jedyny sygnał wieku oferty na OLX.
        """
        if not jobs:
            return jobs

        from utils.known_links import known_links

        known = known_links()
        self.seen_again_links = [j.link for j in jobs if j.link in known]
        jobs = [j for j in jobs if j.link not in known]
        if self.seen_again_links:
            logger.info(
                f"OLX: {len(self.seen_again_links)} offers already in the database "
                f"(their pages are skipped)"
            )
        if not jobs:
            return []

        logger.info(f"OLX: fetching descriptions for {len(jobs)} offers...")
        session = requests.Session()
        stats = {"ok": 0, "expired": 0, "error": 0}

        def work(job: Job) -> Job:
            result = fetch_offer_details(job.link, session=session)
            status = result.get("status")
            stats[status if status in stats else "error"] += 1

            if status == "ok":
                # Podmieniamy opis po utworzeniu obiektu, więc __post_init__
                # (które czyści opis) już się nie wykona - czyścimy ręcznie
                from utils.text_cleaner import clean_job_description
                job.description = clean_job_description(result["description"])
                if result.get("company"):
                    job.company = result["company"]
                if result.get("posted_date"):
                    job.posted_date = result["posted_date"]
            elif status == "expired":
                return None
            return job

        try:
            with ThreadPoolExecutor(max_workers=4) as ex:
                enriched = [j for j in ex.map(work, jobs) if j is not None]
        finally:
            session.close()

        logger.info(
            f"OLX: descriptions fetched - ok: {stats['ok']}, expired (dropped): {stats['expired']}, "
            f"errors: {stats['error']}"
        )
        return enriched

    def get_job_description(self, job_url: str) -> str:
        """Pojedynczy opis - używane przez ręczne dodawanie ofert."""
        result = fetch_offer_details(job_url)
        return result.get("description", "") if result.get("status") == "ok" else ""


    def scrape_current_page(self) -> List[Job]:
        return []  # Nieużywane
        
    def go_to_next_page(self) -> bool:
        return False  # Nieużywane
