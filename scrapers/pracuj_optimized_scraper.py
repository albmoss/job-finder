"""
Scraper Pracuj.pl na urllib i JSON.

Metoda:
1. Listing: pobiera strony wyszukiwania (urllib + __NEXT_DATA__ JSON),
   wyciągając cechy strukturalne (seniority z positionLevels, contract_types,
   schedules, work_modes, salaryDisplayText -> salary, daty publikacji i wygaśnięcia,
   technologie jeśli obecne) oraz aiSummary jako opis zapasowy.
2. Szczegóły (dla NOWYCH ofert, z pominięciem znanych w bazie przez skip_known_details):
   pobieranie urllib + browser headers (200 w ~0.4 s, requests dostaje 403),
   parsowanie sekcji __NEXT_DATA__:
   pełny opis (zakres obowiązków, wymagania, oferujemy, benefity, o projekcie),
   technologies-expected -> skills_required,
   technologies-optional i requirements-optional -> skills_nice,
   kategoria.
   Pacing >= 0.5s, backoff na 403/429, zatrzymanie po serii blokad
   i fallback do listing aiSummary przy błędzie.
3. Zakres: miasto z scope_city(), kody poziomów z scope_levels() zmapowane
   na kody `et` portalu.
"""

import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from utils.candidate_scope import city_slug, scope_city, scope_levels
from utils.data_models import Job
from utils.links import canonical_link, logo_url
from utils.offer_fields import (
    make_salary,
    norm_contracts,
    norm_schedules,
    norm_skills,
    norm_seniority,
    norm_work_modes,
    salary_from_text,
    years_from_text,
)

logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
]

# Mapowanie kodów SENIORITY z utils.offer_fields na kody `et` Pracuj.pl zweryfikowane na żywo:
# 1 = Praktykant / Stażysta (intern)
# 3 = Asystent (junior)
# 17 = Młodszy specjalista / Junior (junior)
# 4 = Specjalista / Mid / Regular (mid)
# 18 = Starszy specjalista / Senior (senior)
# 19 = Ekspert (senior)
# 5 = Kierownik / Koordynator (lead, manager)
# 20 = Menedżer (manager)
# 6 = Dyrektor (manager)
# 21 = Prezes (manager)
# 2 = Pracownik fizyczny
SENIORITY_TO_ET: dict[str, list[int]] = {
    "intern": [1],
    "junior": [3, 17],
    "mid": [4],
    "senior": [18, 19],
    "lead": [5],
    "manager": [5, 6, 20, 21],
}

# Źródło: https://www.pracuj.pl __NEXT_DATA__ dictionary.categories (sprawdzono: 2026-09-30)
PRACUJ_CATEGORIES: dict[str, str] = {
    "5001": "Administracja biurowa",
    "5002": "Badania i rozwój",
    "5003": "Bankowość",
    "5004": "BHP / Ochrona środowiska",
    "5005": "Budownictwo",
    "5006": "Call Center",
    "5007": "Edukacja / Szkolenia",
    "5008": "Finanse / Ekonomia",
    "5009": "Franczyza / Własny biznes",
    "5010": "Hotelarstwo / Gastronomia / Turystyka",
    "5011": "Human Resources / Zasoby ludzkie",
    "5012": "Inne",
    "5013": "Internet / e-Commerce / Nowe media",
    "5014": "Inżynieria",
    "5015": "IT - Administracja",
    "5016": "IT - Rozwój oprogramowania",
    "5017": "Łańcuch dostaw",
    "5018": "Marketing",
    "5019": "Media / Sztuka / Rozrywka",
    "5020": "Nieruchomości",
    "5021": "Obsługa klienta",
    "5022": "Praca fizyczna",
    "5023": "Prawo",
    "5024": "Produkcja",
    "5025": "Public Relations",
    "5026": "Reklama / Grafika / Kreacja / Fotografia",
    "5027": "Sektor publiczny",
    "5028": "Sprzedaż",
    "5031": "Transport / Spedycja / Logistyka",
    "5032": "Ubezpieczenia",
    "5033": "Zakupy",
    "5034": "Kontrola jakości",
    "5035": "Zdrowie / Uroda / Rekreacja",
    "5036": "Energetyka",
    "5037": "Doradztwo / Konsulting",
}


def fetch_pracuj_categories() -> dict[str, str]:
    """Pobiera słownik kategorii z __NEXT_DATA__ strony głównej Pracuj.pl, z fallbackiem do słownika statycznego."""
    try:
        req = urllib.request.Request(
            "https://www.pracuj.pl",
            headers={
                'User-Agent': random.choice(USER_AGENTS),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.8',
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if script and script.string:
            data = json.loads(script.string)
            queries = data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
            for q in queries:
                qk = q.get('queryKey', [])
                if qk and qk[0] == 'dictionary' and len(qk) > 1 and qk[1] == 'categories':
                    cats_data = q.get('state', {}).get('data', [])
                    cats = {str(c['id']): c['name'] for c in cats_data if c.get('level') == 1 and 'id' in c and 'name' in c}
                    if cats:
                        return cats
    except Exception as e:
        logger.debug(f"Pracuj.pl: nie udało się pobrać kategorii dynamicznie ({e}) - używam słownika statycznego")
    return PRACUJ_CATEGORIES


class PracujOptimizedScraper:
    """Scraper na urllib i JSON, bez Playwrighta.

    Wystawia ten sam interfejs: get_source_name() i run().
    """

    BASE_URL = "https://www.pracuj.pl"
    PROGRESS_EVERY = 50

    def __init__(self, config: dict):
        self.config = config
        cfg = config.get("pracuj_pl", {}) or {}
        self.cfg = cfg

        city = scope_city(default=cfg.get("location") or config.get("location", "Warszawa"))
        self.city = city
        self.city_slug = cfg.get("city_slug") or city_slug(city)

        levels = scope_levels()
        et_codes = sorted({code for lvl in levels for code in SENIORITY_TO_ET.get(lvl, [])})
        if not et_codes:
            et_codes = [1, 3, 17]
        self.et_param = ",".join(str(c) for c in et_codes)
        self.days = cfg.get("days_param", 7)

        catalog = fetch_pracuj_categories()
        from utils.portal_categories import pick_categories
        picked = pick_categories("pracuj", catalog)
        if picked is not None:
            self.categories: Optional[List[str]] = picked
            self.cc_param = ",".join(str(c) for c in sorted(picked))
            logger.info(f"Pracuj.pl: {len(picked)} categories from CV")
        else:
            self.categories = None
            self.cc_param = ""

        self.parallel_threads = cfg.get("parallel_threads", 3)
        self.detail_threads = cfg.get("detail_threads", 3)
        self.min_request_interval = 0.5  # sekundy między żądaniami (globalnie)

        self.skip_known_details = cfg.get("skip_known_details", True)
        self.seen_again_links = []
        self.enrich_stats = None

        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0
        self._consecutive_blocks = 0
        self._block_limit = 10
        self._detail_aborted = False

    def _throttle(self):
        """Globalny throttle - odstęp między żądaniami niezależnie od liczby wątków."""
        with self._rate_lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                time.sleep(wait)
                now = time.monotonic()

            delta = now - self._last_request_at
            if delta < self.min_request_interval:
                time.sleep(self.min_request_interval - delta)

            self._last_request_at = time.monotonic()

    def _trigger_cooldown(self, seconds: float):
        """Zatrzymaj wszystkie wątki po napotkaniu 429 lub 403."""
        with self._rate_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def get_source_name(self) -> str:
        return "Pracuj.pl"

    def build_search_url(self, page: int = 1) -> str:
        url = f"{self.BASE_URL}/praca/{self.city_slug};wp?et={self.et_param}&itth={self.days}&pn={page}"
        if self.cc_param:
            url += f"&cc={self.cc_param}"
        return url

    def fetch_html(self, url: str, max_attempts: int = 3, is_detail: bool = False) -> str:
        if is_detail and self._detail_aborted:
            return ""

        for attempt in range(max_attempts):
            self._throttle()

            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.8',
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    content = response.read().decode('utf-8', errors='ignore')
                    with self._rate_lock:
                        self._consecutive_blocks = 0
                    return content

            except urllib.error.HTTPError as e:
                if e.code == 429 and is_detail:
                    # Strony ofert mają osobny, ciasny limit: po ~50 wejściach każde
                    # kolejne dostaje 429 z Retry-After >= 120 s, więc czekanie znaczy
                    # dwie minuty na ofertę. Reszta idzie z danych listingu.
                    with self._rate_lock:
                        if not self._detail_aborted:
                            self._detail_aborted = True
                            logger.warning(
                                "Pracuj.pl: HTTP 429 on offer pages - stopping detail fetch "
                                "and falling back to listing summaries"
                            )
                    return ""

                if e.code == 429:
                    retry_after = e.headers.get('Retry-After') if e.headers else None
                    try:
                        wait = float(retry_after) if retry_after else None
                    except (TypeError, ValueError):
                        wait = None
                    if wait is None:
                        wait = (5 * (2 ** attempt)) + random.random() * 2
                    wait = min(wait, 120)
                    logger.warning(f"Pracuj.pl: HTTP 429 on {url[:60]} - cooldown {wait:.0f}s (attempt {attempt+1}/{max_attempts})")
                    self._trigger_cooldown(wait)
                    continue

                if e.code == 403:
                    with self._rate_lock:
                        self._consecutive_blocks += 1
                        if is_detail and self._consecutive_blocks >= self._block_limit:
                            if not self._detail_aborted:
                                self._detail_aborted = True
                                logger.warning(
                                    f"Pracuj.pl: {self._consecutive_blocks} consecutive 403 blocks - "
                                    f"stopping detail fetch and falling back to listing summaries"
                                )
                    wait = min(8.0 * (1.5 ** attempt), 60.0)
                    logger.warning(f"Pracuj.pl: HTTP 403 on {url[:60]} - cooldown {wait:.0f}s (attempt {attempt+1}/{max_attempts})")
                    self._trigger_cooldown(wait)
                    if is_detail and self._detail_aborted:
                        return ""
                    continue

                if e.code == 404:
                    return ""

                logger.warning(f"Pracuj.pl: HTTP {e.code} on {url[:60]} (attempt {attempt+1}/{max_attempts})")

            except Exception as e:
                logger.warning(f"Pracuj.pl: attempt {attempt+1}/{max_attempts} failed on {url[:60]}: {e}")

            if attempt < max_attempts - 1:
                time.sleep((2 ** attempt) + random.random())

        return ""

    def fetch_page_html(self, page_num: int) -> str:
        return self.fetch_html(self.build_search_url(page_num))

    def parse_listing_page(self, page_num: int) -> List[dict]:
        """Pobiera i parsuje oferty z pojedynczej strony listingu."""
        html = self.fetch_page_html(page_num)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if not script or not script.string:
            logger.warning(f"Pracuj.pl: No __NEXT_DATA__ found on page {page_num}")
            return []

        try:
            data = json.loads(script.string)
            found_offers = []
            queries = data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
            for q in queries:
                qk = q.get('queryKey', [])
                if qk and qk[0] == 'jobOffers':
                    found_offers = q.get('state', {}).get('data', {}).get('groupedOffers', [])
                    break

            if not found_offers:
                def search_all_keys(d):
                    if isinstance(d, dict):
                        if 'jobTitle' in d and 'companyName' in d and ('offers' in d or 'offerAbsoluteUri' in d):
                            found_offers.append(d)
                            return
                        for v in d.values():
                            if isinstance(v, (dict, list)):
                                search_all_keys(v)
                    elif isinstance(d, list):
                        for item in d:
                            if isinstance(item, (dict, list)):
                                search_all_keys(item)
                search_all_keys(data)

            parsed_items = []
            for o in found_offers:
                title = (o.get('jobTitle') or '').strip()
                company = (o.get('companyName') or '').strip()
                if not title:
                    continue

                seniority = norm_seniority(o.get('positionLevels'))
                contract_types = norm_contracts(o.get('typesOfContract'))
                schedules = norm_schedules(o.get('workSchedules'))
                modes = list(o.get('workModes') or [])
                work_modes = norm_work_modes(modes)
                if o.get('isRemoteWorkAllowed'):
                    if not work_modes:
                        work_modes = ['remote']
                    elif 'remote' not in work_modes:
                        work_modes.append('remote')

                salary = salary_from_text(o.get('salaryDisplayText'))
                posted_date = str(o.get('initialPublicated') or o.get('lastPublicated') or '') or None
                valid_through = str(o.get('expirationDate') or '') or None
                skills_required = norm_skills(o.get('technologies'))

                ai_summary = o.get('aiSummary')
                if ai_summary:
                    fallback_desc = BeautifulSoup(ai_summary, 'html.parser').get_text(separator="\n", strip=True)
                elif o.get('jobDescription'):
                    fallback_desc = BeautifulSoup(o['jobDescription'], 'html.parser').get_text(separator="\n", strip=True)
                else:
                    fallback_desc = "Brak opisu"

                sub_offers = o.get('offers') or [{}]
                for so in sub_offers:
                    uri = so.get('offerAbsoluteUri') or o.get('offerAbsoluteUri')
                    if not uri:
                        continue
                    if not uri.startswith('http'):
                        uri = self.BASE_URL + uri

                    workplace = so.get('displayWorkplace')
                    if not workplace:
                        workplaces = o.get('displayWorkplaces') or []
                        workplace = workplaces[0] if workplaces else self.city

                    parsed_items.append({
                        'title': title,
                        'company': company,
                        'link': uri,
                        'location': workplace.strip(),
                        'seniority': seniority,
                        'contract_types': contract_types,
                        'schedules': schedules,
                        'work_modes': work_modes,
                        'salary': salary,
                        'posted_date': posted_date,
                        'valid_through': valid_through,
                        'skills_required': skills_required,
                        'fallback_description': fallback_desc,
                        'logo_url': logo_url(o.get('companyLogoUri')),
                    })

            logger.info(f"Pracuj.pl: Page {page_num}: {len(parsed_items)} offers found")
            return parsed_items

        except Exception as e:
            logger.error(f"Pracuj.pl: Failed to parse __NEXT_DATA__ JSON on page {page_num}: {e}")
            return []

    def fetch_all_listings(self) -> List[dict]:
        """Pobiera wszystkie oferty z listingu wielowątkowo."""
        all_items: List[dict] = []

        logger.info("Pracuj.pl: Fetching page 1 to discover listings...")
        first_page_items = self.parse_listing_page(1)
        all_items.extend(first_page_items)

        if not first_page_items:
            logger.warning("Pracuj.pl: No jobs found on first page")
            return []

        total_pages = 100
        logger.info(f"Pracuj.pl: Scanning up to {total_pages} pages with {self.parallel_threads} threads...")

        consecutive_empty = 0
        current_page = 2

        with ThreadPoolExecutor(max_workers=self.parallel_threads) as executor:
            while current_page <= total_pages and consecutive_empty < 3:
                batch_size = self.parallel_threads
                page_batch = list(range(current_page, min(current_page + batch_size, total_pages + 1)))

                results = list(executor.map(self.parse_listing_page, page_batch))

                batch_found_jobs = False
                for result in results:
                    if result and len(result) > 0:
                        all_items.extend(result)
                        batch_found_jobs = True
                        consecutive_empty = 0

                if not batch_found_jobs:
                    consecutive_empty += 1

                current_page += batch_size

        return all_items

    @staticmethod
    def extract_detail_from_html(html: str) -> Optional[dict]:
        """Parsuje stronę szczegółów __NEXT_DATA__ -> pełny opis, umiejętności i kategoria."""
        if not html:
            return None
        try:
            soup = BeautifulSoup(html, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            if not script or not script.string:
                return None
            data_all = json.loads(script.string)
            queries = data_all.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
            dq0 = next((q for q in queries if q.get('queryKey', []) and q['queryKey'][0] == 'jobOffer'), None)
            if not dq0:
                return None
            data = dq0.get('state', {}).get('data', {})

            sections = data.get('sections', [])
            desc_lines = []
            skills_req = []
            skills_nice = []

            for sec in sections:
                stype = sec.get('sectionType')
                title = sec.get('title') or ""
                m = sec.get('model') or {}
                subs = sec.get('subSections') or []

                if stype == 'technologies':
                    for sub in subs:
                        subtype = sub.get('sectionType')
                        sm = sub.get('model') or {}
                        items = sm.get('customItems', []) + sm.get('items', [])
                        names = [it.get('name') for it in items if isinstance(it, dict) and it.get('name')]
                        if subtype == 'technologies-expected':
                            skills_req.extend(names)
                        elif subtype == 'technologies-optional':
                            skills_nice.extend(names)
                    continue

                sec_text = []
                if 'bullets' in m and m['bullets']:
                    sec_text.extend(f"• {b}" for b in m['bullets'] if b)
                if 'paragraphs' in m and m['paragraphs']:
                    sec_text.extend(p for p in m['paragraphs'] if p)
                if 'customItems' in m and m['customItems']:
                    names = [it.get('name') for it in m['customItems'] if isinstance(it, dict) and it.get('name')]
                    if names:
                        sec_text.extend(f"• {n}" for n in names)

                for sub in subs:
                    subtype = sub.get('sectionType')
                    sub_title = sub.get('title') or ""
                    sm = sub.get('model') or {}
                    sub_bullets = sm.get('bullets') or []
                    sub_paras = sm.get('paragraphs') or []
                    sub_items = sm.get('customItems', []) + sm.get('items', [])

                    if subtype == 'requirements-optional' or 'mile widziane' in sub_title.lower():
                        for b in sub_bullets:
                            cleaned_b = re.sub(
                                r"^(?:znajomo[sś][cć]|wiedza z zakresu|do[sś]wiadczenie z|mile widziane:?)\s*",
                                "", b, flags=re.I
                            ).strip()
                            if cleaned_b and len(cleaned_b.split()) <= 4 and len(cleaned_b) <= 35 and not cleaned_b.endswith((".", "!")):
                                skills_nice.append(cleaned_b)
                        for it in sub_items:
                            if isinstance(it, dict) and it.get('name'):
                                skills_nice.append(it['name'])

                    sub_lines = []
                    if sub_bullets:
                        sub_lines.extend(f"• {b}" for b in sub_bullets if b)
                    if sub_paras:
                        sub_lines.extend(p for p in sub_paras if p)
                    if sub_items:
                        names = [it.get('name') for it in sub_items if isinstance(it, dict) and it.get('name')]
                        if names:
                            sub_lines.extend(f"• {n}" for n in names)

                    if sub_lines:
                        if sub_title.strip():
                            sec_text.append(f"\n{sub_title.strip()}:")
                        sec_text.extend(sub_lines)

                if sec_text:
                    if title.strip():
                        desc_lines.append(f"\n{title.strip()}:")
                    desc_lines.extend(sec_text)

            full_desc = "\n".join(desc_lines).strip()

            category = None
            attrs = data.get('attributes', {})
            if attrs.get('categories'):
                cat_names = [c.get('name') for c in attrs['categories'] if isinstance(c, dict) and c.get('name')]
                category = ", ".join(cat_names) if cat_names else None
            if not category:
                for sc in soup.find_all('script', type='application/ld+json'):
                    try:
                        ld = json.loads(sc.string)
                        if ld.get('@type') == 'JobPosting' and ld.get('industry'):
                            category = ld['industry']
                            break
                    except Exception:
                        pass

            pub = data.get('publicationDetails', {})
            posted_date = pub.get('dateOfInitialPublicationUtc') or pub.get('dateOfLastPublicationUtc')
            valid_through = pub.get('expirationDateUtc')

            detail_salary = None
            for sc in soup.find_all('script', type='application/ld+json'):
                try:
                    ld = json.loads(sc.string)
                    if ld.get('@type') == 'JobPosting' and ld.get('baseSalary'):
                        from scrapers.ldjson_scraper_base import LdJsonPortalScraper
                        detail_salary = LdJsonPortalScraper._parse_salary(ld)
                        if detail_salary:
                            break
                except Exception:
                    pass

            return {
                'description': full_desc or None,
                'skills_required': norm_skills(skills_req),
                'skills_nice': norm_skills(skills_nice),
                'category': category,
                'posted_date': str(posted_date) if posted_date else None,
                'valid_through': str(valid_through) if valid_through else None,
                'salary': detail_salary,
            }
        except Exception as e:
            logger.debug(f"Pracuj.pl: Error extracting detail: {e}")
            return None

    def run(self) -> List[Job]:
        cat_info = f", cc={self.cc_param}" if self.cc_param else ""
        logger.info(f"{self.get_source_name()}: Starting scrape (lokalizacja: {self.city}, et={self.et_param}{cat_info})")
        all_items = self.fetch_all_listings()
        if not all_items:
            logger.warning(f"{self.get_source_name()}: No offers found")
            return []

        # Deduplikacja ofert z listingu po kanonicznym linku
        unique_by_link = {}
        for it in all_items:
            c_link = canonical_link(it['link'])
            if c_link not in unique_by_link:
                unique_by_link[c_link] = it

        all_links = list(unique_by_link.keys())

        if self.skip_known_details:
            from utils.known_links import split_known
            links_to_fetch, self.seen_again_links = split_known(all_links)
            logger.info(
                f"{self.get_source_name()}: {len(links_to_fetch)} new to fetch, "
                f"{len(self.seen_again_links)} already in the database (their pages are skipped)"
            )
        else:
            links_to_fetch = all_links
            self.seen_again_links = []

        if not links_to_fetch:
            self.enrich_stats = {
                "ok": 0,
                "error": 0,
                "blocked": 0,
                "ze_stanu": len(self.seen_again_links),
            }
            return []

        def _fetch_one_detail(link: str) -> tuple[str, Optional[dict]]:
            html = self.fetch_html(link, max_attempts=2, is_detail=True)
            detail = self.extract_detail_from_html(html) if html else None
            return link, detail

        jobs: List[Job] = []
        ok = 0
        fallback_count = 0
        started = time.monotonic()
        total = len(links_to_fetch)

        with ThreadPoolExecutor(max_workers=self.detail_threads) as executor:
            for done, (link, detail) in enumerate(executor.map(_fetch_one_detail, links_to_fetch), 1):
                if done % self.PROGRESS_EVERY == 0 and done < total:
                    rate = done / max(time.monotonic() - started, 1e-6)
                    logger.info(
                        f"{self.get_source_name()}: {done}/{total} descriptions - "
                        f"{rate * 60:.0f}/min, ~{(total - done) / rate / 60:.0f} min left"
                    )

                item = unique_by_link[link]
                if detail and detail.get('description'):
                    ok += 1
                    desc = detail['description']
                    skills_req = norm_skills((item.get('skills_required') or []) + (detail.get('skills_required') or []))
                    skills_nice = detail.get('skills_nice')
                    category = detail.get('category')
                    posted_date = detail.get('posted_date') or item.get('posted_date')
                    valid_through = detail.get('valid_through') or item.get('valid_through')
                    salary = item.get('salary') or detail.get('salary')
                else:
                    fallback_count += 1
                    desc = item.get('fallback_description') or "Brak opisu"
                    skills_req = item.get('skills_required')
                    skills_nice = None
                    category = None
                    posted_date = item.get('posted_date')
                    valid_through = item.get('valid_through')
                    salary = item.get('salary')

                now = datetime.now().isoformat()
                job = Job(
                    title=item['title'],
                    company=item['company'],
                    link=link,
                    description=desc,
                    source=self.get_source_name(),
                    location=item['location'],
                    posted_date=posted_date,
                    valid_through=valid_through,
                    scraped_at=now,
                    last_seen=now,
                    seniority=item.get('seniority'),
                    work_modes=item.get('work_modes'),
                    contract_types=item.get('contract_types'),
                    schedules=item.get('schedules'),
                    salary=salary,
                    skills_required=skills_req,
                    skills_nice=skills_nice,
                    category=category,
                    logo_url=item.get('logo_url'),
                )
                jobs.append(job)

        logger.info(
            f"{self.get_source_name()}: fetched {ok}/{total} descriptions, "
            f"dropped 0 (out of scope) -> {len(jobs)} offers"
        )
        with self._rate_lock:
            blocked = self._consecutive_blocks

        self.enrich_stats = {
            "ok": ok,
            "error": fallback_count,
            "blocked": blocked,
            "ze_stanu": len(self.seen_again_links),
        }
        return jobs
