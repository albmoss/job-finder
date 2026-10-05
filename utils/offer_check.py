"""
Sprawdzenie na żywo, czy portal wciąż wystawia ofertę (ekrany 01 i 02, przy oglądaniu).

`stan_strony` rozstrzyga ze statusu HTTP, adresu po przekierowaniach i HTML-a; znaczniki
zweryfikowane na prawdziwych stronach 05.10.2026. Portale, które blokują gołe `requests`
(Pracuj.pl, OLX, LinkedIn, Indeed, ATS), dostają 'unknown' bez zapytania.
Wynik trafia do `offer_checks.json`: 'gone' jest ostateczne, reszta wygasa po 24 h.
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Optional, Set
from urllib.parse import urlparse

import requests

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic

OFFER_CHECKS_PATH = Path("offer_checks.json")
WAZNOSC = timedelta(hours=24)
LIMIT_CZASU = 10
WATKI = 4

GONE, ALIVE, UNKNOWN = "gone", "alive", "unknown"

PORTALE = {
    "justjoin.it": "candidate_api",
    "rocketjobs.pl": "candidate_api",
    "nofluffjobs.com": "nofluffjobs",
    "praca.pl": "praca",
    "gowork.pl": "gowork",
    "aplikuj.pl": "aplikuj",
    "solid.jobs": "solid",
}

NAGLOWKI = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}

_JOB_POSTING = re.compile(r'"@type"\s*:\s*"JobPosting"')
_ALERT_WYGASLA = re.compile(r'expiredOfferAlert\\?"\s*:\s*\\?"([^"\\]+)')
_ALERT_TYTUL = re.compile(r'<[^>]*\brole="alert"[^>]*\btitle="([^"]*)"')
_NFJ_STATUS = re.compile(r'"status"\s*:\s*"([A-Z_]+)"\s*,\s*"postingUrl"')

_blokada = threading.Lock()


def portal(link: str) -> Optional[str]:
    host = (urlparse(link or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return PORTALE.get(host)


def stan_strony(portal_id: Optional[str], status: int, url: str, html: str) -> str:
    if portal_id not in PORTALE.values():
        return UNKNOWN
    if portal_id == "solid" and "/offer-not-found/" in urlparse(url or "").path:
        return GONE
    if portal_id == "aplikuj" and status == 410:
        return GONE
    if status == 404 and portal_id in ("candidate_api", "nofluffjobs"):
        return GONE
    if status != 200:
        return UNKNOWN
    html = html or ""
    job_posting = bool(_JOB_POSTING.search(html))

    if portal_id == "candidate_api":
        napis = _ALERT_WYGASLA.search(html)
        if napis and napis.group(1) in _ALERT_TYTUL.findall(html):
            return GONE
        return ALIVE if job_posting else UNKNOWN
    if portal_id == "nofluffjobs":
        stan = _NFJ_STATUS.search(html)
        if not stan:
            return UNKNOWN
        if stan.group(1) in ("EXPIRED", "DISABLED"):
            return GONE
        return ALIVE if stan.group(1) == "PUBLISHED" else UNKNOWN
    if portal_id == "praca" and "Oferta pracy jest nieaktualna" in html:
        return GONE
    if portal_id == "gowork" and "Oferta pracy wygasła" in html:
        return GONE
    if portal_id == "aplikuj" and "Pracodawca zakończył zbieranie CV" in html:
        return GONE
    if portal_id == "solid" and "/offer/" not in urlparse(url or "").path:
        return UNKNOWN
    return ALIVE if job_posting else UNKNOWN


def check_offer(link: str) -> str:
    portal_id = portal(link)
    if portal_id is None:
        return UNKNOWN
    try:
        resp = requests.get(link, headers=NAGLOWKI, timeout=LIMIT_CZASU, allow_redirects=True)
    except requests.RequestException:
        return UNKNOWN
    return stan_strony(portal_id, resp.status_code, resp.url, resp.text)


def aktualny(wpis, teraz: datetime) -> bool:
    if not isinstance(wpis, dict):
        return False
    if wpis.get("state") == GONE:
        return True
    try:
        sprawdzono = datetime.fromisoformat(wpis.get("checked_at") or "")
    except ValueError:
        return False
    return teraz - sprawdzono < WAZNOSC


def _wczytaj() -> Dict[str, dict]:
    dane = load_json_safe(OFFER_CHECKS_PATH, default={})
    return dane if isinstance(dane, dict) else {}


def zdjete_ze_sprawdzen() -> Set[str]:
    return {link for link, wpis in _wczytaj().items() if isinstance(wpis, dict) and wpis.get("state") == GONE}


def sprawdz_oferty(links: Iterable[str], teraz: Optional[datetime] = None) -> Set[str]:
    teraz = teraz or datetime.now()
    kanoniczne = {link: canonical_link(link) for link in links if link}
    with _blokada:
        pamiec = _wczytaj()
    do_sprawdzenia = sorted({
        kan for link, kan in kanoniczne.items()
        if portal(link) is not None and not aktualny(pamiec.get(kan), teraz)
    })
    if do_sprawdzenia:
        with ThreadPoolExecutor(max_workers=min(WATKI, len(do_sprawdzenia))) as pula:
            wyniki = dict(zip(do_sprawdzenia, pula.map(check_offer, do_sprawdzenia)))
        sprawdzono = teraz.isoformat(timespec="seconds")
        with _blokada:
            pamiec = _wczytaj()
            for kan, stan in wyniki.items():
                if (pamiec.get(kan) or {}).get("state") != GONE:
                    pamiec[kan] = {"state": stan, "checked_at": sprawdzono}
            save_json_atomic(OFFER_CHECKS_PATH, pamiec)
    return {link for link, kan in kanoniczne.items() if (pamiec.get(kan) or {}).get("state") == GONE}
