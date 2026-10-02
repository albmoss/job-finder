"""
Kategorie portali dobierane do CV.

Portale mają własne kategorie ofert (Pracuj.pl „IT - Rozwój oprogramowania”, OLX
„administracja-biurowa”). Zamiast ściągać wszystko z miasta i odsiewać potem,
scraper pyta tutaj, które z jego kategorii pasują do CV, i szuka tylko w nich.

Wyboru dokonuje Jev: jedno zapytanie na portal, jedno pytanie tak/nie na
kategorię („czy osoba z tym CV szukałaby pracy w tej kategorii”). Próg jest
celowo niski - kategorie portali są nieostre (junior frontend potrafi wisieć w
„Marketingu”), a zbędna kategoria kosztuje tylko trochę ofert więcej. Ocena
pojedynczych ofert i tak idzie później przez przesiew i Jev.

Pytanie o „szansę na pracę w kategorii” nie nadaje się: prace bez wymagań
(sprzątanie, kierowca) dostają wtedy tyle samo co zawód z CV. Pytanie o zawód,
kierunek, dziedzinę pokrewną i dawną pracę daje na liście OLX wyraźny podział
(IT i inżynieria ~0,6, magazyn ~0,2, fryzjerstwo ~0,01) i jest stabilne
między powtórzeniami.

Wynik trafia do `category_scope.json` i liczy się ponownie dopiero po zmianie
profilu CV albo listy kategorii portalu. Bez CV, bez klucza albo przy błędzie
Jev funkcja zwraca None: scraper szuka wtedy bez filtra kategorii, czyli tak
jak przed jego wprowadzeniem.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from utils.safe_io import load_json_safe, save_json_atomic

logger = logging.getLogger(__name__)
CACHE_PATH = Path(__file__).resolve().parent.parent / "category_scope.json"

# Kategoria zostaje, gdy Jev daje jej co najmniej tyle szans.
THRESHOLD = 0.1

_INSTRUCTIONS = (
    "Osoba z tym CV szukałaby pracy w kategorii „{label}”: to jej zawód, kierunek, "
    "w który CV prowadzi, dziedzina pokrewna jednemu z nich albo praca, którą już wykonywała."
)


def _catalog_fp(catalog: dict[str, str]) -> str:
    raw = json.dumps(sorted(catalog.items()), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _ask_jev(catalog: dict[str, str], profile: dict, cv: str | None) -> dict[str, float]:
    from matching import jev

    key = jev.api_key()
    if not key:
        raise jev.JevError("brak TYPESAFE_API_KEY")
    codes = list(catalog)
    # Klucze pytań to indeksy: kody portali bywają liczbami albo mają znaki spoza nazw.
    questions = {
        f"k{i}": {"type": "noul", "instructions": _INSTRUCTIONS.format(label=catalog[code])}
        for i, code in enumerate(codes)
    }
    reply = jev.ask({"kandydat": jev.candidate_state(profile, cv)}, key, questions=questions)
    return {code: float(reply["answers"][f"k{i}"]["noul"]) for i, code in enumerate(codes)}


def pick_categories(portal: str, catalog: dict[str, str]) -> list[str] | None:
    """
    Kody kategorii `portal`, w których warto szukać ofert dla CV.

    `catalog`: kod kategorii w adresie portalu -> nazwa czytelna dla człowieka.
    None = szukaj bez filtra kategorii (brak CV, klucza albo odpowiedzi Jev).
    """
    from utils.candidate_scope import load_profile
    from utils.cv_profile import cv_text, profile_fingerprint

    profile = load_profile()
    if not profile or not catalog:
        return None
    profile_fp = profile_fingerprint(profile)
    catalog_fp = _catalog_fp(catalog)

    cache = load_json_safe(str(CACHE_PATH), default={})
    if not isinstance(cache, dict):
        cache = {}
    entry = cache.get(portal) or {}
    if entry.get("profile_fp") == profile_fp and entry.get("catalog_fp") == catalog_fp:
        return list(entry["picked"])

    from matching.jev import JevError
    try:
        probs = _ask_jev(catalog, profile, cv_text())
    except JevError as e:
        logger.warning(f"{portal}: nie udało się dobrać kategorii do CV ({e}) - szukam bez filtra kategorii")
        return None

    picked = [code for code, p in probs.items() if p >= THRESHOLD]
    if not picked:
        logger.warning(f"{portal}: żadna kategoria nie przeszła progu - szukam bez filtra kategorii")
        return None
    cache[portal] = {
        "profile_fp": profile_fp,
        "catalog_fp": catalog_fp,
        "picked": picked,
        "probabilities": {catalog[c]: round(p, 3) for c, p in sorted(probs.items(), key=lambda x: -x[1])},
        "picked_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_json_atomic(str(CACHE_PATH), cache, backup=False)
    logger.info(f"{portal}: {len(picked)}/{len(catalog)} kategorii pasuje do CV: "
                + ", ".join(catalog[c] for c in picked))
    return picked
