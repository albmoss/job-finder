"""
App Services - obsługa danych, decyzji użytkownika, CV, kluczy API i narzędzi dla Job Finder.
Niezależna warstwa usługowa dla backendu HTTP.
"""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from config import (
    JOBS_DATABASE_PATH,
    LAST_SCRAPE_RUN_PATH,
)
from utils.llm import PLACEHOLDERS, PROVIDERS, settings_from_env
from utils.cv_parser import CVParser
from utils.data_models import JobDatabase, Job, JobMatch, is_placeholder_description
from utils.text_cleaner import detect_work_mode, strip_html
from utils.safe_io import save_json_atomic, load_json_safe
from utils.links import canonical_link
from utils.candidate_scope import load_profile
from utils.liveness import zdjete_z_portalu
from cv_tailor import store as cv_store

logger = logging.getLogger(__name__)

USER_DECISIONS_PATH = Path("user_decisions.json")
MATCH_RESULTS_PATH = Path("match_results.json")

OFFER_TABS = ("Dopasowane", "Ukryte", "Zapisane")
SAVED_STATUSES = frozenset({"save", "aspirational"})
HIDDEN_STATUSES = frozenset({"reject"})
DECIDED_STATUSES = SAVED_STATUSES | HIDDEN_STATUSES | {"apply"}
DECISION_STATUSES = ("save", "apply", "reject")
APP_STAGES = ("apply", "interview", "offer", "archive")

WORK_MODE_LABELS = {"remote": "zdalnie", "hybrid": "hybrydowo", "onsite": "stacjonarnie"}
CURRENCY_LABELS = {"PLN": "zł", "EUR": "€", "USD": "$", "GBP": "£"}
SALARY_PERIODS = {"hour": "/h", "day": "/dzień", "week": "/tydz.", "year": "/rok"}


def app_stage(stage: Optional[str]) -> str:
    return stage if stage in APP_STAGES else "apply"


def _money(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", "\u00a0")


def salary_text(salary: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(salary, dict):
        return None
    low, high = salary.get("min"), salary.get("max")
    if low is None and high is None:
        return None
    if low is not None and high is not None and low != high:
        amount = f"{_money(low)}–{_money(high)}"
    elif low is not None and high is None:
        amount = f"od {_money(low)}"
    elif low is None:
        amount = f"do {_money(high)}"
    else:
        amount = _money(low)
    currency = str(salary.get("currency") or "PLN").upper()
    suffix = SALARY_PERIODS.get(str(salary.get("period") or "month"), "")
    return f"{amount} {CURRENCY_LABELS.get(currency, currency)}{suffix}"


def work_mode_text(job: Job) -> str:
    modes = [m for m in (getattr(job, "work_modes", None) or []) if m in WORK_MODE_LABELS]
    if modes:
        if "hybrid" in modes:
            return WORK_MODE_LABELS["hybrid"]
        return WORK_MODE_LABELS[modes[0]] if len(modes) == 1 else WORK_MODE_LABELS["hybrid"]
    label = detect_work_mode(job.location or "", job.description or "", job.title or "")["label"]
    return {"Hybrydowo": "hybrydowo", "100% Zdalnie": "zdalnie"}.get(label, "")


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
        # Tytuł powtórzony jako nagłówek opisu („Junior Developer\n…”, „Stanowisko: …”) wycinamy
        # razem z tym, co nad nim. Tytuł w środku zdania („poszukujemy Junior Developera z…”)
        # to treść i zostaje.
        pos = text[:60 + len(drop_prefix)].casefold().find(drop_prefix.casefold())
        end = pos + len(drop_prefix)
        if pos != -1:
            before = text[:pos].rsplit("\n", 1)[-1].strip()
            if (not before or before.endswith(":")) and not text[end:end + 1].isalnum():
                text = text[end:].lstrip(" -–—:.\n")

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
        # Oferty odrzucone przez przesiew i oferty bez wpisu w match_results (liczone przy wczytaniu).
        self.filtered_count = 0
        self.pending_count = 0
        self.match_results: Dict[str, dict] = {}
        self.job_lookup: Dict[str, Job] = {}
        self.match_lookup: Dict[str, JobMatch] = {}
        self.zdjete: Set[str] = set()
        self.user_decisions: Dict[str, Any] = {}
        self.data_loaded = False
        self._rev = 0
        self._signatures: Dict[str, Optional[Tuple[int, int]]] = {
            "decisions": None,
            "matches": None,
            "jobs": None,
        }

    def fresh_since(self) -> Optional[str]:
        """Start ostatniego pobierania (ISO, czas lokalny) albo None, gdy nigdy nie zapisany."""
        data = load_json_safe(LAST_SCRAPE_RUN_PATH, default=None)
        started = data.get("started_at") if isinstance(data, dict) else None
        return started if isinstance(started, str) and started else None

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
            sig_mat = _file_signature(MATCH_RESULTS_PATH)
            sig_jobs = _file_signature(JOBS_DATABASE_PATH)

            if (
                self.data_loaded
                and not force
                and sig_dec == self._signatures["decisions"]
                and sig_mat == self._signatures["matches"]
                and sig_jobs == self._signatures["jobs"]
            ):
                return

            needs_dec = force or not self.data_loaded or sig_dec != self._signatures["decisions"]
            needs_mat = force or not self.data_loaded or sig_mat != self._signatures["matches"]
            needs_jobs = force or not self.data_loaded or sig_jobs != self._signatures["jobs"]

            # 1. User decisions
            if needs_dec:
                self.user_decisions = load_json_safe(USER_DECISIONS_PATH, default={}) or {}
                self._signatures["decisions"] = sig_dec
            # 2. Match results
            if needs_mat:
                match_results = {}
                if MATCH_RESULTS_PATH.exists():
                    try:
                        data = load_json_safe(MATCH_RESULTS_PATH, default={})
                        if isinstance(data, dict):
                            match_results = data
                    except Exception as e:
                        logger.error(f"Błąd ładowania match_results.json: {e}")
                self.match_results = match_results
                self._signatures["matches"] = sig_mat
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
            if needs_mat or needs_jobs:
                lookup: Dict[str, Job] = {}
                matches: Dict[str, JobMatch] = {}
                scored_matches: List[JobMatch] = []
                filtered_count = pending_count = 0
                seen_links = set()
                for j in self.raw_jobs:
                    c_link = canonical_link(j.link)
                    lookup[j.link] = j
                    lookup[c_link] = j
                    if j.link not in seen_links and c_link not in seen_links:
                        seen_links.add(j.link)
                        seen_links.add(c_link)
                        res = self.match_results.get(c_link) or self.match_results.get(j.link)
                        pct = None
                        if isinstance(res, dict):
                            raw_pct = res.get("percent")
                            if raw_pct is not None:
                                try:
                                    pct = int(raw_pct)
                                except (ValueError, TypeError):
                                    pct = None
                            if pct is None and res.get("filtered"):
                                filtered_count += 1
                        else:
                            pending_count += 1
                        m = JobMatch(job=j, match_percentage=pct)
                        matches[j.link] = m
                        matches[c_link] = m
                        if pct is not None:
                            scored_matches.append(m)
                    else:
                        m = matches.get(c_link) or matches.get(j.link)
                        if m:
                            matches[j.link] = m
                            matches[c_link] = m
                self.job_lookup = lookup
                self.match_lookup = matches
                self.analyzed_matches = scored_matches
                self.filtered_count = filtered_count
                self.pending_count = pending_count

                try:
                    self.zdjete = zdjete_z_portalu(self.raw_jobs)
                except Exception as e:
                    logger.warning(f"Błąd wykrywania ofert zdjętych: {e}")
                    self.zdjete = set()
            self.data_loaded = True
            self._rev += 1
    def _raw_decision(self, link: str) -> Tuple[Optional[str], Any]:
        for key in (link, canonical_link(link)):
            if key in self.user_decisions:
                return key, self.user_decisions[key]
        return None, None

    def decision(self, link: str) -> Dict[str, Any]:
        """Decyzja w jednym kształcie, niezależnie od formatu zapisu (goły status albo obiekt)."""
        with self._lock:
            _, val = self._raw_decision(link)
            if isinstance(val, str):
                val = {"status": val}
            if not isinstance(val, dict):
                val = {}
            step = val.get("next_step")
            return {
                "status": val.get("status"),
                "rating": val.get("rating"),
                "stage": val.get("stage"),
                "decided_at": val.get("decided_at"),
                "applied_at": val.get("applied_at"),
                "note": val.get("note") or "",
                "next_step": step if isinstance(step, dict) and step.get("label") else None,
                "cv_version_id": val.get("cv_version_id"),
            }

    def _save_decisions(self) -> bool:
        ok = save_json_atomic(USER_DECISIONS_PATH, self.user_decisions, backup=True, keep=20)
        if not ok:
            logger.error("Nie udało się zapisać decyzji użytkownika do pliku!")
        self._rev += 1
        return ok

    def update_decision(self, link: str, status: str, stage: Optional[str] = None,
                        cv_version_id: Optional[str] = None) -> bool:
        with self._lock:
            self.ensure_loaded()
            c_link = canonical_link(link)
            old = self.decision(link)
            # Decyzja mogła zostać zapisana pod oboma postaciami linku - zdejmujemy obie,
            # inaczej stara wersja zostaje obok nowej.
            self.user_decisions.pop(c_link, None)
            self.user_decisions.pop(link, None)

            now = datetime.now()
            data: Dict[str, Any] = {
                "status": status,
                "rating": old["rating"],
                "decided_at": now.strftime("%Y-%m-%d %H:%M"),
            }
            if status == "apply":
                data["stage"] = app_stage(stage or old["stage"])
                data["applied_at"] = old["applied_at"] or now.strftime("%Y-%m-%d %H:%M:%S")
                version_id = cv_version_id or old["cv_version_id"]
                if version_id:
                    data["cv_version_id"] = version_id
            # Notatka i następny krok przeżywają zmianę etapu i ponowną decyzję.
            if old["note"]:
                data["note"] = old["note"]
            if old["next_step"]:
                data["next_step"] = old["next_step"]

            self.user_decisions[c_link] = data
            return self._save_decisions()

    def set_note(self, link: str, note: str) -> Tuple[bool, str]:
        with self._lock:
            self.ensure_loaded()
            key, val = self._raw_decision(link)
            if val is None:
                return False, "Oferta nie ma decyzji — najpierw ją zapisz."
            if isinstance(val, str):
                val = {"status": val, "rating": None}
            if note:
                val["note"] = note
            else:
                val.pop("note", None)
            self.user_decisions[key] = val
            if not self._save_decisions():
                return False, "Błąd zapisu decyzji do pliku"
            return True, ""

    def set_next_step(self, link: str, label: str, due: Optional[str]) -> Tuple[bool, str]:
        """Zapisuje następny krok aplikacji; pusty `label` go usuwa. Wymaga istniejącej decyzji."""
        with self._lock:
            self.ensure_loaded()
            key, val = self._raw_decision(link)
            if val is None:
                return False, "Oferta nie ma decyzji — najpierw ją zapisz albo wyślij."
            if isinstance(val, str):
                val = {"status": val, "rating": None}
            if label:
                val["next_step"] = {"label": label, "due": due}
            else:
                val.pop("next_step", None)
            # Na miejscu, bez przestawiania klucza: kolejność to historia decyzji.
            self.user_decisions[key] = val
            if not self._save_decisions():
                return False, "Błąd zapisu decyzji do pliku"
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

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            self.ensure_loaded()
            fresh_since = self.fresh_since()
            fresh_count = (
                sum(1 for j in self.raw_jobs if (j.scraped_at or "") >= fresh_since) if fresh_since else 0
            )
            return {
                "raw_count": len(self.raw_jobs),
                "scored_count": len(self.analyzed_matches),
                "filtered_count": self.filtered_count,
                "pending_scoring_count": self.pending_count,
                "fresh_count": fresh_count,
                "decisions_count": len(self.user_decisions),
            }

    def _job(self, link: str) -> Optional[Job]:
        return self.job_lookup.get(link) or self.job_lookup.get(canonical_link(link))

    def _percent(self, job: Job) -> Optional[int]:
        match = self.match_lookup.get(job.link) or self.match_lookup.get(canonical_link(job.link))
        return int(match.match_percentage) if match and match.match_percentage is not None else None

    def get_applications(self) -> Dict[str, Any]:
        with self._lock:
            self.ensure_loaded()
            counts = {k: 0 for k in APP_STAGES}
            items = []
            for link in reversed(list(self.user_decisions.keys())):
                rec = self.decision(link)
                if rec["status"] != "apply":
                    continue
                stage = app_stage(rec["stage"])
                counts[stage] += 1
                job = self._job(link)
                version_id = rec["cv_version_id"]
                items.append({
                    "link": job.link if job else link,
                    "title": (job.title if job else None) or "Bez tytułu",
                    "company": (job.company if job else None) or "—",
                    "location": (job.location if job else None) or "",
                    "match_percentage": self._percent(job) if job else None,
                    "stage": stage,
                    "decided_at": rec["decided_at"],
                    "applied_at": rec["applied_at"],
                    "next_step": rec["next_step"],
                    "note": rec["note"],
                    "cv": cv_store.summary(version_id) if version_id else None,
                })
            return {"items": items, "counts": counts, "total": len(items)}

    def ws_collect(self, tab: str, search: str = "", sort: str = "match") -> List[Tuple[Job, Dict[str, Any]]]:
        with self._lock:
            self.ensure_loaded()
            out: List[Tuple[Job, Dict[str, Any]]] = []

            if tab == "Dopasowane":
                for m in self.analyzed_matches:
                    rec = self.decision(m.job.link)
                    if rec["status"] in DECIDED_STATUSES:
                        continue
                    out.append((m.job, rec))
                if sort == "newest":
                    out.sort(key=lambda t: t[0].scraped_at or "", reverse=True)
                else:
                    zdjete = self.zdjete
                    out.sort(
                        key=lambda t: (t[0].link not in zdjete, self._percent(t[0]) or -1),
                        reverse=True,
                    )
            else:
                wanted = SAVED_STATUSES if tab == "Zapisane" else HIDDEN_STATUSES
                seen: Set[str] = set()
                for link in reversed(list(self.user_decisions.keys())):
                    rec = self.decision(link)
                    if rec["status"] not in wanted:
                        continue
                    job = self._job(link)
                    if job is None or job.link in seen:
                        continue
                    seen.add(job.link)
                    out.append((job, rec))

            if search:
                q = search.lower()
                out = [
                    t for t in out
                    if q in (t[0].title or "").lower() or q in (t[0].company or "").lower()
                ]
            return out

    def hidden_count(self) -> int:
        with self._lock:
            self.ensure_loaded()
            return len(self.ws_collect("Ukryte"))

    def list_item(self, job: Job, rec: Dict[str, Any], fresh_since: Optional[str]) -> Dict[str, Any]:
        status = rec["status"]
        return {
            "link": job.link,
            "title": job.title or "Bez tytułu",
            "company": job.company or "—",
            "location": job.location or "",
            "work_mode": work_mode_text(job),
            "source": job.source or "—",
            "logo_url": job.logo_url,
            "is_gone": job.link in self.zdjete,
            "is_new": bool(fresh_since) and (job.scraped_at or "") >= fresh_since,
            "match_percentage": self._percent(job),
            "salary_text": salary_text(getattr(job, "salary", None)),
            "status": "save" if status in SAVED_STATUSES else status if status in DECISION_STATUSES else None,
            "decided_at": rec["decided_at"],
            "note": rec["note"],
            "cv": cv_store.latest_for(job.link),
        }

    def get_offer_detail(self, link: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.ensure_loaded()
            job = self._job(link)
            if not job:
                return None
            rec = self.decision(job.link)
            if rec["status"] is None and job.link != link:
                rec = self.decision(link)
            status = rec["status"]
            pct = self._percent(job)
            # Powód odrzucenia przez przesiew (matching/prefilter.py) - oferta oceniona,
            # tylko bez procentu; karta nie może wtedy mówić, że czeka na ocenę.
            result = self.match_results.get(canonical_link(job.link)) or self.match_results.get(job.link)
            filtered = result.get("filtered") if isinstance(result, dict) and pct is None else None
            description = "" if is_placeholder_description(job.description) else job.description or ""
            paragraphs = format_description(description, drop_prefix=job.title or "")

            return {
                "link": job.link,
                "title": job.title or "Bez tytułu",
                "company": job.company or "—",
                "location": job.location or "",
                "source": job.source or "—",
                "logo_url": job.logo_url,
                "is_gone": job.link in self.zdjete,
                "work_mode": work_mode_text(job),
                "match_percentage": pct,
                "filtered": filtered,
                "fields": {
                    "seniority": getattr(job, "seniority", None),
                    "work_modes": getattr(job, "work_modes", None),
                    "contract_types": getattr(job, "contract_types", None),
                    "schedules": getattr(job, "schedules", None),
                    "salary": getattr(job, "salary", None),
                    "languages": getattr(job, "languages", None),
                    "years_required": getattr(job, "years_required", None),
                    "skills_required": getattr(job, "skills_required", None),
                    "skills_nice": getattr(job, "skills_nice", None),
                    "category": getattr(job, "category", None),
                },
                "description_blocks": format_description_blocks(paragraphs),
                "raw_description": description,
                "scraped_at": job.scraped_at or None,
                "salary_text": salary_text(getattr(job, "salary", None)),
                "status": "save" if status in SAVED_STATUSES else status if status in DECISION_STATUSES else None,
                "stage": app_stage(rec["stage"]) if status == "apply" else None,
                "decided_at": rec["decided_at"],
                "applied_at": rec["applied_at"],
                "note": rec["note"],
                "next_step": rec["next_step"],
                "cv_versions": cv_store.list_versions(job.link),
            }

    def run_summary(self, since: Optional[str], limit: int = 3) -> Dict[str, Any]:
        """Oferty z procentem ocenione od `since` (ISO, czas lokalny) - wyniki bieżącego wyszukiwania."""
        with self._lock:
            self.ensure_loaded()
            if not since:
                return {"matched_count": 0, "recent": []}
            found: List[Tuple[int, Job]] = []
            seen: Set[str] = set()
            for link, res in self.match_results.items():
                if not isinstance(res, dict) or res.get("percent") is None:
                    continue
                if (res.get("scored_at") or "") < since:
                    continue
                job = self._job(link)
                if job is None or job.link in seen:
                    continue
                seen.add(job.link)
                found.append((int(res["percent"]), job))
            found.sort(key=lambda t: t[0], reverse=True)
            return {
                "matched_count": len(found),
                "recent": [
                    {
                        "link": job.link,
                        "title": job.title or "Bez tytułu",
                        "company": job.company or "—",
                        "location": job.location or "",
                        "work_mode": work_mode_text(job),
                        "match_percentage": pct,
                    }
                    for pct, job in found[:limit]
                ],
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
    pdf_current = pdf_is_current()
    pdf_pages = None
    if pdf_current:
        try:
            from pypdf import PdfReader
            pdf_pages = len(PdfReader(str(cv_pdf_path)).pages)
        except Exception as e:
            logger.warning(f"Nie udało się policzyć stron {cv_pdf_path}: {e}")
    filename = "cv.pdf" if pdf_current else ("final_cv_text.txt" if cv_txt_path.exists() else "Brak")

    return {
        "ready": has_cv,
        "text": text,
        "chars": len(text),
        "filename": filename,
        "pdf_url": "/api/cv/file" if pdf_current else None,
        "pdf_pages": pdf_pages,
        "profile": _profile_summary(text),
    }


def pdf_is_current() -> bool:
    """cv.pdf to bieżące CV, dopóki tekst nie został później wklejony ręcznie."""
    if not CV_PDF_PATH.exists():
        return False
    if not CV_TXT_PATH.exists():
        return True
    return CV_PDF_PATH.stat().st_mtime >= CV_TXT_PATH.stat().st_mtime - 60


def _profile_summary(cv: str) -> Optional[Dict[str, Any]]:
    """Profil kandydata z CV (utils/cv_profile.py): wyznacza zakres scrapowania i przesiew.
    `current` = policzony z bieżącego CV; inaczej pipeline przeliczy go na starcie.
    Zakres to zawsze miasto z CV plus praca zdalna (utils/candidate_scope.py)."""
    profile = load_profile()
    if not profile:
        return None
    sha = (profile.get("_metadata") or {}).get("cv_sha256")
    return {
        "city": profile.get("city"),
        "remote": True,
        "seniority": profile.get("seniority"),
        "years": profile.get("years_experience"),
        "roles": [str(r) for r in profile.get("roles") or []],
        "skills": [str(s) for s in profile.get("skills") or []],
        "languages": [
            {"name": str(lang.get("name") or ""), "level": lang.get("level")}
            for lang in profile.get("languages") or [] if isinstance(lang, dict)
        ],
        "current": bool(cv) and sha == hashlib.sha256(cv.encode("utf-8")).hexdigest(),
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


ENV_PATH = Path(__file__).parent / ".env"


def _read_env_file() -> Dict[str, str]:
    """Niepuste wpisy z .env. Plik jest źródłem prawdy dla UI: edycja ręczna
    też ma być widoczna bez restartu serwera."""
    values: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v and v not in PLACEHOLDERS:
                    values[k] = v
    except OSError as e:
        logger.warning(f"Błąd odczytu .env: {e}")
    return values


def _write_env(updates: Dict[str, str]) -> None:
    """Podmienia albo dopisuje wpisy w .env (pusta wartość = wpis wyczyszczony)
    i od razu w środowisku procesu - pipeline startuje z jego kopią."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    written, out = set(), []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                written.add(k)
                continue
        out.append(line)
    out.extend(f"{k}={v}" for k, v in updates.items() if k not in written)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = v


def _llm_settings():
    return settings_from_env({**os.environ, **_read_env_file()})


def get_api_keys_info() -> Dict[str, Any]:
    """Stan dostawcy modelu dla UI - bez wartości kluczy."""
    s = _llm_settings()
    primary = s.api_keys[0] if s.api_keys else ""
    return {
        "ready": not s.error,
        "error": s.error,
        "count": len(s.api_keys),
        "primary_masked": f"{primary[:4]}…{primary[-4:]}" if len(primary) > 8 else ("ustawiony" if primary else "brak"),
        "provider": s.provider.id,
        "models": s.models,
        "models_custom": s.models_overridden,
        "base_url": s.base_url,
        "providers": [
            {
                "id": p.id,
                "label": p.label,
                "key_env": p.key_envs[0],
                "key_hint": p.key_hint,
                "models_env": p.models_env,
                "default_models": list(p.default_models),
                "base_url_env": p.base_url_env,
                "default_base_url": p.default_base_url,
            }
            for p in PROVIDERS.values()
        ],
    }


def save_llm_settings(provider: str, key: str = "", models: Optional[str] = None,
                      base_url: Optional[str] = None) -> Tuple[bool, str]:
    """Zapisuje wybór dostawcy. Pusty klucz zostawia dotychczasowy; `models` i
    `base_url` podane jako pusty tekst wracają do wartości domyślnych dostawcy."""
    p = PROVIDERS.get(provider)
    if p is None:
        return False, f"Nieznany dostawca: {provider}"
    updates = {"LLM_PROVIDER": p.id}
    if key.strip():
        updates[p.key_envs[0]] = key.strip()
    if models is not None:
        updates[p.models_env] = ", ".join(m.strip() for m in models.split(",") if m.strip())
    if base_url is not None and p.base_url_env:
        updates[p.base_url_env] = base_url.strip()
    try:
        _write_env(updates)
    except OSError as e:
        return False, f"Błąd zapisu .env: {e}"
    return True, f"Zapisano ustawienia modelu ({p.label}) w .env."


_PORTAL_FIELDS = [
    ("TYPESAFE_API_KEY", "TypeSafe API Key (Jev)", True),
    ("ADZUNA_APP_ID", "Adzuna App ID", False),
    ("ADZUNA_APP_KEY", "Adzuna App Key", True),
    ("JOOBLE_API_KEY", "Jooble API Key", True),
    ("CAREERJET_API_KEY", "Careerjet API Key", True),
]


def _env_fields() -> List[Tuple[str, str, bool]]:
    """Zapasowe klucze wybranego dostawcy i klucze portali. Klucz główny ma własne
    pole w ustawieniach modelu (save_llm_settings)."""
    p = _llm_settings().provider
    backups = [(name, f"{p.label} - zapas {i}", True) for i, name in enumerate(p.key_envs[1:], 1)]
    return backups + _PORTAL_FIELDS


def get_env_fields_status() -> List[Dict[str, Any]]:
    """Zwraca metadane pól .env bez wysyłania wartości sekretów do przeglądarki."""
    configured = _read_env_file()
    return [
        {"key": key, "label": label, "secret": secret, "configured": key in configured}
        for key, label, secret in _env_fields()
    ]


def save_env_keys(updates: Dict[str, str]) -> Tuple[bool, str]:
    """Zapis kluczy z formularza. Puste pola zostawiają dotychczasową wartość.
    Przyjmuje tylko znane klucze - formularz nie może dopisać dowolnej zmiennej."""
    allowed = {k for p in PROVIDERS.values() for k in p.key_envs} | {k for k, _, _ in _PORTAL_FIELDS}
    unknown = sorted(k for k in updates if k not in allowed)
    if unknown:
        return False, f"Nieobsługiwane pola: {', '.join(unknown)}"
    clean_updates = {k: v.strip() for k, v in updates.items() if v is not None and v.strip()}
    try:
        _write_env(clean_updates)
    except OSError as e:
        return False, f"Błąd zapisu .env: {e}"
    return True, "Zapisano klucze w .env."


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
    # Jev (TypeSafe) ocenia oferty i wybiera kategorie portali - bez klucza etap 3 pada.
    jev_ready = bool((os.environ.get("TYPESAFE_API_KEY") or _read_env_file().get("TYPESAFE_API_KEY") or "").strip())
    # Model z LLM_PROVIDER czyta tylko CV; przy aktualnym profilu przebieg go nie woła.
    profile = cv_info.get("profile")
    llm_needed = not (profile and profile.get("current"))

    issues = []
    if not cv_info["ready"]:
        issues.append("Brak CV. Dodaj plik albo wklej treść w wierszu „CV”.")
    if not jev_ready:
        issues.append("Brak klucza TYPESAFE_API_KEY, bez którego Jev nie oceni ofert.")
    if llm_needed and not api_info["ready"]:
        issues.append(api_info["error"])

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
        "jev_ready": jev_ready,
        "llm_needed": llm_needed,
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
        target = canonical_link(job.link)
        jobs = [j for j in db.load_jobs() if j.link != job.link and canonical_link(j.link) != target]
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
