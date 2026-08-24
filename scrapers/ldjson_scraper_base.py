"""
Wspólna baza dla portali publikujących oferty jako schema.org JobPosting w ld+json.

praca.pl i aplikuj.pl różnią się wyłącznie formatem URL-a listingu i wzorcem linku
do oferty. Cała reszta - paginacja, pobieranie stron szczegółów, wyciąganie
JobPosting, filtry zakresu - jest identyczna, więc siedzi tutaj. To ten sam układ
co `candidate_api_base.py`, który w ten sposób obsługuje JustJoin.it i RocketJobs.

Dlaczego strony szczegółów, a nie same listingi: listing daje wyłącznie tytuł i
link. Opis z ld+json na stronie oferty ma 700-1700 znaków, a przy opisie krótszym
niż 150 znaków `waterfall_analysis` oznacza ofertę jako [BRAK PEŁNEGO OPISU] i
ścina jej ocenę do 55%. Bez pobrania szczegółów każda oferta z tych portali
wpadałaby w ten limit.
"""

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from json import JSONDecodeError, loads
from typing import List, Optional

import requests

from utils.data_models import Job
from utils.links import canonical_link
from utils.text_cleaner import strip_html

logger = logging.getLogger(__name__)

_LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Tytuły wykluczone z góry. Profil kandydata to entry-level (patrz AI_CONFIG
# w config.py), a każda taka oferta to ~1-4 tys. znaków opisu wysłanych do modelu
# tylko po to, żeby dostać ocenę odrzucającą. Filtr jest celowo wąski - łapie
# wyłącznie jednoznaczne sygnały seniority, żeby nie zgubić ofert do przyuczenia.
_SENIOR_TITLE_RE = re.compile(
    r"\b(senior|starszy|starsza|lead|team\s*lead|principal|expert|ekspert|"
    r"kierownik|kierowniczka|manager|menedżer|menedżerka|dyrektor|dyrektorka|"
    r"architekt|architektka|head\s+of|chief|prezes)\b",
    re.IGNORECASE,
)

_REMOTE_RE = re.compile(r"\b(zdaln|remote|home\s*office|praca\s+z\s+domu)", re.IGNORECASE)


class LdJsonPortalScraper:
    """
    Baza scrapera portalu opartego o ld+json. Nie dziedziczy po BaseScraper -
    nie potrzebuje przeglądarki, wystarczy HTTP. Implementuje ten sam interfejs:
    get_source_name() i run().

    Podklasa musi ustawić: SOURCE_NAME, CONFIG_KEY, OFFER_LINK_RE
    i zaimplementować build_listing_url(page).
    """

    SOURCE_NAME = "ld+json portal"
    CONFIG_KEY = ""
    OFFER_LINK_RE: Optional[re.Pattern] = None

    # Domyślne ograniczniki - podklasa może nadpisać, config użytkownika ma pierwszeństwo
    DEFAULT_MAX_PAGES = 15
    DEFAULT_MAX_OFFERS = 400

    # Odstęp między żądaniami jest ADAPTACYJNY. Zmierzone na przebiegu z 23.08:
    # praca.pl i aplikuj.pl wypuściły ponad 2600 żądań przy 0.6 s i nie oddały
    # ani jednego HTTP 429 - stały odstęp dobrany "na oko" pod najostrzejszy
    # portal kosztował tam ~20 minut na nic. Startujemy więc od MIN_REQUEST_INTERVAL,
    # po serii czystych odpowiedzi schodzimy w stronę FLOOR_REQUEST_INTERVAL, a
    # pierwszy 429 wraca do wartości startowej (i dalej w górę, jeśli się powtarza).
    MIN_REQUEST_INTERVAL = 0.6      # punkt startu, nie sztywny limit
    FLOOR_REQUEST_INTERVAL = 0.2    # dolna granica przyspieszania
    SPEEDUP_AFTER = 25              # tyle czystych odpowiedzi z rzędu -> skracamy odstęp
    DETAIL_THREADS = 3

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    ]

    def __init__(self, config: dict):
        self.config = config
        cfg = config.get(self.CONFIG_KEY, {}) or {}
        self.cfg = cfg
        self.location_filter = (cfg.get("location") or config.get("location", "Warszawa")).lower()
        self.max_pages = cfg.get("max_pages", self.DEFAULT_MAX_PAGES)
        self.max_offers = cfg.get("max_offers", self.DEFAULT_MAX_OFFERS)
        self.skip_senior = cfg.get("skip_senior_titles", True)
        # Pomijanie stron szczegółów ofert już znanych bazie. Wyłącz tylko wtedy,
        # gdy chcesz odświeżyć opisy (wtedy i tak potrzebny jest --refresh).
        self.skip_known_details = cfg.get("skip_known_details", True)
        self.seen_again_links = []

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        })

        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0
        self._interval = self.MIN_REQUEST_INTERVAL
        self._clean_streak = 0

    # --- interfejs do nadpisania ---------------------------------------------

    def get_source_name(self) -> str:
        return self.SOURCE_NAME

    def build_listing_url(self, page: int) -> str:
        raise NotImplementedError

    def extract_offer_links(self, html: str) -> List[str]:
        """Domyślnie: wszystkie linki pasujące do OFFER_LINK_RE."""
        if not self.OFFER_LINK_RE:
            return []
        return self.OFFER_LINK_RE.findall(html)

    # --- HTTP ----------------------------------------------------------------

    def _throttle(self):
        """Globalny odstęp między żądaniami, niezależny od liczby wątków."""
        with self._rate_lock:
            interval = self._interval
            now = time.monotonic()
            if now < self._cooldown_until:
                time.sleep(self._cooldown_until - now)
                now = time.monotonic()
            delta = now - self._last_request_at
            if delta < interval:
                time.sleep(interval - delta)
            self._last_request_at = time.monotonic()

    def _note_ok(self):
        """Czysta odpowiedź - po SPEEDUP_AFTER z rzędu przyspieszamy o 20%."""
        with self._rate_lock:
            if self._interval <= self.FLOOR_REQUEST_INTERVAL:
                return
            self._clean_streak += 1
            if self._clean_streak >= self.SPEEDUP_AFTER:
                self._clean_streak = 0
                self._interval = max(self.FLOOR_REQUEST_INTERVAL, self._interval * 0.8)
                logger.info(f"{self.SOURCE_NAME}: no push-back - interval down to {self._interval:.2f}s")

    def _note_throttled(self, wait: float):
        """HTTP 429 - cooldown plus powrót do wolniejszego (a potem coraz wolniejszego) tempa."""
        with self._rate_lock:
            self._clean_streak = 0
            self._interval = min(3.0, max(self.MIN_REQUEST_INTERVAL, self._interval * 1.6))
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
            logger.warning(f"{self.SOURCE_NAME}: HTTP 429 - cooldown {wait:.0f}s, interval {self._interval:.2f}s")

    def _fetch(self, url: str, attempts: int = 3) -> str:
        """Pobierz stronę. Respektuje Retry-After i wspólny cooldown po 429."""
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=25)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else None
                    except (TypeError, ValueError):
                        wait = None
                    wait = min(wait if wait is not None else 5 * (2 ** attempt), 120)
                    self._note_throttled(wait)
                    continue
                if resp.status_code == 404:
                    return ""  # koniec paginacji - nie ma sensu ponawiać
                resp.raise_for_status()
                self._note_ok()
                return resp.text
            except requests.RequestException as e:
                logger.warning(
                    f"{self.SOURCE_NAME}: attempt {attempt}/{attempts} failed "
                    f"({url[:70]}): {type(e).__name__}"
                )
                if attempt < attempts:
                    time.sleep((2 ** attempt) + random.random())
        return ""

    # --- parsowanie ----------------------------------------------------------

    @staticmethod
    def _iter_jobpostings(html: str):
        """Wypluj wszystkie obiekty JobPosting z bloków ld+json na stronie."""
        for block in _LD_JSON_RE.findall(html):
            try:
                data = loads(block.strip())
            except (JSONDecodeError, ValueError):
                continue
            for item in (data if isinstance(data, list) else [data]):
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    yield item

    @staticmethod
    def _company_name(posting: dict) -> str:
        org = posting.get("hiringOrganization")
        if isinstance(org, dict):
            return str(org.get("name") or "Nieznana firma").strip()
        if isinstance(org, str) and org.strip():
            return org.strip()
        return "Nieznana firma"

    @staticmethod
    def _location(posting: dict) -> str:
        loc = posting.get("jobLocation")
        if isinstance(loc, list):
            loc = loc[0] if loc else None
        if isinstance(loc, dict):
            addr = loc.get("address")
            if isinstance(addr, dict):
                parts = [addr.get("addressLocality"), addr.get("addressRegion")]
                return ", ".join(p for p in parts if p) or ""
            if isinstance(addr, str):
                return addr
        return ""

    @staticmethod
    def _salary(posting: dict) -> str:
        base = posting.get("baseSalary")
        if not isinstance(base, dict):
            return ""
        value = base.get("value")
        currency = base.get("currency") or ""
        if not isinstance(value, dict):
            return ""
        lo, hi, unit = value.get("minValue"), value.get("maxValue"), value.get("unitText") or ""
        if lo is None and hi is None:
            lo = value.get("value")
        parts = []
        if lo is not None:
            parts.append(f"od {lo}")
        if hi is not None:
            parts.append(f"do {hi}")
        if not parts:
            return ""
        return f"Wynagrodzenie: {' '.join(parts)} {currency} {unit}".strip()

    def _build_job(self, posting: dict, link: str) -> Optional[Job]:
        title = str(posting.get("title") or "").strip()
        if not title:
            return None

        desc_parts = [strip_html(str(posting.get("description") or ""))]

        salary = self._salary(posting)
        if salary:
            desc_parts.append(salary)
        if posting.get("employmentType"):
            et = posting["employmentType"]
            desc_parts.append(f"Typ zatrudnienia: {', '.join(et) if isinstance(et, list) else et}")
        if posting.get("industry"):
            desc_parts.append(f"Branża: {posting['industry']}")

        description = "\n".join(p for p in desc_parts if p)

        now = datetime.now().isoformat()
        return Job(
            title=title,
            company=self._company_name(posting),
            link=canonical_link(link),
            description=description,
            source=self.get_source_name(),
            location=self._location(posting) or None,
            posted_date=str(posting.get("datePosted") or "") or None,
            # Deklarowana data wygaśnięcia - twardy sygnał martwej oferty
            valid_through=str(posting.get("validThrough") or "") or None,
            scraped_at=now,
            last_seen=now,
        )

    # --- filtry zakresu ------------------------------------------------------

    def _in_scope(self, job: Job, posting: dict) -> bool:
        """
        Zasada z README: oferta z docelowego miasta ALBO w pełni zdalna.

        Filtry portali bywają kłamliwe, więc decydujemy po stronie klienta -
        nawet gdy listing był miejski, trafiają się oferty z innych miast.
        """
        if self.skip_senior and _SENIOR_TITLE_RE.search(job.title):
            return False

        haystack = f"{job.location or ''} {job.title}"
        if self.location_filter in haystack.lower():
            return True

        # Zdalne są niezależne od miasta i mieszczą się w preferencjach
        if _REMOTE_RE.search(haystack) or _REMOTE_RE.search(job.description[:600]):
            return True
        if posting.get("jobLocationType") == "TELECOMMUTE":
            return True

        return False

    # --- przebieg ------------------------------------------------------------

    def _collect_links(self) -> List[str]:
        """Przejdź listingi i zbierz kanoniczne linki ofert."""
        seen, ordered = set(), []
        empty_streak = 0

        for page in range(1, self.max_pages + 1):
            html = self._fetch(self.build_listing_url(page))
            if not html:
                logger.info(f"{self.SOURCE_NAME}: page {page} empty - end of pagination")
                break

            # Ta sama oferta pojawia się na stronie wielokrotnie (logo, tytuł, "aplikuj"):
            # aplikuj.pl daje ~158 dopasowań na ~53 oferty. Duplikat trzeba odciąć
            # od razu przy dodawaniu, bo filtrowanie listy względem `seen` przed
            # jej aktualizacją przepuszcza powtórzenia z tej samej strony.
            found = [canonical_link(u) for u in self.extract_offer_links(html)]
            new = []
            for u in found:
                if u and u not in seen:
                    seen.add(u)
                    ordered.append(u)
                    new.append(u)

            logger.info(
                f"{self.SOURCE_NAME}: page {page} - {len(found)} links, "
                f"{len(new)} new (total {len(ordered)})"
            )

            # Portale przy przekroczeniu ostatniej strony oddają ponownie stronę 1.
            # Bez tego warunku scraper kręciłby się w kółko po tych samych ofertach.
            if not new:
                empty_streak += 1
                if empty_streak >= 2:
                    logger.info(f"{self.SOURCE_NAME}: no new links - stopping pagination")
                    break
            else:
                empty_streak = 0

            if len(ordered) >= self.max_offers:
                logger.info(f"{self.SOURCE_NAME}: reached the limit of {self.max_offers} offers")
                break

        return ordered[: self.max_offers]

    def _fetch_detail(self, link: str) -> Optional[tuple]:
        html = self._fetch(link, attempts=2)
        if not html:
            return None
        for posting in self._iter_jobpostings(html):
            job = self._build_job(posting, link)
            if job:
                return job, posting
        return None

    @staticmethod
    def _slug_words(link: str) -> str:
        """
        Tytuł oferty zaszyty w URL-u, sprowadzony do tekstu ze spacjami.

        Wszystkie trzy portale trzymają w linku slug tytułu
        (/oferta/123/senior-java-developer, /senior-java-developer_998.html),
        więc jednoznaczne sygnały seniority widać PRZED pobraniem strony.
        To nie jest kosmetyka: w przebiegu z 23.08 aplikuj.pl pobrał 2078 stron,
        z czego 1706 poleciało do kosza dopiero po pobraniu.
        """
        tail = link.rsplit("/", 1)[-1]
        return tail.replace("-", " ").replace("_", " ").replace(",", " ")

    def run(self) -> List[Job]:
        logger.info(f"{self.SOURCE_NAME}: start (lokalizacja: {self.location_filter})")

        links = self._collect_links()
        if not links:
            logger.warning(f"{self.SOURCE_NAME}: no offer links found - "
                           f"the board may have changed its listing structure")
            return []

        # Najpierw odsiew po bazie, dopiero potem po tytule: `seen_again_links`
        # ma objąć wszystko, co nadal wisi na portalu, także oferty seniorskie -
        # inaczej połowa bazy przestałaby dostawać aktualizacje `last_seen`.
        if self.skip_known_details:
            from utils.known_links import split_known

            links, self.seen_again_links = split_known(links)
            logger.info(
                f"{self.SOURCE_NAME}: {len(links)} new to fetch, "
                f"{len(self.seen_again_links)} already in the database (their pages are skipped)"
            )

        if self.skip_senior:
            before = len(links)
            links = [l for l in links if not _SENIOR_TITLE_RE.search(self._slug_words(l))]
            if before != len(links):
                logger.info(
                    f"{self.SOURCE_NAME}: {before - len(links)} senior-level offers rejected "
                    f"by their URL slug (their pages are never fetched)"
                )

        if not links:
            return []

        jobs, no_ldjson, out_of_scope = [], 0, 0
        with ThreadPoolExecutor(max_workers=self.DETAIL_THREADS) as executor:
            for result in executor.map(self._fetch_detail, links):
                if result is None:
                    no_ldjson += 1
                    continue
                job, posting = result
                if not self._in_scope(job, posting):
                    out_of_scope += 1
                    continue
                jobs.append(job)

        # Liczby wejścia i wyjścia na INFO - cicha porażka to najgorszy rodzaj błędu
        logger.info(
            f"{self.SOURCE_NAME}: fetched {len(links) - no_ldjson}/{len(links)} descriptions, "
            f"dropped {out_of_scope} (out of scope) -> {len(jobs)} offers"
        )
        if links and (len(links) - no_ldjson) / len(links) < 0.5:
            logger.warning(
                f"{self.SOURCE_NAME}: only {len(links) - no_ldjson}/{len(links)} pages carried "
                f"JobPosting in ld+json - check whether the board changed its structure"
            )

        return jobs
