"""
Konfiguracja centralna: ścieżki, klucze API, ustawienia scraperów i interfejsu.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

from utils.llm import settings_from_env

load_dotenv(Path(__file__).parent / ".env")

BASE_DIR = Path(__file__).parent
JOBS_DATABASE_PATH = BASE_DIR / "jobs.db"

# Model czytający CV (profil kandydata w utils/cv_profile.py): dostawca, modele i pula
# kluczy (LLM_PROVIDER w .env; domyślnie Gemini) - patrz utils/llm.py i .env.example.
# Oferty ocenia Jev (matching/jev.py, TYPESAFE_API_KEY), nie ten model.
LLM = settings_from_env(os.environ)

# === Klucze API zewnętrznych portali ===
# SOLID.Jobs — klucz niepotrzebny, publiczne API
# Adzuna — darmowa rejestracja: https://developer.adzuna.com/
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
# Jooble — darmowa rejestracja: https://jooble.org/api/about
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")
# Careerjet — darmowa rejestracja: https://www.careerjet.com/partners/api/
CAREERJET_API_KEY = os.getenv("CAREERJET_API_KEY", "")

SCRAPER_CONFIG = {
    "location": "Warszawa",
    "radius_km": 0,  # Tylko miasto z CV (plus oferty zdalne); przedmieścia odpadają w przesiewie
    "days_posted": 30,
    "headless": True,
    "timeout_ms": 30000,
    "max_retries": 3,
    "delay_between_requests": 2.0,
    "max_workers": 3,  # Limit równolegle pracujących scraperów
    
    # Filtry poziomu wejściowego - osobne dla każdego portalu
    # Pracuj.pl: pięć poziomów doświadczenia daje ok. 4374 oferty
    "pracuj_pl": {
        # Dni wstecz (parametr itth). Poziomy doświadczenia (parametr et)
        # i miasto wyznacza teraz CV przez utils.candidate_scope (scope_levels, scope_city).
        # Zweryfikowane na żywo kody et:
        # 1 = Praktykant / Stażysta (intern)
        # 3 = Asystent (junior)
        # 17 = Młodszy specjalista / Junior (junior)
        # 4 = Specjalista / Mid / Regular (mid)
        # 18 = Starszy specjalista / Senior (senior)
        # 19 = Ekspert (senior)
        # 5 = Kierownik / Koordynator (lead, manager)
        # 20 = Menedżer (manager)
        # 6 = Dyrektor (manager)
        # 21 = Prezes (manager)
        # 2 = Pracownik fizyczny
        "days_param": 7,  # itth=7, czyli ostatnie 7 dni
    },
    
    "olx_praca": {
        # Zakres (miasto i filtr poziomu) wyznaczany przez CV (utils/candidate_scope.py),
        # kategorie przez utils/portal_categories.py.
        # bez-doswiadczenia włączane tylko gdy poziomy to wyłącznie intern/junior.
    },
    
    "linkedin": {
        # Zakres (miasto i filtr f_E) wyznaczany przez CV (utils/candidate_scope.py).
        "job_types": ["F", "P"],  # F = pełny etat, P = część etatu
    },
    
    # RocketJobs i JustJoin.it dzielą to samo candidate-api.
    # Zakres (miasto i poziomy) wyznaczany przez CV (utils/candidate_scope.py).
    # Candidate-api akceptuje wyłącznie: intern, junior, mid, senior.
    "rocketjobs": {},

    # NoFluffJobs: zakres wyznaczany przez CV (utils/candidate_scope.py).
    # criteriaSearch.seniority: trainee, junior, mid, senior, expert, lead.
    "nofluffjobs": {},

    # SOLID.Jobs: zakres wyznaczany przez CV (utils/candidate_scope.py).
    # experienceLevel w API: Intern / Junior / Regular / Senior
    "solid_jobs": {
         "keep_unknown_level": True,   # oferty bez podanego poziomu zostawiamy
    },

    "justjoinit": {},

    # === Portale ogólne (nie-IT) ===
    # Wspólna baza: scrapers/ldjson_scraper_base.py - listing daje linki,
    # pełny opis pochodzi ze schema.org JobPosting na stronie oferty.
    #
    # Limity ustawione na PEŁNE pokrycie warszawskich listingów (zmierzone
    # 10 sierpnia 2026: aplikuj.pl 94 strony, GoWork 96, praca.pl 30).
    # Kosztowny jest tylko pierwszy przebieg: `skip_known_details` sprawia,
    # że kolejne pobierają strony wyłącznie NOWYCH ofert, a reszta listingu
    # kosztuje jedynie pobranie samych stron listingu.
    "praca_pl": {
        "max_pages": 35,
        "max_offers": 2500,
    },

    "aplikuj": {
        "max_pages": 100,
        "max_offers": 6000,
        # Kategorie dobierane automatycznie do profilu CV przez utils/portal_categories.py
    },

    "gowork": {
        "max_pages": 100,
        "max_offers": 5000,
    },

    # Indeed jest z założenia niskonakładowy - paginacja jest za ścianą logowania,
    # więc bierzemy stronę 1 dla kilku słów kluczowych (po 15 ofert).
    # Opisy zbierane są klikaniem kart, bo wejście na /viewjob kończy się 403.
    # Skracanie przerw albo wydłużanie listy słów = seria 403 i zero ofert.
    #
    # WYŁĄCZONY. W przebiegach z 21 i 23 sierpnia 2026 portal odrzucił każde
    # słowo kluczowe (403, "Security Check") i oddał 0 ofert, kosztując 115 s
    # i uruchomienie Playwrighta. Kod scrapera zostaje - razem z rozpoznaniem
    # portalu w jego docstringu - żeby dało się sprawdzić, czy coś się zmieniło:
    #   python main_scraper.py --only indeed --force
    # Gdy znowu przepuści, wystarczy "enabled": True.
    "indeed": {
        "enabled": False,
        "max_offers_per_keyword": 15,
        "pause_between_keywords": (45, 75),
        "fetch_descriptions": True,
    },
    
    # === Feedy ATS (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Teamtailor, Workable) ===
    "ats_feeds": {
        "enabled": True,
        "max_slugs_per_run": 150,      # Budżet firm (slugów) na pojedynczy przebieg
        "time_budget_seconds": 180,    # Górny limit czasu pracy scrapera ATS (s)
        "request_timeout": 8,
        "max_workers": 8,
        "delay_between_requests": 0.05,
    },
    
    # === API Scraper Keys (passed to scrapers via config dict) ===
    "adzuna_app_id": ADZUNA_APP_ID,
    "adzuna_app_key": ADZUNA_APP_KEY,
    "jooble_api_key": JOOBLE_API_KEY,
    "careerjet_api_key": CAREERJET_API_KEY,
}

# User-agenty do rotacji - utrudniają wykrycie bota
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
]
