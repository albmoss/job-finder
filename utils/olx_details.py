"""
Pobieranie pełnych opisów ofert z OLX.

Kontekst: OLXScraper zapisywał jako opis zaślepkę
"Oferta z OLX (kategoria: <kategoria>)" - czyli ~25% bazy trafiało do analizy AI
z zerową treścią, a model oceniał je wyłącznie po tytule.

Strona oferty OLX osadza cały stan w `window.__PRERENDERED_STATE__` (JSON w stringu).
Dla ogłoszeń o pracę interesuje nas `jobAd.job`, gdzie jest:
  description (HTML), salary (widełki), params (wymiar pracy, typ umowy), employer.
Nie trzeba przeglądarki - zwykły GET wystarczy.
"""

import json
import logging
import random
import re
import threading
import time

import requests

from utils.links import canonical_link

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Wynik oznaczający, że oferty już nie ma (wygasła / usunięta)
EXPIRED = "__EXPIRED__"


class _Throttle:
    """Globalny odstęp między żądaniami, wspólny dla wszystkich wątków."""

    def __init__(self, min_interval=0.4):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta + random.random() * 0.15)
            self._last = time.monotonic()


throttle = _Throttle()


def normalize_olx_link(link: str) -> str:
    """Alias na wspólną normalizację - patrz utils/links.py."""
    return canonical_link(link)


def _extract_state(html: str) -> dict:
    m = _STATE_RE.search(html)
    if not m:
        return {}
    try:
        # Podwójne kodowanie: JSON string, w środku JSON
        return json.loads(json.loads(m.group(1)))
    except Exception as e:
        logger.debug(f"OLX: could not parse PRERENDERED_STATE: {e}")
        return {}


def build_description(job: dict, category: str = "") -> str:
    """Opis oferty OLX - czysty tekst ogłoszenia, bez doklejania parametrów."""
    return (job.get("description") or "").strip()


def parse_olx_fields(ad: dict) -> dict:
    """
    Wyciągnij strukturalne pola oferty z obiektu ogłoszenia OLX (stan lub podstrona).
    """
    from utils.offer_fields import (
        norm_contracts, norm_schedules, norm_work_modes, norm_seniority, make_salary, _fold
    )

    fields = {
        "contract_types": None,
        "schedules": None,
        "work_modes": None,
        "seniority": None,
        "salary": None,
        "valid_through": None,
        "location": None,
    }

    # 1. valid_through
    vt = ad.get("validToTime") or ad.get("validTo")
    if vt and isinstance(vt, str):
        fields["valid_through"] = vt.strip()

    # 2. location (exact city)
    loc = ad.get("location")
    if isinstance(loc, dict):
        city_name = loc.get("cityName")
        if city_name and isinstance(city_name, str) and city_name.strip():
            fields["location"] = city_name.strip()

    # 3. salary
    sal = ad.get("salary")
    if isinstance(sal, dict) and (sal.get("from") or sal.get("to")):
        cur = sal.get("currencyCode") or sal.get("currencySymbol") or "PLN"
        fields["salary"] = make_salary(
            min_value=sal.get("from"),
            max_value=sal.get("to"),
            currency=cur,
            period=sal.get("period"),
        )

    # 4. params
    params = ad.get("params") or ad.get("parameters") or []
    for p in params:
        if not isinstance(p, dict):
            continue
        key = (p.get("key") or "").lower()
        val = p.get("value")
        norm_val = p.get("normalizedValue")
        name = (p.get("name") or p.get("label") or "").lower()

        # Agreement -> contract_types
        if key == "agreement" or "umow" in name:
            c = norm_contracts(norm_val) or norm_contracts(val)
            if c:
                fields["contract_types"] = c

        # Type -> schedules
        if key == "type" or "wymiar" in name:
            s = norm_schedules(val) or norm_schedules(norm_val)
            if s:
                fields["schedules"] = s

        # Workplace -> work_modes
        if key == "workplace" or "miejsce pracy" in name:
            w = norm_work_modes(val) or norm_work_modes(norm_val)
            if w:
                fields["work_modes"] = w

        # Experience -> seniority (exp_no = no experience -> junior)
        if key == "experience" or "doswiadczenie" in name:
            if norm_val == "exp_no" or (isinstance(val, str) and "bez doswiadczenia" in _fold(val)):
                fields["seniority"] = ["junior"]
            else:
                fields["seniority"] = norm_seniority(val) or norm_seniority(norm_val)

    return fields


def extract_company(job: dict, fallback: str = "OLX") -> str:
    posting = job.get("postingComponent") or {}
    employer = job.get("employer") or {}
    for candidate in (posting.get("companyName"), employer.get("companyName"), (job.get("user") or {}).get("name")):
        if candidate and str(candidate).strip() and str(candidate).lower() != "none":
            return str(candidate).strip()
    return fallback


def fetch_offer_details(link: str, session: requests.Session = None, timeout: int = 20) -> dict:
    """
    Pobierz szczegóły oferty OLX.

    Zwraca dict:
      {'description': str, 'company': str, 'posted_date': str, 'status': 'ok'|'expired'|'error'}
    """
    sess = session or requests
    link = normalize_olx_link(link)

    try:
        throttle.wait()
        resp = sess.get(link, headers=HEADERS, timeout=timeout)
        if resp.status_code == 404:
            return {"status": "expired"}
        if resp.status_code != 200:
            return {"status": "error", "http": resp.status_code}
        html = resp.text
    except Exception as e:
        logger.debug(f"OLX detail fetch failed for {link}: {e}")
        return {"status": "error", "error": str(e)}

    return parse_offer_html(html)


def parse_offer_html(html: str) -> dict:
    """
    Wyciagnij szczegoly oferty z HTML-a strony OLX.

    Osobno od pobierania, bo transport sie zmienil: gole `requests` dostaje
    od OLX 403 (blokada na poziomie TLS - pelne naglowki przegladarki nie
    pomagaja, strona glowna tez odpowiada 403), wiec HTML przychodzi teraz
    z przegladarki, ktora scraper i tak ma otwarta. Parsowanie zostaje jedno.
    """
    state = _extract_state(html)
    job = (state.get("jobAd") or {}).get("job") or {}

    if not job:
        # Ogłoszenie niebędące ofertą pracy albo strona "oferta niedostępna"
        if "nie jest już dostępne" in html or "zakończone" in html:
            return {"status": "expired"}
        return {"status": "error", "reason": "no_jobAd"}

    if job.get("status") and job["status"] != "active":
        return {"status": "expired"}

    description = build_description(job)
    if not description.strip():
        return {"status": "error", "reason": "empty_description"}

    return {
        "status": "ok",
        "description": description,
        "company": extract_company(job),
        "posted_date": job.get("addedAt") or job.get("createdAt") or "",
        "valid_to": job.get("validTo") or "",
        "parsed_fields": parse_olx_fields(job),
    }
