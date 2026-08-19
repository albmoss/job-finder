"""
Wspólna baza dla portali korzystających z API "candidate-api".

JustJoin.it i RocketJobs.pl należą do tej samej grupy i wystawiają identyczne
API pod /api/candidate-api/offers:
  - lista:      GET /api/candidate-api/offers?city=&experienceLevels=&from=<cursor>
  - szczegóły:  GET /api/candidate-api/offers/<slug>   -> pole `body` z pełnym opisem HTML

Wcześniej każdy z tych scraperów miał własną implementację (RocketJobs dodatkowo
sniffował ruch przez Playwright i zwracał 0 ofert). Tutaj jest jedna logika.
"""

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from utils.data_models import Job

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
}


class CandidateAPIScraper(BaseScraper):
    """Baza dla scraperów opartych o candidate-api. Nie używa przeglądarki."""

    # Do nadpisania w podklasach
    PORTAL_URL = ""          # np. "https://justjoin.it"
    SOURCE_NAME = ""
    CONFIG_KEY = ""          # klucz w SCRAPER_CONFIG

    # Ile ofert maksymalnie dociągać ze szczegółami (opis) - najdroższa część.
    # RocketJobs zwraca ~960 ofert; przy limicie 600 aż 360 zostawało bez opisu,
    # a że append_jobs nie aktualizuje istniejących rekordów, zostawały takie na stałe.
    # 1200 z zapasem pokrywa oba portale; przy 4 wątkach to ~1-2 minuty.
    MAX_DETAIL_FETCH = 1200
    DETAIL_WORKERS = 3

    def __init__(self, config: dict):
        super().__init__(config)
        self._rate_lock = threading.Lock()
        self._cooldown_until = 0.0
        self._detail_failures = 0
        self._throttled = 0

    @property
    def api_url(self) -> str:
        return f"{self.PORTAL_URL}/api/candidate-api/offers"

    def get_source_name(self) -> str:
        return self.SOURCE_NAME

    def scrape_jobs(self) -> List[Job]:
        """Nieużywane - run() jest nadpisane, żeby pominąć przeglądarkę."""
        return []

    def _headers(self) -> dict:
        h = dict(DEFAULT_HEADERS)
        h["Referer"] = f"{self.PORTAL_URL}/"
        h["Origin"] = self.PORTAL_URL
        return h

    def _fetch_listing(self, session: requests.Session) -> List[dict]:
        """Pobierz wszystkie oferty z listy (paginacja kursorowa)."""
        portal_cfg = self.config.get(self.CONFIG_KEY, {}) or {}
        city = (portal_cfg.get("location") or self.config.get("location", "Warszawa")).capitalize()

        # API akceptuje TYLKO powtórzone parametry (?experienceLevels=junior&experienceLevels=intern).
        # Wersja z przecinkiem ("junior,intern") zwraca 0 wyników - cicha pułapka.
        levels = portal_cfg.get("experience_levels") or [portal_cfg.get("experience_level", "junior")]
        if isinstance(levels, str):
            levels = [levels]

        offers = []
        cursor = 0
        seen_cursors = set()

        while True:
            params = [("city", city)] + [("experienceLevels", lv) for lv in levels] + [("from", cursor)]
            try:
                resp = session.get(self.api_url, params=params, headers=self._headers(), timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"{self.SOURCE_NAME}: listing failed at cursor {cursor}: {e}")
                break

            page = data.get("data", [])
            if not page:
                break
            offers.extend(page)

            meta = data.get("meta", {})
            total = meta.get("totalItems", 0)
            next_cursor = (meta.get("next") or {}).get("cursor")

            logger.info(f"{self.SOURCE_NAME}: cursor {cursor} -> +{len(page)} offers ({len(offers)}/{total})")

            # Zabezpieczenie przed pętlą nieskończoną gdy API zwróci ten sam kursor
            if next_cursor is None or next_cursor in seen_cursors or len(offers) >= total:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            time.sleep(0.3 + random.random() * 0.3)

        logger.info(f"{self.SOURCE_NAME}: fetched {len(offers)} offers from the listing")
        return offers

    def _matches_location(self, offer: dict, target_city: str) -> bool:
        """
        API zwraca ~10% ofert spoza wskazanego miasta (Gdańsk, Wrocław, Kraków).
        Zostawiamy tylko oferty z docelowego miasta ORAZ wszystkie zdalne -
        praca zdalna jest niezależna od lokalizacji i jest w preferencjach.
        """
        if str(offer.get("workplaceType", "")).lower() == "remote":
            return True

        target = target_city.lower()
        if target in str(offer.get("city", "")).lower():
            return True

        for loc in (offer.get("locations") or []):
            if isinstance(loc, dict) and target in str(loc.get("city", "")).lower():
                return True

        return False

    def _cooldown(self, seconds: float):
        """Wstrzymaj wszystkie wątki po napotkaniu limitu."""
        with self._rate_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def _wait_if_cooling(self):
        with self._rate_lock:
            remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def _fetch_detail(self, session: requests.Session, slug: str) -> dict:
        """
        Pobierz szczegóły oferty z ponawianiem.

        Bez retry przy 960 ofertach ~77% pobrań kończyło się porażką (limit
        włącza się przy większym wolumenie), a oferty zostawały z samymi
        metadanymi z listingu zamiast treści ogłoszenia. Błędy szły do
        logger.debug, więc problem był niewidoczny w logach.
        """
        for attempt in range(3):
            self._wait_if_cooling()
            try:
                resp = session.get(f"{self.api_url}/{slug}", headers=self._headers(), timeout=25)

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code in (429, 503):
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else 0
                    except (TypeError, ValueError):
                        wait = 0
                    wait = min(wait or (3 * (2 ** attempt) + random.random() * 2), 60)
                    self._cooldown(wait)
                    with self._rate_lock:
                        self._throttled += 1
                    continue

                if resp.status_code == 404:
                    return {}  # oferta zniknęła - nie ma sensu ponawiać

                logger.debug(f"{self.SOURCE_NAME}: detail {slug} -> HTTP {resp.status_code}")

            except Exception as e:
                logger.debug(f"{self.SOURCE_NAME}: detail {slug} failed: {e}")

            if attempt < 2:
                time.sleep(1.5 * (attempt + 1) + random.random())

        with self._rate_lock:
            self._detail_failures += 1
        return {}

    def build_link(self, slug: str) -> str:
        return f"{self.PORTAL_URL}/offers/{slug}"

    def _parse(self, offer: dict, detail: dict) -> Job:
        """Zbuduj Job z listy + szczegółów. `body` (pełny opis) ma priorytet."""
        title = offer.get("title", "Unknown")
        company = offer.get("companyName") or detail.get("companyName") or "Unknown"
        slug = offer.get("slug", "")

        parts = []

        # Pełny opis oferty - najcenniejsze dane dla analizy AI.
        # Jest w HTML; Job.__post_init__ -> clean_job_description -> strip_html to czyści.
        body = detail.get("body", "")
        if body:
            parts.append(body)

        def skill_names(raw):
            out = []
            for s in raw or []:
                if isinstance(s, dict) and s.get("name"):
                    lvl = s.get("level")
                    out.append(f"{s['name']} (poziom {lvl})" if lvl else s["name"])
                elif isinstance(s, str):
                    out.append(s)
            return out

        req = skill_names(detail.get("requiredSkills") or offer.get("requiredSkills"))
        nice = skill_names(detail.get("niceToHaveSkills") or offer.get("niceToHaveSkills"))
        if req:
            parts.append("Technologie wymagane: " + ", ".join(req))
        if nice:
            parts.append("Technologie mile widziane: " + ", ".join(nice))

        for emp in (detail.get("employmentTypes") or offer.get("employmentTypes") or []):
            if isinstance(emp, dict) and (emp.get("from") or emp.get("to")):
                parts.append(
                    f"Wynagrodzenie ({emp.get('type', '')}): "
                    f"{emp.get('from')}-{emp.get('to')} {emp.get('currency', 'PLN')}"
                )

        if offer.get("experienceLevel"):
            parts.append(f"Poziom: {offer['experienceLevel']}")
        if offer.get("workplaceType"):
            parts.append(f"Tryb pracy: {offer['workplaceType']}")
        if offer.get("workingTime"):
            parts.append(f"Wymiar: {offer['workingTime']}")

        if not parts:
            parts.append(f"Oferta z {self.SOURCE_NAME}: {title}")

        return Job(
            title=title,
            company=company,
            link=self.build_link(slug),
            description="\n\n".join(parts),
            source=self.SOURCE_NAME,
            location=offer.get("city", self.config.get("location", "Warszawa")),
            posted_date=offer.get("publishedAt", ""),
            scraped_at=datetime.now().isoformat(),
        )

    def run(self) -> List[Job]:
        """Wejście - HTTP zamiast przeglądarki."""
        logger.info(f"{self.SOURCE_NAME}: start (candidate-api, no browser)")
        jobs = []
        session = requests.Session()

        try:
            offers = self._fetch_listing(session)
            if not offers:
                return []

            target_city = (
                (self.config.get(self.CONFIG_KEY, {}) or {}).get("location")
                or self.config.get("location", "Warszawa")
            )
            before = len(offers)
            offers = [o for o in offers if self._matches_location(o, target_city)]
            if before != len(offers):
                logger.info(f"{self.SOURCE_NAME}: filtered out {before - len(offers)} offers outside {target_city}")

            # Szczegóły równolegle, ale delikatnie - to najwolniejsza faza.
            to_detail = [o for o in offers if o.get("slug")][: self.MAX_DETAIL_FETCH]
            logger.info(f"{self.SOURCE_NAME}: fetching descriptions for {len(to_detail)} offers...")

            details = {}

            def worker(off):
                slug = off["slug"]
                d = self._fetch_detail(session, slug)
                time.sleep(0.35 + random.random() * 0.25)
                return slug, d

            with ThreadPoolExecutor(max_workers=self.DETAIL_WORKERS) as ex:
                for slug, d in ex.map(worker, to_detail):
                    if d:
                        details[slug] = d

            rate = len(details) / len(to_detail) if to_detail else 0
            msg = (f"{self.SOURCE_NAME}: pobrano {len(details)}/{len(to_detail)} pełnych opisów "
                   f"({rate:.0%})")
            if self._throttled:
                msg += f" | limit portalu: {self._throttled}x"
            if rate < 0.8:
                # Widoczne ostrzeżenie - wcześniej porażki szły do debug i ginęły
                logger.warning(msg + " low success rate - offers will be left without a description")
            else:
                logger.info(msg)

            for offer in offers:
                try:
                    jobs.append(self._parse(offer, details.get(offer.get("slug", ""), {})))
                except Exception as e:
                    logger.warning(f"{self.SOURCE_NAME}: parse failed for {offer.get('slug')}: {e}")

        except Exception as e:
            logger.error(f"{self.SOURCE_NAME}: scrape crashed: {e}")
        finally:
            session.close()

        logger.info(f"{self.SOURCE_NAME}: {len(jobs)} offers total")
        return jobs
