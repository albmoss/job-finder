"""
Data Models for Job Search System
"""

from dataclasses import dataclass, asdict
from typing import Optional
import json
import threading


@dataclass
class Job:
    """Represents a job posting"""
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
        """Automatically clean description on initialization (Token Diet)"""
        from utils.text_cleaner import clean_job_description
        if self.description:
            self.description = clean_job_description(self.description)
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict):
        """Create Job from dict without re-cleaning description."""
        from dataclasses import fields as dc_fields
        known_fields = {f.name for f in dc_fields(cls)}
        obj = cls.__new__(cls)
        for fname in known_fields:
            setattr(obj, fname, data.get(fname))
        return obj


@dataclass
class JobMatch:
    """Represents a job with AI matching results"""
    job: Job
    match_percentage: int
    reason: str
    is_entry_level: bool
    missing_skills: Optional[list[str]] = None
    user_decision: Optional[str] = None  # "apply", "reject", or None
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
        """Create JobMatch instance from dictionary"""
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
    """Simple JSON database for storing jobs"""
    
    def __init__(self, filepath: str):
        self.filepath = filepath
    
    def save_jobs(self, jobs: list[Job]):
        """Save jobs to JSON file (atomic write)"""
        from datetime import datetime
        import os
        for job in jobs:
            if not job.scraped_at:
                job.scraped_at = datetime.now().isoformat()
        jobs_data = [job.to_dict() for job in jobs]
        temp_file = self.filepath + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(jobs_data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.filepath)
    
    def load_jobs(self) -> list[Job]:
        """Load jobs from JSON file"""
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                jobs_data = json.load(f)
            return [Job.from_dict(job_data) for job_data in jobs_data]
        except FileNotFoundError:
            return []
    
    def append_jobs(self, new_jobs: list[Job]):
        """
        Dopisz nowe oferty i odnotuj ponowne zobaczenie już znanych.

        Istniejące rekordy NIE są nadpisywane (od tego jest --refresh), ale
        aktualizujemy `last_seen` i `times_seen`. Bez tego ponowne pobranie tej
        samej oferty nie zostawiało żadnego śladu, a to jedyny sposób, żeby
        zmierzyć jak długo ogłoszenie wisi - dla źródeł bez `posted_date`
        (Pracuj.pl, LinkedIn, SOLID.Jobs) nie ma innego sygnału wieku.
        """
        from datetime import datetime
        from utils.links import canonical_link

        existing_jobs = self.load_jobs()
        current_time = datetime.now().isoformat()

        by_link = {canonical_link(job.link): job for job in existing_jobs}

        unique_new_jobs = []
        for job in new_jobs:
            known = by_link.get(canonical_link(job.link))
            if known is None:
                if not job.scraped_at:
                    job.scraped_at = current_time
                job.last_seen = job.last_seen or current_time
                unique_new_jobs.append(job)
                continue

            known.last_seen = current_time
            known.times_seen = (known.times_seen or 1) + 1
            # Uzupełnij daty, jeśli poprzedni przebieg ich nie miał, a ten ma
            if job.posted_date and not known.posted_date:
                known.posted_date = job.posted_date
            if job.valid_through and not known.valid_through:
                known.valid_through = job.valid_through

        all_jobs = existing_jobs + unique_new_jobs
        self.save_jobs(all_jobs)

        return len(unique_new_jobs)
        
    def touch_seen(self, links) -> int:
        """
        Odnotuj, że oferty nadal wiszą na portalu, bez pobierania ich stron.

        Scrapery pomijają pobieranie szczegółów ofert, które już mamy (opis się
        nie zmienia, a to jest 90% ruchu przy pełnym pokryciu portalu). Bez tej
        metody takie oferty nie dostawałyby aktualizacji `last_seen` i sygnał
        uporczywości z utils/offer_age.py nigdy by nie zadziałał.

        Linki nieznane bazie są ignorowane - nie tworzymy pustych rekordów.
        """
        from datetime import datetime
        from utils.links import canonical_link

        wanted = {canonical_link(l) for l in links if l}
        if not wanted:
            return 0

        jobs = self.load_jobs()
        now = datetime.now().isoformat()
        touched = 0
        for job in jobs:
            if canonical_link(job.link) in wanted:
                job.last_seen = now
                job.times_seen = (job.times_seen or 1) + 1
                touched += 1

        if touched:
            self.save_jobs(jobs)
        return touched

    def remove_job(self, link: str) -> bool:
        """Remove a job from the database by its link. Returns True if removed."""
        existing_jobs = self.load_jobs()
        original_length = len(existing_jobs)
        
        # Keep only jobs that don't match the link
        updated_jobs = [job for job in existing_jobs if job.link != link]
        
        if len(updated_jobs) < original_length:
            self.save_jobs(updated_jobs)
            return True
            
        return False
        
    def check_if_source_scraped_today(self, source_name: str) -> bool:
        """Deprecated: Use ScraperStatusManager instead"""
        return False


class ScraperStatusManager:
    """Manages tracking of successful daily scrapes"""
    _lock = threading.Lock()
    
    def __init__(self, filepath: str = "scraper_status.json"):
        self.filepath = filepath
        
    def load_status(self) -> dict:
        with self._lock:
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return {}
            
    def save_status(self, status_data: dict):
        with self._lock:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(status_data, f, ensure_ascii=False, indent=2)
            
    def is_scraped_today(self, source_name: str) -> bool:
        """Check if source was SUCCESSFULLY scraped today"""
        with self._lock:
            from datetime import datetime
            today = datetime.now().date().isoformat()
            
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            
            source_data = data.get(source_name, {})
            
            last_date = source_data.get('last_run_date')
            status = source_data.get('status')
            
            if last_date == today and status == 'success':
                return True
                
            return False
        
    def mark_as_completed(self, source_name: str, jobs_count: int):
        """Mark source as successfully scraped today"""
        with self._lock:
            from datetime import datetime
            today = datetime.now().date().isoformat()
            
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            
            data[source_name] = {
                'last_run_date': today,
                'status': 'success',
                'jobs_count': jobs_count,
                'timestamp': datetime.now().isoformat()
            }
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
