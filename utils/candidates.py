from __future__ import annotations

import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic
from utils.sqlite_store import RecordStore

ROOT = Path(__file__).resolve().parent.parent / "candidates"
INDEX_NAME = "candidates.json"
FIRST_ID = "k1"

CV_PDF = "cv.pdf"
CV_DOCX = "cv.docx"
CV_TEXT = "final_cv_text.txt"
PROFILE = "candidate_profile.json"
PROFILE_HISTORY = "profile_history.json"
MATCH_RESULTS = "match_results.db"
LEGACY_MATCH_RESULTS = "match_results.json"
DECISIONS = "user_decisions.json"
CATEGORY_SCOPE = "category_scope.json"
LAST_SCRAPE_RUN = "last_scrape_run.json"
BASE_CV = "base_cv.json"
CV_VERSIONS = "cv_versions.json"
CV_VERSIONS_DIR = "cv_versions"
TAILOR_INSTRUCTIONS = "cv_tailor_instructions.md"

LEGACY_NAMES = (CV_PDF, CV_DOCX, CV_TEXT, PROFILE, LEGACY_MATCH_RESULTS, DECISIONS, CATEGORY_SCOPE,
                LAST_SCRAPE_RUN, BASE_CV, CV_VERSIONS, CV_VERSIONS_DIR, TAILOR_INSTRUCTIONS)
NAME_MAX = 60

_lock = threading.RLock()


class CandidateError(ValueError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _index_path() -> Path:
    return ROOT / INDEX_NAME


def _valid(index) -> bool:
    return (isinstance(index, dict) and isinstance(index.get("items"), list) and index["items"]
            and all(isinstance(c, dict) and c.get("id") for c in index["items"]))


def _migrate(target: Path) -> None:
    source = ROOT.parent
    for item in LEGACY_NAMES:
        src = source / item
        if src.exists() and not (target / item).exists():
            shutil.move(str(src), str(target / item))


def _index() -> dict:
    with _lock:
        index = load_json_safe(_index_path(), default=None)
        if _valid(index):
            if not any(c["id"] == index.get("active") for c in index["items"]):
                index["active"] = index["items"][0]["id"]
            return index
        target = ROOT / FIRST_ID
        target.mkdir(parents=True, exist_ok=True)
        _migrate(target)
        index = {"active": FIRST_ID, "items": [{"id": FIRST_ID, "name": "Kandydat 1", "created_at": _now()}]}
        save_json_atomic(_index_path(), index, backup=False)
        return index


def _save(index: dict) -> None:
    save_json_atomic(_index_path(), index, backup=False)


def active_id() -> str:
    return _index()["active"]


def active_dir() -> Path:
    directory = ROOT / active_id()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def path(name: str) -> Path:
    return active_dir() / name


def match_results(directory: Path | None = None) -> RecordStore:
    directory = directory or active_dir()
    return RecordStore(directory / MATCH_RESULTS, directory / LEGACY_MATCH_RESULTS)


def dirs() -> list[Path]:
    return [ROOT / c["id"] for c in _index()["items"]]


def decided_links() -> set[str]:
    links: set[str] = set()
    for directory in dirs():
        decisions = load_json_safe(directory / DECISIONS, default={})
        if isinstance(decisions, dict):
            links |= {canonical_link(k) for k in decisions}
    return links


def _clean_name(name) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return name[:NAME_MAX].rstrip()


def _default_name(index: dict) -> str:
    taken = {c.get("name") for c in index["items"]}
    n = len(index["items"]) + 1
    while f"Kandydat {n}" in taken:
        n += 1
    return f"Kandydat {n}"


def _find(index: dict, candidate_id: str) -> dict:
    for c in index["items"]:
        if c["id"] == candidate_id:
            return c
    raise CandidateError("Nie ma takiego kandydata.")


def _summary(c: dict) -> dict:
    directory = ROOT / c["id"]
    cv_path = directory / CV_TEXT
    has_cv = False
    if cv_path.exists():
        has_cv = len(cv_path.read_text(encoding="utf-8", errors="replace").strip()) > 20
    profile = load_json_safe(directory / PROFILE, default=None)
    profile = profile if isinstance(profile, dict) else {}
    return {
        "id": c["id"],
        "name": c.get("name") or c["id"],
        "created_at": c.get("created_at"),
        "cv_updated_at": (datetime.fromtimestamp(cv_path.stat().st_mtime).isoformat(timespec="seconds")
                          if has_cv else None),
        "has_cv": has_cv,
        "seniority": profile.get("seniority") if has_cv else None,
        "city": profile.get("city") if has_cv else None,
    }


def listing() -> dict:
    index = _index()
    return {"active": index["active"], "items": [_summary(c) for c in index["items"]]}


def create(name=None) -> dict:
    with _lock:
        index = _index()
        numbers = [int(c["id"][1:]) for c in index["items"] if re.fullmatch(r"k\d+", c["id"])]
        candidate_id = f"k{max(numbers, default=0) + 1}"
        while (ROOT / candidate_id).exists():
            candidate_id = f"k{int(candidate_id[1:]) + 1}"
        (ROOT / candidate_id).mkdir(parents=True)
        clean = _clean_name(name)
        index["items"].append({"id": candidate_id, "name": clean or _default_name(index),
                               "auto_name": not clean, "created_at": _now()})
        index["active"] = candidate_id
        _save(index)
    return listing()


def activate(candidate_id: str) -> dict:
    with _lock:
        index = _index()
        _find(index, candidate_id)
        index["active"] = candidate_id
        _save(index)
    return listing()


def rename(candidate_id: str, name) -> dict:
    name = _clean_name(name)
    if not name:
        raise CandidateError("Podaj nazwę kandydata.")
    with _lock:
        index = _index()
        candidate = _find(index, candidate_id)
        candidate["name"] = name
        candidate["auto_name"] = False
        _save(index)
    return listing()


def _auto_named(candidate: dict) -> bool:
    return candidate.get("auto_name", bool(re.fullmatch(r"Kandydat \d+", candidate.get("name") or "")))


def adopt_name(name) -> None:
    name = _clean_name(name)
    if not name:
        return
    with _lock:
        index = _index()
        candidate = _find(index, index["active"])
        if not _auto_named(candidate) or candidate.get("name") == name:
            return
        candidate["name"] = name
        candidate["auto_name"] = True
        _save(index)


def delete(candidate_id: str) -> dict:
    with _lock:
        index = _index()
        _find(index, candidate_id)
        if candidate_id == index["active"]:
            raise CandidateError("Przełącz się na innego kandydata, zanim usuniesz tego.")
        index["items"] = [c for c in index["items"] if c["id"] != candidate_id]
        _save(index)
        shutil.rmtree(ROOT / candidate_id, ignore_errors=True)
    return listing()
