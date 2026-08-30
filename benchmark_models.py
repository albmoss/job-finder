"""
Benchmark modeli Gemini na TWOICH ocenach.

Zamiast wybierać model po nazwie, mierzymy: który model najlepiej odtwarza oceny,
które wystawiłeś ręcznie. Każdy kandydat dostaje ten sam produkcyjny prompt
(z profilem preferencji) i ten sam zestaw ocenionych ofert.

Metryki:
  - Spearman  : czy model układa oferty w tej samej kolejności co Ty (najważniejsze)
  - MAE       : średni błąd bezwzględny przeliczony na skalę 0-10
  - kompletność: czy model zwrócił wszystkie oferty (kluczowe przy batchach po 75)
  - czas      : ile trwało jedno wywołanie - decyduje o realnym czasie przeliczenia

Uruchomienie:  python benchmark_models.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from google import genai
from google.genai import types

from config import GEMINI_API_KEYS
from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic
from waterfall_analysis import (BATCH_SIZE, JobEval, build_active_learning_context,
                                create_prompt, load_cv)

CANDIDATES = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]

RESULTS_FILE = "benchmark_results.json"


def spearman(pairs):
    n = len(pairs)
    if n < 3:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else None


def build_labeled_set(min_desc=150):
    """Oferty ocenione ręcznie, które mają realny opis."""
    decisions = load_json_safe("user_decisions.json", default={})
    rated = {
        canonical_link(k): v["rating"]
        for k, v in decisions.items()
        if isinstance(v, dict) and v.get("rating") is not None
    }

    jobs = {canonical_link(j.get("link", "")): j for j in load_json_safe("jobs_database.json", default=[])}
    for entry in load_json_safe("rated_archive.json", default=[]):
        job = entry.get("job") or {}
        link = canonical_link(job.get("link", ""))
        if link and link not in jobs:
            jobs[link] = job

    out = []
    for link, rating in rated.items():
        job = jobs.get(link)
        if job and len((job.get("description") or "").strip()) >= min_desc:
            out.append((job, rating))
    return out


def run_model(model_name, api_key, prompt, expected_n):
    client = genai.Client(api_key=api_key)
    started = time.time()
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[JobEval],
                max_output_tokens=65535,
                temperature=0.3,
            ),
        )
        elapsed = time.time() - started
        parsed = json.loads(resp.text)
        usage = getattr(resp, "usage_metadata", None)
        return {
            "ok": True,
            "elapsed": elapsed,
            "items": parsed,
            "returned": len(parsed),
            "expected": expected_n,
            "in_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
            "out_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
        }
    except Exception as e:
        return {"ok": False, "elapsed": time.time() - started, "error": str(e)[:200]}


def run_model_batched(model_name, api_key, jobs, cv, context, batch_size=BATCH_SIZE):
    """
    To samo co `run_model`, ale zbiorem podzielonym na paczki.

    Caly zbior w jednym wywolaniu miesci sie dopoki jest maly: przy ~65 tys.
    tokenow wyjscia i 60-90 tokenach na oferte odpowiedz urywa sie w polowie
    JSON-a gdzies kolo 700-900 ofert, a wtedy `json.loads` rzuca i przepada
    KOSZT CALEGO WEJSCIA - takze tych ofert, ktore model zdazyl ocenic.
    Produkcyjna kaskada dzieli po `BATCH_SIZE` wlasnie z tego powodu; tutaj
    dzielimy tak samo, zeby zbior testowy mogl rosnac razem z ocenami.

    Identyfikatory w odpowiedzi sa lokalne dla paczki (`create_prompt`
    numeruje od zera), wiec przy sklejaniu przesuwamy je o poczatek paczki.
    """
    items, elapsed, in_tok, out_tok = [], 0.0, 0, 0
    for start in range(0, len(jobs), batch_size):
        chunk = jobs[start:start + batch_size]
        res = run_model(model_name, api_key, create_prompt(cv, chunk, context), len(chunk))
        if not res["ok"]:
            res["elapsed"] += elapsed
            return res
        for item in res["items"]:
            i = item.get("id")
            if isinstance(i, int) and 0 <= i < len(chunk):
                items.append({**item, "id": start + i})
        elapsed += res["elapsed"]
        in_tok += res["in_tokens"] or 0
        out_tok += res["out_tokens"] or 0
    return {"ok": True, "elapsed": elapsed, "items": items, "returned": len(items),
            "expected": len(jobs), "in_tokens": in_tok, "out_tokens": out_tok}


def main():
    labeled = build_labeled_set()
    if len(labeled) < 15:
        print(f"Not enough rated offers with a description ({len(labeled)}) - the benchmark would be meaningless.")
        return 1

    jobs = [j for j, _ in labeled]
    truth = [r for _, r in labeled]
    print(f"Test set: {len(jobs)} manually rated offers (with full description)")
    print(f"Ratings >=7: {sum(1 for r in truth if r >= 7)}\n")

    cv = load_cv()
    context = build_active_learning_context(jobs)
    paczek = (len(jobs) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Prompt: {len(create_prompt(cv, jobs[:BATCH_SIZE], context)):,} "
          f"characters per batch, {paczek} batch(es)\n")

    results = {}
    for idx, model in enumerate(CANDIDATES):
        key = GEMINI_API_KEYS[idx % len(GEMINI_API_KEYS)]
        print(f"▶ {model} (klucz #{idx % len(GEMINI_API_KEYS)}) ...", flush=True)

        res = run_model_batched(model, key, jobs, cv, context)

        if not res["ok"]:
            print(f"   FAILED after {res['elapsed']:.0f}s: {res['error']}\n")
            results[model] = {"error": res["error"], "elapsed": res["elapsed"]}
            time.sleep(20)
            continue

        # Dopasuj odpowiedzi do prawdy po ID
        pairs = []
        for item in res["items"]:
            i = item.get("id")
            if isinstance(i, int) and 0 <= i < len(truth):
                pairs.append((item.get("match_percentage", 0), truth[i]))

        rho = spearman(pairs)
        mae = sum(abs(s / 10 - r) for s, r in pairs) / len(pairs) if pairs else None
        completeness = res["returned"] / res["expected"]

        results[model] = {
            "spearman": rho,
            "mae": mae,
            "returned": res["returned"],
            "expected": res["expected"],
            "completeness": completeness,
            "elapsed": res["elapsed"],
            "in_tokens": res["in_tokens"],
            "out_tokens": res["out_tokens"],
        }

        print(f"   ✓ {res['returned']}/{res['expected']} offers | {res['elapsed']:.0f}s | "
              f"Spearman {rho:+.3f} | MAE {mae:.2f}\n" if rho is not None
              else f"   ✓ {res['returned']}/{res['expected']} | {res['elapsed']:.0f}s\n")

        time.sleep(20)  # odstęp między modelami, żeby nie kumulować limitów

    # Podsumowanie
    print("=" * 78)
    print(f"{'model':<26}{'Spearman':>10}{'MAE':>7}{'compl.':>9}{'time':>8}{'out tok':>10}")
    print("=" * 78)

    ranked = sorted(
        [(m, r) for m, r in results.items() if r.get("spearman") is not None],
        key=lambda x: -x[1]["spearman"],
    )
    for model, r in ranked:
        print(f"{model:<26}{r['spearman']:>+10.3f}{r['mae']:>7.2f}"
              f"{r['completeness']:>8.0%}{r['elapsed']:>7.0f}s{str(r['out_tokens']):>10}")
    for model, r in results.items():
        if r.get("spearman") is None:
            print(f"{model:<26}{'FAILED':>10}  {r.get('error', '')[:38]}")
    print("=" * 78)

    if ranked:
        best = ranked[0]
        print(f"\nNajlepsza korelacja: {best[0]} ({best[1]['spearman']:+.3f})")

    save_json_atomic(RESULTS_FILE, results)
    print(f"Details saved to {RESULTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
