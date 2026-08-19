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
    """Złóż czytelny opis z pól oferty OLX."""
    parts = []

    desc = (job.get("description") or "").strip()
    if desc:
        parts.append(desc)  # HTML - czyszczony dalej przez clean_job_description

    salary = job.get("salary") or {}
    if isinstance(salary, dict) and (salary.get("from") or salary.get("to")):
        cur = salary.get("currencySymbol") or salary.get("currencyCode") or "zł"
        period = {"monthly": "mies.", "hourly": "godz.", "weekly": "tyg."}.get(salary.get("period"), salary.get("period", ""))
        parts.append(f"Wynagrodzenie: {salary.get('from')}-{salary.get('to')} {cur}/{period}".strip("/"))

    # params: wymiar pracy, typ umowy, wymagane doświadczenie itd.
    labels = []
    for p in (job.get("params") or []):
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        val = p.get("value")
        if isinstance(val, dict):
            val = val.get("label")
        if name and val:
            labels.append(f"{name}: {val}")
    for p in (job.get("parameters") or []):
        if not isinstance(p, dict):
            continue
        name = p.get("label")
        vals = [v.get("label") for v in (p.get("values") or []) if isinstance(v, dict) and v.get("label")]
        if name and vals:
            labels.append(f"{name}: {', '.join(vals)}")
    if labels:
        parts.append(" | ".join(dict.fromkeys(labels)))

    if category:
        parts.append(f"Kategoria OLX: {category}")

    return "\n\n".join(parts)


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
    }
