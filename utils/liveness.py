"""
Które oferty zniknęły już z portalu.

Baza nie kasuje oferty w chwili, gdy portal ją zdejmuje - `purge_stale_offers`
patrzy wyłącznie na wiek (`scraped_at` starsze niż 14 dni). Oferta zdjęta trzy
dni po zescrapowaniu zostaje więc w bazie razem ze swoją oceną i potrafi stać
wysoko w rankingu: 1 września 2026 połowa pierwszej dziesiątki „Dopasowanych"
była martwa.

Sygnałem jest `last_seen`. Każdy udany przebieg stempluje nim oferty, które
w listingu faktycznie były, więc jeśli źródło zwróciło cokolwiek PO tej dacie,
a tej oferty wśród tego nie było - portal jej już nie wystawia.

Dni scrapowania czytamy z samych danych, nie z `scraper_status.json`: pole
`history` dokłada się bez uzupełniania wstecz i dla większości źródeł ma dziś
jeden wpis. Zbiór dat z `last_seen` jest kompletny z definicji.

Sprawdzone 1 września 2026 na dwóch portalach, po 10 i 8 ofert w każdą stronę:
**18 z 18 ofert pominiętych przez ostatni przebieg było zdjętych** („Oferta
archiwalna" na RocketJobs, strona wygaszona na JustJoin), a **18 z 18 ofert
widzianych tego dnia było żywych**. Rozdzielenie bez ani jednego wyjątku.
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


def zdjete_z_portalu(jobs):
    """
    Zbiór linków ofert, których portal już nie wystawia.

    Oferta jest zdjęta, gdy jej źródło zwróciło coś w dniu późniejszym niż jej
    własne `last_seen`. Brak `last_seen` przy istniejącej historii źródła też
    się liczy - to zapisy sprzed wprowadzenia tego pola, czyli sprzed miesięcy.

    Świadomie NIE kasujemy takich ofert z bazy: gdyby reguła kiedyś się myliła,
    ukrycie da się cofnąć, a skasowanie kosztowałoby ponowne płatne ocenianie.
    """
    dni = dni_scrapowania(jobs)
    ostatni = {zrodlo: max(d) for zrodlo, d in dni.items() if d}

    zdjete = set()
    for job in jobs:
        zrodlo = _pole(job, "source")
        koniec = ostatni.get(zrodlo)
        if not koniec:
            # Źródło nie ma ani jednego stempla - nie ma jak orzec
            continue
        znacznik = _pole(job, "last_seen")
        if not znacznik or znacznik[:10] < koniec:
            link = _pole(job, "link")
            if link:
                zdjete.add(link)
    return zdjete
