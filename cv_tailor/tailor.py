from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from pathlib import Path

from cv_tailor import jpath, store
from cv_tailor.model import Cancelled, TailorError, ask, ensure_base_cv, normalize_cv
from cv_tailor.schema import TailorReply
from utils import candidates

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS_PATH = Path(__file__).resolve().parent / "default_instructions.md"
INSTRUCTIONS_FILE_NAME = "cv_tailor.md"

SECTIONS = ("summary", "experience", "projects", "skills", "education", "languages", "certificates", "other")
SECTION_LABELS = {
    "name": "Imię i nazwisko", "headline": "Nagłówek", "contact": "Kontakt", "summary": "Profil zawodowy",
    "experience": "Doświadczenie", "projects": "Projekty", "skills": "Umiejętności",
    "education": "Wykształcenie", "languages": "Języki", "certificates": "Certyfikaty", "other": "Inne",
}
CANCELLED_ERROR = "Przerwano tworzenie CV."

_cancel_flags: dict[str, threading.Event] = {}
_flags_lock = threading.Lock()


def user_instructions_path() -> Path:
    return candidates.path(candidates.TAILOR_INSTRUCTIONS)


def default_instructions() -> str:
    return DEFAULT_INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def instructions() -> dict:
    try:
        text = user_instructions_path().read_text(encoding="utf-8")
        is_default = False
    except FileNotFoundError:
        text, is_default = default_instructions(), True
    return {"text": text, "is_default": is_default, "file_name": INSTRUCTIONS_FILE_NAME}


def save_instructions(text: str) -> dict:
    if text.strip() == default_instructions().strip():
        return reset_instructions()
    user_instructions_path().write_text(text, encoding="utf-8")
    return instructions()


def reset_instructions() -> dict:
    user_instructions_path().unlink(missing_ok=True)
    return instructions()


def load_offer(link: str) -> dict | None:
    from app_services import job_data_service
    from utils.links import canonical_link

    job_data_service.ensure_loaded()
    job = job_data_service.job_lookup.get(link) or job_data_service.job_lookup.get(canonical_link(link))
    if job is None:
        return None
    return {"link": job.link, "title": job.title or "", "company": job.company or "",
            "location": job.location or "", "description": job.description or ""}


def locked_paths(base: dict, lock_contact: bool) -> list[str]:
    if not lock_contact:
        return []
    paths = ["name", "contact"]
    for section in ("experience", "education"):
        for i in range(len(base[section])):
            paths += [f"{section}[{i}].start", f"{section}[{i}].end"]
    return paths


def _prompt(v: dict, offer: dict, base: dict) -> str:
    settings = {
        "language": v["language"],
        "locked": locked_paths(base, v["lock_contact"]),
        "reorderable_sections": not v["keep_order"],
    }
    return (
        f"{instructions()['text'].strip()}\n\n"
        f"<job_offer>\nTitle: {offer['title']}\nCompany: {offer['company']}\n"
        f"Location: {offer['location']}\n\n{offer['description'].strip()}\n</job_offer>\n\n"
        f"<base_cv>\n{json.dumps(base, ensure_ascii=False, indent=1)}\n</base_cv>\n\n"
        f"<candidate_facts>\n{(v.get('facts') or '').strip()}\n</candidate_facts>\n\n"
        f"<settings>\n{json.dumps(settings, ensure_ascii=False)}\n</settings>"
    )


def _match_entry(item: dict, base_items: list[dict], index: int, same_length: bool) -> dict | None:
    for key in (("company", "start", "end"), ("company", "title"), ("school", "start", "end"),
                ("school", "degree"), ("company",), ("school",)):
        if not all(item.get(k) for k in key if k in item) or not all(k in item for k in key):
            continue
        hits = [b for b in base_items if all(b.get(k) == item.get(k) for k in key)]
        if len(hits) == 1:
            return hits[0]
    if same_length and index < len(base_items):
        return base_items[index]
    return None


def _force_locked(tailored: dict, base: dict) -> None:
    tailored["name"] = base["name"]
    tailored["contact"] = copy.deepcopy(base["contact"])
    for section in ("experience", "education"):
        same_length = len(tailored[section]) == len(base[section])
        for i, item in enumerate(tailored[section]):
            match = _match_entry(item, base[section], i, same_length)
            if match is not None:
                item["start"], item["end"] = match["start"], match["end"]


def _is_locked(path: str, lock_contact: bool) -> bool:
    if not lock_contact:
        return False
    parts = jpath.parse(path) or []
    if parts and parts[0] in ("name", "contact"):
        return True
    return len(parts) == 3 and parts[0] in ("experience", "education") and parts[2] in ("start", "end")


def section_label(path: str, cv: dict) -> str:
    parts = jpath.parse(path) or []
    key = parts[0] if parts else ""
    label = SECTION_LABELS.get(key, key)
    if len(parts) > 1 and isinstance(parts[1], int):
        entry = jpath.get(cv, parts[:2])
        if isinstance(entry, dict):
            name = entry.get("company") or entry.get("name") or entry.get("school") or entry.get("heading") \
                or entry.get("category")
            if name:
                label = f"{label} · {name}"
    return label


def build_result(v: dict, base: dict, reply: dict) -> dict:
    if not isinstance(reply.get("cv"), dict):
        raise TailorError("Model nie zwrócił CV. Spróbuj ponownie.")
    tailored = normalize_cv(reply["cv"])
    if v["lock_contact"]:
        _force_locked(tailored, base)

    changes: list[dict] = []
    seen: set[str] = set()
    for raw in reply.get("changes") or []:
        if not isinstance(raw, dict):
            continue
        parts = jpath.parse(str(raw.get("path") or ""))
        if not parts or parts[0] not in SECTION_LABELS:
            continue
        path = jpath.fmt(parts)
        before = str(raw.get("before") or "").strip()
        after = str(raw.get("after") or "").strip()
        if path in seen or _is_locked(path, v["lock_contact"]) or (not before and not after):
            continue
        kind = "added" if not before else "removed" if not after else "modified"
        if kind == "removed":
            parent = jpath.get(tailored, parts[:-1])
            if not jpath.is_item(parts) or not isinstance(parent, list):
                continue
        else:
            current = jpath.get(tailored, parts)
            if current is None:
                continue
            if isinstance(current, str):
                after = current
            if kind == "added" and not jpath.is_item(parts):
                kind = "modified"
                before = jpath.get(base, parts) if isinstance(jpath.get(base, parts), str) else ""
        seen.add(path)
        changes.append({"path": path, "kind": kind, "before": before, "after": after, "model_after": after,
                        "reason": str(raw.get("reason") or "").strip(), "state": "applied",
                        "section": section_label(path, tailored if kind != "removed" else base)})
    for key in ("headline", "summary"):
        if key not in seen and tailored[key] != base[key] and not _is_locked(key, v["lock_contact"]):
            changes.append({"path": key, "kind": "modified", "before": base[key], "after": tailored[key],
                            "model_after": tailored[key], "reason": "", "state": "applied",
                            "section": SECTION_LABELS[key]})
    for i, c in enumerate(changes):
        c["idx"] = i

    numbers = []
    for raw in reply.get("numbers_to_verify") or []:
        if not isinstance(raw, dict):
            continue
        parts = jpath.parse(str(raw.get("path") or ""))
        if not parts or not isinstance(jpath.get(tailored, parts), str):
            continue
        numbers.append({"idx": len(numbers), "path": jpath.fmt(parts), "number": str(raw.get("number") or "").strip(),
                        "question": str(raw.get("question") or "").strip(), "text": "", "state": "pending"})

    missing: list[str] = []
    for r in reply.get("requirements") or []:
        term = str((r or {}).get("term") or "").strip() if isinstance(r, dict) else ""
        if term and r.get("status") == "missing" and term not in missing:
            missing.append(term)

    match = reply.get("match") if isinstance(reply.get("match"), dict) else None
    if match is not None:
        must_met, must_total = int(match.get("must_met") or 0), int(match.get("must_total") or 0)
        ratio = must_met / must_total if must_total else float(match.get("ratio") or 0)
        match = {"must_met": must_met, "must_total": must_total, "ratio": round(max(0.0, min(1.0, ratio)), 2),
                 "recommendation": str(match.get("recommendation") or "").strip()}

    questions = [{"about": str(q.get("about") or ""), "question": str(q.get("question") or ""),
                  "why": str(q.get("why") or "")}
                 for q in reply.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    order = [] if v["keep_order"] else [s for s in dict.fromkeys(reply.get("section_order") or []) if s in SECTIONS]
    return {"tailored": tailored, "changes": changes, "numbers": numbers, "missing": missing,
            "match": match, "questions": questions, "section_order": order}


def _flag(run_id: str) -> threading.Event:
    with _flags_lock:
        return _cancel_flags.setdefault(run_id, threading.Event())


def _write(version_id: str, run_id: str, **fields) -> bool:
    def fn(v: dict):
        if v.get("run_id") != run_id or v.get("status") != "running":
            return False
        v.update(fields)
    v = store.update(version_id, fn)
    return bool(v) and v.get("run_id") == run_id and all(v.get(k) == fields[k] for k in ("status", "phase") if k in fields)


def _run(version_id: str, run_id: str) -> None:
    flag = _flag(run_id)
    cancelled = flag.is_set
    try:
        v = store.get(version_id)
        if v is None:
            return
        base = ensure_base_cv(cancelled)
        if cancelled() or not _write(version_id, run_id, phase="tailoring", base=base):
            return
        offer = load_offer(v["link"])
        if offer is None or not offer["description"].strip():
            raise TailorError("Oferta zniknęła z bazy albo nie ma opisu.")
        reply = ask(_prompt(v, offer, base), TailorReply, cancelled)
        if cancelled():
            return
        result = build_result(v, base, reply)
        _write(version_id, run_id, status="draft", phase=None, error=None, **result)
    except Cancelled:
        return
    except TailorError as e:
        _write(version_id, run_id, status="failed", phase=None, error=str(e))
    except Exception as e:
        logger.exception("CV tailoring failed")
        _write(version_id, run_id, status="failed", phase=None,
               error=f"Nieoczekiwany błąd podczas tworzenia CV: {type(e).__name__}: {str(e)[:200]}")
    finally:
        with _flags_lock:
            _cancel_flags.pop(run_id, None)


def start(version_id: str) -> None:
    v = store.get(version_id)
    if v is None:
        return
    _flag(v["run_id"])
    threading.Thread(target=_run, args=(version_id, v["run_id"]), daemon=True,
                     name=f"cv-tailor-{version_id}").start()


def cancel(version_id: str) -> dict | None:
    def fn(v: dict):
        if v.get("status") != "running":
            return False
        _flag(v["run_id"]).set()
        v.update(status="failed", phase=None, error=CANCELLED_ERROR)
    return store.update(version_id, fn)


def retry(version_id: str) -> dict | None:
    def fn(v: dict):
        if v.get("status") != "failed":
            return False
        v.update(status="running", phase="base_cv", error=None, run_id=uuid.uuid4().hex,
                 tailored=None, changes=[], numbers=[], questions=[], missing=[], match=None, section_order=[])
    v = store.update(version_id, fn)
    if v and v["status"] == "running":
        start(version_id)
    return v
