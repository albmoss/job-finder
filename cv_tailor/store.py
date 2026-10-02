from __future__ import annotations

import copy
import re
import threading
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic

ROOT = Path(__file__).resolve().parent.parent
VERSIONS_PATH = ROOT / "cv_versions.json"
PDF_DIR = ROOT / "cv_versions"

INTERRUPTED_ERROR = "Tworzenie CV przerwało ponowne uruchomienie serwera. Spróbuj ponownie."

_lock = threading.RLock()
_versions: list[dict] | None = None

SUMMARY_KEYS = ("id", "link", "company", "title", "version", "status", "phase", "language",
                "created_at", "updated_at", "file_name", "pending_numbers", "error")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _all() -> list[dict]:
    global _versions
    if _versions is None:
        data = load_json_safe(VERSIONS_PATH, default={"versions": []})
        items = data.get("versions") if isinstance(data, dict) else None
        _versions = [v for v in items or [] if isinstance(v, dict) and v.get("id")]
    return _versions


def _save() -> None:
    save_json_atomic(VERSIONS_PATH, {"versions": _all()}, backup=False)


def _find(version_id: str) -> dict | None:
    return next((v for v in _all() if v["id"] == version_id), None)


def to_summary(v: dict) -> dict:
    out = {k: v.get(k) for k in SUMMARY_KEYS}
    out["pending_numbers"] = sum(1 for n in v.get("numbers") or [] if n.get("state") == "pending")
    if out["status"] != "running":
        out["phase"] = None
    if out["status"] != "failed":
        out["error"] = None
    if out["status"] != "ready":
        out["file_name"] = None
    return out


def summary(version_id: str) -> dict | None:
    with _lock:
        v = _find(version_id)
        return to_summary(v) if v else None


def list_versions(link: str | None = None) -> list[dict]:
    with _lock:
        items = _all()
        if link is not None:
            target = canonical_link(link)
            items = [v for v in items if v.get("link") == target]
        return [to_summary(v) for v in reversed(items)]


def latest_for(link: str) -> dict | None:
    items = list_versions(link)
    return items[0] if items else None


def get(version_id: str) -> dict | None:
    with _lock:
        v = _find(version_id)
        return copy.deepcopy(v) if v else None


def update(version_id: str, fn: Callable[[dict], Any]) -> dict | None:
    with _lock:
        v = _find(version_id)
        if v is None:
            return None
        if fn(v) is False:
            return copy.deepcopy(v)
        v["updated_at"] = now()
        _save()
        return copy.deepcopy(v)


def create(link: str, company: str, title: str, settings: dict) -> dict:
    with _lock:
        link = canonical_link(link)
        number = 1 + max((v.get("version") or 0 for v in _all() if v.get("link") == link), default=0)
        stamp = now()
        v = {
            "id": uuid.uuid4().hex[:12],
            "link": link, "company": company, "title": title,
            "version": number, "status": "running", "phase": "base_cv",
            "language": settings["language"],
            "created_at": stamp, "updated_at": stamp,
            "file_name": None, "error": None,
            "facts": settings["facts"], "lock_contact": settings["lock_contact"],
            "keep_order": settings["keep_order"],
            "run_id": uuid.uuid4().hex,
            "base": None, "tailored": None, "changes": [], "numbers": [],
            "questions": [], "missing": [], "match": None, "section_order": [],
        }
        _all().append(v)
        _save()
        return copy.deepcopy(v)


_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(sp\.?\s*z\s*o\.?\s*o\.?|sp\.?\s*k\.?|sp\.?\s*j\.?|s\.?\s*a\.?|s\.?\s*c\.?|gmbh|ltd\.?|inc\.?|llc)\s*$",
    re.IGNORECASE)


def slug(text: str) -> str:
    text = _LEGAL_SUFFIX.sub("", (text or "").strip())
    text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:40].strip("_")
    return text or "oferta"


def pdf_path(version_id: str) -> Path:
    return PDF_DIR / f"{version_id}.pdf"


def _recover() -> None:
    with _lock:
        stuck = [v for v in _all() if v.get("status") == "running"]
        for v in stuck:
            v.update(status="failed", phase=None, error=INTERRUPTED_ERROR, updated_at=now())
        if stuck:
            _save()


_recover()
