"""
Profil kandydata z CV - jedyne wejście, które podaje użytkownik.

Jedno zapytanie do modelu zamienia tekst CV (`final_cv_text.txt`) na kilka pól,
których potrzebuje reszta systemu: miasto i poziom wyznaczają zakres scrapowania
(utils/candidate_scope.py), a umiejętności, języki i lata doświadczenia -
przesiew i ocenę dopasowania (matching/). Profil zapisuje skrót CV, z którego
powstał; `ensure_profile()` liczy go ponownie tylko po zmianie CV.

Uruchomienie ręczne: python -m utils.cv_profile [--force]
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from utils.candidate_scope import PROFILE_PATH, load_profile
from utils.offer_fields import SENIORITY, norm_language
from utils.safe_io import save_json_atomic

CV_TEXT_PATH = Path(__file__).resolve().parent.parent / "final_cv_text.txt"


class _Language(BaseModel):
    name: str = Field(description="Nazwa języka po polsku albo angielsku, np. angielski")
    level: Optional[str] = Field(description="Poziom CEFR (A1-C2) albo 'ojczysty'; null, gdy CV nie podaje")


class CandidateProfile(BaseModel):
    city: Optional[str] = Field(description=(
        "Miasto zamieszkania albo szukania pracy podane w CV, zapisane w języku kraju, "
        "w którym leży (Warszawa, nie Warsaw; Kraków, nie Cracow) - tej nazwy używają "
        "adresy portali z ofertami; null, gdy brak"))
    seniority: Literal["intern", "junior", "mid", "senior", "lead", "manager"] = Field(
        description="Poziom zawodowy kandydata w zawodzie, do którego CV prowadzi")
    years_experience: float = Field(
        description="Łączna liczba lat płatnej pracy zawodowej opisanej w CV (0 bez doświadczenia)")
    skills: list[str] = Field(
        description="Umiejętności twarde, narzędzia, technologie, uprawnienia wymienione w CV - "
                    "dokładnie tak, jak w CV; bez cech charakteru")
    languages: list[_Language] = Field(description="Języki z CV, łącznie z ojczystym")
    roles: list[str] = Field(
        description="Nazwy stanowisk, na które to CV wskazuje: nagłówek CV i zajmowane stanowiska")


_PROMPT = """Wyciągnij z CV dane kandydata do porównywania z ofertami pracy.
Opisuj tylko to, co jest w CV. Nie oceniaj kandydata i nie dopisuj niczego od siebie.
Poziom (seniority) ustal z lat pracy i stanowisk: intern = student/stażysta bez pracy w zawodzie,
junior = do ok. 2 lat w zawodzie albo pierwsza praca w zawodzie, mid = ok. 2-5 lat,
senior = ponad 5 lat, lead = formalnie kieruje zespołem pracowników w firmie,
manager = zarządza działem lub firmą zatrudniającą ludzi. Jednoosobowa działalność,
kapitan drużyny, lider w hobby czy sporcie nie czynią z nikogo lead ani managera.
Zawód to ten, do którego prowadzi nagłówek i umiejętności CV; lata w innych zawodach
wliczaj do years_experience, ale nie podnoszą poziomu w zawodzie docelowym.

CV:
<cv>
{cv}
</cv>"""


def cv_text() -> str | None:
    try:
        text = CV_TEXT_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(raw: dict) -> dict:
    languages = []
    for item in raw.get("languages") or []:
        code = norm_language(item.get("name"))
        if code and all(lang["name"] != code for lang in languages):
            level = (item.get("level") or "").strip() or None
            if level and level.lower().startswith(("ojczyst", "native")):
                level = "C2"
            languages.append({"name": code, "level": level})
    skills: list[str] = []
    for skill in raw.get("skills") or []:
        skill = str(skill).strip()
        if skill and skill.lower() not in (s.lower() for s in skills):
            skills.append(skill)
    seniority = raw.get("seniority")
    return {
        "city": (raw.get("city") or "").strip() or None,
        "seniority": seniority if seniority in SENIORITY else "junior",
        "years_experience": max(0.0, float(raw.get("years_experience") or 0)),
        "skills": skills,
        "languages": languages,
        "roles": [r.strip() for r in raw.get("roles") or [] if str(r).strip()],
    }


def build_profile(text: str) -> dict:
    """Jedno zapytanie do modelu z utils/llm.py (kaskada modeli profilu i rotacja kluczy)."""
    from config import LLM
    from utils.llm import LLMError, ask_json

    if not LLM.api_keys:
        raise RuntimeError(LLM.error or "Brak klucza modelu w .env")
    prompt = _PROMPT.format(cv=text[:12000])
    last_error: Exception | None = None
    for model in LLM.models:
        for key in LLM.api_keys:
            try:
                reply = ask_json(LLM, model, key, prompt, CandidateProfile, max_tokens=4096, temperature=0)
            except LLMError as e:
                last_error = e
                if e.kind in ("rate_limit", "invalid_key"):
                    continue
                break
            items = reply.data if isinstance(reply.data, list) else [reply.data]
            if items and isinstance(items[0], dict):
                profile = _normalize(items[0])
                profile["_metadata"] = {
                    "cv_sha256": _sha(text),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "model": model,
                }
                return profile
    raise RuntimeError(f"Nie udało się wyciągnąć profilu z CV: {last_error}")


def ensure_profile(force: bool = False) -> dict | None:
    """
    Profil aktualny względem CV. Liczy go od nowa, gdy CV się zmieniło albo
    `force`. Zwraca None, gdy nie ma CV.
    """
    text = cv_text()
    if text is None:
        return None
    current = load_profile()
    if (not force and current
            and current.get("_metadata", {}).get("cv_sha256") == _sha(text)):
        return current
    profile = build_profile(text)
    save_json_atomic(PROFILE_PATH, profile, backup=False)
    return profile


def profile_fingerprint(profile: dict) -> str:
    """Skrót pól profilu używanych przy ocenie - zmiana CV unieważnia wyniki."""
    body = {k: v for k, v in profile.items() if not k.startswith("_")}
    return _sha(json.dumps(body, ensure_ascii=False, sort_keys=True))[:16]


def main(argv: list[str] | None = None) -> int:
    from utils.console import force_utf8
    force_utf8()
    args = sys.argv[1:] if argv is None else argv
    profile = ensure_profile(force="--force" in args)
    if profile is None:
        print("Brak CV (final_cv_text.txt) - wgraj CV w aplikacji.")
        return 1
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
