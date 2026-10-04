"""
Które oferty zniknęły już z portalu.

Sygnałem jest `last_seen`: udany przebieg stempluje nim oferty, które w listingu
faktycznie były. Brak oferty w nowszym przebiegu znaczy „zdjęta" tylko wtedy, gdy
listing tego źródła obejmuje wszystko w swoim zakresie, a oferta była widziana
już przy obecnym zakresie. Oba warunki zapisuje main_scraper w `scraper_status.json`
(pole `liveness`: `scope` i `since`, dzień pierwszego przebiegu z tym zakresem);
źródło bez tego pola nie oznacza ofert jako zdjętych.
"""

from collections import defaultdict


def _pole(job, nazwa):
    """Oferta bywa obiektem `Job` albo słownikiem prosto z pliku."""
    if isinstance(job, dict):
        return job.get(nazwa)
    return getattr(job, nazwa, None)


def dni_scrapowania(jobs):
    """
    Mapa źródło -> zbiór dni, w które to źródło cokolwiek zwróciło.

    Bierzemy to z ofert, a nie z historii scrapera, bo tylko tu mamy pewność,
    że dzień zakończył się listingiem, a nie samą próbą.
    """
    dni = defaultdict(set)
    for job in jobs:
        znacznik = _pole(job, "last_seen")
        if znacznik:
            dni[_pole(job, "source")].add(znacznik[:10])
    return dni


def zdjete_z_portalu(jobs, status: dict):
    """
    Zbiór linków ofert, których portal już nie wystawia.

    `status` to zawartość `scraper_status.json`. Oferta jest zdjęta, gdy jej źródło
    ma wpis `liveness`, oferta była widziana w dniu `since` albo później, a źródło
    zwróciło coś w dniu późniejszym niż jej `last_seen`.

    Świadomie NIE kasujemy takich ofert z bazy: gdyby reguła kiedyś się myliła,
    plakietkę da się cofnąć, a skasowanie kosztowałoby ponowne płatne ocenianie.
    """
    ostatni = {zrodlo: max(d) for zrodlo, d in dni_scrapowania(jobs).items() if d}

    zdjete = set()
    for job in jobs:
        zrodlo = _pole(job, "source")
        wpis = (status.get(zrodlo) or {}).get("liveness") or {}
        koniec = ostatni.get(zrodlo)
        znacznik = _pole(job, "last_seen")
        if not wpis.get("since") or not koniec or not znacznik:
            continue
        if wpis["since"] <= znacznik[:10] < koniec:
            link = _pole(job, "link")
            if link:
                zdjete.add(link)
    return zdjete
