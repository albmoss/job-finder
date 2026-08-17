"""
Configuration for Job Search Automation System
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(Path(__file__).parent / ".env")

# Project paths
BASE_DIR = Path(__file__).parent
JOBS_DATABASE_PATH = BASE_DIR / "jobs_database.json"

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY_PRIMARY", "")
# Model domyślny. Właściwa kaskada modeli jest w waterfall_analysis.py (MODELS)
# i została dobrana empirycznie - patrz benchmark_models.py.
GEMINI_MODEL = "gemini-3.5-flash-lite"

# All available API keys for waterfall/rotation
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", ""),
    os.getenv("GEMINI_API_KEY_2", ""),
    os.getenv("GEMINI_API_KEY_3", ""),
    os.getenv("GEMINI_API_KEY_4", ""),
]
# Filter out empty keys
GEMINI_API_KEYS = [k for k in GEMINI_API_KEYS if k]

# Fallback: jeśli klucz główny jest pusty lub to placeholder, weź pierwszy z rotacji.
# (Nie trzymamy tu żadnych realnych kluczy - nawet wygasłych, nawet jako czarna lista.)
_PLACEHOLDERS = {"", "YOUR_KEY_HERE", "CHANGEME", "TODO"}
if GEMINI_API_KEY.strip() in _PLACEHOLDERS and GEMINI_API_KEYS:
    GEMINI_API_KEY = GEMINI_API_KEYS[0]

# === External Job Portal API Keys ===
# SOLID.Jobs — no key needed (public API)
# Adzuna — free registration at https://developer.adzuna.com/
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
# Jooble — free registration at https://jooble.org/api/about
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")
# Careerjet — free registration at https://www.careerjet.com/partners/api/
CAREERJET_API_KEY = os.getenv("CAREERJET_API_KEY", "")

# Scraper Configuration
SCRAPER_CONFIG = {
    "location": "Warszawa",
    "radius_km": 15,  # User specified +15km
    "days_posted": 30,
    "headless": True,
    "timeout_ms": 30000,
    "max_retries": 3,
    "delay_between_requests": 2.0,
    "max_workers": 3,  # Parallel scraping limit
    
    # Portal-specific entry-level filters
    # Based on user's Pracuj.pl screenshot showing 5 levels = 4374 jobs
    "pracuj_pl": {
        # Experience level codes (et parameter)
        "experience_levels": [2, 3, 4, 5, 6],  
        # 2 = Praktykant/stażysta (146)
        # 3 = Asystent (572)
        # 4 = Młodszy specjalista/Junior (1719)
        # 5 = Menedżer (707)
        # 6 = Pracownik fizyczny (1421)
        # Total: ~4374 jobs
        "days_param": 7,  # itth=7 (last 7 days)
    },
    
    "olx_praca": {
        # OLX has simple experience filter: bez doświadczenia (without experience)
        # No specific categories - user wants ALL entry-level jobs
        "experience_filter": "bez-doswiadczenia",  # Without experience
        "location": "warszawa",
        "radius": "15",  # +15km as specified by user
    },
    
    "linkedin": {
        # LinkedIn experience level filter (f_E parameter)
        "experience_levels": ["1", "2"],  # 1=Internship, 2=Entry level
        "job_types": ["F", "P"],  # F=Full-time, P=Part-time
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
    "indeed": {
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

# User agents for anti-detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
]

# AI Matching Configuration
AI_CONFIG = {
    "temperature": 0.3,
    "max_output_tokens": 500,
    "system_prompt": """Jesteś doświadczonym rekruterem specjalizującym się w stanowiskach entry-level.

Twoim zadaniem jest ocena dopasowania kandydata do oferty pracy.

KRYTERIUM KRYTYCZNE:
- Odrzuć oferty wymagające nauki zaawansowanych narzędzi/umiejętności trwającej dłużej niż 1 tydzień
- Odrzuć oferty wymagające: zaawansowanego programowania, certyfikatów/uprawnień budowlanych, specjalistycznej wiedzy technicznej
- Akceptuj oferty: biurowe, obsługa klienta, asystent, praktyki, prace fizyczne do przyuczenia

Porównaj treść CV z opisem stanowiska i zwróć ocenę w formacie JSON:
{
    "match_percentage": <liczba 0-100>,
    "reason": "<krótkie uzasadnienie po polsku, 2-3 zdania>",
    "is_entry_level": <true/false - czy to prawdziwe stanowisko entry-level?>
}

Bądź krytyczny - lepiej odrzucić wątpliwe oferty niż zaproponować nieodpowiednie.""",
    "rpm_limit": 5,
    "batch_size": 50
}

# Streamlit UI Configuration
UI_CONFIG = {
    "page_title": "Job Search Analytics",
    "page_icon": "💼",
    "page_size": 25,
    "match_color_thresholds": {
        "high": 70,  # >= 70% green
        "medium": 40,  # 40-69% yellow
        # < 40% red
    }
}

# Load overrides from user_config.json if it exists to apply Streamlit UI changes
USER_CONFIG_PATH = BASE_DIR / "user_config.json"
if USER_CONFIG_PATH.exists():
    try:
        import json
        with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
            user_overrides = json.load(f)
        for section, overrides in user_overrides.items():
            if section in SCRAPER_CONFIG and isinstance(SCRAPER_CONFIG[section], dict):
                SCRAPER_CONFIG[section].update(overrides)
            else:
                SCRAPER_CONFIG[section] = overrides
    except Exception as e:
        print(f"Error loading user_config.json: {e}")

