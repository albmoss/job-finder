"""
Konfiguracja centralna: ścieżki, klucze API, ustawienia scraperów i interfejsu.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

from utils.llm import settings_from_env

load_dotenv(Path(__file__).parent / ".env")

BASE_DIR = Path(__file__).parent
JOBS_DATABASE_PATH = BASE_DIR / "jobs_database.json"
# Start ostatniego pobierania ofert (run_final_pipeline, etap 1). Granica „nowych”
# ofert na liście: scraped_at >= started_at.
LAST_SCRAPE_RUN_PATH = BASE_DIR / "last_scrape_run.json"

# Dostawca modelu, kaskada modeli i pula kluczy do analizy ofert (LLM_PROVIDER
# w .env; domyślnie Gemini) - patrz utils/llm.py i .env.example.
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
    "radius_km": 15,  # Promień +15 km od miasta
    "days_posted": 30,
    "headless": True,
    "timeout_ms": 30000,
    "max_retries": 3,
    "delay_between_requests": 2.0,
    "max_workers": 3,  # Limit równolegle pracujących scraperów
    
    # Filtry poziomu wejściowego - osobne dla każdego portalu
    # Pracuj.pl: pięć poziomów doświadczenia daje ok. 4374 oferty
    "pracuj_pl": {
        # Kody poziomu doświadczenia (parametr et)
        "experience_levels": [2, 3, 4, 5, 6],  
        # 2 = Praktykant/stażysta (146)
        # 3 = Asystent (572)
        # 4 = Młodszy specjalista/Junior (1719)
        # 5 = Menedżer (707)
        # 6 = Pracownik fizyczny (1421)
        # Razem: ok. 4374 oferty
        "days_param": 7,  # itth=7, czyli ostatnie 7 dni
    },
    
    "olx_praca": {
        # OLX ma tylko jeden filtr doświadczenia: bez doświadczenia
        # Bez zawężania kategorii - bierzemy wszystko dla początkujących
        "experience_filter": "bez-doswiadczenia",
        "location": "warszawa",
        "radius": "15",  # +15 km
    },
    
    "linkedin": {
        # LinkedIn: filtr poziomu doświadczenia (parametr f_E)
        "experience_levels": ["1", "2"],  # 1 = staż, 2 = poziom podstawowy
        "job_types": ["F", "P"],  # F = pełny etat, P = część etatu
    },
    
    # RocketJobs i JustJoin.it dzielą to samo candidate-api.
    # Jedyne prawidłowe wartości experienceLevels to: junior, mid, senior, intern.
    # ("staz", "asystent", "trainee", "entry" zwracają 0 wyników - nie zmieniaj bez sprawdzenia.)
    "rocketjobs": {
        "location": "warszawa",
        "experience_levels": ["junior", "intern"],
    },

    "nofluffjobs": {
         "location": "warszawa",
         "criteria": "seniority=trainee,junior"
    },

    # SOLID.Jobs zwraca oferty z całej Polski i wszystkich poziomów.
    # Bez filtra ~750 ofert trafiało do analizy, z czego 31% seniorskich
    # i tylko ~2% juniorskich - przy medianie opisu 3800 znaków.
    # experienceLevel w API: Intern / Junior / Regular / Senior
    "solid_jobs": {
         "location": "warszawa",
         "experience_levels": ["Junior", "Intern", "Trainee"],
         "keep_unknown_level": True,   # oferty bez podanego poziomu zostawiamy
    },

    "justjoinit": {
         "location": "warszawa",
         "experience_levels": ["junior", "intern"],
    },

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
        "location": "warszawa",
        "max_pages": 35,
        "max_offers": 2500,
        "skip_senior_titles": True,
    },

    "aplikuj": {
        "location": "warszawa",
        "max_pages": 100,
        "max_offers": 6000,
        "skip_senior_titles": True,
        # Kategorie tematyczne dobierane po wyczerpaniu listingu miejskiego
        "extra_paths": ["staz", "przy-komputerze"],
    },

    "gowork": {
        "location": "warszawa",
        "max_pages": 100,
        "max_offers": 5000,
        "skip_senior_titles": True,
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
        "location": "Warszawa",
        "keywords": ["junior", "praktykant", "asystent"],
        "max_offers_per_keyword": 15,
        "pause_between_keywords": (45, 75),
        "fetch_descriptions": True,
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
