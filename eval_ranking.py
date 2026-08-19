"""
Ewaluacja jakości rankingu AI na ręcznych ocenach użytkownika.

Pytanie, na które ten skrypt odpowiada: czy match_percentage z modelu faktycznie
przewiduje, które oferty ocenisz wysoko? Bez tego nie da się stwierdzić, czy zmiana
promptu / profilu / modelu cokolwiek poprawiła - a dotąd nic tego nie mierzyło.

WAŻNE: masowe odrzucenia po słowie kluczowym (legacy: wartość "reject" jako goły
string) są wykluczane. To nie są oceny jakości, tylko czyszczenie bazy - włączenie
ich zawyżyłoby każdą metrykę.

Uruchomienie:
    python eval_ranking.py
    python eval_ranking.py --threshold 7    # co uznajemy za trafienie
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.safe_io import load_json_safe

ANALYZED_FILE = "analyzed_jobs_waterfall.json"
DECISIONS_FILE = "user_decisions.json"


def load_manual_ratings() -> dict:
    """Zwróć {link: rating} tylko dla ocen wystawionych ręcznie."""
    raw = load_json_safe(DECISIONS_FILE, default={})
    ratings = {}
    for link, val in raw.items():
        # Legacy string ("reject") = masowe czyszczenie po słowie kluczowym - pomijamy
        if isinstance(val, dict) and val.get("rating") is not None:
            try:
                ratings[link] = int(val["rating"])
            except (TypeError, ValueError):
                continue
    return ratings


def load_scores() -> dict:
    """Zwróć {link: match_percentage}."""
    data = load_json_safe(ANALYZED_FILE, default=[])
    scores = {}
    for item in data:
        link = (item.get("job") or {}).get("link")
        if link and item.get("match_percentage") is not None:
            scores[link] = item["match_percentage"]
    return scores


def precision_at_k(ranked, k, threshold):
    """
    Odsetek trafień w top-k, liczony jako wartość OCZEKIWANA przy losowym
    rozstrzyganiu remisów.

    Zwykłe `ranked[:k]` daje wynik zależny od kolejności wejściowej: przy wielu
    ofertach z identycznym match_percentage (a tak jest - dużo ofert dostaje
    równe 10% czy 20%) precision@5 potrafi skakać o kilkadziesiąt punktów
    w zależności od tego, jak posortował się plik. Tutaj remisy na granicy k
    dzielą się proporcjonalnie, więc wynik jest powtarzalny.
    """
    if not ranked or k <= 0:
        return None, 0, 0
    k = min(k, len(ranked))

    boundary = ranked[k - 1][0]
    above = [p for p in ranked if p[0] > boundary]
    tied = [p for p in ranked if p[0] == boundary]

    hits_above = sum(1 for _, r in above if r >= threshold)
    slots_left = k - len(above)

    if tied and slots_left > 0:
        tied_hit_rate = sum(1 for _, r in tied if r >= threshold) / len(tied)
        expected_hits = hits_above + tied_hit_rate * slots_left
    else:
        expected_hits = hits_above

    return expected_hits / k, expected_hits, k


def spearman(pairs):
    """Korelacja rangowa Spearmana bez scipy (obsługa remisów przez rangi średnie)."""
    n = len(pairs)
    if n < 3:
        return None

    def ranks(values):
        order = sorted(range(n), key=lambda i: values[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else None


def bar(frac, width=28):
    if frac is None:
        return ""
    filled = int(round(frac * width))
    return "█" * filled + "·" * (width - filled)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=7,
                    help="Od jakiej oceny uznajemy ofertę za trafioną (domyślnie 7/10)")
    args = ap.parse_args()

    ratings = load_manual_ratings()
    scores = load_scores()

    if not ratings:
        print("No manual ratings - nothing to evaluate.")
        return 1

    # Wspólny zbiór: oferty ocenione ręcznie ORAZ mające wynik z AI
    common = [(scores[link], ratings[link]) for link in ratings if link in scores]

    print("=" * 64)
    print("  EWALUACJA RANKINGU AI")
    print("=" * 64)
    print(f"Manual ratings in database:         {len(ratings)}")
    print(f"Of those, with an AI score:         {len(common)}")
    print(f"Rated but not analysed:             {len(ratings) - len(common)}")

    if len(common) < 10:
        print("\nNot enough common records for meaningful metrics.")
        return 1

    dist = {}
    for _, r in common:
        dist[r] = dist.get(r, 0) + 1
    print("\nDistribution of manual ratings:")
    for r in sorted(dist, reverse=True):
        print(f"   {r:>2}/10 : {dist[r]:>4}  {'▪' * min(dist[r], 40)}")

    positives = sum(1 for _, r in common if r >= args.threshold)
    baseline = positives / len(common)

    print(f"\nHits (rating >= {args.threshold}): {positives}/{len(common)} = {baseline:.1%}")
    print(f"This is the BASELINE - the hit rate of a random pick.\n")

    # Ranking wg wyniku AI, malejąco
    ranked = sorted(common, key=lambda x: -x[0])

    print("-" * 64)
    print(f"{'k':>5} | {'precision@k':>11} | {'trafienia':>10} | vs baseline")
    print("-" * 64)
    for k in (5, 10, 25, 50, 100, len(ranked)):
        if k > len(ranked):
            continue
        p, hits, tot = precision_at_k(ranked, k, args.threshold)
        lift = (p / baseline) if baseline else 0
        label = f"{k:>5}" if k != len(ranked) else f"{k:>5}*"
        print(f"{label} | {p:>10.1%} | {hits:>4.1f}/{tot:<5} | {lift:>5.2f}x  {bar(p)}")
    print("-" * 64)
    print("* = whole set (equal to baseline by definition)")

    rho = spearman(common)
    if rho is not None:
        strength = ("brak", "słaba", "umiarkowana", "silna")[
            min(3, int(abs(rho) / 0.25))
        ]
        print(f"\nSpearman correlation (AI score vs rating): {rho:+.3f}  ({strength})")

    # Średnia ocena w przedziałach wyniku AI - pokazuje czy skala jest monotoniczna
    print("\nMean manual rating per match_percentage bucket:")
    buckets = {}
    for s, r in common:
        b = min(int(s // 20) * 20, 80)
        buckets.setdefault(b, []).append(r)
    for b in sorted(buckets, reverse=True):
        vals = buckets[b]
        avg = sum(vals) / len(vals)
        hi = sum(1 for v in vals if v >= args.threshold)
        print(f"   {b:>3}-{b+19:<3}% : mean {avg:>4.1f}/10  (n={len(vals):>3}, hits {hi:>2})  {bar(avg / 10, 20)}")

    print("\n" + "=" * 64)
    print("HOW TO READ:")
    print("  precision@25 clearly above baseline = the ranking works.")
    print("  Result ~= baseline = the model cannot tell good offers from bad.")
    print("  Correlation < 0.2 = match_percentage is effectively random.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
