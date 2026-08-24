"""
Modele danych: oferta, ocena dopasowania, baza ofert, stan scraperów.
"""

from dataclasses import dataclass, fields as dc_fields
from typing import Optional
import json
import threading


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
    # To jedyny sygnał wieku działający dla źródeł, które nie podają żadnej daty
    # (Pracuj.pl to 62% bazy) - ogłoszenie wiszące tygodniami samo się zdradza.
    last_seen: Optional[str] = None
    times_seen: int = 1
    # Pozostałe portale, na których wisi ta sama oferta - wypełniane przez
    # deduplicate_db.py. Musi być polem dataclassy, bo Job.from_dict czyta
    # wyłącznie znane pola, a to_dict() robi asdict(): każdy klucz spoza
    # dataclassy zniknąłby przy pierwszym zapisie scrapera.
    also_on: Optional[list] = None
    
    def __post_init__(self):
        from utils.text_cleaner import clean_job_description
        if self.description:
            self.description = clean_job_description(self.description)
    
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
    """Oferta razem z oceną dopasowania od modelu."""
    job: Job
    match_percentage: int
    reason: str
    is_entry_level: bool
    missing_skills: Optional[list[str]] = None
    user_decision: Optional[str] = None  # „apply”, „reject” albo None
    learnable_in_month: bool = False
    industry: str = "Other"
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'job': self.job.to_dict(),
            'match_percentage': self.match_percentage,
            'reason': self.reason,
            'is_entry_level': self.is_entry_level,
            'missing_skills': self.missing_skills or [],
            'user_decision': self.user_decision,
            'learnable_in_month': self.learnable_in_month,
            'industry': self.industry
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        job = Job.from_dict(data['job'])
        return cls(
            job=job,
            match_percentage=data['match_percentage'],
            reason=data['reason'],
            is_entry_level=data.get('is_entry_level', False),
            missing_skills=data.get('missing_skills', []),
            user_decision=data.get('user_decision'),
            learnable_in_month=data.get('learnable_in_month', False),
            industry=data.get('industry', 'Other')
        )


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
        import os

        now = datetime.now().isoformat()
        for job in jobs:
            if not job.scraped_at:
                job.scraped_at = now

        temp_file = self.filepath + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump([job.to_dict() for job in jobs], f, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.filepath)

        self._jobs = jobs
        self._by_link = None
        self._stamp = self._file_stamp()

    # --- operacje ------------------------------------------------------------

    @staticmethod
    def _mark_seen(job: Job, now: str):
        """
        Odnotuj, że oferta nadal wisi na portalu.

        `last_seen` i `times_seen` to jedyny sygnał wieku dla źródeł, które nie
        podają żadnej daty (Pracuj.pl to 62% bazy) - ogłoszenie wiszące
        tygodniami samo się zdradza liczbą przebiegów, w których je widziano.
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
        uzupełniamy tylko daty, których poprzedni przebieg nie znał. Linki
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
            # Uzupełnij daty, jeśli poprzedni przebieg ich nie miał, a ten ma
            if job.posted_date and not known.posted_date:
                known.posted_date = job.posted_date
            if job.valid_through and not known.valid_through:
                known.valid_through = job.valid_through

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

    def remove_job(self, link: str) -> bool:
        """Usuwa ofertę po linku. Zwraca True, jeśli coś usunięto."""
        jobs = self.load_jobs()
        kept = [job for job in jobs if job.link != link]

        if len(kept) == len(jobs):
            return False

        self.save_jobs(kept)
        return True


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
            data[source_name] = {
                'last_run_date': now.date().isoformat(),
                'status': 'success',
                'jobs_count': jobs_count,
                'timestamp': now.isoformat(),
            }
            self._write(data)

    # --- jedyne miejsce dotykające pliku -------------------------------------

    def _read(self) -> dict:
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict):
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
