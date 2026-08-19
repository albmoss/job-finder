"""
Porównanie "przed / po" na IDENTYCZNYM zbiorze ofert.

Po co: eval_ranking.py czyta wyniki zapisane w analyzed_jobs_waterfall.json, ale
waterfall_analysis.py celowo pomija oferty, na których podjąłeś decyzję - czyli
dokładnie te, które mają Twoje oceny. Skutek: zapisane wyniki dla zbioru testowego
nigdy się nie odświeżają i eval_ranking nie wykryje poprawy promptu ani modelu.

Ten skrypt ocenia te same oferty jeszcze raz, aktualnym promptem/profilem/modelem
(bez zapisywania czegokolwiek do produkcyjnych danych) i zestawia obie wersje.

Uruchomienie:  python compare_before_after.py [--model gemini-3.5-flash-lite]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from benchmark_models import run_model, spearman
from config import GEMINI_API_KEYS
from eval_ranking import precision_at_k as _precision_at_k
from utils.links import canonical_link
from utils.safe_io import load_json_safe
from waterfall_analysis import MODELS, build_active_learning_context, create_prompt, load_cv


def precision_at_k(ranked, k, threshold=7):
    """Współdzielona z eval_ranking - odporna na remisy wyników."""
    p, _, _ = _precision_at_k(ranked, k, threshold)
    return p


def report(label, pairs, threshold=7):
    """pairs = [(score_0_100, user_rating_0_10)]"""
    rho = spearman(pairs)
    ranked = sorted(pairs, key=lambda x: -x[0])
    mae = sum(abs(s / 10 - r) for s, r in pairs) / len(pairs)
    return {
        "label": label,
        "n": len(pairs),
        "spearman": rho,
        "mae": mae,
        "p5": precision_at_k(ranked, 5, threshold),
        "p10": precision_at_k(ranked, 10, threshold),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODELS[0])
    ap.add_argument("--threshold", type=int, default=7)
    args = ap.parse_args()

    decisions = load_json_safe("user_decisions.json", default={})
    ratings = {
        canonical_link(k): v["rating"]
        for k, v in decisions.items()
        if isinstance(v, dict) and v.get("rating") is not None
    }

    analyzed = load_json_safe("analyzed_jobs_waterfall.json", default=[])
    old_scores, jobs_by_link = {}, {}
    for entry in analyzed:
        job = entry.get("job") or {}
        link = canonical_link(job.get("link", ""))
        if link in ratings and entry.get("match_percentage") is not None:
            old_scores[link] = entry["match_percentage"]
            jobs_by_link[link] = job

    if len(old_scores) < 10:
        print(f"Not enough offers with both a stored AI score and a rating ({len(old_scores)}).")
        return 1

    links = list(old_scores)
    jobs = [jobs_by_link[l] for l in links]
    truth = [ratings[l] for l in links]

    print("=" * 66)
    print(f"BEFORE/AFTER COMPARISON on {len(jobs)} offers (same set)")
    print(f"Model: {args.model}")
    print("=" * 66)

    before = report("PRZED (zapisane wyniki)", [(old_scores[l], ratings[l]) for l in links], args.threshold)

    print("\nRescoring with the current prompt and profile...")
    prompt = create_prompt(load_cv(), jobs, build_active_learning_context(jobs))
    res = run_model(args.model, GEMINI_API_KEYS[0], prompt, len(jobs))

    if not res["ok"]:
        print(f"FAILED: {res['error']}")
        return 1

    new_pairs = [
        (item.get("match_percentage", 0), truth[item["id"]])
        for item in res["items"]
        if isinstance(item.get("id"), int) and 0 <= item["id"] < len(truth)
    ]
    print(f"Received {len(new_pairs)}/{len(jobs)} ratings in {res['elapsed']:.0f}s\n")

    after = report("PO (nowy profil + model)", new_pairs, args.threshold)

    print(f"{'metryka':<16}{'PRZED':>12}{'PO':>12}{'zmiana':>12}")
    print("-" * 66)
    for key, name, fmt, higher_better in [
        ("spearman", "Spearman", "{:+.3f}", True),
        ("mae", "MAE (0-10)", "{:.2f}", False),
        ("p5", "precision@5", "{:.0%}", True),
        ("p10", "precision@10", "{:.0%}", True),
    ]:
        b, a = before[key], after[key]
        if b is None or a is None:
            continue
        delta = a - b
        good = (delta > 0) if higher_better else (delta < 0)
        mark = "▲" if good and abs(delta) > 1e-9 else ("▼" if abs(delta) > 1e-9 else "=")
        dtxt = fmt.format(delta) if key != "mae" else f"{delta:+.2f}"
        if key in ("p5", "p10"):
            dtxt = f"{delta:+.0%}"
        print(f"{name:<16}{fmt.format(b):>12}{fmt.format(a):>12}{mark + ' ' + dtxt:>12}")
    print("-" * 66)

    print("\nNOTE: the set holds only {} offers, so correlation differences below ~0.05"
          "\nfall within the noise. The more offers you rate, the firmer the measurement.".format(len(jobs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
