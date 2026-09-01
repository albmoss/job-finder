"""
OLX Praca Scraper

Listing zbierany przez Playwright (OLX renderuje wyniki po stronie klienta),
ale opisy dociągane są zwykłym HTTP z podstron ofert - patrz utils/olx_details.
Wcześniej opis był zaślepką "Oferta z OLX (kategoria: X)", przez co AI oceniało
te oferty wyłącznie po tytule.
"""

import logging
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from utils.data_models import Job
from utils.olx_details import fetch_offer_details, normalize_olx_link

# Odstep miedzy podstronami ofert. Zmierzone na 15 ofertach kazde:
#   0,50 s -> 14 opisow, 0 blokad
#   0,25 s -> 9 opisow, juz 1 blokada
#   0,00 s -> 0 opisow, 15 blokad (portal odcina natychmiast)
# Ponizej 0,5 s OLX zaczyna odmawiac takze przegladarce, wiec to nie jest
# pokretlo do krecenia "dla szybkosci" - to granica, ktora portal wyznaczyl.
OPIS_PRZERWA = 0.5
# Po blokadzie odstep rosnie geometrycznie; po BLOKADY_LIMIT z rzedu
# przerywamy dociaganie, bo kazde kolejne wejscie tylko pograza sesje.
BLOKADA_MNOZNIK = 2.0
BLOKADA_SUFIT = 8.0
BLOKADY_LIMIT = 12

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
        if getattr(self, "force", False) and os.path.exists(state_file):
            # Przy --force to wlasnie checkpoint blokuje ponowne przejscie:
            # znacznik "zescrapowane dzisiaj" flaga omija, a ten - nie.
            logger.info("OLX: --force - kasuje checkpoint kategorii")
            try:
                os.remove(state_file)
            except OSError as e:
                logger.warning(f"OLX: nie udalo sie skasowac {state_file}: {e}")
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
        Dociągnij pełne opisy z podstron ofert (przez przeglądarkę - patrz niżej).
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
        if not self.page:
            logger.warning("OLX: brak otwartej przegladarki - pomijam opisy")
            return jobs

        from utils.olx_details import parse_offer_html
        from utils.text_cleaner import clean_job_description

        # Opisy ida przez przegladarke, nie przez `requests`: OLX odpowiada
        # golemu klientowi 403 na kazdy adres, takze na strone glowna, i nie
        # ratuja tego pelne naglowki - blokada siedzi nizej, na odcisku TLS.
        # Skutkiem byly przebiegi typu "ok: 0, errors: 1272" konczace sie
        # napisem "Successfully scraped", a w bazie 1819 ofert z zaslepka.
        # Przegladarka wchodzi bez problemu; zeby nie placic za to czasem,
        # odcinamy obrazy, media i czcionki - opis siedzi w HTML.
        stats = {"ok": 0, "expired": 0, "error": 0, "blocked": 0}
        page = self.page
        try:
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
        except Exception as e:
            logger.debug(f"OLX: nie udalo sie odciac zasobow: {e}")

        enriched = []
        przerwa = OPIS_PRZERWA
        blokady_z_rzedu = 0
        for nr, job in enumerate(jobs):
            # Odstep PRZED wejsciem, nie po udanym: wczesniej `time.sleep` stal
            # na koncu petli, za wszystkimi `continue`, wiec przy 403 byl
            # pomijany. Pierwsza blokada kasowala odstep, kolejne wejscia szly
            # 20 razy na sekunde i blokada sie utrwalala - przebieg z 1 wrzesnia
            # 2026 zrobil 1164 zapytania w 58 s i skonczyl na 1137 blokadach.
            if nr:
                time.sleep(przerwa)
            html, status_http = "", None
            try:
                resp = page.goto(job.link, wait_until="domcontentloaded",
                                 timeout=self.timeout)
                status_http = resp.status if resp else None
                if status_http == 404:
                    stats["expired"] += 1
                    continue
                if status_http == 403:
                    # Osobny licznik, zeby blokada nie ginela w worku "error" -
                    # to jedyny stan, ktory znaczy "przestalo dzialac w ogole".
                    stats["blocked"] += 1
                    blokady_z_rzedu += 1
                    przerwa = min(przerwa * BLOKADA_MNOZNIK, BLOKADA_SUFIT)
                    if blokady_z_rzedu >= BLOKADY_LIMIT:
                        logger.warning(
                            f"OLX: {blokady_z_rzedu} blokad z rzedu - przerywam "
                            f"dociaganie opisow po {nr + 1} z {len(jobs)} ofert"
                        )
                        break
                    continue
                if status_http != 200:
                    stats["error"] += 1
                    enriched.append(job)
                    continue
                html = page.content()
            except Exception as e:
                logger.debug(f"OLX: {job.link} - {type(e).__name__}: {e}")
                stats["error"] += 1
                enriched.append(job)
                continue

            result = parse_offer_html(html)
            status = result.get("status")
            if status == "ok":
                # Podmieniamy opis po utworzeniu obiektu, więc __post_init__
                # (które czyści opis) już się nie wykona - czyścimy ręcznie
                job.description = clean_job_description(result["description"])
                if result.get("company"):
                    job.company = result["company"]
                if result.get("posted_date"):
                    job.posted_date = result["posted_date"]
                stats["ok"] += 1
                blokady_z_rzedu = 0
                przerwa = OPIS_PRZERWA
                enriched.append(job)
            elif status == "expired":
                stats["expired"] += 1
            else:
                stats["error"] += 1
                enriched.append(job)

        try:
            page.unroute("**/*")
        except Exception:
            pass

        self.enrich_stats = stats
        logger.info(
            f"OLX: descriptions fetched - ok: {stats['ok']}, expired (dropped): {stats['expired']}, "
            f"errors: {stats['error']}, blocked (403): {stats['blocked']}"
        )
        if stats["blocked"] and stats["ok"] == 0:
            logger.error(
                "OLX: kazde zapytanie o opis dostalo 403 - portal blokuje ten "
                "sposob pobierania. Oferty ida do bazy z zaslepka."
            )
        return enriched

    def get_job_description(self, job_url: str) -> str:
        """
        Pojedynczy opis - używane przez ręczne dodawanie ofert.

        Przez przeglądarkę, jeśli jest otwarta; `fetch_offer_details` po
        HTTP zostaje jako zapasowa droga, choć dziś OLX odpowiada na nią 403.
        """
        if self.page:
            try:
                from utils.olx_details import parse_offer_html
                resp = self.page.goto(job_url, wait_until="domcontentloaded",
                                      timeout=self.timeout)
                if resp and resp.status == 200:
                    result = parse_offer_html(self.page.content())
                    if result.get("status") == "ok":
                        return result.get("description", "")
            except Exception as e:
                logger.debug(f"OLX: pojedynczy opis przez przegladarke nie wyszedl: {e}")

        result = fetch_offer_details(job_url)
        return result.get("description", "") if result.get("status") == "ok" else ""


    def scrape_current_page(self) -> List[Job]:
        return []  # Nieużywane
        
    def go_to_next_page(self) -> bool:
        return False  # Nieużywane
