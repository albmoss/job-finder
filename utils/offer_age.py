"""
Wiek oferty i sygnały martwego ogłoszenia ("ghost job").

Wg Greenhouse 18-22% ogłoszeń w danym kwartale to oferty bez zamiaru zatrudnienia,
a ogłoszenie wiszące 60+ dni bez zmian to najmocniejszy pojedynczy sygnał.
Problem: w tej bazie nie da się tego policzyć wprost.

Zmierzone na 7851 ofertach - pokrycie `posted_date`:
    Pracuj.pl     0/4890  (62% bazy, zero dat)
    LinkedIn      0/108
    SOLID.Jobs    0/55
    OLX        1849/1852
    RocketJobs  737/737
Do tego `purge_stale_offers` kasuje wszystko niewidziane od 14 dni, więc
najstarsza oferta w bazie miała 25 dni. Reguła "60+ dni = trup" nie odpaliłaby
ani razu.

Dlatego liczymy trzy niezależne sygnały i używamy tego, który akurat jest
dostępny - zamiast udawać, że wiemy więcej niż wiemy:

1. `valid_through` w przeszłości  - twarde, deklaracja pracodawcy (ld+json)
2. wiek z `posted_date`           - działa dla ~35% bazy
3. uporczywość: ile dni oferta wciąż pojawia się w kolejnych przebiegach
   (`last_seen` - `scraped_at`) - JEDYNY sygnał dla źródeł bez dat

Gdy żaden sygnał nie jest dostępny, zwracamy `level="unknown"`. Karta nie
pokazuje wtedy nic - brak danych to nie to samo co "oferta jest świeża".
"""

from datetime import date, datetime
from typing import Optional

# Progi z badań rynkowych: 30-45 dni to normalny czas obsadzenia etatu,
# powyżej 60 dni bez zmian ogłoszenie zwykle nie jest już realne.
AGE_WATCH_DAYS = 45
AGE_GHOST_DAYS = 75
# Uporczywość liczy się od PIERWSZEGO zobaczenia, więc progi są niższe:
# ogłoszenie, które widzimy nieprzerwanie miesiąc, wisi już dłużej niż miesiąc.
LISTED_WATCH_DAYS = 30
LISTED_GHOST_DAYS = 50


def _as_date(value) -> Optional[date]:
    """Znieś różnice formatów: '2026-08-07', ISO z czasem, ISO ze strefą, 'Z'."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _get(job, field):
    """Job bywa dataclassą albo zwykłym dict-em (pliki analizy)."""
    if isinstance(job, dict):
        return job.get(field)
    return getattr(job, field, None)


def offer_age_days(job, today: Optional[date] = None) -> Optional[int]:
    """Ile dni temu pracodawca wystawił ofertę. None, gdy portal nie podaje daty."""
    posted = _as_date(_get(job, "posted_date"))
    if posted is None:
        return None
    return ((today or date.today()) - posted).days


def days_listed(job, today: Optional[date] = None) -> Optional[int]:
    """
    Ile dni oferta utrzymuje się w kolejnych przebiegach scrapera.

    Liczone jako last_seen - scraped_at (pierwsze zobaczenie). Wymaga co najmniej
    dwóch przebiegów, więc dla świeżo dodanych źródeł zwraca 0 i nabiera
    wartości dopiero z czasem.
    """
    first = _as_date(_get(job, "scraped_at"))
    last = _as_date(_get(job, "last_seen"))
    if first is None:
        return None
    return ((last or (today or date.today())) - first).days


def ghost_signals(job, today: Optional[date] = None) -> dict:
    """
    Zwróć ocenę żywotności oferty.

    level: "ghost" | "watch" | "ok" | "unknown"
    reasons: lista krótkich opisów po polsku (do pokazania w UI)
    """
    today = today or date.today()
    reasons = []
    level = "unknown"

    def bump(new_level):
        nonlocal level
        order = {"unknown": 0, "ok": 1, "watch": 2, "ghost": 3}
        if order[new_level] > order[level]:
            level = new_level

    # 1. Deklarowane wygaśnięcie - sygnał twardy, ma pierwszeństwo
    expires = _as_date(_get(job, "valid_through"))
    if expires is not None:
        if expires < today:
            reasons.append(f"termin minął {(today - expires).days} dni temu")
            bump("ghost")
        else:
            bump("ok")

    # 2. Wiek ogłoszenia wg daty publikacji
    age = offer_age_days(job, today)
    if age is not None:
        if age >= AGE_GHOST_DAYS:
            reasons.append(f"wystawiona {age} dni temu")
            bump("ghost")
        elif age >= AGE_WATCH_DAYS:
            reasons.append(f"wystawiona {age} dni temu")
            bump("watch")
        else:
            bump("ok")

    # 3. Uporczywość - jedyny sygnał dla źródeł bez dat
    listed = days_listed(job, today)
    seen = _get(job, "times_seen") or 1
    if listed is not None and seen > 1:
        if listed >= LISTED_GHOST_DAYS:
            reasons.append(f"wisi od {listed} dni")
            bump("ghost")
        elif listed >= LISTED_WATCH_DAYS:
            reasons.append(f"wisi od {listed} dni")
            bump("watch")
        else:
            bump("ok")

    return {
        "level": level,
        "reasons": reasons,
        "age_days": age,
        "days_listed": listed,
        "times_seen": seen,
        "expired": expires is not None and expires < today,
    }


def ghost_label(job, today: Optional[date] = None) -> Optional[str]:
    """
    Krótki podpis na kartę albo None, gdy nie ma czego pokazać.

    Poziom "ghost" dostaje jawny werdykt, bo sam powód ("wystawiona 76 dni temu")
    nie mówi, czy to już problem, czy jeszcze nie - a odróżnianie obu poziomów
    wyłącznie odcieniem koloru okazało się nieczytelne. "watch" zostaje samym
    powodem: to informacja, nie ostrzeżenie.
    """
    signals = ghost_signals(job, today)
    if signals["level"] not in ("ghost", "watch") or not signals["reasons"]:
        return None
    reason = signals["reasons"][0]
    return f"nieaktualna? · {reason}" if signals["level"] == "ghost" else reason
