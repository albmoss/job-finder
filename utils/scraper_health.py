"""
Wykrywanie cichej awarii scraperów.

Scraper oparty o HTML psuje się bezgłośnie: portal zmienia znaczniki, parser nie
rzuca wyjątkiem, przebieg kończy się kodem 0 i logiem `Successfully scraped`, a do
bazy leci zero ofert albo oferty z pustą firmą. Ani `status: success`, ani liczba
ofert same z siebie tego nie pokazują - dwa źródła stały w tej bazie dwa tygodnie
i nikt się nie dowiedział.

Ten moduł nie wysyła ANI JEDNEGO dodatkowego zapytania. Cała diagnoza powstaje z
dwóch rzeczy, które przebieg i tak ma: z rekordów, które właśnie wróciły, i z
historii wydajności zapisanej w scraper_status.json.

Werdykty:
    ok            - nic nie mów; zdrowe źródła nie zaśmiecają raportu
    broken        - zero ofert, choć wcześniej to źródło je dawało
    weak          - poniżej 20% mediany z ostatnich przebiegów
    degraded      - oferty przyszły, ale są uszkodzone (pusta firma, encje HTML,
                    link poza domenę portalu)
    inconclusive  - źródło padło na blokadzie albo limicie zapytań; to NIE jest
                    dowód awarii parsera i nie wolno tego mylić z `broken`
"""

from collections import Counter
from urllib.parse import urlparse

# Ile ostatnich przebiegów pamiętamy na źródło. Mediana z dziesięciu jest odporna
# na jeden nietypowy przebieg, a plik zostaje mały.
HISTORY_LEN = 10

# Poniżej tego ułamka mediany uznajemy przebieg za podejrzanie chudy. Portale
# faluja z dnia na dzień, więc próg jest celowo niski - ma łapać awarie, nie wahania.
WEAK_RATIO = 0.2

# Historia krótsza niż to nie mówi nic o normie tego źródła.
MIN_HISTORY = 3

# Ślady po niedokończonym parsowaniu: nieodkodowane encje i surowe znaczniki
# w tytule oznaczają, że tekst wyjęto z HTML-a, ale nie przepuszczono przez parser.
_HTML_MARKERS = ("&amp;", "&nbsp;", "&quot;", "&#", "<div", "<span", "<p>", "<br")

# Komunikaty, po których błąd czytamy jako blokadę portalu, a nie awarię parsera.
_BLOCKED_MARKERS = ("429", "403", "too many requests", "rate limit", "captcha",
                    "timeout", "timed out", "blocked")


def _text(value) -> str:
    """Wartości z JSON-a bywają stringiem 'None' - traktujemy je jak pustkę."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in ("None", "nan", "NaN") else text


def expected_domain(known_jobs, source_name: str):
    """
    Domena, pod którą historycznie wisiały oferty tego źródła.

    Wyprowadzona z bazy, nie wpisana w kod: nowy scraper nie wymaga niczego
    dopisywać, a portal, który zmienił domenę, sam się ujawni po jednym przebiegu.
    Zwraca None, gdy w bazie nie ma jeszcze dość rekordów, żeby cokolwiek twierdzić.
    """
    hosts = Counter()
    for job in known_jobs:
        get = job.get if isinstance(job, dict) else lambda k: getattr(job, k, None)
        if _text(get("source")) != source_name:
            continue
        host = urlparse(_text(get("link"))).netloc.lower().removeprefix("www.")
        if host:
            hosts[host] += 1

    if sum(hosts.values()) < 20:
        return None
    host, count = hosts.most_common(1)[0]
    # Portal rozrzucony po wielu subdomenach nie daje sensownej normy
    return host if count / sum(hosts.values()) >= 0.8 else None


def scan_records(jobs, domain=None) -> list[str]:
    """
    Objawy uszkodzonego parsowania w tym, co źródło właśnie zwróciło.

    Kryteria są celowo takie, że pojedynczy dziwny rekord ich nie wyzwala -
    ogłoszenie bez nazwy firmy zdarza się naprawdę. Dopiero brak pola we
    WSZYSTKICH rekordach znaczy, że parser zgubił selektor.
    """
    jobs = list(jobs)
    if not jobs:
        return []

    symptoms = []

    def get(job, key):
        return _text(job.get(key) if isinstance(job, dict) else getattr(job, key, None))

    for field, label in (("company", "firma"), ("title", "tytuł"), ("description", "opis")):
        if all(not get(job, field) for job in jobs):
            symptoms.append(f"puste pole `{field}` ({label}) we wszystkich {len(jobs)} ofertach")

    dirty = [job for job in jobs
             if any(marker in get(job, "title") for marker in _HTML_MARKERS)]
    if dirty:
        symptoms.append(f"nieodkodowany HTML w tytule ({len(dirty)}/{len(jobs)})")

    if domain:
        stray = [job for job in jobs
                 if urlparse(get(job, "link")).netloc.lower().removeprefix("www.") != domain]
        # Jeden link na inną domenę bywa przekierowaniem; połowa oznacza, że parser
        # zbiera odnośniki z zupełnie innego miejsca strony niż wcześniej.
        if len(stray) > len(jobs) / 2:
            symptoms.append(f"linki poza {domain} ({len(stray)}/{len(jobs)})")

    return symptoms


def yield_verdict(count: int, history: list) -> tuple[str, str]:
    """
    Werdykt z samej liczby ofert, na tle poprzednich przebiegów tego źródła.

    Historia jest jedynym sensownym punktem odniesienia: dla NoFluffJobs 39 ofert
    to normalny dzień, dla Pracuj.pl to awaria. Stały próg dla wszystkich źródeł
    musiałby być albo tak niski, że nic nie łapie, albo tak wysoki, że krzyczy
    na małe portale przy każdym przebiegu.
    """
    counts = sorted(h["count"] for h in history if isinstance(h, dict) and "count" in h)

    if count == 0:
        if counts and max(counts) > 0:
            return "broken", f"zero ofert, a wcześniej dawało do {max(counts)}"
        return "ok", ""

    if len(counts) < MIN_HISTORY:
        return "ok", ""

    median = counts[len(counts) // 2]
    if median and count < median * WEAK_RATIO:
        return "weak", f"{count} ofert wobec mediany {median} z ostatnich {len(counts)} przebiegów"

    return "ok", ""


def error_verdict(error: str) -> tuple[str, str]:
    """
    Werdykt dla źródła, które rzuciło wyjątkiem.

    Limit zapytań albo captcha NIGDY nie są dowodem, że scraper jest zepsuty -
    portal odpowiedział, tylko odmówił. Zapisanie tego jako awarii kończy się
    wyłączaniem działających źródeł.
    """
    lowered = (error or "").lower()
    if any(marker in lowered for marker in _BLOCKED_MARKERS):
        return "inconclusive", f"portal odmówił obsługi ({error[:80]})"
    return "broken", f"wyjątek: {error[:120]}"


# Udział blokad, powyżej którego dociąganie opisów uznajemy za zepsute,
# nawet jeśli część opisów wróciła.
BLOKADY_PROG = 0.5


def enrich_verdict(enrich) -> str | None:
    """
    Dociaganie opisow, ktore nie przyniosło ani jednego opisu, to awaria -
    nawet gdy sam przebieg zebral komplet ofert.

    Liczba ofert wygladala wtedy normalnie i przebieg konczyl sie sukcesem,
    a do bazy szly zaslepki zamiast tresci. `blocked` (403) wyroznione osobno,
    bo tylko ono mowi wprost, ze portal odmawia tej drogi pobierania.
    """
    if not isinstance(enrich, dict):
        return None
    ok = enrich.get("ok", 0)
    blocked = enrich.get("blocked", 0)
    proby = ok + enrich.get("error", 0) + blocked
    if proby == 0:
        return None
    if not ok:
        if blocked:
            return f"portal odrzucił wszystkie {proby} zapytań o opis (403)"
        return f"żadne z {proby} zapytań o opis nie zwróciło treści"
    # Sam fakt, że COKOLWIEK wróciło, nie znaczy, że dociąganie działa.
    # Przebieg z 1 września 2026 miał ok: 24 przy blocked: 1137 i nie dostał
    # ani jednej linijki w raporcie, bo warunek pytał wyłącznie o zero.
    if blocked / proby >= BLOKADY_PROG:
        return (f"portal odrzucił {blocked} z {proby} zapytań o opis (403) - "
                f"opisy wróciły tylko dla {ok}")
    return None


def check(source_name: str, count: int, jobs=(), history=(), error=None,
          domain=None, enrich=None) -> dict | None:
    """Jeden werdykt dla jednego źródła. None = zdrowe, czyli milczymy."""
    if error:
        verdict, detail = error_verdict(error)
        return {"source": source_name, "verdict": verdict, "detail": detail}

    brak_opisow = enrich_verdict(enrich)
    if brak_opisow:
        return {"source": source_name, "verdict": "broken", "detail": brak_opisow}

    verdict, detail = yield_verdict(count, list(history))
    symptoms = scan_records(jobs, domain)

    if symptoms:
        # Uszkodzone dane biją chudy przebieg: 200 ofert bez nazwy firmy to
        # gorszy stan niż 20 ofert poprawnych.
        return {"source": source_name, "verdict": "degraded",
                "detail": "; ".join(symptoms)}

    if verdict == "ok":
        return None
    return {"source": source_name, "verdict": verdict, "detail": detail}


def format_report(findings, skipped_no_key=(), disabled=()) -> str:
    """
    Blok do wypisania na końcu przebiegu.

    Zdrowe źródła nie dostają linijki - raport, w którym wszystko jest zielone,
    przestaje być czytany po tygodniu. Pominięte źródła wypisujemy zawsze, bo
    świadoma rezygnacja i awaria wyglądają w logu identycznie: brakiem ofert.
    """
    lines = []

    if skipped_no_key:
        lines.append(f"pominięte (brak klucza API): {', '.join(sorted(skipped_no_key))}")
    if disabled:
        lines.append(f"pominięte (wyłączone w konfiguracji): {', '.join(sorted(disabled))}")

    for finding in sorted(findings, key=lambda f: f["source"]):
        lines.append(f"health: {finding['source']} - {finding['verdict']} ({finding['detail']})")

    if not lines:
        return ""

    header = "SCRAPER HEALTH"
    return "\n".join(["", "=" * 60, header, "=" * 60, *lines,
                      "", "Naprawa jednego źródła: python main_scraper.py --only <nazwa> --force"])
