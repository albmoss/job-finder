"""
Czego brakuje najczęściej w ofertach, które i tak są dobrze dopasowane.

Ranking odpowiada na pytanie „w co aplikować dzisiaj". To pytanie inne: „czego się
nauczyć, żeby ranking miał z czego wybierać za pół roku". Dane są w bazie od
początku - model przy każdej ocenie zwraca `missing_skills` - ale nikt ich nigdy
nie zsumował.

Próg ma znaczenie i dlatego nie da się go pominąć. Bez filtra po dopasowaniu na
czoło wychodzi „wykształcenie medyczne" i „uprawnienia SEP" - braki prawdziwe,
tylko względem ofert, których i tak nikt nie tknie. Dopiero powyżej ~50% zostają
oferty, do których dzieli Cię jedna umiejętność.

Uruchomienie:
    python skill_gaps.py                     # próg 50%, 25 pozycji
    python skill_gaps.py --threshold 40      # szerzej: więcej danych, więcej szumu
    python skill_gaps.py --json              # do interfejsu
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.console import force_utf8
from utils.links import canonical_link
from utils.safe_io import load_json_safe

ANALYZED_FILE = "analyzed_jobs_waterfall.json"
DECISIONS_FILE = "user_decisions.json"

# Oceny, po których ofertę uznajemy za odrzuconą przez użytkownika. Jej braki nie
# mówią nic o kierunku nauki - to droga, którą już świadomie odrzucił.
REJECTED_RATING = 3


def dismissed_links() -> set:
    """Linki ofert, które użytkownik odrzucił - w obu formatach decyzji naraz."""
    out = set()
    for link, value in (load_json_safe(DECISIONS_FILE, default={}) or {}).items():
        # Stary format to goły string ("reject"), nowy to słownik ze statusem i oceną
        if isinstance(value, str):
            if value == "reject":
                out.add(canonical_link(link))
        elif isinstance(value, dict):
            rating = value.get("rating")
            if value.get("status") == "reject" or (rating is not None and rating <= REJECTED_RATING):
                out.add(canonical_link(link))
    return out


def normalize(skill: str) -> str:
    """
    Sprowadź zapis umiejętności do postaci, w której da się je zliczać.

    Celowo tylko wielkość liter, białe znaki i interpunkcja na końcu. Model
    zwraca też całe zdania („brak doświadczenia w sprzedaży ubezpieczeń") i
    kusi, żeby je skracać regułami - ale każda taka reguła sklejałaby rzeczy
    różne (SQL i „SQL na poziomie zaawansowanym" to nie to samo zadanie) i
    wynik przestałby dać się sprawdzić z ofertą w ręku.
    """
    text = re.sub(r"\s+", " ", str(skill or "")).strip().strip(".,;:-–—")
    return text.casefold()


def collect(analyzed, threshold: int, skip: set) -> tuple[dict, int, int]:
    """
    Zwróć (braki, ile ofert nad progiem, ile z nich pominięto jako odrzucone).

    Liczniki wychodzą stąd, a nie z osobnej pętli w main: „pominięto 1145" liczone
    po całym pliku brzmiałoby jak informacja o tym raporcie, a dotyczyłoby ofert,
    których ten raport i tak nigdy nie oglądał.
    """
    gaps = defaultdict(lambda: {"label": None, "count": 0, "scores": [], "learnable": 0})
    considered = skipped = 0

    for entry in analyzed:
        score = entry.get("match_percentage")
        if score is None or score < threshold:
            continue
        considered += 1

        link = canonical_link(((entry.get("job") or {}).get("link")) or "")
        if link in skip:
            skipped += 1
            continue

        for raw in entry.get("missing_skills") or []:
            key = normalize(raw)
            if not key:
                continue
            gap = gaps[key]
            # Pierwsze napotkane brzmienie zostaje etykietą - w raporcie ma stać
            # to, co naprawdę napisał model, nie wersja po naszej normalizacji.
            gap["label"] = gap["label"] or str(raw).strip()
            gap["count"] += 1
            gap["scores"].append(score)
            if entry.get("learnable_in_month"):
                gap["learnable"] += 1

    return gaps, considered, skipped


def rank(gaps: dict, top: int) -> list:
    """Posortuj braki: najpierw częstość, przy remisie średnie dopasowanie ofert."""
    rows = []
    for gap in gaps.values():
        scores = gap["scores"]
        rows.append({
            "skill": gap["label"],
            "count": gap["count"],
            "mean_match": round(sum(scores) / len(scores), 1),
            "max_match": max(scores),
            "learnable": gap["learnable"],
        })
    rows.sort(key=lambda r: (-r["count"], -r["mean_match"]))
    return rows[:top]


def bar(value, top_value, width=22):
    filled = int(round(width * value / top_value)) if top_value else 0
    return "█" * filled + "·" * (width - filled)


def main(argv=None):
    # argv=[] przy wywołaniu z innego skryptu - bez tego argparse czyta sys.argv
    ap = argparse.ArgumentParser(description="Najczęstsze braki w dobrze dopasowanych ofertach")
    ap.add_argument("--threshold", type=int, default=50,
                    help="Od jakiego dopasowania liczyć ofertę (domyślnie 50%%)")
    ap.add_argument("--top", type=int, default=25, help="Ile pozycji pokazać")
    ap.add_argument("--json", action="store_true", help="Wypisz wynik jako JSON")
    args = ap.parse_args(argv)

    analyzed = load_json_safe(ANALYZED_FILE, default=[])
    if not analyzed:
        print("Brak wyników analizy - najpierw uruchom pipeline.")
        return 1

    skip = dismissed_links()
    gaps, considered, skipped = collect(analyzed, args.threshold, skip)
    rows = rank(gaps, args.top)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("=" * 64)
    print(f"  BRAKI W OFERTACH OD {args.threshold}% DOPASOWANIA")
    print("=" * 64)
    print(f"Ofert powyżej progu:        {considered}")
    print(f"Pominiętych jako odrzucone: {skipped}")
    print(f"Różnych braków:             {len(gaps)}")

    if not rows:
        print("\nNic do pokazania - obniż próg albo oceń więcej ofert.")
        return 1

    print()
    top_value = rows[0]["count"]
    print(f"{'ile ofert':>9} | {'śr. dopas.':>10} | umiejętność")
    print("-" * 64)
    for row in rows:
        print(f"{row['count']:>9} | {row['mean_match']:>9.1f}% | "
              f"{bar(row['count'], top_value)} {row['skill']}")
    print("-" * 64)
    print("\nJak to czytać:")
    print("  Pozycja wysoko = tyle ofert, które poza tym pasują, odpada na tej jednej rzeczy.")
    print("  Średnie dopasowanie mówi, jak blisko były te oferty - im wyżej, tym mniej brakuje.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    force_utf8()
    sys.exit(main())
