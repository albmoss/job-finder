"""
Wstępna ocena ofert przed pełną oceną Jev.

Jedno zapytanie niesie kandydata (profil + CV) raz i do BATCH pytań tak/nie, po
jednym na ofertę z samym tytułem, kategorią, firmą i umiejętnościami. Do pełnej
oceny (cała treść oferty w każdym zapytaniu) idą tylko oferty z szansą co
najmniej THRESHOLD.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json

import requests

from matching import jev
from utils.data_models import Job

THRESHOLD = 0.15
BATCH = 100
WORKERS = 4
INSTRUCTIONS = (
    "Osoba z tym CV ma sens aplikować na to stanowisko: to jej zawód, kierunek, w który CV prowadzi, "
    "dziedzina pokrewna albo praca, którą już wykonywała."
)


def offer_summary(job: Job) -> dict:
    summary = {
        "stanowisko": job.title,
        "kategoria": job.category,
        "umiejetnosci": (job.skills_required or [])[:8],
        "firma": job.company,
    }
    return {k: v for k, v in summary.items() if v}


def triage_fp(job: Job) -> str:
    raw = json.dumps(offer_summary(job), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ask_batch(jobs: list[Job], candidate: dict, key: str,
              session: requests.Session | None = None) -> tuple[list[float], int]:
    questions = {
        f"o{i}": {"type": "noul", "instructions": {"oferta": offer_summary(job), "pytanie": INSTRUCTIONS}}
        for i, job in enumerate(jobs)
    }
    reply = jev.ask({"kandydat": candidate}, key, session, questions=questions)
    chances = [float(reply["answers"][f"o{i}"]["noul"]) for i in range(len(jobs))]
    return chances, int((reply.get("usage") or {}).get("input_tokens") or 0)


def triage(items: list[tuple[str, Job]], candidate: dict, key: str, session: requests.Session,
           should_stop=lambda: False) -> tuple[dict[str, float], int, int, bool]:
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    chances: dict[str, float] = {}
    tokens = errors = 0
    stopped = False
    with cf.ThreadPoolExecutor(WORKERS) as pool:
        pending = {}
        queue = iter(batches)
        while True:
            while not stopped and len(pending) < WORKERS:
                batch = next(queue, None)
                if batch is None:
                    break
                fut = pool.submit(ask_batch, [job for _, job in batch], candidate, key, session)
                pending[fut] = batch
            if not pending:
                break
            finished, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for fut in finished:
                batch = pending.pop(fut)
                try:
                    values, used = fut.result()
                except jev.JevError as e:
                    errors += len(batch)
                    print(f"   Jev error: {e}")
                    if "klucz" in str(e):
                        stopped = True
                    continue
                tokens += used
                chances.update({link: value for (link, _), value in zip(batch, values, strict=True)})
            if not stopped and should_stop():
                stopped = True
    return chances, tokens, errors, stopped
