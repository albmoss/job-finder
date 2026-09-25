"""
Współdzielona kolejka i kryteria kwalifikacji ofert do oceny AI (Waterfall Analysis).

Zapewnia spójność semantyki między backendem oceniania (waterfall_analysis.py),
głównym pipeline'em (run_final_pipeline.py) a interfejsem użytkownika (server.py / app_services.py).

Kryteria kwalifikacji oferty:
1. Posiada sensowny opis (niepusty, >20 znaków, nie zaślepka "Brak opisu").
2. Nie posiada decyzji użytkownika (user_decisions.json - apply/reject/rated/aspirational).
3. Nie została oznaczona jako zdjęta z portalu (utils.liveness.zdjete_z_portalu).
4. Nie wygasła (valid_through deklarowane przez pracodawcę nie leży w przeszłości).
5. Nie została jeszcze oceniona w analyzed_jobs_waterfall.json (chyba że rescore_all=True
   lub rescore_changed=True ze zmienionym opisem).
"""

from datetime import date, datetime
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from utils.links import canonical_link
from utils.liveness import zdjete_z_portalu
from utils.offer_age import _as_date
from utils.safe_io import load_json_safe


def _val(obj: Any, attr: str, default: Any = None) -> Any:
    """Pobierz atrybut z obiektu Job (dataclass) lub słownika (dict)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def is_valid_job(job: Any) -> bool:
    """
    Oferta z sensownym opisem kwalifikująca się do oceny AI (>20 znaków, nie zaślepka).
    Działa zarówno na instancjach Job jak i słownikach.
    """
    desc = (_val(job, "description") or "").strip()
    return bool(desc and desc != "Brak opisu" and len(desc) > 20)


def is_expired_job(job: Any, today: Optional[date] = None) -> bool:
    """
    Sprawdź czy oferta ma deklarowaną datę ważności (valid_through), która minęła.
    Twardy sygnał martwej oferty (ld+json).
    """
    if today is None:
        today = date.today()
    vt = _val(job, "valid_through")
    if vt:
        d = _as_date(vt)
        if d and d < today:
            return True
    return False


def description_fingerprint(job: Any) -> str:
    """
    Skrót opisu i tytułu oferty. Pozwala wykryć wzbogacenie opisu (np. dociągnięcie
    pełnego opisu z OLX) i selektywne przeliczenie w trybie --rescore-changed.
    """
    desc = _val(job, "description") or ""
    title = _val(job, "title") or ""
    text = desc + "||" + title
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def is_stale_result(
    entry: Any,
    job: Any,
    rescore_all: bool = False,
    rescore_changed: bool = False,
    rescore_cutoff: Optional[str] = None,
) -> bool:
    """
    Sprawdź czy istniejący wynik oceny wymaga przeliczenia.
    W trybie standardowym (normal mode) istniejące wyniki są bezwzględnie zachowywane.
    W trybie rescore_all: jeśli podano rescore_cutoff (np. wznowienie sesji rescore_all),
    wyniki przeliczone po cutoffie są uznawane za aktualne i nie są kolejkowane ponownie.
    """
    if rescore_all:
        if rescore_cutoff:
            analyzed_at = _val(entry, "_analyzed_at")
            if analyzed_at and str(analyzed_at) >= str(rescore_cutoff):
                return False
        return True
    if rescore_changed:
        stamped_hash = _val(entry, "_description_hash")
        if stamped_hash and stamped_hash != description_fingerprint(job):
            return True
    return False


def get_pending_scoring_jobs(
    jobs: Optional[Iterable[Any]] = None,
    analyzed: Optional[Iterable[Any]] = None,
    decisions: Optional[Any] = None,
    rescore_all: bool = False,
    rescore_changed: bool = False,
    rescore_cutoff: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> List[Any]:
    """
    Zwraca listę unikalnych ofert oczekujących na ocenę AI zgodnie z pełną semantyką pipeline'u:
    - odrzuca oferty bez wartościowego opisu (is_valid_job)
    - odrzuca oferty z decyzją użytkownika (decided)
    - odrzuca oferty zdjęte z portalu (zdjete_z_portalu)
    - odrzuca oferty przeterminowane (valid_through)
    - odrzuca oferty już ocenione (chyba że rescore_all lub rescore_changed dla zmienionych opisów)
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent

    # 1. Załaduj oferty źródłowe jeśli nie podano
    if jobs is None:
        jobs_path = base_dir / "jobs_database.json"
        raw_list = load_json_safe(jobs_path, default=[]) or []
        jobs_list = list(raw_list)
    else:
        jobs_list = list(jobs)

    if not jobs_list:
        return []

    # 2. Załaduj istniejące analizy jeśli nie podano
    if analyzed is None:
        analyzed_path = base_dir / "analyzed_jobs_waterfall.json"
        raw_analyzed = load_json_safe(analyzed_path, default=[]) or []
        analyzed_list = list(raw_analyzed)
    else:
        analyzed_list = list(analyzed)

    # 3. Załaduj decyzje użytkownika jeśli nie podano
    if decisions is None:
        decisions_path = base_dir / "user_decisions.json"
        decisions_data = load_json_safe(decisions_path, default={}) or {}
    else:
        decisions_data = decisions

    if isinstance(decisions_data, dict):
        decided_links: Set[str] = {
            canonical_link(k) for k in decisions_data.keys() if k
        }
    elif isinstance(decisions_data, (set, list, tuple)):
        decided_links = {
            canonical_link(k) for k in decisions_data if k
        }
    else:
        decided_links = set()

    # 4. Wykrywanie ofert zdjętych z portalu (liveness)
    try:
        zdjete_set: Set[str] = {
            canonical_link(link) for link in zdjete_z_portalu(jobs_list) if link
        }
    except Exception:
        zdjete_set = set()

    # 5. Indeks istniejących analiz po kanonicznym linku
    analyzed_by_link: Dict[str, Any] = {}
    for item in analyzed_list:
        job_obj = _val(item, "job")
        link = canonical_link(_val(job_obj, "link") or "")
        if link:
            analyzed_by_link[link] = item

    today = date.today()
    pending: List[Any] = []
    seen_links: Set[str] = set()

    for job in jobs_list:
        link = canonical_link(_val(job, "link") or "")
        if not link or link in seen_links:
            continue
        seen_links.add(link)

        # Semantyka 1: Walidacja opisu
        if not is_valid_job(job):
            continue

        # Semantyka 2: Decyzja użytkownika
        if link in decided_links:
            continue

        # Semantyka 3: Oferta zdjęta z portalu
        if link in zdjete_set:
            continue

        # Semantyka 4: Oferta przeterminowana wg deklaracji pracodawcy
        if is_expired_job(job, today=today):
            continue

        # Semantyka 5: Stan wcześniejszej analizy
        if link in analyzed_by_link:
            existing_entry = analyzed_by_link[link]
            if not is_stale_result(
                existing_entry,
                job,
                rescore_all=rescore_all,
                rescore_changed=rescore_changed,
                rescore_cutoff=rescore_cutoff,
            ):
                continue

        pending.append(job)

    return pending


def get_pending_scoring_count(
    jobs: Optional[Iterable[Any]] = None,
    analyzed: Optional[Iterable[Any]] = None,
    decisions: Optional[Any] = None,
    rescore_all: bool = False,
    rescore_changed: bool = False,
    rescore_cutoff: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> int:
    """
    Zwraca liczbę ofert oczekujących na ocenę AI.
    Służy jako jednoznaczne źródło prawdy dla widżetów nagłówka, podsumowań i kontroli pipeline'u.
    """
    return len(
        get_pending_scoring_jobs(
            jobs=jobs,
            analyzed=analyzed,
            decisions=decisions,
            rescore_all=rescore_all,
            rescore_changed=rescore_changed,
            rescore_cutoff=rescore_cutoff,
            base_dir=base_dir,
        )
    )
