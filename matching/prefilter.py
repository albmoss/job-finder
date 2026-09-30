"""
Przesiew ofert w kodzie, przed płatną oceną.

Odrzuca wyłącznie przypadki pewne. Oferta odrzucona tutaj nigdy nie trafi do
oceny ani na listę, a oferta przepuszczona niepotrzebnie kosztuje tylko jedno
tanie zapytanie - więc każda reguła działa tylko na twardych polach i przy
wyraźnej rozbieżności. Wątpliwe przypadki przechodzą dalej.

Miasto: oferta przechodzi, gdy jest zdalna albo jej lokalizacja wymienia miasto
z CV (także w nazwie angielskiej: Warsaw, Cracow). Inne miasta i przedmieścia
odpadają - scrapery szukają tylko w mieście z CV, ale źródła bez filtra miasta
(feedy firm, LinkedIn) oddają oferty z całego świata. Brak lokalizacji nie jest
pewnym odrzuceniem, więc taka oferta przechodzi.
"""

from __future__ import annotations

from utils.data_models import Job
from utils.offer_fields import SENIORITY, _fold, norm_seniority, norm_work_modes

_RANK = {code: i for i, code in enumerate(SENIORITY)}
# Poziom oferty dalej niż tyle stopni ponad CV = pewne odrzucenie.
MAX_LEVEL_GAP = 1
# Wymagane lata ponad doświadczenie z CV, które jeszcze przepuszczamy.
YEARS_SLACK = 2
# Stanowiska, dla których sam tytuł jest twardym sygnałem poziomu.
_TITLE_LEVELS = ("senior", "lead", "manager")
# Nazwy miast w innych językach, których nie da się dostać samym zdjęciem polskich znaków.
_CITY_ALIASES = {"warszawa": ("warsaw", "varsovie", "warschau"), "krakow": ("cracow", "krakau")}
# Lokalizacje bez miasta: nie wiadomo, gdzie jest praca, więc to nie jest pewne odrzucenie.
_NO_CITY = ("nieznana", "unknown")
_COUNTRY_ONLY = {"polska", "poland", "cala polska"}


def _min_rank(levels) -> int | None:
    ranks = [_RANK[c] for c in levels or () if c in _RANK]
    return min(ranks) if ranks else None


def outside_city(job: Job, city: str | None) -> bool:
    """True, gdy oferta nie jest zdalna i jej lokalizacja nie wymienia miasta z CV."""
    if not city:
        return False
    if "remote" in (job.work_modes or ()):
        return False
    location = _fold(job.location or "").strip()
    if not location or location in _COUNTRY_ONLY or any(w in location for w in _NO_CITY):
        return False
    if "remote" in (norm_work_modes(location) or ()):
        return False
    home = _fold(city).strip()
    return not any(name in location for name in (home, *_CITY_ALIASES.get(home, ())))


def reject_reason(job: Job, profile: dict) -> str | None:
    """
    Powód odrzucenia albo None, gdy oferta idzie do oceny.

    Powody są krótkie i stałe (służą do statystyk przebiegu): "miasto", "poziom", "lata", "jezyk".
    """
    if outside_city(job, profile.get("city")):
        return "miasto"

    cand_rank = _RANK.get(profile.get("seniority"), _RANK["junior"])

    # Poziom: bierzemy NAJNIŻSZY poziom z oferty („Junior / Mid” przechodzi dla juniora).
    offer_rank = _min_rank(job.seniority)
    if offer_rank is not None and offer_rank - cand_rank > MAX_LEVEL_GAP:
        return "poziom"
    # Tytuł „Senior …”, „Kierownik …” przy pustym polu poziomu.
    if offer_rank is None:
        title_levels = [c for c in norm_seniority(job.title) or () if c in _TITLE_LEVELS]
        title_rank = _min_rank(title_levels)
        if title_rank is not None and title_rank - cand_rank > MAX_LEVEL_GAP:
            return "poziom"

    years = job.years_required
    if years is not None and years > float(profile.get("years_experience") or 0) + YEARS_SLACK:
        return "lata"

    known_languages = {lang.get("name") for lang in profile.get("languages") or []}
    for lang in job.languages or ():
        if lang.get("required") and lang.get("name") not in known_languages:
            return "jezyk"

    return None
