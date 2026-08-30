"""
Deduplikacja bazy ofert ("Highlander Protocol").

Ta sama oferta bywa publikowana na kilku portalach albo wielokrotnie na jednym.
Odcisk palca to: znormalizowana firma | znormalizowany tytuł | miasto.

DLACZEGO NIE PO OPISIE (poprzednia wersja brała `desc[:50]`):
przy cross-postingu każdy portal trzyma inny tekst tej samej oferty - Pracuj.pl
zapisuje wygenerowane `aiSummary` ("Masz doświadczenie..."), a OLX surowy tekst
pracodawcy. Zmierzone na bazie 8453 ofert: podobieństwo opisów w parach
międzyportalowych jest dwugarbne (p25=0.13, mediana=0.75), więc żaden próg nie
rozdziela duplikatów od różnych ofert. Odcisk po opisie łapał 156 duplikatów,
odcisk po mieście - 614, z czego 199 to pary międzyportalowe, których stara
wersja nie widziała w ogóle.

DLACZEGO FAŁSZYWE SKLEJENIE JEST TANIE: scalony rekord zachowuje linki do
wszystkich wystąpień w polu `also_on`. Nawet gdy klucz połączy dwie różne
oferty tej samej agencji o identycznym tytule, druga nadal jest jednym
kliknięciem - a UI pokazuje "także na: ...".

WAŻNE: przy wyborze, który duplikat zachować, oferta z decyzją użytkownika ma
pierwszeństwo przed dłuższym opisem. Inaczej deduplikacja kasowała ofertę,
którą oceniłeś, a ocena traciła powiązanie i przepadała.

Bez pandas celowo: `to_dict('records')` wstawia NaN w brakujące pola, a NaN
serializuje się do JSON-a jako gołe `NaN`, czego nie da się potem wczytać.
"""

import logging
import re
from pathlib import Path

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("HighlanderProtocol_v3")

# Formy prawne i sufiksy, które nie odróżniają firm od siebie
_LEGAL_FORMS = re.compile(
    r"\b(sp\.?\s*z\s*o\.?\s*o\.?|spolka|spółka|s\.?\s*a\.?|sp\.?\s*k\.?|"
    r"sp\.?\s*j\.?|s\.?\s*c\.?|z\s*o\.?\s*o\.?|ltd|llc|gmbh|inc|corp)\b",
    re.IGNORECASE,
)
# "(K/M)", "k/m/n", "m/k" - ten sam etat, inny zapis na każdym portalu
_GENDER_TAG = re.compile(r"\(?\b[kmn](\s*/\s*[kmn])+\b\)?", re.IGNORECASE)
_POSTAL_CODE = re.compile(r"\d{2}-\d{3}")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

# Nazwy, które nie identyfikują pracodawcy - przy nich klucz musi opierać się
# na czymś jeszcze, inaczej wszystkie "ukryta firma | magazynier | warszawa"
# zlałyby się w jeden rekord.
_GENERIC_COMPANY = ("praca", "ukryta", "confidential", "pracodawca", "klient", "firma")


def normalize_company(company: str) -> str:
    c = (company or "").lower()
    c = _LEGAL_FORMS.sub(" ", c)
    c = _PUNCT.sub(" ", c)
    return _WS.sub(" ", c).strip()


def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = _GENDER_TAG.sub(" ", t)
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def normalize_city(location: str) -> str:
    """
    Sprowadź lokalizację do samego miasta. Portale zapisują je bardzo różnie:
    "01-570 Warszawa" (Indeed), "Warszawa-Żoliborz" (praca.pl),
    "Warszawa, mazowieckie" (aplikuj.pl) - bez tego ta sama oferta ma trzy
    różne klucze.
    """
    loc = (location or "").lower()
    loc = loc.split(",")[0]
    loc = _POSTAL_CODE.sub(" ", loc)
    loc = _PUNCT.sub(" ", loc)
    loc = _WS.sub(" ", loc).strip()
    if "warszawa" in loc or "warsaw" in loc:
        return "warszawa"
    return loc


def _clean_description(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").lower())


def create_fingerprint(job: dict) -> str:
    """Klucz grupujący duplikaty: firma | tytuł | miasto."""
    company = normalize_company(job.get("company"))
    title = normalize_title(job.get("title"))
    city = normalize_city(job.get("location"))

    if not company or any(k in company for k in _GENERIC_COMPANY):
        # Bez wiarygodnej nazwy firmy opieramy się na początku opisu -
        # tak jak robiła to wersja poprzednia.
        return f"unknown|{title}|{city}|{_clean_description(job.get('description'))[:50]}"

    return f"{company}|{title}|{city}"


def _sanitize_nan(job: dict) -> int:
    """
    Zamień NaN na None. Poprzednia wersja szła przez pandas, a `to_dict('records')`
    wstawia NaN w brakujące pola - w efekcie oba pliki danych zawierały gołe
    `NaN`, czyli NIEPOPRAWNY JSON (zmierzone: 5312 rekordów w bazie, wszystkie
    w `posted_date`). Python `json.load` to przepuszcza, ale każdy inny parser
    się na tym wywraca, a `posted_date` jest potrzebne do liczenia wieku oferty.
    """
    fixed = 0
    for key, value in job.items():
        if isinstance(value, float) and value != value:  # NaN != NaN
            job[key] = None
            fixed += 1
    return fixed


def _decided_links() -> set:
    """Linki, na których użytkownik podjął decyzję - chronione przed usunięciem."""
    decisions = load_json_safe("user_decisions.json", default={})
    return {canonical_link(k) for k in decisions}


def _merge_provenance(winner: dict, losers: list) -> bool:
    """
    Dopisz do zachowanego rekordu, gdzie jeszcze wisi ta sama oferta.
    Scala się z tym, co już tam było - deduplikacja bywa uruchamiana wielokrotnie
    i wcześniejsze wystąpienia nie mogą wyparować.

    Zwraca True, gdy rekord faktycznie się zmienił - `_apply` po tym poznaje,
    czy plik trzeba w ogóle przepisać.
    """
    entries = list(winner.get("also_on") or [])
    seen = {e.get("link") for e in entries if isinstance(e, dict)}
    seen.add(winner.get("link"))

    for job in losers:
        link = job.get("link")
        if not link or link in seen:
            continue
        seen.add(link)
        entries.append({"source": job.get("source"), "link": link})
        # Przenieś też to, co zebrał wcześniej odrzucany rekord
        for nested in (job.get("also_on") or []):
            nlink = nested.get("link") if isinstance(nested, dict) else None
            if nlink and nlink not in seen:
                seen.add(nlink)
                entries.append(nested)

    nowe = entries or None
    if nowe == winner.get("also_on") and "also_on" in winner:
        return False
    winner["also_on"] = nowe
    return True


def _plan(all_jobs: dict, decided: set) -> tuple:
    """
    Zdecyduj RAZ dla wszystkich plików, kogo zostawiamy w każdej grupie.

    `all_jobs` to unia rekordów z obu plików (link -> najbogatszy wariant).
    Decyzja musi powstać na unii, a nie osobno per plik: pliki mają różną
    zawartość (8453 vs 8325 rekordów) i różne długości opisów, więc niezależna
    deduplikacja wybierała innego zwycięzcę w 18 grupach - a to dokładnie ten
    rozjazd, przez który ocena zostaje bez oferty.

    Zwraca (zbiór linków do zachowania, {link_główny: [proweniencja]}).
    """
    groups = {}
    for link, job in all_jobs.items():
        groups.setdefault(create_fingerprint(job), []).append(link)

    keep, provenance = set(), {}
    stats = {"groups": 0, "cross": 0, "extra_decided": 0, "dropped": 0}

    for links in groups.values():
        links.sort(key=lambda l: (
            0 if l in decided else 1,
            -len(all_jobs[l].get("description") or ""),
            l,
        ))

        # Oferty z decyzją NIE są ze sobą scalane. Ta sama praca oceniona na
        # dwóch portalach to dwie decyzje w user_decisions.json - zostawienie
        # jednego rekordu osierociłoby drugą ocenę. Scalanie i tak nic by tu
        # nie dało: te oferty są już przeanalizowane, więc nie kosztują tokenów.
        decided_links = [l for l in links if l in decided]
        if decided_links:
            keepers = decided_links
            dropped = [l for l in links if l not in decided]
            stats["extra_decided"] += len(decided_links) - 1
        else:
            keepers, dropped = links[:1], links[1:]

        keep.update(keepers)

        if dropped:
            stats["groups"] += 1
            stats["dropped"] += len(dropped)
            if len({all_jobs[l].get("source") for l in links}) > 1:
                stats["cross"] += 1
            provenance[keepers[0]] = [
                {"source": all_jobs[l].get("source"), "link": l} for l in dropped
            ]

    return keep, provenance, stats


def _apply(path: Path, is_analyzed: bool, keep: set, provenance: dict, decided: set, data=None):
    """
    Zapisz plik zachowując wyłącznie rekordy wskazane przez wspólny plan.

    `data` to zawartość wczytana już przez wołającego. Plan i tak powstaje na
    treści obu plików, więc bez tego argumentu każdy z nich (27 i 35 MB) był
    parsowany drugi raz tylko po to, żeby dostać te same obiekty.
    """
    if data is None:
        if not path.exists():
            logger.warning(f"File not found: {path}")
            return
        data = load_json_safe(path, default=[])

    if not data:
        logger.info(f"{path.name} is empty - skipping.")
        return

    def job_of(item):
        return item["job"] if is_analyzed else item

    initial = len(data)
    final, seen_links, nan_fixed, scalone = [], set(), 0, 0

    for item in data:
        job = job_of(item)
        nan_fixed += _sanitize_nan(job)
        link = canonical_link(job.get("link", ""))

        if link not in keep or link in seen_links:
            continue
        seen_links.add(link)

        extra = provenance.get(link)
        if extra and _merge_provenance(job, extra):
            scalone += 1
        final.append(item)

    # Sanity check: żadna oceniona oferta nie mogła zniknąć
    before = sum(1 for i in data if canonical_link(job_of(i).get("link", "")) in decided)
    after = sum(1 for i in final if canonical_link(job_of(i).get("link", "")) in decided)
    if after < before:
        logger.error(
            f"Deduplication would remove {before - after} rated offers "
            f"- aborting the write to {path.name}."
        )
        return

    if nan_fixed:
        logger.info(f"   {path.name}: fixed {nan_fixed} NaN fields (invalid JSON) -> null")

    # Zapis tylko przy realnej zmianie. Przy bazie bez duplikatow ten etap
    # przepisywal caly plik razem z kopia zapasowa, zeby odtworzyc go bajt
    # w bajt - a rotacja i tak kasowala te kopie w tym samym przebiegu.
    if len(final) != initial or nan_fixed or scalone:
        save_json_atomic(path, final, backup=True)
        logger.info(f"{path.name}: {initial} -> {len(final)} (removed {initial - len(final)})")
    else:
        logger.info(f"{path.name}: {initial} ofert, brak duplikatow - plik bez zmian")


def deduplicate_file(filepath, is_analyzed=False):
    """Deduplikacja pojedynczego pliku (plan liczony z tego samego pliku)."""
    path = Path(filepath)
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return

    data = load_json_safe(path, default=[])
    if not data:
        return

    decided = _decided_links()
    all_jobs = {}
    for item in data:
        job = item["job"] if is_analyzed else item
        _sanitize_nan(job)
        all_jobs[canonical_link(job.get("link", ""))] = job

    keep, provenance, _ = _plan(all_jobs, decided)
    _apply(path, is_analyzed, keep, provenance, decided, data=data)


def run():
    logger.info("Starting Highlander Protocol v3...")

    files = [
        (Path("analyzed_jobs_waterfall.json"), True),
        (Path("jobs_database.json"), False),
    ]

    decided = _decided_links()

    # Unia obu plików - dla każdego linku bierzemy wariant z najdłuższym opisem,
    # żeby decyzja opierała się na najlepszej dostępnej wersji rekordu.
    # Wczytana zawartość leci dalej do _apply: to te same obiekty, więc drugi
    # odczyt tych plików (27 i 35 MB) niczego by nie wniósł.
    loaded, all_jobs = {}, {}
    for path, is_analyzed in files:
        if not path.exists():
            continue
        loaded[path] = load_json_safe(path, default=[])
        for item in loaded[path]:
            job = item["job"] if is_analyzed else item
            _sanitize_nan(job)
            link = canonical_link(job.get("link", ""))
            if not link:
                continue
            current = all_jobs.get(link)
            if current is None or len(job.get("description") or "") > len(current.get("description") or ""):
                all_jobs[link] = job

    keep, provenance, stats = _plan(all_jobs, decided)
    logger.info(
        f"Plan for {len(all_jobs)} unique offers: keeping {len(keep)}, "
        f"merged {stats['dropped']} in {stats['groups']} groups "
        f"({stats['cross']} across boards)"
    )
    if stats["extra_decided"]:
        logger.info(
            f"   kept {stats['extra_decided']} extra records carrying a decision "
            f"(duplicates, but merging would orphan the rating)"
        )

    for path, is_analyzed in files:
        _apply(path, is_analyzed, keep, provenance, decided, data=loaded.get(path))

    logger.info("Protocol v3 Complete. Your data is safe.")


if __name__ == "__main__":
    run()
