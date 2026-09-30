"""
Scraper ofert bezpośrednio z systemów ATS (Applicant Tracking Systems) firm.

Pobiera publiczne feedy ogłoszeń z:
  - Greenhouse:      https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  - Lever:           https://api.lever.co/v0/postings/{slug}?mode=json
  - Ashby:           https://api.ashbyhq.com/posting-api/job-board/{slug}
  - SmartRecruiters: https://api.smartrecruiters.com/v1/companies/{id}/postings?country=pl (+ detail)
  - Recruitee:       https://{slug}.recruitee.com/api/offers/
  - Teamtailor:      https://{slug}.teamtailor.com/jobs.json
  - Workable:        https://apply.workable.com/api/v1/widget/accounts/{slug} (+ detail dla ofert w PL/remote)

Katalog firm i rotacja:
  - Slugi Greenhouse, Lever i Ashby są pobierane w runtime z publicznego repozytorium
    `Feashliaa/job-board-aggregator` (GitHub raw). Repozytorium jest udostępniane na licencji
    MIT (Copyright (c) 2026 Riley Dorrington), która wprost zezwala na bezpłatne użycie,
    kopiowanie, modyfikację i dystrybucję bez ograniczeń.
  - Pobrana lista jest zapisywana w lokalnym pliku cache `ats_slugs_cache.json` (gitignored).
  - Stan firm, które miały oferty w Polsce / zdalne, trafia do `ats_state.json` (gitignored).
  - Każdy przebieg odpytuje firmy ze stanem aktywnym (sprawdzone w PL) oraz rotacyjny wycinek
    pozostałych firm (pełny obrót bazy trwa ok. 7 dni), z zachowaniem limitów zapytań i czasu.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from scrapers.base_scraper import BaseScraper
from utils.candidate_scope import scope_city
from utils.data_models import Job
from utils.links import canonical_link
from utils.offer_fields import (
    _fold,
    make_salary,
    norm_contracts,
    norm_schedules,
    norm_seniority,
    norm_work_modes,
)
from utils.safe_io import load_json_safe, save_json_atomic
from utils.text_cleaner import strip_html

logger = logging.getLogger(__name__)

# Ścieżki do plików stanu i cache (katalog główny job-finder)
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = BASE_DIR / "ats_slugs_cache.json"
STATE_FILE = BASE_DIR / "ats_state.json"

# Publiczny zbiór danych o slugach ATS (licencja MIT, Riley Dorrington)
UPSTREAM_SLUG_URLS = {
    "greenhouse": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
    "lever": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
    "ashby": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
}

# Zweryfikowane firmy zatrudniające w Polsce (ziarno startowe dla każdego ATS)
VERIFIED_POLISH_SEEDS: Dict[str, List[str]] = {
    "greenhouse": ["boxinc"],
    "lever": ["fresha"],
    "ashby": ["elevenlabs"],
    "smartrecruiters": ["cdprojektred", "inpost"],
    "recruitee": ["tylko", "huuugegames"],
    "teamtailor": ["ipfdigital"],
    "workable": ["monterail"],
}

# Słownik synonimów i odmian głównych polskich miast (uwzględniający zapis angielski i bez diakrytyków)
CITY_SYNONYMS: Dict[str, Set[str]] = {
    "warszawa": {"warszawa", "warsaw", "warschau"},
    "krakow": {"krakow", "cracow"},
    "wroclaw": {"wroclaw", "breslau"},
    "poznan": {"poznan"},
    "gdansk": {"gdansk", "danzig"},
    "gdynia": {"gdynia"},
    "sopot": {"sopot"},
    "lodz": {"lodz"},
    "katowice": {"katowice"},
    "szczecin": {"szczecin", "stettin"},
    "lublin": {"lublin"},
    "bydgoszcz": {"bydgoszcz"},
    "bialystok": {"bialystok"},
    "rzeszow": {"rzeszow"},
    "torun": {"torun"},
    "gliwice": {"gliwice"},
    "zabrze": {"zabrze"},
    "bielsko-biala": {"bielsko-biala", "bielsko biala"},
    "olsztyn": {"olsztyn"},
    "radom": {"radom"},
    "czestochowa": {"czestochowa"},
    "kielce": {"kielce"},
    "sosnowiec": {"sosnowiec"},
    "zielona-gora": {"zielona-gora", "zielona gora"},
    "opole": {"opole"},
}

ALL_PL_CITIES: Set[str] = set()
for _syns in CITY_SYNONYMS.values():
    ALL_PL_CITIES.update(_syns)

DISALLOWED_REMOTE_REGIONS = (
    r"\b(us only|usa|united states|north america|americas|latam|apac|australia|canada|japan|india|uk only|germany only)\b",
)

GLOBAL_REMOTE_REGIONS = (
    r"\b(poland|polska|\bpl\b|europe|emea|\beu\b|cee|worldwide|global|anywhere)\b",
)


def matches_target_city(text: str, target_city: str) -> bool:
    """Sprawdza, czy tekst lokalizacji pasuje do docelowego miasta kandydata."""
    if not text:
        return False
    folded = _fold(text)
    tf = _fold(target_city)
    patterns = {tf}
    for k, syns in CITY_SYNONYMS.items():
        if tf == k or tf in syns:
            patterns.update(syns)
    return any(re.search(r"\b" + re.escape(p) + r"\b", folded) for p in patterns)


def is_pl_location(text: str, country_code: str = "") -> bool:
    """Czy dana lokalizacja wskazuje na Polskę (kod kraju, nazwa kraju lub polskie miasto)."""
    if country_code and country_code.upper() in ("PL", "POL"):
        return True
    f = _fold(text or "")
    if re.search(r"\b(poland|polska)\b", f):
        return True
    return any(re.search(r"\b" + re.escape(c) + r"\b", f) for c in ALL_PL_CITIES)


def is_remote_pl_allowed(text: str, country_code: str = "") -> bool:
    """Czy oferta zdalna zezwala na pracę z Polski (Polska, Europa/EMEA, Worldwide/Anywhere)."""
    if country_code and country_code.upper() in ("PL", "POL"):
        return True
    f = _fold(text or "")
    if any(re.search(pat, f) for pat in GLOBAL_REMOTE_REGIONS):
        return True
    if any(re.search(r"\b" + re.escape(c) + r"\b", f) for c in ALL_PL_CITIES):
        return True
    if any(re.search(pat, f) for pat in DISALLOWED_REMOTE_REGIONS):
        return False
    return "remote" in f or "anywhere" in f or "zdaln" in f


# =====================================================================
# Baza i adaptery poszczególnych platform ATS
# =====================================================================

class BaseATSHandler:
    """Bazowy interfejs dla pojedynczego systemu ATS."""
    ATS_NAME = ""

    def __init__(self, session_headers: Optional[dict] = None):
        self.headers = session_headers or {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def fetch_json(self, url: str, timeout: int = 10) -> Optional[Any]:
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8", errors="replace")
                return json.loads(data)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        raise NotImplementedError


class GreenhouseFeed(BaseATSHandler):
    ATS_NAME = "Greenhouse"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, dict):
            return []

        jobs: List[Job] = []
        company_name = slug.replace("-", " ").capitalize()

        for item in data.get("jobs", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            comp = item.get("company_name") or company_name
            loc_name = (item.get("location") or {}).get("name") or ""
            offices = [o.get("location") or o.get("name") or "" for o in item.get("offices", [])]
            all_locs = [loc_name] + offices
            loc_combined = " ; ".join(filter(None, all_locs))

            is_remote = "remote" in loc_combined.lower() or "zdaln" in loc_combined.lower()

            in_scope = False
            if is_remote and is_remote_pl_allowed(loc_combined):
                in_scope = True
            elif is_pl_location(loc_combined) and matches_target_city(loc_combined, target_city):
                in_scope = True

            if not in_scope:
                continue

            raw_content = item.get("content") or ""
            desc = strip_html(html.unescape(raw_content))

            emp_type = next(
                (m.get("value") for m in item.get("metadata", []) if (m.get("name") or "").lower() == "employment type"),
                None
            )
            departments = [d.get("name") for d in item.get("departments", []) if d.get("name")]
            work_modes = ["remote"] if is_remote else norm_work_modes(loc_combined)

            jobs.append(Job(
                title=title,
                company=comp,
                link=item.get("absolute_url") or "",
                description=desc,
                source="ATS Feeds",
                location=loc_name or (offices[0] if offices else target_city),
                posted_date=item.get("first_published") or item.get("updated_at"),
                seniority=norm_seniority(title),
                work_modes=work_modes,
                contract_types=norm_contracts(emp_type),
                schedules=norm_schedules(emp_type),
                category=" / ".join(departments) if departments else None,
            ))
        return jobs


class LeverFeed(BaseATSHandler):
    ATS_NAME = "Lever"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, list):
            return []

        jobs: List[Job] = []
        company_name = slug.replace("-", " ").capitalize()

        for item in data:
            title = (item.get("text") or "").strip()
            if not title:
                continue

            cat = item.get("categories") or {}
            loc = cat.get("location") or ""
            country = item.get("country") or ""
            all_locs = cat.get("allLocations") or []
            loc_combined = f"{loc} ; {country} ; " + " ; ".join(all_locs)

            wpt = (item.get("workplaceType") or "").lower()
            is_remote = wpt == "remote" or "remote" in loc_combined.lower()

            in_scope = False
            if is_remote and is_remote_pl_allowed(loc_combined, country):
                in_scope = True
            elif is_pl_location(loc_combined, country) and matches_target_city(loc_combined, target_city):
                in_scope = True

            if not in_scope:
                continue

            desc_parts = []
            if item.get("descriptionPlain"):
                desc_parts.append(item["descriptionPlain"])
            elif item.get("description"):
                desc_parts.append(strip_html(item["description"]))
            for l in item.get("lists") or []:
                ltitle = l.get("text") or ""
                lcontent = strip_html(l.get("content") or "")
                if ltitle or lcontent:
                    desc_parts.append(f"{ltitle}\n{lcontent}")
            if item.get("additionalPlain"):
                desc_parts.append(item["additionalPlain"])
            desc = "\n\n".join(desc_parts)

            sal = None
            sr = item.get("salaryRange")
            if sr and (sr.get("min") or sr.get("max")):
                sal = make_salary(sr.get("min"), sr.get("max"), sr.get("currency"), sr.get("interval"))

            p_date = None
            if item.get("createdAt"):
                try:
                    p_date = datetime.fromtimestamp(item["createdAt"] / 1000).isoformat()
                except Exception:
                    p_date = None

            jobs.append(Job(
                title=title,
                company=company_name,
                link=item.get("hostedUrl") or "",
                description=desc,
                source="ATS Feeds",
                location=loc if is_pl_location(loc) else target_city,
                posted_date=p_date,
                seniority=norm_seniority(title),
                work_modes=norm_work_modes(wpt) or (["remote"] if is_remote else None),
                contract_types=norm_contracts(cat.get("commitment")),
                schedules=norm_schedules(cat.get("commitment")),
                salary=sal,
                category=cat.get("department") or cat.get("team"),
            ))
        return jobs


class AshbyFeed(BaseATSHandler):
    ATS_NAME = "Ashby"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, dict):
            return []

        jobs: List[Job] = []
        company_name = slug.replace("-", " ").capitalize()

        for item in data.get("jobs", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            loc = item.get("location") or ""
            sec_locs = item.get("secondaryLocations") or []
            sec_texts = []
            for s in sec_locs:
                sec_texts.append(s.get("location") or "")
                addr = (s.get("address") or {}).get("postalAddress") or {}
                sec_texts.extend([addr.get("addressCountry") or "", addr.get("addressLocality") or ""])
            primary_addr = (item.get("address") or {}).get("postalAddress") or {}
            all_texts = [loc, primary_addr.get("addressCountry") or "", primary_addr.get("addressLocality") or ""] + sec_texts
            loc_combined = " ; ".join(filter(None, all_texts))

            wpt = (item.get("workplaceType") or "").lower()
            is_remote = bool(item.get("isRemote")) or wpt == "remote"

            in_scope = False
            if is_remote and is_remote_pl_allowed(loc_combined):
                in_scope = True
            elif is_pl_location(loc_combined) and matches_target_city(loc_combined, target_city):
                in_scope = True

            if not in_scope:
                continue

            desc = item.get("descriptionPlain") or strip_html(item.get("descriptionHtml") or "")
            sal = None
            comp = item.get("compensation")
            if comp and isinstance(comp, dict):
                sal = make_salary(comp.get("minSalary"), comp.get("maxSalary"), comp.get("currency"), comp.get("interval"))

            jobs.append(Job(
                title=title,
                company=company_name,
                link=item.get("jobUrl") or "",
                description=desc,
                source="ATS Feeds",
                location=loc if is_pl_location(loc) else target_city,
                posted_date=item.get("publishedAt"),
                seniority=norm_seniority(title),
                work_modes=norm_work_modes(wpt) or (["remote"] if is_remote else None),
                contract_types=norm_contracts(item.get("employmentType")),
                schedules=norm_schedules(item.get("employmentType")),
                salary=sal,
                category=item.get("department") or item.get("team"),
            ))
        return jobs


class SmartRecruitersFeed(BaseATSHandler):
    ATS_NAME = "SmartRecruiters"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        # Filtrujemy po stronie API SmartRecruiters dla Polski: country=pl
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=pl"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, dict):
            return []

        jobs: List[Job] = []
        company_name = slug.replace("-", " ").capitalize()

        for item in data.get("content", []):
            title = (item.get("name") or "").strip()
            if not title:
                continue

            loc_obj = item.get("location") or {}
            city = loc_obj.get("city") or ""
            full_loc = loc_obj.get("fullLocation") or ""
            is_remote = bool(loc_obj.get("remote"))
            is_hybrid = bool(loc_obj.get("hybrid"))

            in_scope = False
            if is_remote:
                in_scope = True
            elif matches_target_city(city or full_loc, target_city):
                in_scope = True

            if not in_scope:
                continue

            posting_id = item.get("id")
            det_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
            det = self.fetch_json(det_url, timeout=timeout) or {}

            sections = (det.get("jobAd") or {}).get("sections") or {}
            parts = []
            for s_name in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
                s_val = sections.get(s_name)
                if s_val and s_val.get("text"):
                    parts.append(strip_html(s_val["text"]))
            desc = "\n\n".join(parts)

            comp = (item.get("company") or {}).get("name") or company_name
            wm = ["remote"] if is_remote else ["hybrid"] if is_hybrid else ["onsite"]
            exp = (item.get("experienceLevel") or {}).get("label")
            emp = (item.get("typeOfEmployment") or {}).get("label")
            dept = (item.get("department") or {}).get("label")

            jobs.append(Job(
                title=title,
                company=comp,
                link=det.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{posting_id}",
                description=desc,
                source="ATS Feeds",
                location=city or target_city,
                posted_date=item.get("releasedDate"),
                seniority=norm_seniority([exp, title]),
                work_modes=wm,
                contract_types=norm_contracts(emp),
                schedules=norm_schedules(emp),
                category=dept,
            ))
        return jobs


class RecruiteeFeed(BaseATSHandler):
    ATS_NAME = "Recruitee"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        url = f"https://{slug}.recruitee.com/api/offers/"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, dict):
            return []

        jobs: List[Job] = []
        company_name = slug.replace("-", " ").capitalize()

        for item in data.get("offers", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            city = item.get("city") or ""
            country = item.get("country") or ""
            cc = item.get("country_code") or ""
            loc = item.get("location") or ""
            is_remote = bool(item.get("remote"))
            is_hybrid = bool(item.get("hybrid"))

            in_scope = False
            if is_remote:
                in_scope = True
            elif (cc.upper() == "PL" or is_pl_location(f"{city} {country} {loc}", cc)) and matches_target_city(f"{city} {loc}", target_city):
                in_scope = True

            if not in_scope:
                continue

            desc = strip_html(item.get("description") or "") + "\n\n" + strip_html(item.get("requirements") or "")
            sal = None
            s_obj = item.get("salary")
            if s_obj and (s_obj.get("min") or s_obj.get("max")):
                sal = make_salary(s_obj.get("min"), s_obj.get("max"), s_obj.get("currency"), s_obj.get("period"))

            wm = ["remote"] if is_remote else ["hybrid"] if is_hybrid else ["onsite"]
            emp = item.get("employment_type_code")

            jobs.append(Job(
                title=title,
                company=item.get("company_name") or company_name,
                link=item.get("careers_url") or "",
                description=desc,
                source="ATS Feeds",
                location=city or target_city,
                posted_date=item.get("published_at") or item.get("created_at"),
                seniority=norm_seniority(title),
                work_modes=wm,
                contract_types=norm_contracts(emp),
                schedules=norm_schedules(emp),
                salary=sal,
                category=item.get("department"),
            ))
        return jobs


class TeamtailorFeed(BaseATSHandler):
    ATS_NAME = "Teamtailor"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        url = f"https://{slug}.teamtailor.com/jobs.json"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, dict):
            return []

        jobs: List[Job] = []
        feed_title = data.get("title") or slug.replace("-", " ").capitalize()

        for item in data.get("items", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            jp = item.get("_jobposting") or {}
            locs = jp.get("jobLocation") or []
            is_remote = "remote" in title.lower() or "zdaln" in title.lower()

            in_scope = False
            matched_loc = None
            for l in locs:
                addr = l.get("address") or {}
                c_code = addr.get("addressCountry") or ""
                locality = addr.get("addressLocality") or ""
                if c_code.upper() == "PL" or is_pl_location(f"{locality} {c_code}", c_code):
                    if is_remote or matches_target_city(locality, target_city):
                        in_scope = True
                        matched_loc = locality
                        break

            if not in_scope:
                continue

            desc = strip_html(item.get("content_html") or "")
            emp = jp.get("employmentType")
            hiring_org = (jp.get("hiringOrganization") or {}).get("name")

            jobs.append(Job(
                title=title,
                company=hiring_org or feed_title,
                link=item.get("url") or "",
                description=desc,
                source="ATS Feeds",
                location=matched_loc or target_city,
                posted_date=item.get("date_published"),
                seniority=norm_seniority(title),
                work_modes=["remote"] if is_remote else ["onsite"],
                contract_types=norm_contracts(emp),
                schedules=norm_schedules(emp),
                category=jp.get("occupationalCategory"),
            ))
        return jobs


class WorkableFeed(BaseATSHandler):
    ATS_NAME = "Workable"

    def get_jobs(self, slug: str, target_city: str, timeout: int = 10) -> List[Job]:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        data = self.fetch_json(url, timeout=timeout)
        if not data or not isinstance(data, dict):
            return []

        jobs: List[Job] = []
        account_name = data.get("name") or slug.replace("-", " ").capitalize()

        for item in data.get("jobs", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue

            city = item.get("city") or ""
            country = item.get("country") or ""
            is_remote = bool(item.get("telecommuting"))
            locs = item.get("locations") or []
            has_pl = country.lower() == "poland" or any(l.get("countryCode") == "PL" for l in locs)

            in_scope = False
            if has_pl:
                if is_remote:
                    in_scope = True
                elif matches_target_city(city, target_city):
                    in_scope = True

            if not in_scope:
                continue

            # Workable: detail call wykonujemy TYLKO dla ofert, które zakwalifikowały się do Polski/remote
            shortcode = item.get("shortcode")
            det_url = f"https://apply.workable.com/api/v1/accounts/{slug}/jobs/{shortcode}"
            det = self.fetch_json(det_url, timeout=timeout) or {}

            desc = strip_html(det.get("description") or "") + "\n\n" + strip_html(det.get("requirements") or "")
            emp = item.get("employment_type")

            jobs.append(Job(
                title=title,
                company=account_name,
                link=item.get("url") or item.get("shortlink") or f"https://apply.workable.com/j/{shortcode}",
                description=desc,
                source="ATS Feeds",
                location=city or target_city,
                posted_date=item.get("published_on") or item.get("created_at"),
                seniority=norm_seniority(title),
                work_modes=["remote"] if is_remote else ["onsite"],
                contract_types=norm_contracts(emp),
                schedules=norm_schedules(emp),
                category=item.get("department") or item.get("industry"),
            ))
        return jobs


# Mapa platform
ATS_HANDLERS: Dict[str, type[BaseATSHandler]] = {
    "greenhouse": GreenhouseFeed,
    "lever": LeverFeed,
    "ashby": AshbyFeed,
    "smartrecruiters": SmartRecruitersFeed,
    "recruitee": RecruiteeFeed,
    "teamtailor": TeamtailorFeed,
    "workable": WorkableFeed,
}


# =====================================================================
# Główny Scraper Feederowy ATS
# =====================================================================

class ATSFeedsScraper(BaseScraper):
    """
    Główny koordynator pobierania ofert z feedów ATS.
    Zgodny z architekturą BaseScraper i pipeline_manager.
    """

    SOURCE_NAME = "ATS Feeds"

    def __init__(self, config: dict):
        super().__init__(config)
        ats_cfg = config.get("ats_feeds", {}) or {}

        # Budżet slugów i czasu na jeden przebieg
        self.max_slugs_per_run: int = ats_cfg.get("max_slugs_per_run", 150)
        self.time_budget_seconds: int = ats_cfg.get("time_budget_seconds", 180)
        self.request_timeout: int = ats_cfg.get("request_timeout", 8)
        self.max_workers: int = ats_cfg.get("max_workers", 8)
        self.delay_between_requests: float = ats_cfg.get("delay_between_requests", 0.05)
        self.enabled_ats: List[str] = ats_cfg.get(
            "enabled_ats",
            ["greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "teamtailor", "workable"]
        )

        self.enrich_stats = {"ok": 0, "error": 0, "blocked": 0, "ze_stanu": 0}
        self.seen_again_links: List[str] = []
        self._host_locks: Dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def get_source_name(self) -> str:
        return self.SOURCE_NAME

    def scrape_jobs(self) -> List[Job]:
        """Implementacja metody abstrakcyjnej BaseScraper (nieużywana bez przeglądarki)."""
        return []

    def _load_or_fetch_slugs(self) -> Dict[str, List[str]]:
        """
        Ładuje listę slugów z lokalnego cache lub pobiera z GitHub raw (Feashliaa/job-board-aggregator).
        Repozytorium Feashliaa/job-board-aggregator jest licencjonowane pod MIT License.
        """
        now = time.time()
        cached = load_json_safe(str(CACHE_FILE), default=None)
        if cached and isinstance(cached, dict):
            cached_at = cached.get("fetched_at_epoch", 0)
            # Cache ważny przez 7 dni
            if now - cached_at < 7 * 86400 and cached.get("slugs"):
                logger.info(f"{self.SOURCE_NAME}: załadowano slugi z lokalnego cache ({CACHE_FILE.name})")
                return cached["slugs"]

        # Pobieranie z GitHub raw
        logger.info(f"{self.SOURCE_NAME}: pobieranie bazy slugów z GitHub raw (Feashliaa/job-board-aggregator, MIT License)...")
        slugs_by_ats: Dict[str, List[str]] = {}

        for ats, url in UPSTREAM_SLUG_URLS.items():
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    raw_data = resp.read().decode("utf-8")
                    parsed = json.loads(raw_data)
                    if isinstance(parsed, list):
                        slugs_by_ats[ats] = parsed
            except Exception as e:
                logger.warning(f"{self.SOURCE_NAME}: błąd pobierania slugów {ats}: {e}")
                if cached and cached.get("slugs", {}).get(ats):
                    slugs_by_ats[ats] = cached["slugs"][ats]
                else:
                    slugs_by_ats[ats] = VERIFIED_POLISH_SEEDS.get(ats, [])

        # Dla ATS bez bazy upstream (SmartRecruiters, Recruitee, Teamtailor, Workable) używamy ziaren
        for ats in ("smartrecruiters", "recruitee", "teamtailor", "workable"):
            if cached and cached.get("slugs", {}).get(ats):
                slugs_by_ats[ats] = cached["slugs"][ats]
            else:
                slugs_by_ats[ats] = list(VERIFIED_POLISH_SEEDS.get(ats, []))

        # Zapisz cache
        save_json_atomic(str(CACHE_FILE), {
            "fetched_at": datetime.now().isoformat(),
            "fetched_at_epoch": now,
            "license": "MIT License (Riley Dorrington, https://github.com/Feashliaa/job-board-aggregator)",
            "slugs": slugs_by_ats,
        })
        return slugs_by_ats

    def _load_state(self) -> Dict[str, Any]:
        state = load_json_safe(str(STATE_FILE), default=None)
        if not state or not isinstance(state, dict):
            state = {
                "polish_slugs": {k: list(v) for k, v in VERIFIED_POLISH_SEEDS.items()},
                "rotation_cursors": {k: 0 for k in ATS_HANDLERS},
                "last_run": None,
            }
        # Upewnij się, że zweryfikowane ziarna są zawsze obecne
        for ats, seeds in VERIFIED_POLISH_SEEDS.items():
            current = state.setdefault("polish_slugs", {}).setdefault(ats, [])
            for s in seeds:
                if s not in current:
                    current.append(s)
        state.setdefault("rotation_cursors", {})
        return state

    def _save_state(self, state: Dict[str, Any]):
        state["last_run"] = datetime.now().isoformat()
        save_json_atomic(str(STATE_FILE), state)

    def _select_slugs_to_scrape(
        self, all_slugs: Dict[str, List[str]], state: Dict[str, Any]
    ) -> List[Tuple[str, str, bool]]:
        """
        Zwraca listę krotek (ats_name, slug, is_active).
        Aktywne slugi (z ofertami w PL) są badane zawsze, reszta rotacyjnie
        w wycinku dobowym (pełny sweep w ~7 dni).
        """
        polish_slugs = state.get("polish_slugs", {})
        cursors = state.get("rotation_cursors", {})

        scheduled: List[Tuple[str, str, bool]] = []

        # 1. Zawsze dodaj znane polskie slugi
        for ats in self.enabled_ats:
            for slug in polish_slugs.get(ats, []):
                scheduled.append((ats, slug, True))

        # 2. Rotacyjny wycinek pozostałych slugów
        remaining_budget = max(0, self.max_slugs_per_run - len(scheduled))
        if remaining_budget > 0 and self.enabled_ats:
            per_ats_slice = max(1, remaining_budget // len(self.enabled_ats))
            for ats in self.enabled_ats:
                pool = all_slugs.get(ats, [])
                known = set(polish_slugs.get(ats, []))
                untested = [s for s in pool if s not in known]
                if not untested:
                    continue

                cur = cursors.get(ats, 0) % len(untested)
                # Dzienny plasterek (1/7 puli albo per_ats_slice)
                slice_len = min(per_ats_slice, len(untested))
                end = cur + slice_len
                if end <= len(untested):
                    selected = untested[cur:end]
                    cursors[ats] = end % len(untested)
                else:
                    selected = untested[cur:] + untested[: (end % len(untested))]
                    cursors[ats] = end % len(untested)

                for slug in selected:
                    scheduled.append((ats, slug, False))

        return scheduled[: self.max_slugs_per_run]

    def run(self) -> List[Job]:
        """Główna pętla wykonania scrapera ATS."""
        start_time = time.time()
        target_city = scope_city()
        logger.info(f"{self.SOURCE_NAME}: uruchamianie feedów dla miasta docelowego: {target_city}...")

        all_slugs = self._load_or_fetch_slugs()
        state = self._load_state()
        work_items = self._select_slugs_to_scrape(all_slugs, state)

        total_items = len(work_items)
        logger.info(f"{self.SOURCE_NAME}: zaplanowano {total_items} firm do odpytania (budżet: {self.max_slugs_per_run} firm, {self.time_budget_seconds}s)")

        jobs: List[Job] = []
        new_polish_found: Dict[str, Set[str]] = {ats: set() for ats in ATS_HANDLERS}
        completed_count = 0
        error_count = 0

        # Inicjalizacja adapterów
        handlers = {ats: ATS_HANDLERS[ats]() for ats in self.enabled_ats if ats in ATS_HANDLERS}

        def process_slug(item: Tuple[str, str, bool]) -> Tuple[str, str, List[Job], bool]:
            ats, slug, is_active = item
            handler = handlers.get(ats)
            if not handler:
                return ats, slug, [], False

            # Uprzejme tempo zapytań
            if self.delay_between_requests > 0:
                time.sleep(self.delay_between_requests)

            try:
                res_jobs = handler.get_jobs(slug, target_city, timeout=self.request_timeout)
                return ats, slug, res_jobs, True
            except Exception as e:
                logger.debug(f"{self.SOURCE_NAME} [{ats}/{slug}] error: {e}")
                return ats, slug, [], False

        # Wykonanie równoległe z ograniczeniem czasu
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_item = {executor.submit(process_slug, item): item for item in work_items}

            for future in as_completed(future_to_item):
                completed_count += 1
                try:
                    ats, slug, found_jobs, ok = future.result()
                    if ok:
                        if found_jobs:
                            jobs.extend(found_jobs)
                            new_polish_found[ats].add(slug)
                    else:
                        error_count += 1
                except Exception:
                    error_count += 1

                # Telemetria postępu w standardowym formacie pipeline'u:
                # "ATS Feeds: X/Y descriptions"
                if completed_count % 25 == 0 or completed_count == total_items:
                    logger.info(f"{self.SOURCE_NAME}: {completed_count}/{total_items} descriptions")

                # Sprawdzenie budżetu czasowego
                elapsed = time.time() - start_time
                if elapsed > self.time_budget_seconds:
                    logger.warning(
                        f"{self.SOURCE_NAME}: osiągnięto budżet czasowy ({elapsed:.1f}s > {self.time_budget_seconds}s) "
                        f"- przerwano po zbadaniu {completed_count}/{total_items} firm"
                    )
                    break

        # Podsumowanie telemetrii
        logger.info(f"{self.SOURCE_NAME}: fetched {completed_count}/{total_items} descriptions")

        # Aktualizacja stanu: dopisanie nowo odkrytych firm z ofertami w Polsce
        polish_dict = state.setdefault("polish_slugs", {})
        added_new_slugs = 0
        for ats, found_set in new_polish_found.items():
            cur_list = polish_dict.setdefault(ats, [])
            for s in found_set:
                if s not in cur_list:
                    cur_list.append(s)
                    added_new_slugs += 1

        self._save_state(state)
        if added_new_slugs > 0:
            logger.info(f"{self.SOURCE_NAME}: dopisano {added_new_slugs} nowych firm zatrudniających w PL do stanu")

        # Statystyki enrich_stats dla scraper_health
        self.enrich_stats["ok"] = len(jobs)
        self.enrich_stats["ze_stanu"] = len(jobs)
        self.enrich_stats["error"] = error_count

        logger.info(f"{self.SOURCE_NAME}: zakończono przebieg — {len(jobs)} ofert w Polsce/remote z {completed_count} firm")
        return jobs
