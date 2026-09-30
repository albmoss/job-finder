"""
Wspólny słownik cech oferty.

Każdy portal nazywa te same rzeczy inaczej: Pracuj.pl pisze „Młodszy specjalista
(Junior)”, JustJoin `junior`, SOLID `Junior`, OLX `experience: exp_no`. Scrapery
tłumaczą swoje wartości na jeden zestaw kodów stąd, a przesiew i ocena
dopasowania porównują już tylko te kody.

Pola strukturalne oferty (atrybuty `Job`):
    seniority       lista kodów z SENIORITY
    work_modes      lista kodów z WORK_MODES
    contract_types  lista kodów z CONTRACTS
    schedules       lista kodów z SCHEDULES
    salary          słownik z make_salary() albo None
    skills_required lista nazw umiejętności podanych przez portal jako wymagane
    skills_nice     lista nazw podanych jako mile widziane
    languages       lista {"name": kod z LANGUAGES, "level": "B2" | None, "required": bool}
    category        branża / kategoria portalu, tekst
    years_required  minimalna liczba lat doświadczenia z treści oferty albo None

Brak wartości to None, nie pusta lista: pusta lista mówiłaby „portal podał,
że nic”, a zwykle portal po prostu tego nie podaje.
"""

from __future__ import annotations

import re
import unicodedata

SENIORITY = ("intern", "junior", "mid", "senior", "lead", "manager")
WORK_MODES = ("onsite", "hybrid", "remote")
CONTRACTS = ("uop", "b2b", "zlecenie", "dzielo", "staz", "other")
SCHEDULES = ("full_time", "part_time", "other")
LANGUAGES = (
    "polish", "english", "german", "french", "spanish", "italian", "russian",
    "ukrainian", "dutch", "czech", "slovak", "swedish", "norwegian", "danish",
    "finnish", "portuguese", "hungarian", "romanian", "japanese", "chinese",
)

STRUCTURED_FIELDS = (
    "seniority", "work_modes", "contract_types", "schedules", "salary",
    "skills_required", "skills_nice", "languages", "category", "years_required",
)


def _fold(text: str) -> str:
    """Małe litery bez polskich znaków - do dopasowań odpornych na zapis."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("ł", "l")


def _or_none(values: list[str]):
    return values or None


_PIECE_SPLIT_RE = re.compile(r"[/,|;&+]| i | lub | or ")


def _pick(raw, table: tuple[tuple[str, str], ...]) -> list[str]:
    """
    Kody z `table` (wzorzec, kod) dla wartości `raw`.

    Wartość dzielimy na kawałki („Junior / Mid”, „Kierownik / Koordynator”)
    i każdy kawałek dostaje JEDEN kod: pierwszy pasujący wzorzec w kolejności
    tabeli. Bez tego „Starszy specjalista (Senior)” pasowałby też do „mid”
    przez słowo „specjalista”.
    """
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    found: list[str] = []
    for item in items:
        for piece in _PIECE_SPLIT_RE.split(_fold(item)):
            code = next((code for pattern, code in table if re.search(pattern, piece)), None)
            if code and code not in found:
                found.append(code)
    return found


# Kolejność = pierwszeństwo w obrębie jednego kawałka. „Senior” stoi przed „mid”,
# bo „Starszy specjalista” zawiera też „specjalista”.
_SENIORITY_TABLE = (
    (r"praktyk|stazyst|intern|trainee|\bstaz\b|student|absolwent", "intern"),
    (r"junior|mlodsz|asystent|assistant|bez doswiadczenia|exp_no|entry|podstawow|associate", "junior"),
    (r"senior|starsz|ekspert|expert", "senior"),
    (r"lead|lider|principal|architect|architekt", "lead"),
    (r"manager|menedzer|kierownik|dyrektor|director|head of|\bc-level|prezes|kierownictw", "manager"),
    (r"\bmid\b|regular|specjalist|specialist|medior|intermediate|sredni", "mid"),
)

_WORK_MODE_TABLE = (
    (r"zdaln|remote|home ?office|telecommute|z domu", "remote"),
    (r"hybryd|hybrid", "hybrid"),
    (r"stacjonar|on-?site|office|w biurze|full_office|mobiln|w terenie", "onsite"),
)


_CONTRACT_TABLE = (
    (r"staz|praktyk|internship|trainee", "staz"),
    (r"o dzielo|\buod\b|specific[- ]task", "dzielo"),
    (r"zlecen|mandate|\buz\b", "zlecenie"),
    (r"b2b|kontrakt|\bcontract\b|self-?employ|dzialalnosc|samozatrudn", "b2b"),
    (r"tymczasow|agenc|zastepstw|freelance|wolontari|temporary", "other"),
    (r"o prace|umowa o prac|\buop\b|permanent|employment contract|etat", "uop"),
    (r"inn[yae]|other", "other"),
)

_SCHEDULE_TABLE = (
    (r"pelny|full[ _-]?time|caly etat|full", "full_time"),
    (r"czesc|niepeln|part[ _-]?time|half[ _-]?time|pol etatu|1/2|3/4|dorywcz|weekend", "part_time"),
    (r"zmian|elastyczn|flexible|shift|nocn|temporary|dodatkow", "other"),
)


def norm_seniority(raw) -> list[str] | None:
    """Kody poziomu z dowolnej wartości portalu (tekst albo lista tekstów)."""
    return _or_none(_pick(raw, _SENIORITY_TABLE))


def norm_work_modes(raw) -> list[str] | None:
    return _or_none(_pick(raw, _WORK_MODE_TABLE))


def norm_contracts(raw) -> list[str] | None:
    return _or_none(_pick(raw, _CONTRACT_TABLE))


def norm_schedules(raw) -> list[str] | None:
    return _or_none(_pick(raw, _SCHEDULE_TABLE))


def norm_skills(raw) -> list[str] | None:
    """Nazwy umiejętności: przycięte, bez pustych i powtórek, kolejność zachowana."""
    if not raw:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else [raw]:
        name = (item.get("name") or item.get("value")) if isinstance(item, dict) else item
        name = str(name or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out or None


_PERIODS = {
    "month": "month", "monthly": "month", "mies": "month", "m": "month",
    "hour": "hour", "hourly": "hour", "godz": "hour", "h": "hour",
    "year": "year", "yearly": "year", "annual": "year", "rok": "year", "y": "year",
    "day": "day", "daily": "day", "dzien": "day", "d": "day",
    "week": "week", "weekly": "week", "tydz": "week", "w": "week",
}


def make_salary(min_value=None, max_value=None, currency: str | None = "PLN",
                period: str | None = None, gross: bool | None = None,
                contract: str | None = None) -> dict | None:
    """
    Widełki w jednym kształcie. Zwraca None, gdy nie ma żadnej kwoty.

    `period` przyjmuje formy portali („month”, „MONTHLY”, „mies.”, „h”) i
    zapisuje jedną z: month, hour, day, week, year. `gross`: True = brutto,
    False = netto (B2B zwykle netto), None = portal nie podał.
    """
    def _num(value):
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(" ", "").replace("\xa0", "").replace(",", "."))
        except ValueError:
            return None

    low, high = _num(min_value), _num(max_value)
    if low is None and high is None:
        return None
    period_key = _fold(period or "").strip(" .")
    period_code = _PERIODS.get(period_key)
    if period_code is None and period_key:
        period_code = next((code for key, code in _PERIODS.items()
                            if len(key) > 1 and period_key.startswith(key)), None)
    return {
        "min": low,
        "max": high,
        "currency": (currency or "PLN").upper(),
        "period": period_code,
        "gross": gross,
        "contract": contract,
    }


_SALARY_TEXT_RE = re.compile(
    r"(?P<min>\d[\d \xa0]*(?:[.,]\d+)?)\s*(?:[-–—]|do)\s*(?P<max>\d[\d \xa0]*(?:[.,]\d+)?)"
    r"|(?P<single>\d[\d \xa0]*(?:[.,]\d+)?)"
)


def salary_from_text(text: str | None, contract: str | None = None) -> dict | None:
    """
    Widełki z napisu portalu, np. „4 806–5 300 zł brutto / mies.”,
    „25–35 zł netto (+ VAT) / godz.”. Zwraca None, gdy w napisie nie ma kwoty.
    """
    if not text:
        return None
    folded = _fold(text)
    match = _SALARY_TEXT_RE.search(folded)
    if not match:
        return None
    low = match.group("min") or match.group("single")
    high = match.group("max") or match.group("single")
    currency = "EUR" if ("eur" in folded or "€" in text) else "USD" if ("usd" in folded or "$" in text) else "PLN"
    period = None
    for key in ("godz", "hour", "/h", "mies", "month", "rok", "year", "dzien", "day", "tydz", "week"):
        if key in folded:
            period = key.strip("/")
            break
    gross = True if "brutto" in folded or "gross" in folded else False if ("netto" in folded or "net" in folded) else None
    return make_salary(low, high, currency, period, gross, contract)


_LANG_PATTERNS = (
    (r"angiel|english", "english"),
    (r"niemieck|german|deutsch", "german"),
    (r"francus|french", "french"),
    (r"hiszpan|spanish", "spanish"),
    (r"wlosk|italian", "italian"),
    (r"rosyjsk|russian", "russian"),
    (r"ukrain", "ukrainian"),
    (r"niderland|holendersk|dutch", "dutch"),
    (r"czesk|czech", "czech"),
    (r"slowack|slovak", "slovak"),
    (r"szwedzk|swedish", "swedish"),
    (r"norwesk|norwegian", "norwegian"),
    (r"dunsk|danish", "danish"),
    (r"finsk|finnish", "finnish"),
    (r"portugal|portuguese", "portuguese"),
    (r"wegiersk|hungarian", "hungarian"),
    (r"rumunsk|romanian", "romanian"),
    (r"japonsk|japanese", "japanese"),
    (r"chinsk|chinese|mandarin", "chinese"),
    (r"polsk|polish", "polish"),
)

_CEFR_RE = re.compile(r"\b([abc][12])\b")
_NICE_RE = re.compile(r"mile widzian|nice to have|dodatkow\w* atut|plus\b|preferowan|an advantage|is a plus|bonus")
_LEVEL_WORDS = (
    (r"expert", "C2"),
    (r"ojczyst|native|biegl|fluent|swobodn|proficient|zaawansowan|advanced", "C1"),
    (r"komunikatywn|dobr\w* znajomosc|good|intermediate|sredniozaawansowan", "B2"),
    (r"podstaw|basic|elementar", "A2"),
)


def norm_language(raw) -> str | None:
    """Kod języka z nazwy w dowolnym języku („angielski”, „English”, „EN”)."""
    _iso = {
        "en": "english", "eng": "english",
        "pl": "polish", "pol": "polish",
        "de": "german", "ger": "german", "deu": "german",
        "fr": "french", "fra": "french", "fre": "french",
        "es": "spanish", "spa": "spanish",
        "it": "italian", "ita": "italian",
        "ru": "russian", "rus": "russian",
        "uk": "ukrainian", "ukr": "ukrainian", "ua": "ukrainian",
        "nl": "dutch", "nld": "dutch", "dut": "dutch",
        "cs": "czech", "ces": "czech", "cze": "czech",
        "sk": "slovak", "slk": "slovak", "slo": "slovak",
        "sv": "swedish", "swe": "swedish",
        "no": "norwegian", "nor": "norwegian",
        "da": "danish", "dan": "danish",
        "fi": "finnish", "fin": "finnish",
        "pt": "portuguese", "por": "portuguese",
        "hu": "hungarian", "hun": "hungarian",
        "ro": "romanian", "ron": "romanian", "rum": "romanian",
        "ja": "japanese", "jpn": "japanese",
        "zh": "chinese", "zho": "chinese", "chi": "chinese",
    }
    folded = _fold(raw or "").strip()
    if folded in _iso:
        return _iso[folded]
    for pattern, code in _LANG_PATTERNS:
        if re.search(pattern, folded):
            return code
    return None


def make_language(name, level=None, required: bool = True) -> dict | None:
    code = norm_language(name)
    if code is None:
        return None
    level_text = str(level or "").strip().upper() or None
    if level_text and not _CEFR_RE.search(level_text.lower()):
        level_text = next((cefr for pattern, cefr in _LEVEL_WORDS if re.search(pattern, _fold(level_text))), level_text)
    return {"name": code, "level": level_text, "required": bool(required)}


# Język liczy się jako wymaganie tylko wtedy, gdy jego nazwa stoi obok słowa
# „język/language” albo poziomu. Samo „duński klient” czy „Czech office”
# w zdaniu o angielskim nie jest wymaganiem.
_LANG_CUE = (r"jezyk\w*|language\w*|[abc][12]|biegl\w*|fluent\w*|komunikatywn\w*|znajomosc\w*"
             r"|native|ojczyst\w*|speak\w*|mowi\w*|poziom\w*|proficien\w*|with|z jezykiem")


def _near(pattern: str, sentence: str, span: int = 30) -> bool:
    return bool(re.search(rf"(?:{_LANG_CUE})[^.]{{0,{span}}}?(?:{pattern})"
                          rf"|(?:{pattern})\w*[^.]{{0,{span}}}?(?:{_LANG_CUE})", sentence))


def languages_from_text(text: str | None) -> list[dict] | None:
    """
    Języki wymagane w treści oferty. Zdanie z „mile widziany”, „nice to have”
    oznacza język dodatkowy (`required: False`). Polski pomijamy, gdy pada
    tylko jako język ogłoszenia - liczy się dopiero z poziomem albo wymogiem.
    """
    if not text:
        return None
    found: dict[str, dict] = {}
    for sentence in re.split(r"[.\n;•·●▪]+", _fold(text)):
        if "jezyk" not in sentence and "language" not in sentence and not _CEFR_RE.search(sentence) \
                and not re.search(r"angielsk|english|niemieck|german|with ", sentence):
            continue
        nice = bool(_NICE_RE.search(sentence))
        cefr = _CEFR_RE.search(sentence)
        level = cefr.group(1).upper() if cefr else next(
            (lvl for pattern, lvl in _LEVEL_WORDS if re.search(pattern, sentence)), None)
        for pattern, code in _LANG_PATTERNS:
            if not re.search(pattern, sentence) or not _near(pattern, sentence):
                continue
            if code == "polish" and not level:
                continue
            entry = found.get(code)
            if entry is None:
                found[code] = {"name": code, "level": level, "required": not nice}
            else:
                entry["required"] = entry["required"] or not nice
                entry["level"] = entry["level"] or level
    return list(found.values()) or None


_NO_EXPERIENCE_RE = re.compile(
    r"bez doswiadczenia|nie wymagamy doswiadczenia|doswiadczenie nie jest wymagane"
    r"|no (prior )?experience (is )?(required|needed)|brak doswiadczenia nie jest przeszkoda"
)
_YEARS_RE = re.compile(
    r"(?:min(?:imum|\.)?|co najmniej|at least|od|powyzej|ponad|over)?\s*"
    r"(?P<low>\d{1,2})(?:\s*(?:-|–|do|to)\s*(?P<high>\d{1,2}))?\s*\+?\s*"
    r"(?:lat[a]?|rok(?:u|i)?|years?|yrs?)\b"
)


def years_from_text(text: str | None) -> int | None:
    """
    Minimalna liczba lat doświadczenia z treści oferty.

    Liczba musi stać w tym samym zdaniu co „doświadczenie”/„experience”, żeby
    „firma działa od 20 lat” nie stało się wymaganiem. Przy widełkach („2-3 lata”)
    bierzemy dolną granicę. „Bez doświadczenia” daje 0. Brak informacji: None.
    """
    if not text:
        return None
    folded = _fold(text)
    if _NO_EXPERIENCE_RE.search(folded):
        return 0
    best = None
    for sentence in re.split(r"[.\n;•·●▪]+", folded):
        if "doswiadcz" not in sentence and "experience" not in sentence and "stazu pracy" not in sentence:
            continue
        for match in _YEARS_RE.finditer(sentence):
            years = int(match.group("low"))
            if 0 < years <= 20:
                best = years if best is None else min(best, years)
    return best


def text_features(description: str | None) -> dict:
    """Cechy wyciągane z samej treści oferty: lata doświadczenia i języki."""
    return {
        "years_required": years_from_text(description),
        "languages": languages_from_text(description),
    }
