"""
Modele danych: oferta, ocena dopasowania, baza ofert, stan scraperów.
"""

from dataclasses import dataclass, fields as dc_fields
from typing import Optional
import json
import re
import threading
from utils.safe_io import save_json_atomic
from utils.offer_fields import STRUCTURED_FIELDS

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


class JobDatabase:
    """
    Prosta baza ofert trzymana w pliku JSON.

    Trzyma wczytaną zawartość w pamięci razem z indeksem po kanonicznym linku.
    Powód: `main_scraper` woła `record_scrape` po każdym z 14 źródeł, a plik ma
    ~27 MB - poprzednia wersja parsowała go i budowała indeks od nowa przy
    każdym wywołaniu. Cache jest unieważniany, gdy plik zmieni się pod spodem
    (inny proces, ręczna edycja), więc nie da się nim nadpisać cudzych zmian.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._jobs = None
        self._by_link = None
        self._stamp = None

    # --- cache ---------------------------------------------------------------

    def _file_stamp(self):
        import os
        try:
            st = os.stat(self.filepath)
            return (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return None

    def _index(self) -> dict:
        """Mapa kanoniczny link -> Job dla aktualnie wczytanej listy."""
        if self._by_link is None:
            from utils.links import canonical_link
            self._by_link = {canonical_link(job.link): job for job in self._jobs}
        return self._by_link

    # --- odczyt/zapis --------------------------------------------------------

    def load_jobs(self) -> list[Job]:
        if self._jobs is not None and self._stamp == self._file_stamp():
            return self._jobs

        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                jobs_data = json.load(f)
            self._jobs = [Job.from_dict(d) for d in jobs_data]
        except FileNotFoundError:
            self._jobs = []

        self._by_link = None
        self._stamp = self._file_stamp()
        return self._jobs

    def save_jobs(self, jobs: list[Job]):
        """Zapis atomowy - przerwany zapis nie zostawia obciętego pliku."""
        from datetime import datetime
        from utils.safe_io import save_json_atomic

        now = datetime.now().isoformat()
        for job in jobs:
            if not job.scraped_at:
                job.scraped_at = now

        save_json_atomic(self.filepath, [job.to_dict() for job in jobs], indent=2)

        self._jobs = jobs
        self._by_link = None
        self._stamp = self._file_stamp()

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
        mamy (`skip_known_details`, 60-90% listingu). Osobne przebiegi po
        jednym i po drugim przepisywały plik ~27 MB dwa razy na każde z 14
        źródeł, a i tak kończyły się tym samym stanem.

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

        added, touched = [], 0

        for job in new_jobs:
            link = canonical_link(job.link)
            known = by_link.get(link)

            if known is None:
                job.scraped_at = job.scraped_at or now
                job.last_seen = job.last_seen or now
                added.append(job)
                by_link[link] = job
                continue

            # Ofertę pobraną ponownie liczymy jako zobaczoną - inaczej źródło,
            # które oddało same znane oferty, nie zostawiłoby po sobie ŻADNEGO
            # śladu i zapis zostałby pominięty razem z uzupełnionymi datami.
            self._mark_seen(known, now)
            touched += 1
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
            job = by_link.get(canonical_link(link)) if link else None
            if job is None:
                continue
            self._mark_seen(job, now)
            touched += 1

        if added or touched:
            # save_jobs zeruje indeks, a mamy go już aktualnego - odtwarzamy po zapisie
            self.save_jobs(jobs + added)
            self._by_link = by_link

        return len(added), touched


class ScraperStatusManager:
    """Pilnuje, które źródła zostały dziś poprawnie zescrapowane."""
    _lock = threading.Lock()

    def __init__(self, filepath: str = "scraper_status.json"):
        self.filepath = filepath

    def load_status(self) -> dict:
        with self._lock:
            return self._read()

    def save_status(self, status_data: dict):
        with self._lock:
            self._write(status_data)

    def is_scraped_today(self, source_name: str) -> bool:
        """Czy źródło zostało dziś poprawnie zescrapowane."""
        from datetime import datetime

        with self._lock:
            entry = self._read().get(source_name, {})

        return (entry.get('last_run_date') == datetime.now().date().isoformat()
                and entry.get('status') == 'success')

    def mark_as_completed(self, source_name: str, jobs_count: int):
        """Odnotowuje udany dzisiejszy scraping źródła."""
        from datetime import datetime

        now = datetime.now()
        with self._lock:
            data = self._read()
            entry = data.get(source_name, {})
            entry.update({
                'last_run_date': now.date().isoformat(),
                'status': 'success',
                'jobs_count': jobs_count,
                'timestamp': now.isoformat(),
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
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict):
        save_json_atomic(self.filepath, data, backup=False)
