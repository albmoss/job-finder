from __future__ import annotations

import concurrent.futures as cf
import copy
import heapq
import math
import re
import sys
import threading
from collections import Counter
from dataclasses import dataclass, field

import requests

from matching import jev, triage
from matching.prefilter import reject_reason
from utils.data_models import Job
from utils.offer_fields import _fold, text_features
from utils.sqlite_store import RecordStore

WANTED = 3
GOOD = 40
FALLBACK = 25
HIGH_CHANCE = 0.5
MAX_TRIAGE = 600
MAX_SCORED = 40
SCORE_ROUND = 8

_TOKEN_RE = re.compile(r"[a-z0-9+#]{2,}")
_STEM = 6
_STOP = {
    "and", "the", "for", "with", "of", "in", "to", "oraz", "dla", "na", "do", "ds", "sp", "zo", "oo",
    "praca", "pracy", "pracownik", "pracownica", "specjalista", "specjalistka", "mlodszy", "mlodsza",
    "starszy", "starsza", "junior", "senior", "mid", "regular", "lead", "intern", "stazysta", "stazystka",
    "staz", "praktykant", "praktykantka", "kobieta", "mezczyzna", "kandydat",
}
_FIELD_WEIGHT = {"title": 3.0, "skills": 2.0, "category": 1.0}


def _tokens(text: str | None) -> frozenset[str]:
    return frozenset(sys.intern(t[:_STEM]) for t in _TOKEN_RE.findall(_fold(text or "")) if t not in _STOP)


_DROPPED = ("description", "posted_date", "scraped_at", "valid_through", "times_seen", "also_on", "last_seen")
_SHARED = ("source", "location", "category", "company")


@dataclass(slots=True)
class _Offer:
    key: str
    job: Job
    fields: dict[str, frozenset[str]]
    title_folded: str
    last_seen: str


class OfferIndex:
    def __init__(self, db_path):
        self.store = RecordStore(db_path)
        self.rev: int | None = None
        self.offers: list[_Offer] = []
        self.df: Counter = Counter()
        self._lock = threading.Lock()

    def refresh(self) -> None:
        with self._lock:
            rev = self.store.revision()
            if rev == self.rev:
                return
            offers, df = [], Counter()
            for key, record in RecordStore(self.store.path).iter_records():
                job = Job.from_dict(record)
                if not job.title:
                    continue
                if job.years_required is None and job.languages is None:
                    for name, value in text_features(job.description).items():
                        setattr(job, name, value)
                last_seen = job.last_seen or job.scraped_at or ""
                for name in _DROPPED:
                    setattr(job, name, None)
                for name in _SHARED:
                    value = getattr(job, name)
                    if isinstance(value, str):
                        setattr(job, name, sys.intern(value))
                fields = {
                    "title": _tokens(job.title),
                    "skills": _tokens(" ".join(map(str, (job.skills_required or []) + (job.skills_nice or [])))),
                    "category": _tokens(job.category),
                }
                for token in fields["title"] | fields["skills"] | fields["category"]:
                    df[token] += 1
                offers.append(_Offer(key, job, fields, _fold(job.title), last_seen))
            self.offers, self.df, self.rev = offers, df, rev

    def descriptions(self, keys: list[str]) -> dict[str, str]:
        return {k: (r.get("description") or "") for k, r in self.store.get_many(keys).items()}


def _query(profile: dict) -> tuple[dict[str, float], list[str]]:
    weights: dict[str, float] = {}
    for role in profile.get("roles") or []:
        for token in _tokens(role):
            weights[token] = max(weights.get(token, 0.0), 3.0)
    for skill in profile.get("skills") or []:
        for token in _tokens(skill):
            weights[token] = max(weights.get(token, 0.0), 1.5)
    phrases = [p for p in (_fold(r).strip() for r in profile.get("roles") or []) if len(p) >= 4]
    return weights, phrases


def local_rank(offers: list[_Offer], profile: dict, df: Counter, total: int) -> list[_Offer]:
    weights, phrases = _query(profile)
    idf = {t: math.log(1 + total / (1 + df.get(t, 0))) for t in weights}

    def score(offer: _Offer) -> float:
        value = 0.0
        for token, weight in weights.items():
            best = max((w for name, w in _FIELD_WEIGHT.items() if token in offer.fields[name]), default=0.0)
            value += weight * idf[token] * best
        if any(p in offer.title_folded for p in phrases):
            value *= 1.5
        return value

    scored = [(score(o), o.last_seen, i) for i, o in enumerate(offers)]
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [offers[i] for _, _, i in scored]


@dataclass
class Result:
    picks: list[dict] = field(default_factory=list)
    good: int = 0
    pool: int = 0
    triaged: int = 0
    scored: int = 0
    promising: int = 0
    stopped_early: bool = False
    errors: int = 0


def _pick(offer: _Offer, percent: int) -> dict:
    job = offer.job
    return {
        "link": job.link, "title": job.title, "company": job.company, "location": job.location,
        "source": job.source, "salary": job.salary, "work_modes": job.work_modes,
        "logo_url": job.logo_url, "percent": percent,
    }


def _distinct(ranked: list[tuple[int, _Offer]], limit: int) -> list[tuple[int, _Offer]]:
    seen, out = set(), []
    for pct, offer in ranked:
        ident = (_fold(offer.job.company), offer.title_folded)
        if ident in seen:
            continue
        seen.add(ident)
        out.append((pct, offer))
        if len(out) == limit:
            break
    return out


def find_matches(index: OfferIndex, profile: dict, cv: str, key: str,
                 progress=lambda **_: None, cancelled=lambda: False) -> Result:
    index.refresh()
    pool = [o for o in index.offers if not reject_reason(o.job, profile)]
    ranked = local_rank(pool, profile, index.df, len(index.offers))
    result = Result(pool=len(pool))
    progress(stage="ranking", pool=len(pool))

    candidate = jev.candidate_state(profile, cv)
    session = requests.Session()
    heap: list[tuple[float, int, _Offer]] = []
    scored: list[tuple[int, _Offer]] = []
    cursor = 0
    budget = min(len(ranked), MAX_TRIAGE)

    def triage_next() -> None:
        nonlocal cursor
        batch = ranked[cursor:cursor + triage.BATCH]
        cursor += len(batch)
        chances, _, errors, _ = triage.triage([(o.key, o.job) for o in batch], candidate, key, session)
        result.errors += errors
        result.triaged = cursor
        if errors and not chances:
            raise jev.JevError("Wstępna ocena nie odpowiedziała")
        for i, offer in enumerate(batch):
            p = chances.get(offer.key)
            if p is not None and p >= triage.THRESHOLD:
                heapq.heappush(heap, (-p, cursor + i, offer))
        progress(stage="triage", triaged=cursor, budget=budget, promising=len(heap))

    def score(offer: _Offer, description: str) -> int:
        job = copy.copy(offer.job)
        job.description = description
        reply = jev.ask(jev.build_state(job, profile, cv), key, session)
        return jev.percent(reply["answers"])

    def best(min_percent: int) -> list[tuple[int, _Offer]]:
        return _distinct(sorted((s for s in scored if s[0] >= min_percent), key=lambda s: -s[0]), WANTED)

    with cf.ThreadPoolExecutor(SCORE_ROUND) as pool_exec:
        while len(best(GOOD)) < WANTED and len(scored) < MAX_SCORED and not cancelled():
            if cursor < budget and (not heap or -heap[0][0] < HIGH_CHANCE):
                triage_next()
                continue
            if not heap:
                break
            take = min(SCORE_ROUND, MAX_SCORED - len(scored), len(heap))
            round_offers = [heapq.heappop(heap)[2] for _ in range(take)]
            texts = index.descriptions([o.key for o in round_offers])
            futures = {pool_exec.submit(score, o, texts.get(o.key, "")): o for o in round_offers}
            before = len(scored)
            for fut in cf.as_completed(futures):
                try:
                    scored.append((fut.result(), futures[fut]))
                except Exception:
                    result.errors += 1
            if len(scored) == before:
                raise jev.JevError("Ocena ofert nie odpowiedziała")
            result.scored = len(scored)
            progress(stage="scoring", scored=len(scored), good=len(best(GOOD)), triaged=cursor)

    good = best(GOOD)
    result.picks = [_pick(offer, pct) for pct, offer in best(FALLBACK)]
    result.good = len(good)
    result.promising = len(heap)
    result.stopped_early = len(good) >= WANTED and cursor < len(ranked)
    return result
