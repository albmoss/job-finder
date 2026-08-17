"""
Testy integralności pipeline'u - bez zużywania limitu API.

Poprzednia wersja testowała utils/gemini_client.py, który został zastąpiony przez
waterfall_analysis.py i trafił do _archive/. Te testy sprawdzają rzeczy, które
faktycznie psuły się w praktyce: spójność linków, czyszczenie opisów, wykrywanie
nieaktualnych wyników i zgodność decyzji z bazą.

Uruchomienie:  python tests/integration_test.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic
from utils.text_cleaner import clean_job_description, strip_html

ROOT = Path(__file__).parent.parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'✓' if condition else '✗'} {name}" + (f"  -> {detail}" if detail and not condition else ""))
    return condition


def test_canonical_link():
    print("\n[1] Normalizacja linków")
    check("ucina parametry śledzące",
          canonical_link("https://www.olx.pl/oferta/x-ID1.html?search_reason=search%7Corganic")
          == "https://www.olx.pl/oferta/x-ID1.html")
    check("ucina fragment",
          canonical_link("https://a.pl/of/1#opis") == "https://a.pl/of/1")
    check("ucina końcowy slash",
          canonical_link("https://a.pl/of/1/") == "https://a.pl/of/1")
    check("zachowuje query z ID oferty",
          canonical_link("https://a.pl/job?id=55") == "https://a.pl/job?id=55")
    check("idempotentna",
          canonical_link(canonical_link("https://a.pl/x?utm=1")) == canonical_link("https://a.pl/x?utm=1"))
    check("odporna na pusty input", canonical_link("") == "")


def test_text_cleaning():
    print("\n[2] Czyszczenie opisów")
    html = "<p><strong>Obowiązki:</strong></p><ul><li>Praca&nbsp;z klientem</li><li>Excel</li></ul>"
    out = strip_html(html)
    check("usuwa tagi HTML", "<" not in out and ">" not in out, out)
    check("dekoduje encje", "\xa0" not in out and "&nbsp;" not in out, out)
    check("zachowuje treść", "Obowiązki" in out and "Excel" in out, out)
    check("usuwa script", "alert" not in strip_html("<div>ok</div><script>alert(1)</script>"))
    check("tekst bez HTML nietknięty", strip_html("Zwykły opis") == "Zwykły opis")
    check("obsługuje None/pusty", strip_html("") == "" and clean_job_description("") == "")


def test_stale_detection():
    print("\n[3] Wykrywanie nieaktualnych wyników analizy")
    import waterfall_analysis as wa

    job = {"title": "Junior Dev", "description": "Opis oferty " * 20}
    fingerprint = wa.description_fingerprint(job)

    fresh = {"_description_hash": fingerprint, "_profile_version": "v3@2026"}
    check("aktualny wynik nie jest przeliczany",
          not wa.is_stale(fresh, job, "v3@2026"))

    job_changed = dict(job, description="Zupełnie nowy, wzbogacony opis oferty")
    check("zmiana opisu unieważnia wynik",
          wa.is_stale(fresh, job_changed, "v3@2026"))

    check("nowszy profil unieważnia wynik",
          wa.is_stale(fresh, job, "v4@2026"))

    check("stary wpis bez stempli traktowany jako aktualny",
          not wa.is_stale({}, job, "v3@2026"))


def test_safe_io(tmp_name="_test_safe_io.json"):
    print("\n[4] Bezpieczny zapis")
    path = ROOT / tmp_name
    try:
        data = {"a": 1, "ą": "ę"}
        check("zapis atomowy zwraca sukces", save_json_atomic(path, data) is True)
        check("odczyt zwraca to samo", load_json_safe(path) == data)
        check("nie zostawia pliku .tmp", not path.with_suffix(".json.tmp").exists())

        path.write_text("{uszkodzony json", encoding="utf-8")
        recovered = load_json_safe(path, default={"fallback": True})
        check("uszkodzony plik nie wywala aplikacji", isinstance(recovered, dict))

        check("brakujący plik zwraca default",
              load_json_safe(ROOT / "_nie_istnieje_.json", default={"d": 1}) == {"d": 1})
    finally:
        for p in (path, path.with_suffix(".json.tmp")):
            if p.exists():
                p.unlink()
        bdir = ROOT / "backups"
        if bdir.exists():
            for stale in bdir.glob(f"{tmp_name}.*.bak"):
                stale.unlink()


def test_data_consistency():
    print("\n[5] Spójność danych na dysku")
    jobs = load_json_safe(ROOT / "jobs_database.json", default=[])
    decisions = load_json_safe(ROOT / "user_decisions.json", default={})

    if not jobs:
        print("  ⚠ brak jobs_database.json - pomijam")
        return

    links = [j.get("link", "") for j in jobs]
    check("brak duplikatów linków w bazie",
          len(links) == len(set(links)), f"{len(links) - len(set(links))} duplikatów")
    check("wszystkie linki w postaci kanonicznej",
          all(canonical_link(l) == l for l in links),
          f"{sum(1 for l in links if canonical_link(l) != l)} nieznormalizowanych")

    if decisions:
        rated = {k: v for k, v in decisions.items()
                 if isinstance(v, dict) and v.get("rating") is not None}
        db_links = set(links)
        orphans = [k for k in rated if k not in db_links]
        # Oceny osieroconych ofert trafiają do rated_archive.json - to nie błąd,
        # ale duży odsetek oznacza, że archiwizacja nie działa.
        archive = load_json_safe(ROOT / "rated_archive.json", default=[])
        archived = {canonical_link((a.get("job") or {}).get("link", "")) for a in archive}
        truly_lost = [k for k in orphans if k not in archived]
        print(f"  ℹ ocen ręcznych: {len(rated)}, poza bazą: {len(orphans)}, "
              f"w archiwum: {len(orphans) - len(truly_lost)}")
        check("klucze decyzji są kanoniczne",
              all(canonical_link(k) == k for k in decisions),
              f"{sum(1 for k in decisions if canonical_link(k) != k)} nieznormalizowanych")


def main():
    print("=" * 62)
    print("  TESTY INTEGRACYJNE (bez wywołań API)")
    print("=" * 62)

    for test in (test_canonical_link, test_text_cleaning, test_stale_detection,
                 test_safe_io, test_data_consistency):
        try:
            test()
        except Exception as e:
            FAILED.append(test.__name__)
            print(f"  ✗ {test.__name__} rzucił wyjątek: {e}")

    print("\n" + "=" * 62)
    print(f"  ZALICZONE: {len(PASSED)}   NIEZALICZONE: {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print(f"    ✗ {f}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
