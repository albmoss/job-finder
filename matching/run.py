"""
Etap dopasowania: CV -> profil, przesiew w kodzie, ocena Jev, procent.

Wynik trafia do `match_results.db` kandydata (utils/sqlite_store.py): kanoniczny link -> wpis
    {"percent": int | None, "filtered": powód | None, "answers": {...} | None,
     "offer_fp": str, "profile_fp": str, "model": str | None, "scored_at": ISO}
`percent` jest None dla ofert odrzuconych przez przesiew (`filtered`).

Oferta jest oceniana raz dla treści oferty: zmiana CV u tego samego kandydata
nie unieważnia ocen. Wpis trzyma `profile_fp` profilu, z którym powstał, a przesiew
takiej oferty liczy się z tym profilem (historia w utils/cv_profile.py). Nowe
i zmienione oferty ocenia bieżący profil. Przed pełną oceną idzie wstępna (matching/triage.py):
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

import requests

from matching import jev, triage
from matching.prefilter import reject_reason
from utils import candidates, stop, telemetry
from utils.cv_profile import cv_text, ensure_profile, profile_fingerprint, profile_history, remember_profile
from utils.data_models import Job, JobDatabase
from utils.links import canonical_link
from utils.offer_fields import text_features

WORKERS = 8
SAVE_EVERY = 50
# Linia postępu co tyle sekund (i na końcu): arkusz pipeline'u liczy z niej postęp i ETA.
PROGRESS_EVERY_S = 2.0


def offer_fingerprint(job: Job) -> str:
    """Odcisk oferty z pól zapisanych w bazie, w kształcie, w jakim widzi je Jev."""
    return hashlib.sha256(json.dumps(jev.offer_state(job), ensure_ascii=False, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:16]


def stored_percent(answers) -> int | None:
    """Procent z zapisanych odpowiedzi; None, gdy odpowiedzi są niepełne (np. po zmianie pytań)."""
    if not isinstance(answers, dict) or not set(jev.QUESTIONS) <= answers.keys():
        return None
    try:
        return jev.percent(answers)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _entry(offer_fp, profile_fp, scored_at, triage_mark=None, **fields) -> dict:
    """Wpis w match_results; znacznik wstępnej oceny przechodzi do każdego nowego wpisu."""
    entry = {"percent": None, "filtered": None, "answers": None, "offer_fp": offer_fp,
             "profile_fp": profile_fp, "model": None, "scored_at": scored_at, **fields}
    if triage_mark:
        entry["triage"] = triage_mark
    return entry


def _triage(todo, results, profile, cv, profile_fp, kept, key, session, now):
    known, ask = {}, []
    for link, job, _ in todo:
        cached = (results.get(link) or {}).get("triage") or {}
        if cached.get("fp") == triage.triage_fp(job) and (
                cached.get("profile_fp") == profile_fp or link in kept):
            known[link] = (cached["p"], cached.get("profile_fp") or profile_fp)
        else:
            ask.append((link, job))
    errors, stopped = 0, False
    if ask:
        fresh, tokens, errors, stopped = triage.triage(
            ask, jev.candidate_state(profile, cv), key, session, stop.requested)
        print(f"Triage: {len(fresh)}/{len(ask)} offers checked, {tokens} input tokens, "
              f"{len(known)} cached")
        known.update({link: (p, profile_fp) for link, p in fresh.items()})

    passed, rejected = [], 0
    for link, job, fp in todo:
        if link not in known:
            continue
        p, basis_fp = known[link]
        mark = {"p": round(p, 4), "fp": triage.triage_fp(job), "profile_fp": basis_fp}
        if p >= triage.THRESHOLD:
            entry = results.setdefault(link, _entry(None, None, None))
            entry["triage"] = mark
            passed.append((link, job, fp))
        else:
            rejected += 1
            fresh = _entry(fp, basis_fp, now, mark, filtered="kierunek")
            entry = results.get(link)
            if entry is None or dict(entry, scored_at=now) != fresh:
                results[link] = fresh
    return passed, rejected, errors, len(ask), stopped


def main(argv: list[str] | None = None) -> int:
    from utils.console import force_utf8
    import config  # wczytuje .env (TYPESAFE_API_KEY, klucze modelu profilu)
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
    remember_profile(profile)
    history = profile_history()
    print(f"Profile: {profile.get('seniority')} | {profile.get('city')} | "
          f"{len(profile.get('skills') or [])} skills | fp {profile_fp}")
    telemetry.emit("profile", seniority=profile.get("seniority"), city=profile.get("city"),
                   skills=len(profile.get("skills") or []))

    jobs = JobDatabase(str(config.JOBS_DATABASE_PATH)).load_jobs()
    store = candidates.match_results()
    store.backup()
    loaded = store.load()
    # --rescore-all: pełne przeliczenie po zmianie pytań w matching/jev.py; wagi liczą się z zapisanych odpowiedzi.
    results = {} if "--rescore-all" in args else loaded
    live_links = set()
    kept: set[str] = set()
    todo: list[tuple[str, Job, str]] = []
    filtered: dict[str, int] = {}
    now = datetime.now().isoformat(timespec="seconds")

    for job in jobs:
        link = canonical_link(job.link)
        live_links.add(link)
        fp = offer_fingerprint(job)
        # Oferty zapisane przed dodaniem pól strukturalnych nie mają cech z treści
        # (Job.from_dict pomija __post_init__). Liczymy je tu, w pamięci.
        if job.years_required is None and job.languages is None:
            for name, value in text_features(job.description).items():
                setattr(job, name, value)
        entry = results.get(link)
        basis_fp = profile_fp
        if entry and entry.get("offer_fp") == fp:
            kept.add(link)
            paid = entry.get("percent") is not None or entry.get("answers") or entry.get("filtered") == "kierunek"
            if paid and entry.get("profile_fp") in history:
                basis_fp = entry["profile_fp"]
        # Przesiew idzie po każdej ofercie, także ocenionej: jest darmowy, a nowa
        # reguła ma zdjąć z listy także oferty ocenione przed jej dodaniem.
        reason = reject_reason(job, history.get(basis_fp, profile))
        if reason:
            filtered[reason] = filtered.get(reason, 0) + 1
            fresh = _entry(fp, basis_fp, now, (entry or {}).get("triage"), filtered=reason)
            if link in kept and entry.get("answers"):
                fresh.update(answers=entry["answers"], model=entry.get("model"),
                             profile_fp=entry.get("profile_fp") or basis_fp)
            if entry is None or dict(entry, scored_at=now) != fresh:
                results[link] = fresh
            continue
        if link in kept:
            pct = stored_percent(entry.get("answers"))
            if pct is not None:
                if entry.get("percent") != pct or entry.get("filtered"):
                    results[link] = dict(entry, percent=pct, filtered=None)
                continue
            if entry.get("percent") is not None:
                continue
        todo.append((link, job, fp))

    # Wyniki ofert, których nie ma już w bazie, znikają razem z nimi.
    for link in [l for l in results if l not in live_links]:
        del results[link]

    session = requests.Session()
    stopped = False
    errors = triage_errors = triage_sent = 0
    if limit != 0 and todo:
        todo, rejected, triage_errors, triage_sent, stopped = _triage(
            todo, results, profile, cv, profile_fp, kept, key, session, now)
        if rejected:
            filtered["kierunek"] = rejected

    if limit is not None:
        todo = todo[:limit]
    total = len(todo)
    print(f"Prefilter: {sum(filtered.values())} rejected {filtered} | to score: {total}")
    telemetry.emit("prefilter", rejected=sum(filtered.values()), reasons=filtered, to_score=total)
    store.sync(results)

    done = 0
    unsaved: dict[str, dict] = {}
    started = last_report = time.monotonic()

    def score(item):
        link, job, fp = item
        reply = jev.ask(jev.build_state(job, profile, cv), key, session)
        return link, fp, reply, jev.percent(reply["answers"])

    def flush():
        if unsaved:
            store.put(unsaved)
            unsaved.clear()

    try:
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
                        link, fp, reply, pct = fut.result()
                    except Exception as e:
                        errors += 1
                        print(f"   Jev error: {type(e).__name__}: {e}")
                        telemetry.emit("jev_error")
                        if isinstance(e, jev.JevError) and "klucz" in str(e):
                            stopped = True
                        continue
                    fresh = _entry(fp, profile_fp, datetime.now().isoformat(timespec="seconds"),
                                   (results.get(link) or {}).get("triage"),
                                   percent=pct, answers=reply["answers"], model=reply.get("model"))
                    results[link] = unsaved[link] = fresh
                    done += 1
                    if len(unsaved) >= SAVE_EVERY:
                        flush()
                    now_s = time.monotonic()
                    if now_s - last_report >= PROGRESS_EVERY_S or done == total:
                        last_report = now_s
                        flush()
                        rate = done / max(now_s - started, 1e-6)
                        print(f"   Scored {done}/{total} ({rate:.1f}/s)")
                        telemetry.emit("scored", done=done, total=total, rate=round(rate, 1))
                if not stopped and stop.requested():
                    print("   Stop requested - finishing in-flight offers.")
                    stopped = True
    finally:
        flush()
    scored = sum(1 for e in results.values() if e.get("percent") is not None)
    print(f"Matching done: {done} scored now, {errors} errors, {triage_errors} triage errors, "
          f"{scored} offers with a percent.")
    telemetry.emit("matching_done", scored_now=done, errors=errors + triage_errors, with_percent=scored)
    # Zatrzymanie (flaga albo odrzucony klucz) to nie sukces: pipeline nie może
    # oznaczyć etapu jako ukończonego, bo reszta ofert czeka na ocenę.
    if stopped:
        return 1
    if triage_errors >= max(1, triage_sent // 10):
        return 1
    return 0 if errors < max(1, total // 10) else 1


if __name__ == "__main__":
    sys.exit(main())
