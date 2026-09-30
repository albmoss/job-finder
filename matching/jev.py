"""
Ocena dopasowania oferty do CV modelem Jev (TypeSafe).

Jev nie pisze tekstu: na każde pytanie zwraca typowaną odpowiedź z
prawdopodobieństwami. Zamiast jednego pytania „ile procent” zadajemy kilka
wąskich pytań, a procent liczy `percent()` ze stałego wzoru. Ta sama oferta
z tym samym CV dostaje wtedy zawsze ten sam wynik, a zmiana wag to zmiana
liczby w kodzie, nie w prompcie. Tak zaleca dokumentacja TypeSafe:
https://docs.typesafe.ai/primitives

Klucz: TYPESAFE_API_KEY w .env. API: https://docs.typesafe.ai/api
"""

from __future__ import annotations

import os
import time

import requests

from utils.data_models import Job

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
DESCRIPTION_LIMIT = 6000
CV_LIMIT = 6000

# Pytania. Treść po polsku, bo po polsku są oferty i CV.
QUESTIONS = {
    "requirements": {
        "type": "score",
        "instructions": "Jaką część wymagań obowiązkowych z oferty kandydat spełnia według swojego CV?",
        "criteria": [
            "Prawie żadnych",
            "Mniej niż połowę",
            "Mniej więcej połowę",
            "Większość",
            "Wszystkie albo prawie wszystkie",
        ],
    },
    "role": {
        "type": "score",
        "instructions": "Na ile praca opisana w ofercie pokrywa się z tym, co kandydat robił i umie według CV?",
        "criteria": [
            "Zupełnie inna dziedzina i inne zadania",
            "Pokrewna dziedzina, ale inne zadania",
            "Ta sama dziedzina, inna rola",
            "Ta sama albo bardzo podobna rola",
        ],
    },
    "level": {
        "type": "choice",
        "instructions": "Jak poziom stanowiska w ofercie ma się do poziomu doświadczenia kandydata z CV?",
        "criteria": {
            "too_high": "Oferta wymaga wyraźnie więcej doświadczenia, niż kandydat ma",
            "stretch": "Oferta jest o jeden stopień wyżej niż kandydat",
            "match": "Poziom oferty odpowiada kandydatowi",
            "below": "Oferta jest wyraźnie poniżej kwalifikacji kandydata",
        },
    },
    "blocker": {
        "type": "noul",
        "instructions": (
            "Oferta wymaga czegoś, czego kandydat według CV nie ma i nie zdobędzie w kilka tygodni: "
            "uprawnień zawodowych, licencji, dyplomu konkretnego kierunku, prawa jazdy innej kategorii "
            "albo języka, którego nie zna."
        ),
    },
    "fit": {
        "type": "noul",
        "instructions": (
            "Kandydat z takim CV ma realną szansę zostać zaproszony na rozmowę na to stanowisko."
        ),
    },
    # Bez tego pytania oferta z dawnego zawodu (np. magazyn w CV frontendowca)
    # wygrywała z ofertami w zawodzie, który CV wskazuje jako cel.
    "direction": {
        "type": "noul",
        "instructions": (
            "To stanowisko jest w kierunku zawodowym, który kandydat sam wskazuje w CV: "
            "w nagłówku, podsumowaniu lub celu zawodowym i w ostatnich projektach."
        ),
    },
}

# Wagi wzoru. Suma wag części bazowej = 1.
WEIGHTS = {"requirements": 0.3, "role": 0.2, "fit": 0.2, "direction": 0.3}
LEVEL_FACTOR = {"match": 1.0, "stretch": 0.8, "below": 0.7, "too_high": 0.2}
BLOCKER_PENALTY = 0.7


class JevError(Exception):
    pass


def api_key() -> str | None:
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    return key or None


def build_state(job: Job, profile: dict, cv_text: str | None) -> dict:
    """Stan dla Jev: kandydat (profil + CV) i oferta (pola + treść)."""
    candidate = {k: v for k, v in profile.items() if not k.startswith("_")}
    if cv_text:
        candidate["cv"] = cv_text[:CV_LIMIT]
    offer = {
        "stanowisko": job.title,
        "firma": job.company,
        "lokalizacja": job.location,
        "poziom": job.seniority,
        "tryb_pracy": job.work_modes,
        "umowa": job.contract_types,
        "wymiar": job.schedules,
        "wymagane_umiejetnosci": job.skills_required,
        "mile_widziane": job.skills_nice,
        "jezyki": job.languages,
        "wymagane_lata": job.years_required,
        "kategoria": job.category,
        "opis": (job.description or "")[:DESCRIPTION_LIMIT],
    }
    return {
        "kandydat": candidate,
        "oferta": {k: v for k, v in offer.items() if v not in (None, [], "")},
    }


def ask(state: dict, key: str, session: requests.Session | None = None,
        retries: int = 5) -> dict:
    """Jedno zapytanie z ponawianiem przy 429/529/5xx. Zwraca słownik `answers`."""
    http = session or requests
    delay = 1.0
    for attempt in range(retries):
        try:
            resp = http.post(
                API_URL,
                json={"state": state, "model": MODEL, "questions": QUESTIONS},
                headers={"Authorization": f"Bearer {key}"},
                timeout=60,
            )
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise JevError(f"Brak połączenia z TypeSafe: {e}") from e
        else:
            if resp.status_code == 200:
                body = resp.json()
                return {"answers": body["answers"], "model": body.get("model"),
                        "usage": body.get("usage")}
            if resp.status_code in (401, 403):
                raise JevError(f"TypeSafe odrzucił klucz ({resp.status_code})")
            if resp.status_code not in (429, 529) and resp.status_code < 500:
                raise JevError(f"TypeSafe {resp.status_code}: {resp.text[:300]}")
        time.sleep(delay)
        delay = min(delay * 2, 30)
    raise JevError("TypeSafe nie odpowiedział po ponowieniach")


def _score01(answer: dict) -> float:
    """Oczekiwana pozycja na skali Score, znormalizowana do 0..1."""
    levels = len(answer.get("legend") or {}) or 1
    return float(answer.get("score") or 0) / max(levels - 1, 1)

def percent(answers: dict) -> int:
    """
    Procent dopasowania ze wzoru:
    baza = 0.3·wymagania + 0.2·rola + 0.2·szansa na rozmowę + 0.3·kierunek z CV
    (każde 0..1), mnożona przez zgodność poziomu (oczekiwana wartość LEVEL_FACTOR
    po prawdopodobieństwach) i przez karę za twardą przeszkodę (1 − 0.7·P(przeszkoda)).
    Prawdopodobieństwa zamiast twardych odpowiedzi: niepewna odpowiedź waży mało.
    """
    base = (WEIGHTS["requirements"] * _score01(answers["requirements"])
            + WEIGHTS["role"] * _score01(answers["role"])
            + WEIGHTS["fit"] * float(answers["fit"]["noul"])
            + WEIGHTS["direction"] * float(answers["direction"]["noul"]))
    probs = answers["level"].get("probabilities") or {answers["level"]["choice"]: 1.0}
    level = sum(LEVEL_FACTOR.get(label, 0.5) * p for label, p in probs.items())
    blocker = 1 - BLOCKER_PENALTY * float(answers["blocker"]["noul"])
    return max(0, min(100, round(100 * base * level * blocker)))
