"""
App Services - obsługa danych, decyzji użytkownika, CV, kluczy API i narzędzi dla Job Finder.
Niezależna warstwa usługowa dla backendu HTTP.
"""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from config import (
    JOBS_DATABASE_PATH,
    LAST_SCRAPE_RUN_PATH,
    GEMINI_API_KEYS,
    _PLACEHOLDERS,
)
from utils.cv_parser import CVParser
from utils.data_models import JobDatabase, Job, JobMatch
from utils.text_cleaner import detect_work_mode, strip_html
from utils.safe_io import save_json_atomic, load_json_safe
from utils.links import canonical_link
from utils.liveness import zdjete_z_portalu
from utils.scoring_queue import get_pending_scoring_count
import skill_gaps
import ui_theme

logger = logging.getLogger(__name__)

USER_DECISIONS_PATH = Path("user_decisions.json")
ANALYZED_JOBS_PATH = Path("analyzed_jobs_waterfall.json")
PREFERENCE_PROFILE_PATH = Path("preference_profile.json")
SCRAPER_STATUS_PATH = Path("scraper_status.json")

DECIDED_STATUSES = frozenset({"reject", "save", "apply", "rated", "aspirational"})

WS_TABS = ["Dopasowane", "Wszystkie", "Ocenione", "Zapisane", "Aspiracyjne", "Odrzucone"]
WS_TOOLS = ["Uruchom pipeline", "Czego brakuje", "Dodaj z linku"]
WS_ALL = WS_TABS + WS_TOOLS

WS_TAB_HINT = {
    "Dopasowane": "ocenione przez AI, jeszcze nietknięte przez Ciebie",
    "Wszystkie": "cała baza prosto ze skraperów",
    "Ocenione": "wystawiłeś ocenę i zostawiłeś na później",
    "Zapisane": "zapisane oraz te, gdzie aplikacja już poszła",
    "Aspiracyjne": "za wysoko na teraz, ale w tę stronę celujesz",
    "Odrzucone": "odrzucone - profil uczy się, czego nie chcesz",
    "Uruchom pipeline": "konfiguracja CV i kluczy, uruchomienie pełnego pipeline'u oraz postęp na żywo",
    "Czego brakuje": "umiejętności, przez które odpadają oferty skądinąd dopasowane",
    "Dodaj z linku": "wklej adres oferty; po lewej podgląd tego, co wpadnie do bazy",
    "Panel sterowania": "konfiguracja CV i kluczy, uruchomienie pełnego pipeline'u oraz postęp na żywo",
}

WS_STAGE_NAMES = {
    "save": "Zapisane",
    "apply": "Wysłane",
    "interview": "Rozmowa",
    "offer": "Oferta",
    "archive": "Archiwum",
}
WS_STAGE_LEGACY = {"saved": "save", "reject": "archive", "rejected": "archive"}

DECISION_STYLE = {
    "apply": (ui_theme.STATES["moss"], "wysłane"),
    "save": (ui_theme.STATES["slate"], "zapisane"),
    "aspirational": (ui_theme.STATES["amber"], "aspiruję"),
    "reject": (ui_theme.STATES["clay"], "odrzucone"),
    "rated": (ui_theme.STATES["grey"], "ocenione"),
}

def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def format_description(text: str, drop_prefix: str = "") -> List[str]:
    text = strip_html(text or "")
    if drop_prefix and len(drop_prefix) > 8:
        pos = text[:60 + len(drop_prefix)].casefold().find(drop_prefix.casefold())
        if pos != -1:
            text = text[pos + len(drop_prefix):].lstrip(" -–—:.\n")

    paragraphs = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = re.sub(r"\s*\n\s*", " ", chunk)
        chunk = re.sub(r"\s+([.,;:!?])", r"\1", chunk)
        chunk = re.sub(r"\s{2,}", " ", chunk).strip()
        if chunk:
            paragraphs.append(chunk)
    return paragraphs


def format_description_blocks(paragraphs: List[str]) -> List[Dict[str, Any]]:
    """Zwraca bloki strukturalne: akapity i listy punktowane."""
    blocks: List[Dict[str, Any]] = []
    bullets: List[str] = []

    def flush():
        if bullets:
            blocks.append({"type": "list", "items": list(bullets)})
            bullets.clear()

    listing = False
    for raw in paragraphs:
        text = (raw or "").strip()
        if not text:
            continue
        short = len(text) <= 140
        if short and text.endswith(":"):
            flush()
            listing = True
            blocks.append({"type": "lead", "text": text})
            continue
        if short and (listing or not text.endswith((".", "!", "?"))):
            listing = True
            bullets.append(text)
            continue
        flush()
        listing = False
        blocks.append({"type": "p", "text": text})
    flush()
    return blocks


def _file_signature(path: Path) -> Optional[Tuple[int, int]]:
    """Zwraca (mtime_ns, size) lub None jeśli plik nie istnieje."""
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except (FileNotFoundError, OSError):
        return None


class JobDataService:
    """Zarządza pamięcią podręczną bazy ofert, ocen AI i decyzji użytkownika."""

    def __init__(self):
        self._lock = threading.RLock()
        self.raw_jobs: List[Job] = []
        self.analyzed_matches: List[JobMatch] = []
        self.job_lookup: Dict[str, Job] = {}
        self.match_lookup: Dict[str, JobMatch] = {}
        self.zdjete: Set[str] = set()
        self.user_decisions: Dict[str, Any] = {}
        self.data_loaded = False
        self._rev = 0
        self._signatures: Dict[str, Optional[Tuple[int, int]]] = {
            "decisions": None,
            "analyzed": None,
            "jobs": None,
        }
        # (rev, próg, wynik skill_gaps.collect) — lista „Pokaż N ofert” i panel braków
        # czytają ten sam indeks, więc liczba na przycisku zgadza się z listą.
        self._gap_cache: Optional[Tuple[int, int, Tuple[dict, int, int]]] = None

    def fresh_since(self) -> Optional[str]:
        """Start ostatniego pobierania (ISO, czas lokalny) albo None, gdy nigdy nie zapisany."""
        data = load_json_safe(LAST_SCRAPE_RUN_PATH, default=None)
        started = data.get("started_at") if isinstance(data, dict) else None
        return started if isinstance(started, str) and started else None

    def _skill_gap_index(self, threshold: int) -> Tuple[dict, int, int]:
        with self._lock:
            self.ensure_loaded()
            cached = self._gap_cache
            if cached and cached[0] == self._rev and cached[1] == threshold:
                return cached[2]
            entries = [
                {
                    "job": {"link": m.job.link},
                    "match_percentage": m.match_percentage,
                    "missing_skills": m.missing_skills,
                    "learnable_in_month": m.learnable_in_month,
                    "match": m,
                }
                for m in self.analyzed_matches
            ]
            result = skill_gaps.collect(entries, threshold, skill_gaps.dismissed_links(self.user_decisions))
            self._gap_cache = (self._rev, threshold, result)
            return result

    def touch(self):
        with self._lock:
            self._rev += 1

    def reload(self):
        with self._lock:
            self.data_loaded = False
            self.ensure_loaded(force=True)

    def ensure_loaded(self, force=False):
        with self._lock:
            sig_dec = _file_signature(USER_DECISIONS_PATH)
            sig_ana = _file_signature(ANALYZED_JOBS_PATH)
            sig_jobs = _file_signature(JOBS_DATABASE_PATH)

            if (
                self.data_loaded
                and not force
                and sig_dec == self._signatures["decisions"]
                and sig_ana == self._signatures["analyzed"]
                and sig_jobs == self._signatures["jobs"]
            ):
                return

            needs_dec = force or not self.data_loaded or sig_dec != self._signatures["decisions"]
            needs_ana = force or not self.data_loaded or sig_ana != self._signatures["analyzed"]
            needs_jobs = force or not self.data_loaded or sig_jobs != self._signatures["jobs"]

            # 1. User decisions
            if needs_dec:
                self.user_decisions = load_json_safe(USER_DECISIONS_PATH, default={}) or {}
                self._signatures["decisions"] = sig_dec
            # 2. Analyzed jobs
            if needs_ana:
                analyzed_matches = []
                if ANALYZED_JOBS_PATH.exists():
                    try:
                        data = load_json_safe(ANALYZED_JOBS_PATH, default=[]) or []
                        seen = set()
                        for d in data:
                            if isinstance(d, dict) and "job" in d and "link" in d["job"]:
                                link = d["job"]["link"]
                                if link not in seen:
                                    analyzed_matches.append(JobMatch.from_dict(d))
                                    seen.add(link)
                    except Exception as e:
                        logger.error(f"Błąd ładowania wyników analizy AI: {e}")
                self.analyzed_matches = analyzed_matches
                self._signatures["analyzed"] = sig_ana
            # 3. Raw jobs from JobDatabase
            if needs_jobs:
                raw_jobs = []
                if JOBS_DATABASE_PATH.exists():
                    try:
                        db = JobDatabase(str(JOBS_DATABASE_PATH))
                        all_jobs = db.load_jobs()
                        seen_raw = set()
                        for j in all_jobs:
                            if j.link not in seen_raw:
                                raw_jobs.append(j)
                                seen_raw.add(j.link)
                    except Exception as e:
                        logger.error(f"Błąd ładowania bazy surowej: {e}")
                self.raw_jobs = raw_jobs
                self._signatures["jobs"] = sig_jobs
            if needs_ana or needs_jobs:
                lookup: Dict[str, Job] = {}
                matches: Dict[str, JobMatch] = {}
                for m in self.analyzed_matches:
                    lookup[m.job.link] = m.job
                    matches[m.job.link] = m
                for j in self.raw_jobs:
                    if j.link not in lookup:
                        lookup[j.link] = j
                self.job_lookup = lookup
                self.match_lookup = matches

                try:
                    self.zdjete = zdjete_z_portalu(self.raw_jobs)
                except Exception as e:
                    logger.warning(f"Błąd wykrywania ofert zdjętych: {e}")
                    self.zdjete = set()
            self.data_loaded = True
            self._rev += 1

    def get_decision(self, link: str) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str], Optional[str]]:
        """Zwraca (status, rating, stage, decided_at, applied_at)."""
        with self._lock:
            val = self.user_decisions.get(link)
            if val is None:
                val = self.user_decisions.get(canonical_link(link))
            if isinstance(val, dict):
                return (
                    val.get("status"),
                    val.get("rating"),
                    val.get("stage"),
                    val.get("decided_at"),
                    val.get("applied_at"),
                )
            elif isinstance(val, str):
                return val, None, None, None, None
            return None, None, None, None, None

    def update_decision(self, link: str, status: str, rating: Optional[int], stage: Optional[str] = None):
        with self._lock:
            self.ensure_loaded()
            c_link = canonical_link(link)
            if c_link in self.user_decisions:
                old_val = self.user_decisions.pop(c_link)
            elif link in self.user_decisions:
                old_val = self.user_decisions.pop(link)
            else:
                old_val = {}

            existing_stage = old_val.get("stage") if isinstance(old_val, dict) else None
            new_stage = stage if stage else (existing_stage or status)

            decision_data = {
                "status": status,
                "rating": rating,
                "stage": new_stage,
                "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            if status == "apply" or new_stage == "apply":
                decision_data["applied_at"] = (
                    old_val.get("applied_at")
                    if isinstance(old_val, dict) and old_val.get("applied_at")
                    else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            # Następny krok przeżywa zmianę etapu i ponowną decyzję.
            if isinstance(old_val, dict) and old_val.get("next_step"):
                decision_data["next_step"] = old_val["next_step"]

            self.user_decisions[c_link] = decision_data
            ok = save_json_atomic(USER_DECISIONS_PATH, self.user_decisions, backup=True, keep=20)
            if not ok:
                logger.error("Nie udało się zapisać decyzji użytkownika do pliku!")
            self._rev += 1
            return ok

    def get_next_step(self, link: str) -> Optional[Dict[str, Any]]:
        """`{"label", "due"}` z decyzji w nowym formacie; stary format (string) go nie ma."""
        with self._lock:
            val = self.user_decisions.get(link)
            if val is None:
                val = self.user_decisions.get(canonical_link(link))
            step = val.get("next_step") if isinstance(val, dict) else None
            return step if isinstance(step, dict) and step.get("label") else None

    def set_next_step(self, link: str, label: str, due: Optional[str]) -> Tuple[bool, str]:
        """Zapisuje następny krok aplikacji; pusty `label` go usuwa. Wymaga istniejącej decyzji."""
        with self._lock:
            self.ensure_loaded()
            key = link if link in self.user_decisions else canonical_link(link)
            val = self.user_decisions.get(key)
            if val is None:
                return False, "Oferta nie ma decyzji — najpierw ją zapisz albo wyślij."
            # Stary format (goły status) zamienia się w obiekt, który czytniki już obsługują.
            if isinstance(val, str):
                val = {"status": val, "rating": None, "stage": val}
            if label:
                val["next_step"] = {"label": label, "due": due}
            else:
                val.pop("next_step", None)
            # Na miejscu, bez przestawiania klucza: kolejność to historia decyzji.
            self.user_decisions[key] = val
            ok = save_json_atomic(USER_DECISIONS_PATH, self.user_decisions, backup=True, keep=20)
            if not ok:
                logger.error("Nie udało się zapisać następnego kroku do pliku!")
                return False, "Błąd zapisu decyzji do pliku"
            self._rev += 1
            return True, ""

    def restore_decision(self, link: str):
        with self._lock:
            self.ensure_loaded()
            c_link = canonical_link(link)
            changed = False
            if link in self.user_decisions:
                self.user_decisions.pop(link, None)
                changed = True
            if c_link in self.user_decisions:
                self.user_decisions.pop(c_link, None)
                changed = True
            if changed:
                save_json_atomic(USER_DECISIONS_PATH, self.user_decisions, backup=True, keep=20)
                self._rev += 1
            return changed

    def delete_job_permanent(self, link: str):
        with self._lock:
            self.ensure_loaded()
            # 1. DB
            try:
                db = JobDatabase(str(JOBS_DATABASE_PATH))
                db.remove_job(link)
            except Exception as e:
                logger.error(f"Błąd usuwania z JobDatabase: {e}")

            # 2. Analyzed jobs
            try:
                if ANALYZED_JOBS_PATH.exists():
                    data = load_json_safe(ANALYZED_JOBS_PATH, default=[]) or []
                    new_data = [d for d in data if d.get("job", {}).get("link") != link]
                    if len(new_data) != len(data):
                        save_json_atomic(ANALYZED_JOBS_PATH, new_data, backup=True, keep=5)
            except Exception as e:
                logger.error(f"Błąd usuwania z analyzed_jobs_waterfall: {e}")

            # 3. Decision
            self.restore_decision(link)

            # In-memory purge
            c_link = canonical_link(link)
            self.job_lookup.pop(link, None)
            self.job_lookup.pop(c_link, None)
            self.match_lookup.pop(link, None)
            self.match_lookup.pop(c_link, None)
            self.raw_jobs = [j for j in self.raw_jobs if j.link != link and canonical_link(j.link) != c_link]
            self.analyzed_matches = [
                m for m in self.analyzed_matches if m.job.link != link and canonical_link(m.job.link) != c_link
            ]
            self.zdjete.discard(link)
            self.zdjete.discard(c_link)
            self._rev += 1

    def get_funnel_stages(self) -> Dict[str, int]:
        with self._lock:
            self.ensure_loaded()
            stages = {k: 0 for k in WS_STAGE_NAMES}
            for link, ddata in self.user_decisions.items():
                if isinstance(ddata, dict):
                    status = ddata.get("status")
                    stage = ddata.get("stage") or status
                else:
                    status = ddata
                    stage = status
                stage = WS_STAGE_LEGACY.get(stage, stage)
                if status == "reject" or stage == "archive":
                    continue
                if stage in stages:
                    stages[stage] += 1
            return stages

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            self.ensure_loaded()
            valid_db_links = {
                canonical_link(j.link)
                for j in self.raw_jobs
                if (getattr(j, "description", None) or "").strip()
                and (getattr(j, "description", None) or "").strip() != "Brak opisu"
                and len((getattr(j, "description", None) or "").strip()) > 20
            }
            analyzed_links = {canonical_link(m.job.link) for m in self.analyzed_matches}
            decided_links = {canonical_link(l) for l in self.user_decisions}
            try:
                pending = get_pending_scoring_count(
                    jobs=self.raw_jobs,
                    analyzed=self.analyzed_matches,
                    decisions=self.user_decisions,
                    base_dir=Path(__file__).parent,
                )
            except Exception:
                pending = len((valid_db_links - analyzed_links) - decided_links)

            today = datetime.now().date()
            stale_count = 0
            for link, ddata in self.user_decisions.items():
                if isinstance(ddata, dict):
                    status = ddata.get("status")
                    stage = ddata.get("stage") or status
                    decided_at = ddata.get("decided_at")
                else:
                    status = ddata
                    stage = status
                    decided_at = None
                stage_norm = WS_STAGE_LEGACY.get(stage, stage)
                if status == "reject" or stage_norm == "archive":
                    continue
                if stage_norm in WS_STAGE_NAMES and decided_at:
                    dt = _parse_dt(decided_at)
                    if dt and (today - dt.date()).days >= 14:
                        stale_count += 1

            return {
                "raw_count": len(self.raw_jobs),
                "analyzed_count": len(self.analyzed_matches),
                "pending_scoring_count": pending,
                "decisions_count": len(self.user_decisions),
                "funnel": self.get_funnel_stages(),
                "applications_stale": stale_count,
            }

    def get_applications(self) -> Dict[str, Any]:
        with self._lock:
            self.ensure_loaded()
            counts = {k: 0 for k in WS_STAGE_NAMES}
            items = []
            today = datetime.now().date()
            stale_count = 0

            for link, ddata in reversed(list(self.user_decisions.items())):
                if isinstance(ddata, dict):
                    status = ddata.get("status")
                    stage = ddata.get("stage") or status
                    rating = ddata.get("rating")
                    decided_at = ddata.get("decided_at")
                    applied_at = ddata.get("applied_at")
                else:
                    status = ddata
                    stage = status
                    rating = None
                    decided_at = None
                    applied_at = None

                stage_norm = WS_STAGE_LEGACY.get(stage, stage)
                if status == "reject":
                    continue
                if stage_norm not in WS_STAGE_NAMES:
                    continue

                dt = _parse_dt(decided_at)
                age_days = max(0, (today - dt.date()).days) if dt else None

                if stage_norm != "archive" and age_days is not None and age_days >= 14:
                    stale_count += 1

                counts[stage_norm] += 1

                job = self.job_lookup.get(link) or self.job_lookup.get(canonical_link(link))
                match = self.match_lookup.get(link) or (self.match_lookup.get(job.link) if job else None)
                title = (job.title if job else None) or "Bez tytułu"
                company = (job.company if job else None) or "—"
                location = (job.location if job else None) or ""
                pct = int(match.match_percentage) if match and match.match_percentage is not None else None

                items.append({
                    "link": link,
                    "title": title,
                    "company": company,
                    "location": location,
                    "match_percentage": pct,
                    "status": status or stage_norm,
                    "rating": rating,
                    "stage": stage_norm,
                    "decided_at": decided_at,
                    "applied_at": applied_at,
                    "age_days": age_days,
                    "next_step": self.get_next_step(link),
                })

            return {
                "items": items,
                "counts": counts,
                "total": len(items),
                "stale_count": stale_count,
            }

    def ws_collect(
        self, tab: str, search: str = "", gap: Optional[str] = None, gap_threshold: int = 50
    ) -> List[Tuple[Job, Optional[JobMatch], Optional[str], Optional[int]]]:
        """`gap`: oferty z tym brakiem (ta sama normalizacja i próg co panel braków), zamiast zakładki."""
        with self._lock:
            self.ensure_loaded()
            matches_by_link = self.match_lookup
            out = []

            if gap:
                gaps, _, _ = self._skill_gap_index(gap_threshold)
                entry = gaps.get(skill_gaps.normalize(gap))
                for e in (entry["links"].values() if entry else ()):
                    m = e["match"]
                    status, rating, _, _, _ = self.get_decision(m.job.link)
                    out.append((m.job, m, status, rating))
                out.sort(key=lambda t: t[1].match_percentage, reverse=True)

            elif tab == "Dopasowane":
                for m in self.analyzed_matches:
                    status, rating, _, _, _ = self.get_decision(m.job.link)
                    if status in DECIDED_STATUSES:
                        continue
                    out.append((m.job, m, status, rating))
                zdjete = self.zdjete
                out.sort(
                    key=lambda t: (
                        t[0].link not in zdjete,
                        t[1].match_percentage if t[1] else -1,
                    ),
                    reverse=True,
                )

            elif tab == "Wszystkie":
                for j in self.raw_jobs:
                    status, rating, _, _, _ = self.get_decision(j.link)
                    out.append((j, matches_by_link.get(j.link), status, rating))
                zdjete = self.zdjete
                out.sort(
                    key=lambda t: (
                        t[0].link not in zdjete,
                        t[1].match_percentage if t[1] else -1,
                        getattr(t[0], "scraped_at", "") or "",
                    ),
                    reverse=True,
                )

            else:
                wanted = {
                    "Ocenione": ("rated",),
                    "Zapisane": ("save", "apply"),
                    "Aspiracyjne": ("aspirational",),
                    "Odrzucone": ("reject",),
                }.get(tab, ())

                for link in reversed(list(self.user_decisions.keys())):
                    status, rating, _, _, _ = self.get_decision(link)
                    if status not in wanted:
                        continue
                    job = self.job_lookup.get(link) or self.job_lookup.get(canonical_link(link))
                    if job is None:
                        continue
                    out.append((job, matches_by_link.get(link) or matches_by_link.get(job.link), status, rating))
                # Ocenione: od najwyższej oceny użytkownika; sort jest stabilny, więc remisy
                # zostają w kolejności od najnowszej decyzji.
                if tab == "Ocenione":
                    out.sort(key=lambda t: t[3] if isinstance(t[3], (int, float)) else -1, reverse=True)

            if search:
                q = search.lower()
                out = [
                    t
                    for t in out
                    if q in (t[0].title or "").lower() or q in (t[0].company or "").lower()
                ]

            return out

    def get_offer_detail(self, link: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.ensure_loaded()
            job = self.job_lookup.get(link) or self.job_lookup.get(canonical_link(link))
            if not job:
                return None

            match = self.match_lookup.get(job.link) or self.match_lookup.get(link)
            status, rating, stage, decided_at, applied_at = self.get_decision(job.link)

            pct = int(match.match_percentage) if match else None

            stage_norm = WS_STAGE_LEGACY.get(stage, stage)
            if stage_norm not in WS_STAGE_NAMES:
                stage_norm = status if status in WS_STAGE_NAMES else "save"

            work_mode = detect_work_mode(job.location or "", job.description or "", job.title or "")
            paragraphs = format_description(job.description or "", drop_prefix=job.title or "")
            blocks = format_description_blocks(paragraphs)

            dot_color, dot_label = DECISION_STYLE.get(status, (None, None))
            if status == "rated" and rating:
                dot_label = f"ocena {rating}/10"

            industry = getattr(match, "industry", None) if match else None
            is_entry_level = bool(getattr(match, "is_entry_level", False)) if match else False
            learnable_in_month = bool(getattr(match, "learnable_in_month", False)) if match else False
            missing_skills = list(getattr(match, "missing_skills", None) or []) if match else []
            return {
                "link": job.link,
                "title": job.title or "Bez tytułu",
                "company": job.company or "—",
                "location": job.location or "Warszawa",
                "source": job.source or "—",
                "source_color": ui_theme.source_color(job.source),
                "is_gone": job.link in self.zdjete,
                "work_mode": work_mode["label"],
                "match_percentage": pct,
                "reason": getattr(match, "reason", None) if match else None,
                "industry": industry,
                "is_entry_level": is_entry_level,
                "learnable_in_month": learnable_in_month,
                "missing_skills": missing_skills,
                "description_blocks": blocks,
                "raw_description": job.description or "",
                "status": status,
                "rating": rating,
                "stage": stage_norm,
                "decided_at": decided_at,
                "applied_at": applied_at,
                "dot_color": dot_color,
                "dot_label": dot_label,
                "next_step": self.get_next_step(job.link) or self.get_next_step(link),
                "highlights": list(getattr(match, "highlights", None) or []) if match else [],
            }

    def get_activity_rows(self, limit: int = 6) -> List[Dict[str, Any]]:
        rows = []
        # 1. Scraper status
        if SCRAPER_STATUS_PATH.exists():
            runs = load_json_safe(SCRAPER_STATUS_PATH, default={}) or {}
            for source, info in runs.items():
                if not isinstance(info, dict):
                    continue
                raw = info.get("timestamp") or info.get("last_run_date") or ""
                try:
                    when = datetime.fromisoformat(str(raw))
                except ValueError:
                    continue
                count = info.get("jobs_count")
                ok = info.get("status") == "success"
                detail = f"{count} ofert" if isinstance(count, int) else str(info.get("status", ""))
                rows.append({
                    "timestamp": when.isoformat(),
                    "time_str": when.strftime("%Y-%m-%d %H:%M:%S"),
                    "dt": when,
                    "op": "pobieranie",
                    "what": source,
                    "source_color": ui_theme.source_color(source),
                    "detail": detail,
                    "bad": not ok,
                })

        # 2. AI evaluation
        if ANALYZED_JOBS_PATH.exists():
            mtime = datetime.fromtimestamp(ANALYZED_JOBS_PATH.stat().st_mtime)
            rows.append({
                "timestamp": mtime.isoformat(),
                "time_str": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                "dt": mtime,
                "op": "ocena AI",
                "what": "waterfall_analysis.py",
                "source_color": None,
                "detail": f"{len(self.analyzed_matches)} ocen",
                "bad": False,
            })

        # 3. Profile rebuild
        if PREFERENCE_PROFILE_PATH.exists():
            mtime = datetime.fromtimestamp(PREFERENCE_PROFILE_PATH.stat().st_mtime)
            profile = load_json_safe(PREFERENCE_PROFILE_PATH, default={}) or {}
            built = profile.get("_metadata", {}).get("total_decisions_analyzed", 0) or 0
            rows.append({
                "timestamp": mtime.isoformat(),
                "time_str": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                "dt": mtime,
                "op": "profil",
                "what": "generate_preference_profile.py",
                "source_color": None,
                "detail": f"z {built} decyzji",
                "bad": False,
            })

        rows.sort(key=lambda r: r["dt"], reverse=True)
        for r in rows:
            r.pop("dt", None)
        return rows[:limit]

    def get_recent_decisions(self, limit: int = 7) -> List[Dict[str, Any]]:
        with self._lock:
            self.ensure_loaded()
            out = []
            for link in reversed(list(self.user_decisions.keys())):
                if len(out) >= limit:
                    break
                status, rating, stage, decided_at, applied_at = self.get_decision(link)
                dot_color, label = DECISION_STYLE.get(status, (None, None))
                if not dot_color:
                    continue
                job = self.job_lookup.get(link) or self.job_lookup.get(canonical_link(link))
                if not job:
                    continue
                if status == "rated" and rating:
                    label = f"ocena {rating}/10"

                out.append({
                    "link": job.link,
                    "title": job.title or "Bez tytułu",
                    "company": job.company or "",
                    "status": status,
                    "rating": rating,
                    "label": label,
                    "color": dot_color,
                    "stamp": decided_at or applied_at,
                })
            return out

    def get_skill_gaps(self, threshold: int = 50) -> Dict[str, Any]:
        with self._lock:
            gaps, considered, skipped = self._skill_gap_index(threshold)
            rows = skill_gaps.rank(gaps, 14)
            top = rows[0]["offers"] if rows else 1
            for r in rows:
                r["width_pct"] = max(4, round(100 * r["offers"] / top))

            return {
                "threshold": threshold,
                "rows": rows,
                "considered": considered,
                "skipped": skipped,
                "total_gaps": len(gaps),
            }


# Singleton instancja
job_data_service = JobDataService()


# --- CV & API Keys & Environment Management ---

CV_TXT_PATH = Path(__file__).parent / "final_cv_text.txt"
CV_PDF_PATH = Path(__file__).parent / "cv.pdf"

def get_cv_info() -> Dict[str, Any]:
    cv_txt_path = CV_TXT_PATH
    cv_pdf_path = CV_PDF_PATH
    text = ""
    if cv_txt_path.exists():
        try:
            with open(cv_txt_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content and not content.startswith("ERROR:"):
                    text = content
        except Exception as e:
            logger.warning(f"Błąd odczytu {cv_txt_path}: {e}")

    if not text and cv_pdf_path.exists():
        try:
            extracted = CVParser.extract_from_pdf(str(cv_pdf_path))
            if extracted and len(extracted.strip()) > 20:
                text = CVParser.clean_text(extracted)
                with open(cv_txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
        except Exception as e:
            logger.warning(f"Błąd ekstrakcji z {cv_pdf_path}: {e}")

    has_cv = bool(text and len(text.strip()) > 20)
    chars = len(text)
    words = len(text.split()) if text else 0
    filename = "final_cv_text.txt" if cv_txt_path.exists() else ("cv.pdf" if cv_pdf_path.exists() else "Brak")

    return {
        "ready": has_cv,
        "text": text,
        "chars": chars,
        "words": words,
        "filename": filename,
        "pdf_exists": cv_pdf_path.exists(),
        "txt_exists": cv_txt_path.exists(),
    }


def save_uploaded_cv(filename: str, content_bytes: bytes) -> Tuple[bool, str]:
    cv_txt_path = CV_TXT_PATH
    name = filename.lower()
    text = None

    try:
        if name.endswith(".pdf"):
            pdf_path = CV_PDF_PATH
            with open(pdf_path, "wb") as f:
                f.write(content_bytes)
            text = CVParser.extract_from_pdf(str(pdf_path))
        elif name.endswith((".docx", ".doc")):
            docx_path = CV_PDF_PATH.with_name("cv.docx")
            with open(docx_path, "wb") as f:
                f.write(content_bytes)
            text = CVParser.extract_from_docx(str(docx_path))
        else:
            text = content_bytes.decode("utf-8", errors="replace")

        if text and len(text.strip()) > 20:
            cleaned = CVParser.clean_text(text)
            with open(cv_txt_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            return True, f"Zapisano CV ({len(cleaned)} znaków)."
        else:
            return False, "Plik CV jest pusty lub nie udało się wyodrębnić tekstu."
    except Exception as e:
        logger.error(f"Błąd zapisu CV: {e}")
        return False, f"Błąd przetwarzania pliku CV: {e}"


def save_pasted_cv_text(raw_text: str) -> Tuple[bool, str]:
    cv_txt_path = CV_TXT_PATH
    if not raw_text or len(raw_text.strip()) < 20:
        return False, "Wklejona treść CV jest za krótka (minimum 20 znaków)."
    try:
        cleaned = CVParser.clean_text(raw_text)
        with open(cv_txt_path, "w", encoding="utf-8") as f:
            f.write(cleaned)
        return True, f"Zapisano treść CV ({len(cleaned)} znaków)."
    except Exception as e:
        logger.error(f"Błąd zapisu tekstu CV: {e}")
        return False, f"Błąd zapisu tekstu CV: {e}"


def open_local_cv_pdf() -> Tuple[bool, str]:
    cv_pdf_file = CV_PDF_PATH
    if not cv_pdf_file.exists():
        return False, "Plik cv.pdf nie istnieje na dysku."
    try:
        os.startfile(str(cv_pdf_file))
        return True, "Otwarto plik CV."
    except Exception as e:
        return False, f"Nie udało się otworzyć pliku: {e}"


def get_api_keys_info() -> Dict[str, Any]:
    base = Path(__file__).parent
    env_path = base / ".env"
    gemini_keys = []

    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k.startswith("GEMINI_API_KEY") and v and v not in _PLACEHOLDERS:
                        gemini_keys.append((k, v))
        except Exception as e:
            logger.warning(f"Błąd czytania .env: {e}")

    if not gemini_keys:
        for k in ("GEMINI_API_KEY_PRIMARY", "GEMINI_API_KEY_1", "GEMINI_API_KEY"):
            v = os.getenv(k, "").strip()
            if v and v not in _PLACEHOLDERS:
                gemini_keys.append((k, v))

    primary_key = next((v for k, v in gemini_keys if k == "GEMINI_API_KEY_PRIMARY"), "")
    if not primary_key and gemini_keys:
        primary_key = gemini_keys[0][1]

    has_key = bool(primary_key)
    masked = f"{primary_key[:4]}…{primary_key[-4:]}" if len(primary_key) > 8 else ("skonfigurowany" if primary_key else "brak")

    return {
        "ready": has_key,
        "count": len(gemini_keys),
        "primary_masked": masked,
    }


def save_quick_gemini_key(new_key: str):
    env_path = Path(__file__).parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    written, out = False, []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k == "GEMINI_API_KEY_PRIMARY":
                out.append(f"GEMINI_API_KEY_PRIMARY={new_key}")
                written = True
                continue
        out.append(line)
    if not written:
        out.append(f"GEMINI_API_KEY_PRIMARY={new_key}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY_PRIMARY"] = new_key


ENV_FIELDS = [
    ("GEMINI_API_KEY_PRIMARY", "Gemini - klucz główny", True),
    ("GEMINI_API_KEY_1", "Gemini - zapas 1", True),
    ("GEMINI_API_KEY_2", "Gemini - zapas 2", True),
    ("GEMINI_API_KEY_3", "Gemini - zapas 3", True),
    ("GEMINI_API_KEY_4", "Gemini - zapas 4", True),
    ("ADZUNA_APP_ID", "Adzuna App ID", False),
    ("ADZUNA_APP_KEY", "Adzuna App Key", True),
    ("JOOBLE_API_KEY", "Jooble API Key", True),
    ("CAREERJET_API_KEY", "Careerjet API Key", True),
]


def get_env_fields_status() -> List[Dict[str, Any]]:
    """Zwraca metadane pól .env bez wysyłania wartości sekretów do przeglądarki."""
    env_path = Path(__file__).parent / ".env"
    configured_keys = set()
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if v and v not in _PLACEHOLDERS:
                        configured_keys.add(k)
        except OSError as e:
            logger.warning(f"Błąd odczytu .env: {e}")

    out = []
    for key, label, secret in ENV_FIELDS:
        out.append({
            "key": key,
            "label": label,
            "secret": secret,
            "configured": key in configured_keys,
        })
    return out


def save_env_keys(updates: Dict[str, str]) -> Tuple[bool, str]:
    env_path = Path(__file__).parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    written, out = set(), []
    # Filter out empty or whitespace-only values that weren't changed
    clean_updates = {k: v.strip() for k, v in updates.items() if v is not None and v.strip() != ""}

    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in clean_updates:
                out.append(f"{k}={clean_updates[k]}")
                written.add(k)
                continue
        out.append(line)
    for k, v in clean_updates.items():
        if k not in written:
            out.append(f"{k}={v}")
    try:
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        # Update process env for immediately visible changes
        for k, v in clean_updates.items():
            os.environ[k] = v
        return True, "Zapisano klucze w .env."
    except Exception as e:
        return False, f"Błąd zapisu .env: {e}"


def _get_chromium_executable_path() -> Optional[str]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        return p.chromium.executable_path


def check_playwright_chromium() -> Tuple[bool, str]:
    try:
        import playwright
    except ImportError:
        return False, "Brak Playwright (uruchom: pip install playwright)"

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            exe_path = executor.submit(_get_chromium_executable_path).result()
    except ImportError:
        return False, "Brak Playwright (uruchom: pip install playwright)"
    except Exception as e:
        return False, f"Błąd weryfikacji Playwright: {e}"

    try:
        if exe_path and Path(exe_path).is_file():
            return True, "Playwright + Chromium gotowe"
    except (OSError, ValueError):
        pass
    return False, "Brak Chromium (uruchom: python -m playwright install chromium)"

def check_pipeline_prerequisites() -> Dict[str, Any]:
    job_data_service.ensure_loaded()
    cv_info = get_cv_info()
    api_info = get_api_keys_info()
    db_count = len(job_data_service.raw_jobs)
    pw_ok, pw_msg = check_playwright_chromium()

    issues = []
    if not cv_info["ready"]:
        issues.append("Brak aktywnego CV (dodaj plik lub wklej treść w lewym panelu).")
    if not api_info["ready"]:
        issues.append("Brak klucza Gemini API (skonfiguruj w .env lub lewym panelu).")

    ready_skip = len(issues) == 0 and db_count > 0
    if db_count == 0:
        issues_skip = issues + ["Baza ofert jest pusta (wymagany pełny przebieg ze scrapingiem)."]
    else:
        issues_skip = issues

    issues_full = list(issues)
    if not pw_ok:
        issues_full.append(f"Środowisko scraperów: {pw_msg}")
    ready_full = len(issues_full) == 0

    return {
        "ready_full": ready_full,
        "ready_skip": ready_skip,
        "issues": issues_full,
        "issues_skip": issues_skip,
        "cv_ready": cv_info["ready"],
        "api_ready": api_info["ready"],
        "playwright_ready": pw_ok,
        "playwright_msg": pw_msg,
        "db_count": db_count,
    }


def fetch_job_from_link(url: str) -> Dict[str, Any]:
    from utils.link_fetcher import extract_job_info_from_url
    fetched = extract_job_info_from_url(url)
    fetched["link"] = url
    return fetched


def save_manual_job(data: Dict[str, Any]) -> Tuple[bool, str]:
    job_data_service.ensure_loaded()
    job = Job(
        title=data.get("title", ""),
        company=data.get("company", ""),
        link=data.get("link", ""),
        description=data.get("description", ""),
        source=data.get("source", "") or "Manual",
        location=data.get("location", "") or "Warszawa",
    )
    if not job.title or not job.company or not job.link:
        return False, "Tytuł, firma i link są wymagane."

    try:
        db = JobDatabase(str(JOBS_DATABASE_PATH))
        jobs = [j for j in db.load_jobs() if j.link != job.link]
        jobs.append(job)
        db.save_jobs(jobs)

        # Update in-memory
        existing = job_data_service.job_lookup.get(job.link)
        if existing is not None:
            for field in ("title", "company", "location", "source", "description"):
                setattr(existing, field, getattr(job, field))
        else:
            job_data_service.raw_jobs.append(job)
            job_data_service.job_lookup[job.link] = job

        job_data_service.touch()
        return True, "Oferta zapisana w bazie."
    except Exception as e:
        logger.error(f"Nie udało się zapisać oferty do bazy: {e}")
        return False, f"Błąd zapisu: {e}"
