"""
Scraper publicznych ogłoszeń LinkedIn przez unauthenticated guest HTTP API.

Zastąpił powolny i blokowany przepływ Playwrighta dwoma lekkimi punktami końcowymi:
1. Wyszukiwarka: GET /jobs-guest/jobs/api/seeMoreJobPostings/search
2. Szczegóły: GET /jobs-guest/jobs/api/jobPosting/{job_id}

Pobieranie szczegółów (pełny opis, poziom, forma zatrudnienia) wykonywane jest
wyłącznie dla nowych ofert (omijając known_links), z ostrożnym tempem (1.5-2.5s)
i rotacją nagłówków, aby nie prowokować limitów LinkedIna dla gości.
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from config import USER_AGENTS
from utils import stop
from utils.candidate_scope import scope_city, scope_levels
from utils.data_models import Job
from utils.links import canonical_link, logo_url
from utils.known_links import known_links
from utils.offer_fields import (
    _fold,
    norm_contracts,
    norm_schedules,
    norm_seniority,
    norm_work_modes,
)
from utils.text_cleaner import clean_job_description

logger = logging.getLogger(__name__)

# Mapowanie kodów SENIORITY z utils/offer_fields na parametr f_E LinkedIna:
# 1 = Staż (Internship)
# 2 = Poziom podstawowy (Entry level)
# 3 = Współpracownik (Associate)
# 4 = Kadra średniego szczebla (Mid-Senior level)
# 5 = Dyrektor (Director)
# 6 = Kierownictwo (Executive)
_SENIORITY_TO_F_E = {
    "intern": ["1"],
    "junior": ["2"],
    "mid": ["3", "4"],
    "senior": ["4"],
    "lead": ["4", "5"],
    "manager": ["5", "6"],
}


class LinkedInScraper:
    """Scraper ofert LinkedIn oparty o publiczne punkty końcowe gościa (HTTP)."""

    BASE_URL = "https://www.linkedin.com"
    SEARCH_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"

    def __init__(self, config: dict):
        self.config = config
        self.cfg = config.get("linkedin", {}) or {}
        self.session = requests.Session()
        self.seen_again_links = []
        self.force = getattr(self, "force", False)

        # Maksymalna liczba ofert z listingu na jeden przebieg
        self.max_offers = self.cfg.get("max_offers", 100)
        # Pauzy między zapytaniami dla uniknięcia 429
        self.listing_delay = 1.5
        self.detail_delay_range = (1.5, 2.5)

    def get_source_name(self) -> str:
        return "LinkedIn"

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def _map_f_e_param(self, levels: list[str]) -> str:
        """Kody f_E z poziomów ze scope_levels()."""
        codes = sorted(set(c for lvl in levels for c in _SENIORITY_TO_F_E.get(lvl, [])))
        return ",".join(codes) if codes else "1,2"

    def build_search_url(self, start: int = 0) -> str:
        """Zbuduj URL zapytania seeMoreJobPostings z filtrem miasta i f_E."""
        city = scope_city(default="Warszawa")
        levels = scope_levels()
        f_e = self._map_f_e_param(levels)

        job_types = self.cfg.get("job_types", ["F", "P"])
        jt_param = ",".join(job_types) if job_types else "F,P"

        loc_encoded = urllib.parse.quote(city)
        url = (
            f"{self.SEARCH_ENDPOINT}?keywords=&location={loc_encoded}"
            f"&f_E={f_e}&f_JT={jt_param}&f_TPR=r2592000&start={start}"
        )
        return url

    def _extract_job_id(self, link_url: str, card_soup) -> Optional[str]:
        """Wyciągnij numeryczny identyfikator oferty LinkedIn."""
        # 1. Z data-entity-urn karty (np. urn:li:jobPosting:4471858913)
        entity_el = card_soup.select_one("[data-entity-urn]")
        if entity_el:
            urn = entity_el.get("data-entity-urn") or ""
            m = re.search(r"jobPosting:(\d+)", urn)
            if m:
                return m.group(1)

        # 2. Z końcówki linku (np. ...-4471858913?position=...)
        m = re.search(r"-(\d{8,})(?:\?|$)", link_url)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _card_logo(card_soup) -> Optional[str]:
        img = card_soup.select_one("img.artdeco-entity-image")
        if not img:
            return None
        raw = img.get("data-delayed-url") or ""
        return logo_url(raw) if raw != img.get("data-ghost-url") else None

    def _fetch_job_detail(self, job_id: str) -> dict:
        """Pobierz szczegóły oferty z endpointu gościa."""
        url = f"{self.DETAIL_ENDPOINT}/{job_id}"
        out = {
            "description": "",
            "raw_seniority": None,
            "contract_types": None,
            "schedules": None,
            "category": None,
        }
        for attempt in range(1, 3):
            if stop.requested():
                return out
            try:
                time.sleep(random.uniform(*self.detail_delay_range))
                resp = self.session.get(url, headers=self._headers(), timeout=15)
                if resp.status_code == 404:
                    return out
                if resp.status_code == 429:
                    wait_time = 5 * attempt
                    logger.warning(f"LinkedIn: 429 Rate Limit on detail {job_id}, waiting {wait_time}s")
                    time.sleep(wait_time)
                    continue
                if resp.status_code != 200:
                    logger.debug(f"LinkedIn: detail {job_id} HTTP {resp.status_code}")
                    return out

                soup = BeautifulSoup(resp.text, "html.parser")
                desc_el = soup.select_one(".show-more-less-html__markup")
                if desc_el:
                    out["description"] = clean_job_description(desc_el.decode_contents())

                for c in soup.select(".description__job-criteria-list li"):
                    header = (c.select_one(".description__job-criteria-subheader") or c.select_one("h3"))
                    val_el = (c.select_one(".description__job-criteria-text") or c.select_one("span"))
                    h = header.get_text(strip=True).lower() if header else ""
                    v = val_el.get_text(strip=True) if val_el else ""
                    if not v:
                        continue
                    if "poziom" in h or "seniority" in h:
                        out["raw_seniority"] = v
                    elif "zatrudnienia" in h or "employment" in h:
                        out["contract_types"] = norm_contracts(v)
                        out["schedules"] = norm_schedules(v)
                    elif "bran" in h or "industr" in h or "funkcj" in h or "function" in h:
                        out["category"] = v
                return out
            except Exception as e:
                logger.debug(f"LinkedIn: detail fetch error {job_id}: {e}")
                time.sleep(1)
        return out

    @staticmethod
    def _resolve_seniority(title: str, crit_val: str | None) -> list[str] | None:
        """
        Ustal seniority z kryterium LinkedIna oraz jawnego poziomu w tytule oferty.
        LinkedIn 'Kadra średniego szczebla' / 'Mid-Senior' oznacza mid i senior.
        Jawne słowo 'Senior' w tytule przy kryterium 'Specjalista' daje ['senior', 'mid'].
        """
        found = []
        if crit_val:
            c_folded = _fold(crit_val)
            if "sredniego szczebla" in c_folded or "mid-senior" in c_folded:
                found.extend(["mid", "senior"])
            else:
                s = norm_seniority(crit_val)
                if s:
                    found.extend(s)
        t_s = norm_seniority(title)
        if t_s:
            for s in t_s:
                if s not in found:
                    found.insert(0, s) if s in ("senior", "lead", "manager") else found.append(s)
        return list(dict.fromkeys(found)) or None

    def scrape_jobs(self) -> List[Job]:
        """Pobierz oferty z publicznego wyszukiwania LinkedIn."""
        jobs: List[Job] = []
        seen_links = set()
        known = known_links() if not getattr(self, "force", False) else set()

        city = scope_city(default="Warszawa")
        levels = scope_levels()
        f_e = self._map_f_e_param(levels)
        logger.info(f"LinkedIn: starting guest scrape for city='{city}', levels={levels} (f_E={f_e})")

        start = 0
        step = 10
        empty_pages = 0

        while len(jobs) < self.max_offers and empty_pages < 2 and not stop.requested():
            search_url = self.build_search_url(start=start)
            logger.debug(f"LinkedIn: fetching search page start={start}")

            try:
                time.sleep(self.listing_delay)
                resp = self.session.get(search_url, headers=self._headers(), timeout=15)
                if resp.status_code == 429:
                    logger.warning("LinkedIn: search rate limit (429), pausing 10s")
                    time.sleep(10)
                    resp = self.session.get(search_url, headers=self._headers(), timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"LinkedIn search returned HTTP {resp.status_code}")
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select("li")
                if not cards:
                    empty_pages += 1
                    start += step
                    continue

                empty_pages = 0
                page_found = 0

                for card in cards:
                    title_el = card.select_one(".base-search-card__title, h3")
                    comp_el = card.select_one(".base-search-card__subtitle, h4")
                    loc_el = card.select_one(".job-search-card__location")
                    time_el = card.select_one("time")
                    link_el = card.select_one("a.base-card__full-link")

                    if not (title_el and link_el):
                        continue

                    title = title_el.get_text(strip=True)
                    company = comp_el.get_text(strip=True) if comp_el else "Unknown"
                    loc = loc_el.get_text(strip=True) if loc_el else city
                    raw_link = link_el.get("href") or ""
                    if not raw_link:
                        continue

                    link = canonical_link(raw_link)
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    posted_date = time_el.get("datetime") if time_el else None
                    job_id = self._extract_job_id(raw_link, card)
                    logo = self._card_logo(card)

                    # Wykryj tryb pracy z lokalizacji lub tytułu
                    work_modes = norm_work_modes(f"{loc} {title}")

                    # Znane oferty: nie dociągamy opisu, oznaczamy last_seen
                    if link in known:
                        self.seen_again_links.append(link)
                        job = Job(
                            title=title,
                            company=company,
                            link=link,
                            description=title,
                            source=self.get_source_name(),
                            location=loc,
                            posted_date=posted_date,
                            work_modes=work_modes,
                            scraped_at=datetime.now().isoformat(),
                            logo_url=logo,
                        )
                        jobs.append(job)
                        page_found += 1
                        continue

                    # Nowe oferty: pobierz pełny opis i kryteria z endpointu szczegółów
                    detail_data = {}
                    if job_id:
                        detail_data = self._fetch_job_detail(job_id)

                    desc = detail_data.get("description") or title
                    work_modes = norm_work_modes(f"{loc} {title}") or norm_work_modes(desc[:500])

                    job = Job(
                        title=title,
                        company=company,
                        link=link,
                        description=desc,
                        source=self.get_source_name(),
                        location=loc,
                        posted_date=posted_date,
                        seniority=self._resolve_seniority(title, detail_data.get("raw_seniority")),
                        contract_types=detail_data.get("contract_types"),
                        schedules=detail_data.get("schedules"),
                        category=detail_data.get("category"),
                        work_modes=work_modes,
                        scraped_at=datetime.now().isoformat(),
                        logo_url=logo,
                    )
                    jobs.append(job)
                    page_found += 1
                    if len(jobs) >= self.max_offers:
                        break

                logger.info(f"LinkedIn: processed start={start}, collected {page_found} jobs (total: {len(jobs)})")
                start += step

            except Exception as e:
                logger.error(f"LinkedIn: error scraping start={start}: {e}")
                break

        logger.info(f"LinkedIn: successfully collected {len(jobs)} jobs")
        return jobs

    def run(self) -> List[Job]:
        """Główny punkt wejścia wywoływany przez main_scraper."""
        return self.scrape_jobs()
