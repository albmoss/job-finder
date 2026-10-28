from __future__ import annotations

import concurrent.futures as cf
import copy
import heapq
import json
import math
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import requests

from matching import jev, triage
from matching.prefilter import reject_reason
from utils.data_models import Job
from utils.offer_fields import _fold, text_features

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
_FIELDS = (3.0, 2.0, 1.0)
_DROPPED = ("description", "posted_date", "scraped_at", "valid_through", "times_seen", "also_on", "last_seen")
_CHUNK = 900
_SCHEMA = """
DROP TABLE IF EXISTS search_postings;
DROP TABLE IF EXISTS search_offers;
DROP TABLE IF EXISTS search_logos;
CREATE TABLE search_offers (id INTEGER PRIMARY KEY, title_folded TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE search_postings (token TEXT NOT NULL, id INTEGER NOT NULL, fields INTEGER NOT NULL,
                              PRIMARY KEY (token, id)) WITHOUT ROWID;
CREATE TABLE search_logos (url TEXT PRIMARY KEY) WITHOUT ROWID;
"""


def _tokens(text: str | None) -> frozenset[str]:
    return frozenset(sys.intern(t[:_STEM]) for t in _TOKEN_RE.findall(_fold(text or "")) if t not in _STOP)


def _chunks(items: list, size: int = _CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _field_masks(job: Job) -> dict[str, int]:
    texts = (job.title, " ".join(map(str, (job.skills_required or []) + (job.skills_nice or []))), job.category)
    masks: dict[str, int] = {}
    for bit, text in enumerate(texts):
        for token in _tokens(text):
            masks[token] = masks.get(token, 0) | 1 << bit
    return masks


def build_index(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA cache_size = -100000")
        conn.executescript(_SCHEMA)
        offers, postings, logos, count = [], [], [], 0

        def flush() -> None:
            conn.executemany("INSERT INTO search_offers VALUES (?, ?, ?)", offers)
            conn.executemany("INSERT INTO search_postings VALUES (?, ?, ?)", postings)
            conn.executemany("INSERT OR IGNORE INTO search_logos VALUES (?)", logos)
            offers.clear()
            postings.clear()
            logos.clear()

        for rowid, text in conn.cursor().execute("SELECT rowid, data FROM records ORDER BY rowid"):
            job = Job.from_dict(json.loads(text))
            if not job.title:
                continue
            offers.append((rowid, _fold(job.title), job.last_seen or job.scraped_at or ""))
            postings.extend((token, rowid, mask) for token, mask in _field_masks(job).items())
            if job.logo_url:
                logos.append((job.logo_url,))
            count += 1
            if len(offers) >= 2000:
                flush()
        flush()
        conn.execute("CREATE INDEX search_offers_recent ON search_offers (last_seen DESC, id)")
        conn.commit()
    return count


@dataclass(slots=True)
class _Offer:
    key: str
    job: Job
    title_folded: str
    last_seen: str


def _offer(key: str, record: dict) -> _Offer:
    job = Job.from_dict(record)
    if job.years_required is None and job.languages is None:
        for name, value in text_features(job.description).items():
            setattr(job, name, value)
    last_seen = job.last_seen or job.scraped_at or ""
    for name in _DROPPED:
        setattr(job, name, None)
    return _Offer(key, job, _fold(job.title), last_seen)


class OfferIndex:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self._uri = self.path.resolve().as_uri() + "?mode=ro"
        with closing(self._connect()) as conn:
            try:
                self.total = conn.execute("SELECT COUNT(*) FROM search_offers").fetchone()[0]
            except sqlite3.OperationalError:
                raise RuntimeError(f"{self.path.name} nie ma indeksu wyszukiwania, "
                                   f"opublikuj bazę przez publish_offers.py") from None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._uri, uri=True)

    def _scores(self, conn: sqlite3.Connection, profile: dict) -> tuple[dict[int, float], dict[int, str]]:
        weights, phrases = _query(profile)
        scores: dict[int, float] = {}
        for token, weight in weights.items():
            rows = conn.execute("SELECT id, fields FROM search_postings WHERE token = ?", (token,)).fetchall()
            idf = math.log(1 + self.total / (1 + len(rows)))
            for oid, mask in rows:
                best = next(w for bit, w in enumerate(_FIELDS) if mask >> bit & 1)
                scores[oid] = scores.get(oid, 0.0) + weight * idf * best
        recent: dict[int, str] = {}
        for chunk in _chunks(list(scores)):
            marks = ",".join("?" * len(chunk))
            for oid, title_folded, last_seen in conn.execute(
                    f"SELECT id, title_folded, last_seen FROM search_offers WHERE id IN ({marks})", chunk):
                if any(p in title_folded for p in phrases):
                    scores[oid] *= 1.5
                recent[oid] = last_seen
        return scores, recent

    def _passing(self, conn: sqlite3.Connection, ids: list[int], profile: dict) -> list[_Offer]:
        marks = ",".join("?" * len(ids))
        rows = {rowid: (key, data) for rowid, key, data in conn.execute(
            f"SELECT rowid, key, data FROM records WHERE rowid IN ({marks})", ids)}
        offers = [_offer(rows[oid][0], json.loads(rows[oid][1])) for oid in ids]
        return [o for o in offers if not reject_reason(o.job, profile)]

    def ranked(self, profile: dict, limit: int) -> list[_Offer]:
        with closing(self._connect()) as conn:
            scores, recent = self._scores(conn, profile)
            order = sorted(scores)
            order.sort(key=lambda oid: (scores[oid], recent[oid]), reverse=True)
            ranked: list[_Offer] = []
            for chunk in _chunks(order, 200):
                ranked += self._passing(conn, chunk, profile)
                if len(ranked) >= limit:
                    return ranked[:limit]
            rest = [oid for (oid,) in conn.execute("SELECT id FROM search_offers ORDER BY last_seen DESC, id")
                    if oid not in scores]
            for chunk in _chunks(rest, 200):
                ranked += self._passing(conn, chunk, profile)
                if len(ranked) >= limit:
                    break
            return ranked[:limit]

    def has_logo(self, url: str) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM search_logos WHERE url = ?", (url,)).fetchone() is not None

    def descriptions(self, keys: list[str]) -> dict[str, str]:
        with closing(self._connect()) as conn:
            marks = ",".join("?" * len(keys))
            return {k: json.loads(d).get("description") or "" for k, d in conn.execute(
                f"SELECT key, data FROM records WHERE key IN ({marks})", keys)}

    def meta(self) -> dict:
        with closing(self._connect()) as conn:
            try:
                return {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM publish_meta")}
            except sqlite3.OperationalError:
                return {}


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


@dataclass
class Result:
    picks: list[dict] = field(default_factory=list)
    good: int = 0
    ranked: int = 0
    triaged: int = 0
    scored: int = 0
    promising: int = 0
    stopped_early: bool = False
    errors: int = 0
    tokens: int = 0


@dataclass
class TopResult:
    picks: list[dict] = field(default_factory=list)
    ranked: int = 0
    scored: int = 0
    reused: int = 0
    errors: int = 0
    tokens: int = 0


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


def _score(offer: _Offer, description: str, profile: dict, cv: str, key: str,
           session: requests.Session) -> tuple[int, int]:
    job = copy.copy(offer.job)
    job.description = description
    reply = jev.ask(jev.build_state(job, profile, cv), key, session)
    return jev.percent(reply["answers"]), int((reply.get("usage") or {}).get("input_tokens") or 0)


def score_top(index: OfferIndex, profile: dict, cv: str, key: str, limit: int, known: dict[str, int],
              progress=lambda **_: None) -> TopResult:
    offers = [o for _, o in _distinct([(0, o) for o in index.ranked(profile, limit * 3 // 2)], limit)]
    result = TopResult(ranked=len(offers))
    progress(stage="ranking", ranked=len(offers))
    percents = {o.job.link: known[o.job.link] for o in offers if o.job.link in known}
    result.reused = len(percents)
    todo = [o for o in offers if o.job.link not in percents]
    session = requests.Session()
    with cf.ThreadPoolExecutor(SCORE_ROUND) as pool_exec:
        for chunk in _chunks(todo, 40):
            texts = index.descriptions([o.key for o in chunk])
            futures = {pool_exec.submit(_score, o, texts.get(o.key, ""), profile, cv, key, session): o
                       for o in chunk}
            for fut in cf.as_completed(futures):
                try:
                    pct, used = fut.result()
                except Exception:
                    result.errors += 1
                    continue
                percents[futures[fut].job.link] = pct
                result.tokens += used
                result.scored += 1
            progress(stage="scoring", scored=result.scored + result.reused, total=len(offers))
    if todo and not result.scored:
        raise jev.JevError("Ocena ofert nie odpowiedziała")
    result.picks = sorted((_pick(o, percents[o.job.link]) for o in offers if o.job.link in percents),
                          key=lambda p: -p["percent"])
    return result


def find_matches(index: OfferIndex, profile: dict, cv: str, key: str,
                 progress=lambda **_: None, cancelled=lambda: False) -> Result:
    ranked = index.ranked(profile, MAX_TRIAGE)
    result = Result(ranked=len(ranked))
    progress(stage="ranking", ranked=len(ranked))

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
        chances, tokens, errors, _ = triage.triage([(o.key, o.job) for o in batch], candidate, key, session)
        result.tokens += tokens
        result.errors += errors
        result.triaged = cursor
        if errors and not chances:
            raise jev.JevError("Wstępna ocena nie odpowiedziała")
        for i, offer in enumerate(batch):
            p = chances.get(offer.key)
            if p is not None and p >= triage.THRESHOLD:
                heapq.heappush(heap, (-p, cursor + i, offer))
        progress(stage="triage", triaged=cursor, budget=budget, promising=len(heap))

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
            futures = {pool_exec.submit(_score, o, texts.get(o.key, ""), profile, cv, key, session): o
                       for o in round_offers}
            before = len(scored)
            for fut in cf.as_completed(futures):
                try:
                    pct, used = fut.result()
                    scored.append((pct, futures[fut]))
                    result.tokens += used
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
