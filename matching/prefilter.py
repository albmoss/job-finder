"""
Przesiew ofert w kodzie, przed płatną oceną.

Odrzuca wyłącznie przypadki pewne. Oferta odrzucona tutaj nigdy nie trafi do
oceny ani na listę, a oferta przepuszczona niepotrzebnie kosztuje tylko jedno
tanie zapytanie - więc każda reguła działa tylko na twardych polach i przy
wyraźnej rozbieżności. Wątpliwe przypadki przechodzą dalej.

Miasta tu nie sprawdzamy: zakres miasta (z okolicami) wyznaczają już scrapery
przez utils/candidate_scope.py, a nazwy przedmieść z promienia („Piaseczno”,
„Ząbki”) wyglądałyby tutaj jak inne miasto.
"""

from __future__ import annotations

from utils.data_models import Job
from utils.offer_fields import SENIORITY, norm_seniority

_RANK = {code: i for i, code in enumerate(SENIORITY)}
# Poziom oferty dalej niż tyle stopni ponad CV = pewne odrzucenie.
MAX_LEVEL_GAP = 1
# Wymagane lata ponad doświadczenie z CV, które jeszcze przepuszczamy.
YEARS_SLACK = 2
# Stanowiska, dla których sam tytuł jest twardym sygnałem poziomu.
_TITLE_LEVELS = ("senior", "lead", "manager")


def _min_rank(levels) -> int | None:
    ranks = [_RANK[c] for c in levels or () if c in _RANK]
    return min(ranks) if ranks else None


def reject_reason(job: Job, profile: dict) -> str | None:
    """
    Powód odrzucenia albo None, gdy oferta idzie do oceny.

    Powody są krótkie i stałe (służą do statystyk przebiegu): "poziom", "lata", "jezyk".
    """
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
