"""
Zakres scrapowania wyznaczany przez CV.

Profil kandydata (`candidate_profile.json`, tworzy go utils/cv_profile.py) podaje
miasto i poziom. Scrapery pytają tutaj, jakie miasto i jakie poziomy ściągać,
zamiast trzymać je na sztywno w konfiguracji. Poziomy to poziom z CV plus
sąsiednie: oferta o stopień wyżej bywa osiągalna, a o stopień niżej bywa
jedynym wejściem po zmianie branży. Resztę odsiewa przesiew w matching/.

Bez profilu (CV jeszcze nie wczytane) wartości biorą się z SCRAPER_CONFIG,
żeby scraping dało się uruchomić przed pierwszym CV.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils import candidates
from utils.offer_fields import SENIORITY, _fold


def profile_path() -> Path:
    return candidates.path(candidates.PROFILE)


# Poziom z CV -> poziomy do ściągania.
_LEVEL_WINDOW = {
    "intern": ("intern", "junior"),
    "junior": ("intern", "junior", "mid"),
    "mid": ("junior", "mid", "senior"),
    "senior": ("mid", "senior", "lead"),
    "lead": ("senior", "lead", "manager"),
    "manager": ("senior", "lead", "manager"),
}


def load_profile(path: Path | None = None) -> dict | None:
    """Profil kandydata albo None, gdy CV nie zostało jeszcze przetworzone."""
    target = path or profile_path()
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def scope_city(default: str = "Warszawa") -> str:
    """Miasto z CV, a bez profilu - `default` (zwykle SCRAPER_CONFIG["location"])."""
    profile = load_profile()
    city = (profile or {}).get("city")
    return city.strip() if isinstance(city, str) and city.strip() else default


def city_slug(city: str) -> str:
    """„Zielona Góra” -> „zielona-gora” - postać używana w adresach portali."""
    return re.sub(r"[^a-z0-9]+", "-", _fold(city)).strip("-")


def scope_levels(default: tuple[str, ...] = ("intern", "junior")) -> list[str]:
    """Kody poziomów z utils/offer_fields.SENIORITY do ściągania z portali."""
    profile = load_profile()
    level = (profile or {}).get("seniority")
    if level not in SENIORITY:
        return list(default)
    return list(_LEVEL_WINDOW[level])
