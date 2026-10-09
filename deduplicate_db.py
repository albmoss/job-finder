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
oferty tej samej agencji o identycznym tytule, link do drugiej zostaje w bazie.

JEDNA OFERTA, KILKA MIAST: NoFluffJobs, JustJoinIT i RocketJobs dają osobny link
na każdą lokalizację (`…-remote`, `…-wroclaw`), z tym samym tekstem. Klucz
z miastem ich nie łączy, więc lista pokazywała tę samą ofertę trzy razy, a każda
kopia szła osobno do płatnej oceny. Drugi klucz - firma | tytuł | cały opis -
skleja takie grupy mimo różnych miast. Sam tytuł bez opisu nie wystarcza: firma
potrafi szukać na to samo stanowisko w dwóch miastach do dwóch różnych zespołów.

WAŻNE: przy wyborze, który duplikat zachować, oferta z decyzją użytkownika ma
pierwszeństwo przed dłuższym opisem. Inaczej deduplikacja kasowała ofertę,
którą zapisałeś albo ukryłeś, a decyzja traciła powiązanie i przepadała.

Bez pandas celowo: `to_dict('records')` wstawia NaN w brakujące pola, a NaN
serializuje się do JSON-a jako gołe `NaN`, czego nie da się potem wczytać.
"""

import hashlib
import logging
import re

import config
from utils import candidates, telemetry
from utils.data_models import JobDatabase
from utils.links import canonical_link

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

# Lokalizacje zdalne po normalize_city - przy sklejaniu miast wygrywa taki wariant.
_REMOTE = {"remote", "zdalnie", "zdalna", "praca zdalna", "fully remote"}


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


# Krótszy opis to zwykle zaślepka portalu ("Zobacz ogłoszenie"), a nie tekst oferty -
# równość zaślepek niczego nie dowodzi.
_MIN_SAME_TEXT = 200


def same_text_key(job: dict):
    """Klucz tej samej oferty wystawionej pod kilkoma miastami: firma | tytuł | skrót opisu."""
    company = normalize_company(job.get("company"))
    if not company or any(k in company for k in _GENERIC_COMPANY):
        return None
    text = _clean_description(job.get("description"))
    if len(text) < _MIN_SAME_TEXT:
        return None
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{company}|{normalize_title(job.get('title'))}|{digest}"


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
    """Linki, na których któryś kandydat podjął decyzję - chronione przed usunięciem."""
    return candidates.decided_links()


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
    rozjazd, przez który decyzja zostaje bez oferty.

    Zwraca (zbiór linków do zachowania, {link_główny: [proweniencja]}).
    """
    groups = {}
    for link, job in all_jobs.items():
        groups.setdefault(create_fingerprint(job), []).append(link)

    # Grupy z różnych miast, które łączy identyczny tekst oferty, stają się jedną.
    parent = {key: key for key in groups}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    first_by_text = {}
    for link, job in all_jobs.items():
        text_key = same_text_key(job)
        if text_key is None:
            continue
        fp = create_fingerprint(job)
        if text_key in first_by_text:
            parent[root(fp)] = root(first_by_text[text_key])
        else:
            first_by_text[text_key] = fp
    merged = {}
    for key, links in groups.items():
        merged.setdefault(root(key), []).extend(links)

    keep, provenance = set(), {}
    stats = {"groups": 0, "cross": 0, "extra_decided": 0, "dropped": 0}

    for links in merged.values():
        # Przy równych opisach zostaje wariant zdalny - obejmuje każde miasto.
        links.sort(key=lambda l: (
            0 if l in decided else 1,
            -len(all_jobs[l].get("description") or ""),
            0 if normalize_city(all_jobs[l].get("location")) in _REMOTE else 1,
            l,
        ))

        # Oferty z decyzją NIE są ze sobą scalane. Ta sama praca zapisana na
        # dwóch portalach to dwie decyzje w user_decisions.json - zostawienie
        # jednego rekordu osierociłoby drugą decyzję. Scalanie i tak nic by tu
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


def _apply(db: JobDatabase, keep: set, provenance: dict, decided: set, data=None):
    """
    Zapisz bazę zachowując wyłącznie rekordy wskazane przez plan.

    `data` to zawartość wczytana już przez wołającego.
    """
    name = db.filepath.name
    if data is None:
        data = db.load_records()

    if not data:
        logger.info(f"{name} is empty - skipping.")
        return

    initial = len(data)
    final, seen_links, nan_fixed, scalone = [], set(), 0, 0

    for job in data:
        nan_fixed += _sanitize_nan(job)
        link = canonical_link(job.get("link", ""))

        if link not in keep or link in seen_links:
            continue
        seen_links.add(link)

        extra = provenance.get(link)
        if extra and _merge_provenance(job, extra):
            scalone += 1
        final.append(job)

    # Sanity check: żadna oferta z decyzją nie mogła zniknąć
    before = sum(1 for j in data if canonical_link(j.get("link", "")) in decided)
    after = sum(1 for j in final if canonical_link(j.get("link", "")) in decided)
    if after < before:
        logger.error(
            f"Deduplication would remove {before - after} offers with a decision "
            f"- aborting the write to {name}."
        )
        return

    if nan_fixed:
        logger.info(f"   {name}: fixed {nan_fixed} NaN fields -> null")

    if len(final) != initial or nan_fixed or scalone:
        db.save_records(final)
        logger.info(f"{name}: {initial} -> {len(final)} (removed {initial - len(final)})")
    else:
        logger.info(f"{name}: {initial} ofert, brak duplikatow - baza bez zmian")
    telemetry.emit("stage_stats", id="phase2", removed=initial - len(final))


def run():
    """
    Deduplikacja bazy ofert. Wyniki dopasowania nie wymagają osobnego przejścia:
    matching/run.py usuwa wyniki ofert, których nie ma już w bazie.
    """
    logger.info("Starting Highlander Protocol v3...")

    db = JobDatabase(config.JOBS_DATABASE_PATH)
    decided = _decided_links()

    # Dla każdego linku bierzemy wariant z najdłuższym opisem, żeby decyzja
    # opierała się na najlepszej dostępnej wersji rekordu.
    data = db.load_records()
    all_jobs = {}
    for job in data:
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
            f"(duplicates, but merging would orphan the decision)"
        )

    _apply(db, keep, provenance, decided, data=data)

    logger.info("Protocol v3 Complete. Your data is safe.")


if __name__ == "__main__":
    run()
