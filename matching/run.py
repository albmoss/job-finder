"""
Etap dopasowania: CV -> profil, przesiew w kodzie, ocena Jev, procent.

Wynik trafia do `match_results.json`: słownik kanoniczny link -> wpis
    {"percent": int | None, "filtered": powód | None, "answers": {...} | None,
     "offer_fp": str, "profile_fp": str, "model": str | None, "scored_at": ISO}
`percent` jest None dla ofert odrzuconych przez przesiew (`filtered`).

Oferta jest oceniana raz dla pary „treść oferty + profil z CV”. Kolejny
przebieg pomija wpisy z tym samym `offer_fp` i `profile_fp`, więc ocenia tylko
nowe albo zmienione oferty. Przed pełną oceną idzie wstępna (matching/triage.py):
oferty bez szansy dostają `filtered = "kierunek"` i nie kosztują pełnego zapytania.

Uruchomienie ręczne: python -m matching.run [--limit N] [--rescore-all]
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from matching import jev, triage
from matching.prefilter import reject_reason
from utils.cv_profile import cv_text, ensure_profile, profile_fingerprint
from utils.data_models import Job, JobDatabase
from utils.links import canonical_link
from utils.offer_fields import STRUCTURED_FIELDS, text_features
from utils.safe_io import load_json_safe, save_json_atomic

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_PATH = BASE_DIR / "match_results.json"
JOBS_PATH = BASE_DIR / "jobs_database.json"
STOP_FLAG_FILE = BASE_DIR / "pipeline_stop_requested.flag"

WORKERS = 8
SAVE_EVERY = 200
# Linia postępu co tyle sekund (i na końcu): arkusz pipeline'u liczy z niej postęp i ETA.
PROGRESS_EVERY_S = 2.0
MIN_DESCRIPTION = 150


def offer_fingerprint(job: Job) -> str:
    body = {"t": job.title, "c": job.company, "d": job.description or ""}
    body.update({name: getattr(job, name) for name in STRUCTURED_FIELDS})
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:16]


def load_results() -> dict:
    data = load_json_safe(str(RESULTS_PATH), default={})
    return data if isinstance(data, dict) else {}


def _has_content(job: Job) -> bool:
    """Jest co oceniać: opis z ogłoszenia albo lista umiejętności przy krótkim opisie.

    Oferta z zaślepką zamiast opisu, ale z listą umiejętności z portalu, idzie do oceny: tytuł
    i umiejętności wystarczają Jev na sensowny wynik, a scraper dociągnie opis w kolejnym
    przebiegu (utils/known_links.py) - wtedy zmienia się odcisk oferty i ocena zapada ponownie.
    """
    return len((job.description or "").strip()) >= MIN_DESCRIPTION or bool(job.skills_required)


def _triage(todo, results, profile, cv, profile_fp, key, session, now):
    known, ask = {}, []
    for link, job, _ in todo:
        cached = (results.get(link) or {}).get("triage") or {}
        if cached.get("fp") == triage.triage_fp(job) and cached.get("profile_fp") == profile_fp:
            known[link] = cached["p"]
        else:
            ask.append((link, job))
    errors, stopped = 0, False
    if ask:
        fresh, tokens, errors, stopped = triage.triage(
            ask, jev.candidate_state(profile, cv), key, session, STOP_FLAG_FILE.exists)
        print(f"Triage: {len(fresh)}/{len(ask)} offers checked, {tokens} input tokens, "
              f"{len(known)} cached")
        known.update(fresh)

    passed, rejected = [], 0
    for link, job, fp in todo:
        if link not in known:
            continue
        mark = {"p": round(known[link], 4), "fp": triage.triage_fp(job), "profile_fp": profile_fp}
        if known[link] >= triage.THRESHOLD:
            entry = results.setdefault(link, {"percent": None, "filtered": None, "answers": None,
                                              "offer_fp": None, "profile_fp": None, "model": None,
                                              "scored_at": None})
            entry["triage"] = mark
            passed.append((link, job, fp))
        else:
            rejected += 1
            results[link] = {"percent": None, "filtered": "kierunek", "answers": None,
                             "offer_fp": fp, "profile_fp": profile_fp, "model": None,
                             "scored_at": now, "triage": mark}
    return passed, rejected, errors, stopped


def main(argv: list[str] | None = None) -> int:
    from utils.console import force_utf8
    import config  # noqa: F401 - wczytuje .env (TYPESAFE_API_KEY, klucze modelu profilu)
    force_utf8()
    args = sys.argv[1:] if argv is None else argv
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    if "--rescore-all" in args:
        print("Rescore all: ignoring cached results.")

    key = jev.api_key()
    if not key:
        print("Brak TYPESAFE_API_KEY w .env - ocena dopasowania niemożliwa.")
        return 1
    profile = ensure_profile()
    if profile is None:
        print("Brak CV - wgraj CV w aplikacji, zanim uruchomisz dopasowanie.")
        return 1
    cv = cv_text()
    profile_fp = profile_fingerprint(profile)
    print(f"Profile: {profile.get('seniority')} | {profile.get('city')} | "
          f"{len(profile.get('skills') or [])} skills | fp {profile_fp}")

    jobs = JobDatabase(str(JOBS_PATH)).load_jobs()
    # --rescore-all: pełne przeliczenie, np. po zmianie pytań albo wag w matching/jev.py.
    results = {} if "--rescore-all" in args else load_results()
    live_links = set()
    todo: list[tuple[str, Job, str]] = []
    filtered: dict[str, int] = {}
    now = datetime.now().isoformat(timespec="seconds")

    for job in jobs:
        link = canonical_link(job.link)
        live_links.add(link)
        # Oferty zapisane przed dodaniem pól strukturalnych nie mają cech z treści
        # (Job.from_dict pomija __post_init__). Liczymy je tu, w pamięci.
        if job.years_required is None and job.languages is None:
            for name, value in text_features(job.description).items():
                setattr(job, name, value)
        fp = offer_fingerprint(job)
        # Przesiew idzie po każdej ofercie, także ocenionej: jest darmowy, a nowa
        # reguła ma zdjąć z listy także oferty ocenione przed jej dodaniem.
        reason = reject_reason(job, profile) if _has_content(job) else "brak_opisu"
        if reason:
            filtered[reason] = filtered.get(reason, 0) + 1
            results[link] = {"percent": None, "filtered": reason, "answers": None,
                             "offer_fp": fp, "profile_fp": profile_fp, "model": None,
                             "scored_at": now}
            continue
        entry = results.get(link)
        if (entry and entry.get("percent") is not None and entry.get("offer_fp") == fp
                and entry.get("profile_fp") == profile_fp):
            continue
        todo.append((link, job, fp))

    # Wyniki ofert, których nie ma już w bazie, znikają razem z nimi.
    for link in [l for l in results if l not in live_links]:
        del results[link]

    session = requests.Session()
    stopped = False
    errors = 0
    if limit != 0 and todo:
        todo, rejected, errors, stopped = _triage(todo, results, profile, cv, profile_fp, key, session, now)
        if rejected:
            filtered["kierunek"] = rejected

    if limit is not None:
        todo = todo[:limit]
    total = len(todo)
    print(f"Prefilter: {sum(filtered.values())} rejected {filtered} | to score: {total}")
    save_json_atomic(str(RESULTS_PATH), results, backup=True)

    done = 0
    started = last_report = time.monotonic()

    def score(item):
        link, job, fp = item
        reply = jev.ask(jev.build_state(job, profile, cv), key, session)
        return link, fp, reply

    with cf.ThreadPoolExecutor(WORKERS) as pool:
        pending = set()
        queue = iter(todo)
        while True:
            while not stopped and len(pending) < WORKERS * 2:
                item = next(queue, None)
                if item is None:
                    break
                pending.add(pool.submit(score, item))
            if not pending:
                break
            finished, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for fut in finished:
                try:
                    link, fp, reply = fut.result()
                except jev.JevError as e:
                    errors += 1
                    print(f"   Jev error: {e}")
                    if "klucz" in str(e):
                        stopped = True
                    continue
                results[link] = {
                    "percent": jev.percent(reply["answers"]), "filtered": None,
                    "answers": reply["answers"], "offer_fp": fp, "profile_fp": profile_fp,
                    "model": reply.get("model"), "scored_at": datetime.now().isoformat(timespec="seconds"),
                }
                done += 1
                if done % SAVE_EVERY == 0:
                    save_json_atomic(str(RESULTS_PATH), results, backup=False)
                now_s = time.monotonic()
                if now_s - last_report >= PROGRESS_EVERY_S or done == total:
                    last_report = now_s
                    rate = done / max(now_s - started, 1e-6)
                    print(f"   Scored {done}/{total} ({rate:.1f}/s)")
            if not stopped and STOP_FLAG_FILE.exists():
                print("   Stop requested - finishing in-flight offers.")
                stopped = True

    save_json_atomic(str(RESULTS_PATH), results, backup=False)
    scored = sum(1 for e in results.values() if e.get("percent") is not None)
    print(f"Matching done: {done} scored now, {errors} errors, {scored} offers with a percent.")
    # Zatrzymanie (flaga albo odrzucony klucz) to nie sukces: pipeline nie może
    # oznaczyć etapu jako ukończonego, bo reszta ofert czeka na ocenę.
    if stopped:
        return 1
    return 0 if errors < max(1, total // 10) else 1


if __name__ == "__main__":
    sys.exit(main())
