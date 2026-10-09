"""
Modele danych: oferta, ocena dopasowania, baza ofert, stan scraperów.
"""

from dataclasses import dataclass, fields as dc_fields
from pathlib import Path
from typing import Optional
import re
import threading
from utils.safe_io import load_json_safe, save_json_atomic
from utils.offer_fields import STRUCTURED_FIELDS
from utils.sqlite_store import RecordStore

# Zaślepka, którą scraper zapisuje, gdy portal nie oddał treści ogłoszenia:
# „Oferta z JustJoinIT: Tytuł”, „Oferta z OLX (kategoria: …)”. Jedna linia, sam tytuł.
_PLACEHOLDER_RE = re.compile(r"^Oferta z [^\n]{1,250}$")


def is_placeholder_description(text: Optional[str]) -> bool:
    """True, gdy opisu brak albo to zaślepka scrapera, a nie treść ogłoszenia."""
    stripped = (text or "").strip()
    return not stripped or bool(_PLACEHOLDER_RE.match(stripped))


@dataclass
class Job:
    """Pojedyncza oferta pracy."""
    title: str
    company: str
    link: str
    description: str
    source: str
    location: Optional[str] = None
    posted_date: Optional[str] = None
    scraped_at: Optional[str] = None  # ISO format timestamp - PIERWSZE zobaczenie
    # Data wygaśnięcia deklarowana przez pracodawcę (validThrough z ld+json).
    # Twardy sygnał martwej oferty - nie trzeba niczego szacować.
    valid_through: Optional[str] = None
    # Kiedy ofertę widzieliśmy OSTATNI raz i w ilu przebiegach się pojawiła.
    # `last_seen` wykrywa oferty zdjęte z portalu (utils/liveness.py) - także
    # dla źródeł, które nie podają żadnej daty (Pracuj.pl to 62% bazy).
    last_seen: Optional[str] = None
    times_seen: int = 1
    # Pozostałe portale, na których wisi ta sama oferta - wypełniane przez
    # deduplicate_db.py. Musi być polem dataclassy, bo Job.from_dict czyta
    # wyłącznie znane pola, a to_dict() robi asdict(): każdy klucz spoza
    # dataclassy zniknąłby przy pierwszym zapisie scrapera.
    also_on: Optional[list] = None
    # Cechy strukturalne - kody i kształty opisane w utils/offer_fields.py.
    # Scraper wypełnia to, co portal podaje jako osobne pola; resztę
    # (lata doświadczenia, języki) uzupełnia z treści utils.offer_fields.text_features.
    # None = portal tego nie podał.
    seniority: Optional[list] = None
    work_modes: Optional[list] = None
    contract_types: Optional[list] = None
    schedules: Optional[list] = None
    salary: Optional[dict] = None
    skills_required: Optional[list] = None
    skills_nice: Optional[list] = None
    languages: Optional[list] = None
    category: Optional[str] = None
    years_required: Optional[int] = None
    logo_url: Optional[str] = None
    
    def __post_init__(self):
        from utils.text_cleaner import clean_job_description
        from utils.offer_fields import languages_from_text, years_from_text
        if self.description:
            self.description = clean_job_description(self.description)
            # Pola z portalu mają pierwszeństwo; treść uzupełnia tylko braki.
            if self.years_required is None:
                self.years_required = years_from_text(self.description)
            if self.languages is None:
                self.languages = languages_from_text(self.description)
    
    def to_dict(self):
        """
        Postać do zapisu w JSON.

        Ręcznie, a nie przez dataclasses.asdict(): asdict robi głęboką kopię
        każdego pola, a przy 17 tys. ofert zapisywanych po każdym źródle to
        widoczny koszt bez żadnego zysku - wszystkie pola są płaskie.
        """
        return {name: getattr(self, name) for name in _JOB_FIELDS}

    @classmethod
    def from_dict(cls, data: dict):
        """
        Zbuduj ofertę z rekordu z pliku, z pominięciem __init__.

        __post_init__ czyści opis (clean_job_description), a rekordy z bazy są
        już wyczyszczone - powtarzanie tego przy każdym wczytaniu bazy kosztuje
        sekundy i niczego nie zmienia. Nieznane klucze są ignorowane celowo.
        """
        obj = cls.__new__(cls)
        for name in _JOB_FIELDS:
            setattr(obj, name, data.get(name))
        return obj


# Nazwy pól Job - liczone raz, używane w to_dict/from_dict przy każdym rekordzie.
_JOB_FIELDS = tuple(f.name for f in dc_fields(Job))


@dataclass
class JobMatch:
    """Oferta razem z procentem dopasowania (None = bez oceny)."""
    job: Job
    match_percentage: Optional[int] = None


LEGACY_JOBS_JSON = "jobs_database.json"


def job_key(record: dict) -> str:
    from utils.links import canonical_link
    return canonical_link(record.get("link") or "")


class JobDatabase:
    """
    Baza ofert w pliku SQLite (`utils/sqlite_store.py`): wiersz na ofertę, klucz to
    kanoniczny link, treść to rekord `Job.to_dict()` w JSON.

    Zapis dotyka tylko zmienionych wierszy. Cache wczytanej listy jest ważny, dopóki
    licznik zmian bazy (`meta.rev`) się nie przesunie, więc inny proces nie zostanie nadpisany.
    """

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self._store = RecordStore(self.filepath, self.filepath.with_name(LEGACY_JOBS_JSON), job_key)
        self._jobs = None
        self._by_link = None
        self._rev = None

    def _index(self) -> dict:
        """Mapa kanoniczny link -> Job dla aktualnie wczytanej listy."""
        if self._by_link is None:
            from utils.links import canonical_link
            self._by_link = {canonical_link(job.link): job for job in self._jobs}
        return self._by_link

    def revision(self) -> int:
        return self._store.revision()

    def count(self) -> int:
        return self._store.count()

    def backup(self):
        return self._store.backup()

    def load_jobs(self) -> list[Job]:
        if self._jobs is not None and self._rev == self._store.revision():
            return self._jobs
        records = self._store.load()
        self._jobs = [Job.from_dict(r) for r in records.values()]
        self._by_link = None
        self._rev = self._store.rev
        return self._jobs

    def load_records(self) -> list[dict]:
        return list(self._store.load().values())

    def save_records(self, records) -> tuple[int, int]:
        by_key = {}
        for record in records:
            key = job_key(record)
            if key:
                by_key[key] = record
        result = self._store.sync(by_key)
        self._jobs = None
        self._by_link = None
        return result

    def put_jobs(self, jobs: list[Job]) -> None:
        from datetime import datetime

        now = datetime.now().isoformat()
        rows = {}
        for job in jobs:
            job.scraped_at = job.scraped_at or now
            record = job.to_dict()
            key = job_key(record)
            if key:
                rows[key] = record
        self._store.put(rows)
        self._jobs = None
        self._by_link = None

    def delete_links(self, links) -> int:
        from utils.links import canonical_link
        removed = self._store.delete({canonical_link(link) for link in links if link})
        self._jobs = None
        self._by_link = None
        return removed

    # --- operacje ------------------------------------------------------------

    @staticmethod
    def _mark_seen(job: Job, now: str):
        """
        Odnotuj, że oferta nadal wisi na portalu.

        `last_seen` to podstawa wykrywania ofert zdjętych z portalu
        (utils/liveness.py); działa także dla źródeł, które nie podają żadnej daty.
        """
        job.last_seen = now
        job.times_seen = (job.times_seen or 1) + 1

    def record_scrape(self, new_jobs: list[Job] = (), seen_again=()) -> tuple[int, int]:
        """
        Zapisz wynik jednego źródła: nowe oferty plus ślad po już znanych.

        Zwraca (ile dopisano, ilu ofertom odświeżono `last_seen`).

        Jeden zapis na źródło, nie dwa. Scrapery zwracają OBIE rzeczy naraz -
        pobrane oferty i linki, których stron celowo nie pobierały, bo już je
        mamy (`skip_known_details`, 60-90% listingu). Zapis obejmuje tylko
        dopisane i odświeżone wiersze.

        Istniejące rekordy NIE są nadpisywane (od tego jest --refresh);
        uzupełniamy tylko daty i cechy, których poprzedni przebieg nie znał,
        oraz treść w miejsce zaślepki (`is_placeholder_description`) - takie
        oferty `known_links` celowo podaje scraperom jako nowe. Linki
        nieznane bazie są ignorowane - nie tworzymy pustych rekordów.
        """
        from datetime import datetime
        from utils.links import canonical_link

        jobs = self.load_jobs()
        by_link = self._index()
        now = datetime.now().isoformat()

        added, touched, changed = [], 0, {}

        for job in new_jobs:
            link = canonical_link(job.link)
            known = by_link.get(link)

            if known is None:
                job.scraped_at = job.scraped_at or now
                job.last_seen = job.last_seen or now
                added.append(job)
                by_link[link] = job
                changed[link] = job
                continue

            # Ofertę pobraną ponownie liczymy jako zobaczoną - inaczej źródło,
            # które oddało same znane oferty, nie zostawiłoby po sobie ŻADNEGO
            # śladu i zapis zostałby pominięty razem z uzupełnionymi datami.
            self._mark_seen(known, now)
            touched += 1
            changed[link] = known
            # Uzupełnij daty i cechy, których poprzedni przebieg nie znał, a ten zna
            if job.posted_date and not known.posted_date:
                known.posted_date = job.posted_date
            if job.valid_through and not known.valid_through:
                known.valid_through = job.valid_through
            if job.logo_url and not known.logo_url:
                known.logo_url = job.logo_url
            if (is_placeholder_description(known.description)
                    and not is_placeholder_description(job.description)):
                known.description = job.description
            for name in STRUCTURED_FIELDS:
                value = getattr(job, name)
                if value is not None and getattr(known, name) is None:
                    setattr(known, name, value)

        for link in seen_again:
            key = canonical_link(link) if link else None
            job = by_link.get(key) if key else None
            if job is None:
                continue
            self._mark_seen(job, now)
            touched += 1
            changed[key] = job

        if changed:
            self._store.put({key: job.to_dict() for key, job in changed.items()})
            self._jobs = jobs + added
            self._by_link = by_link
            self._rev = self._store.rev

        return len(added), touched


class ScraperStatusManager:
    """Pilnuje, które źródła zostały dziś poprawnie zescrapowane."""
    _lock = threading.Lock()

    def __init__(self, filepath: str = str(Path(__file__).resolve().parent.parent / "scraper_status.json")):
        self.filepath = filepath

    def load_status(self) -> dict:
        with self._lock:
            return self._read()

    def save_status(self, status_data: dict):
        with self._lock:
            self._write(status_data)

    def is_scraped_today(self, source_name: str) -> bool:
        """Czy źródło zostało dziś poprawnie zescrapowane dla aktywnego kandydata."""
        from datetime import datetime
        from utils import candidates

        with self._lock:
            entry = self._read().get(source_name, {})

        return (entry.get('last_run_date') == datetime.now().date().isoformat()
                and entry.get('status') == 'success'
                and entry.get('candidate', candidates.FIRST_ID) == candidates.active_id())

    def mark_as_completed(self, source_name: str, jobs_count: int):
        """Odnotowuje udany dzisiejszy scraping źródła."""
        from datetime import datetime
        from utils import candidates

        now = datetime.now()
        with self._lock:
            data = self._read()
            entry = data.get(source_name, {})
            entry.update({
                'last_run_date': now.date().isoformat(),
                'status': 'success',
                'jobs_count': jobs_count,
                'timestamp': now.isoformat(),
                'candidate': candidates.active_id(),
            })
            data[source_name] = entry
            self._write(data)

    def record_yield(self, source_name: str, jobs_count: int):
        """
        Dopisz wynik przebiegu do historii źródła.

        Osobno od mark_as_completed, bo tamto odnotowuje TYLKO przebiegi powyżej
        progu - a przebieg, który przyniósł zero ofert, jest dokładnie tym, co
        chcemy zapamiętać. Bez tego awaria nie zostawia po sobie żadnego śladu.

        Historia jest polem dokładanym: wpisy sprzed jej wprowadzenia go nie mają
        i nie uzupełniamy ich wstecz - liczby z tamtych przebiegów nie istnieją,
        a zmyślone popsułyby medianę, na której stoi cała diagnoza.
        """
        from datetime import datetime
        from utils.scraper_health import HISTORY_LEN

        now = datetime.now()
        with self._lock:
            data = self._read()
            entry = data.setdefault(source_name, {})
            history = entry.get('history') or []
            history.append({'date': now.date().isoformat(), 'count': jobs_count})
            entry['history'] = history[-HISTORY_LEN:]
            self._write(data)

    def record_liveness_scope(self, source_name: str, scope: Optional[str]):
        """Zakres pełnego listingu źródła (utils/liveness.py); None = listing nie jest pełny."""
        from datetime import datetime

        with self._lock:
            data = self._read()
            entry = data.setdefault(source_name, {})
            if scope is None:
                if entry.pop('liveness', None) is None:
                    return
            elif (entry.get('liveness') or {}).get('scope') != scope:
                entry['liveness'] = {'scope': scope, 'since': datetime.now().date().isoformat()}
            else:
                return
            self._write(data)

    def get_history(self, source_name: str) -> list:
        """Ostatnie przebiegi źródła; pusta lista, gdy nic o nim nie wiemy."""
        with self._lock:
            return (self._read().get(source_name, {}) or {}).get('history') or []

    # --- jedyne miejsce dotykające pliku -------------------------------------

    def _read(self) -> dict:
        data = load_json_safe(self.filepath, default={})
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict):
        save_json_atomic(self.filepath, data, backup=False)
