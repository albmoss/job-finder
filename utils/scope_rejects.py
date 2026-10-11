from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from utils.safe_io import load_json_safe, save_json_atomic

PATH = Path(__file__).resolve().parent.parent / "scope_rejects.json"
KEEP_DAYS = 14

_lock = threading.Lock()


def _cutoff() -> str:
    return (date.today() - timedelta(days=KEEP_DAYS)).isoformat()


def load(scope: str) -> set[str]:
    with _lock:
        data = load_json_safe(PATH, default={}) or {}
    cutoff = _cutoff()
    return {link for link, day in (data.get(scope) or {}).items() if day >= cutoff}


def remember(scope: str, links: Iterable[str]) -> None:
    links = list(links)
    with _lock:
        data = load_json_safe(PATH, default={}) or {}
        cutoff, today = _cutoff(), date.today().isoformat()
        entries = {link: day for link, day in (data.get(scope) or {}).items() if day >= cutoff}
        if not links and len(entries) == len(data.get(scope) or {}):
            return
        for link in links:
            entries.setdefault(link, today)
        data[scope] = entries
        save_json_atomic(PATH, data, backup=False)
