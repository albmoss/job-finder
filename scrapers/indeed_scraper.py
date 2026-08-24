"""
Indeed (pl.indeed.com) - największy agregator ofert w Polsce, ale też najbardziej
wrogi automatom. Ten scraper jest zbudowany wokół jego ograniczeń, nie wokół
tego, jak wygodnie byłoby go pobierać.

Co zostało sprawdzone empirycznie (sierpień 2026):

1. Zwykły HTTP dostaje 403 (Cloudflare). Konieczny Playwright.
2. Pierwsza nawigacja w świeżym kontekście przechodzi i zwraca 15 ofert
   w `window.mosaic.providerData["mosaic-provider-jobcards"]`.
3. Paginacja (`&start=10`) NIE działa - przekierowuje na ścianę logowania
   ("Zaloguj się | Konta Indeed"), zwracając HTTP 200 i zero ofert.
   Dlatego scraper nie paginuje: bierze stronę 1 dla kilku słów kluczowych.
4. Wejście na /viewjob?jk=... osobną nawigacją dostaje 403 "Security Check".
   ALE kliknięcie karty na liście ładuje pełny opis w panelu bocznym
   (#jobDescriptionText, ~1300 znaków) bez nowej nawigacji - i to przechodzi.
   Stąd zbieranie opisów klikaniem, a nie wchodzeniem na podstrony.
5. Sam snippet z listingu ma 133-171 znaków, czyli ociera się o próg 150,
   poniżej którego `waterfall_analysis` oznacza ofertę jako [BRAK PEŁNEGO OPISU]
   i ścina jej ocenę do 55%. Bez opisu z panelu te oferty byłyby bezużyteczne.

Wniosek: to źródło jest z założenia niskonakładowe i wolne - kilkadziesiąt ofert
z pełnym opisem na przebieg. Podnoszenie `keywords` albo skracanie przerw
kończy się serią 403 i zerem ofert.
"""

import json
import logging
import random
import re
import time
from datetime import datetime
from typing import List, Optional

from utils.data_models import Job
from utils.links import canonical_link

logger = logging.getLogger(__name__)

_MOSAIC_RE = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});',
    re.DOTALL,
)

# Sygnały, że Indeed przerwał sesję: interstitial, security check albo login wall
_BLOCK_TITLES = ("cierpliwości", "security check", "zaloguj się", "just a moment")

_CARD_SELECTOR = "h2.jobTitle a, a.jcs-JobTitle, [data-testid='job-title'] a"
_DESC_SELECTORS = (
    "#jobDescriptionText",
    ".jobsearch-JobComponent-description",
    "[data-testid='jobsearch-JobComponent-description']",
)


class IndeedScraper:
    """
    Scraper Indeed oparty o Playwright. Nie dziedziczy po BaseScraper, bo potrzebuje
    świeżego kontekstu przeglądarki na każde słowo kluczowe (kontekst po serii
    kliknięć jest "spalony" i kolejne nawigacje dostają 403).
    Implementuje ten sam interfejs: get_source_name() i run().
    """

    BASE_URL = "https://pl.indeed.com"

    DEFAULT_KEYWORDS = ["junior", "praktykant", "asystent"]

    def __init__(self, config: dict):
        self.config = config
        cfg = config.get("indeed", {}) or {}
        self.cfg = cfg
        self.location = cfg.get("location") or config.get("location", "Warszawa")
        self.keywords = cfg.get("keywords", self.DEFAULT_KEYWORDS)
        self.headless = config.get("headless", True)
        # Przerwa między słowami kluczowymi. Przy 12-18 s cała seria kończyła się
        # 403 - stąd domyślnie znacznie więcej.
        self.pause_range = tuple(cfg.get("pause_between_keywords", (45, 75)))
        self.click_pause_range = tuple(cfg.get("pause_between_clicks", (1.5, 3.5)))
        self.max_offers_per_keyword = cfg.get("max_offers_per_keyword", 15)
        self.fetch_descriptions = cfg.get("fetch_descriptions", True)

        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )

    def get_source_name(self) -> str:
        return "Indeed"

    # --- pomocnicze ----------------------------------------------------------

    @staticmethod
    def _extract_cards(html: str) -> List[dict]:
        """Wyciągnij listę ofert z bloku mosaic-provider-jobcards."""
        m = _MOSAIC_RE.search(html)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Indeed: could not parse mosaic JSON: {e}")
            return []
        return (
            data.get("metaData", {})
            .get("mosaicProviderJobCardsModel", {})
            .get("results", [])
        )

    @staticmethod
    def _is_blocked(title: str) -> bool:
        t = (title or "").lower()
        return any(sig in t for sig in _BLOCK_TITLES)

    def _card_to_job(self, card: dict, description: str) -> Optional[Job]:
        jobkey = card.get("jobkey")
        if not jobkey:
            return None

        title = str(card.get("displayTitle") or card.get("title") or "").strip()
        if not title:
            return None

        desc_parts = [description] if description else []
        if not description:
            # Snippet jako ostatnia deska ratunku - krótki, ale lepszy niż pustka
            snippet = card.get("snippet") or ""
            if snippet:
                desc_parts.append(re.sub(r"<[^>]+>", " ", snippet))

        salary = card.get("salarySnippet") or {}
        if isinstance(salary, dict) and salary.get("text"):
            desc_parts.append(f"Wynagrodzenie: {salary['text']}")
        if card.get("formattedRelativeTime"):
            desc_parts.append(f"Opublikowano: {card['formattedRelativeTime']}")

        posted = None
        for key in ("pubDate", "createDate"):
            ts = card.get(key)
            if isinstance(ts, (int, float)) and ts > 0:
                try:
                    posted = datetime.fromtimestamp(ts / 1000).isoformat()
                    break
                except (ValueError, OSError):
                    pass

        return Job(
            title=title,
            company=str(card.get("company") or "Nieznana firma").strip(),
            link=canonical_link(f"{self.BASE_URL}/viewjob?jk={jobkey}"),
            description="\n".join(p for p in desc_parts if p),
            source=self.get_source_name(),
            location=card.get("formattedLocation") or None,
            posted_date=posted,
            scraped_at=datetime.now().isoformat(),
        )

    def _harvest_descriptions(self, page, cards: List[dict]) -> dict:
        """
        Zbierz pełne opisy klikając kolejne karty. Zwraca {jobkey: opis}.

        Kolejność kart w DOM odpowiada kolejności w JSON-ie mosaic - obie pochodzą
        z tej samej odpowiedzi serwera. Gdy liczby się nie zgadzają, bierzemy część
        wspólną zamiast zgadywać przypisanie.
        """
        descriptions = {}
        try:
            links = page.query_selector_all(_CARD_SELECTOR)
        except Exception as e:
            logger.warning(f"Indeed: could not read cards from the DOM: {e}")
            return descriptions

        usable = min(len(links), len(cards), self.max_offers_per_keyword)
        if len(links) != len(cards):
            logger.info(
                f"Indeed: {len(links)} cards in the DOM vs {len(cards)} in JSON - "
                f"taking the first {usable}"
            )

        for i in range(usable):
            jobkey = cards[i].get("jobkey")
            if not jobkey:
                continue
            try:
                links[i].click()
                page.wait_for_timeout(int(random.uniform(*self.click_pause_range) * 1000))

                if self._is_blocked(page.title()):
                    logger.warning(
                        f"Indeed: session cut short by bot protection after {len(descriptions)} "
                        f"descriptions - the rest stay without a full description"
                    )
                    break

                for sel in _DESC_SELECTORS:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text().strip()
                        if len(text) > 100:
                            descriptions[jobkey] = text
                        break
            except Exception as e:
                logger.debug(f"Indeed: karta {i} - {type(e).__name__}: {e}")
                continue

        return descriptions

    def _scrape_keyword(self, browser, keyword: str) -> tuple:
        """
        Jedno słowo kluczowe = jeden świeży kontekst = jedna nawigacja.

        Zwraca (oferty, czy_zablokowano). Sama pusta lista nie wystarczy:
        wołający musi odróżnić "nic nie znaleziono" od "portal nas odciął",
        bo w drugim przypadku nie ma po co próbować dalej.
        """
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self.user_agent,
            locale="pl-PL",
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = context.new_page()

        try:
            url = f"{self.BASE_URL}/jobs?q={keyword}&l={self.location}"
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)

            status = response.status if response else 0
            title = page.title()

            if status >= 400 or self._is_blocked(title):
                logger.warning(
                    f"Indeed [{keyword}]: blocked (HTTP {status}, title: {title[:50]}) - "
                    f"skipping. Raise pause_between_keywords if this repeats every run."
                )
                return [], True

            cards = self._extract_cards(page.content())
            if not cards:
                logger.warning(
                    f"Indeed [{keyword}]: page loaded but zero offers in the mosaic JSON - "
                    f"Indeed may have changed its listing structure"
                )
                return [], False

            cards = cards[: self.max_offers_per_keyword]
            descriptions = (
                self._harvest_descriptions(page, cards) if self.fetch_descriptions else {}
            )

            jobs = []
            for card in cards:
                job = self._card_to_job(card, descriptions.get(card.get("jobkey"), ""))
                if job:
                    jobs.append(job)

            logger.info(
                f"Indeed [{keyword}]: {len(jobs)} offers, "
                f"{len(descriptions)}/{len(cards)} with a full description"
            )
            return jobs, False

        except Exception as e:
            logger.error(f"Indeed [{keyword}]: {type(e).__name__}: {e}")
            return [], False
        finally:
            context.close()

    # --- przebieg ------------------------------------------------------------

    def run(self) -> List[Job]:
        from playwright.sync_api import sync_playwright

        logger.info(
            f"Indeed: start (location: {self.location}, "
            f"keywords: {', '.join(self.keywords)})"
        )

        all_jobs, seen = [], set()
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                for i, keyword in enumerate(self.keywords):
                    if i:
                        pause = random.uniform(*self.pause_range)
                        logger.info(f"Indeed: przerwa {pause:.0f}s przed '{keyword}'")
                        time.sleep(pause)

                    jobs, blocked = self._scrape_keyword(browser, keyword)

                    for job in jobs:
                        if job.link not in seen:
                            seen.add(job.link)
                            all_jobs.append(job)

                    # Blokada jest na poziomie adresu IP, nie zapytania: kolejne
                    # słowa kluczowe dostaną 403 tak samo, a każde kosztuje minutę
                    # przerwy i osobny kontekst przeglądarki. W przebiegach z 21
                    # i 23 sierpnia były to trzy porażki z rzędu i 115 s na nic.
                    if blocked:
                        logger.warning(
                            f"Indeed: blocked on '{keyword}' - skipping the remaining "
                            f"{len(self.keywords) - i - 1} keywords, the block is IP-wide"
                        )
                        break
            finally:
                browser.close()

        with_desc = sum(1 for j in all_jobs if len(j.description) >= 150)
        logger.info(
            f"Indeed: {len(all_jobs)} unique offers, {with_desc} with a description >=150 chars"
        )
        if all_jobs and with_desc / len(all_jobs) < 0.5:
            logger.warning(
                f"Indeed: only {with_desc}/{len(all_jobs)} offers have a full description - "
                f"the rest are capped at 55% during analysis"
            )
        return all_jobs
