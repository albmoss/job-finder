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
    print("\n[1] Link normalisation")
    check("strips tracking parameters",
          canonical_link("https://www.olx.pl/oferta/x-ID1.html?search_reason=search%7Corganic")
          == "https://www.olx.pl/oferta/x-ID1.html")
    check("strips fragment",
          canonical_link("https://a.pl/of/1#opis") == "https://a.pl/of/1")
    check("strips trailing slash",
          canonical_link("https://a.pl/of/1/") == "https://a.pl/of/1")
    check("keeps query holding the offer ID",
          canonical_link("https://a.pl/job?id=55") == "https://a.pl/job?id=55")
    check("is idempotent",
          canonical_link(canonical_link("https://a.pl/x?utm=1")) == canonical_link("https://a.pl/x?utm=1"))
    check("handles empty input", canonical_link("") == "")


def test_text_cleaning():
    print("\n[2] Description cleanup")
    html = "<p><strong>Obowiązki:</strong></p><ul><li>Praca&nbsp;z klientem</li><li>Excel</li></ul>"
    out = strip_html(html)
    check("removes HTML tags", "<" not in out and ">" not in out, out)
    check("decodes entities", "\xa0" not in out and "&nbsp;" not in out, out)
    check("preserves content", "Obowiązki" in out and "Excel" in out, out)
    check("removes script", "alert" not in strip_html("<div>ok</div><script>alert(1)</script>"))
    check("plain text left untouched", strip_html("Zwykły opis") == "Zwykły opis")
    check("handles None/empty", strip_html("") == "" and clean_job_description("") == "")


def test_stale_detection():
    print("\n[3] Stale analysis detection")
    import waterfall_analysis as wa

    job = {"title": "Junior Dev", "description": "Opis oferty " * 20}
    fingerprint = wa.description_fingerprint(job)

    fresh = {"_description_hash": fingerprint, "_profile_version": "v3@2026"}
    check("fresh result is not rescored",
          not wa.is_stale(fresh, job, "v3@2026"))

    job_changed = dict(job, description="Zupełnie nowy, wzbogacony opis oferty")
    check("description change invalidates result",
          wa.is_stale(fresh, job_changed, "v3@2026"))

    check("newer profile invalidates result",
          wa.is_stale(fresh, job, "v4@2026"))

    check("legacy entry without stamps counts as fresh",
          not wa.is_stale({}, job, "v3@2026"))


def test_safe_io(tmp_name="_test_safe_io.json"):
    print("\n[4] Safe writes")
    path = ROOT / tmp_name
    try:
        data = {"a": 1, "ą": "ę"}
        check("atomic write reports success", save_json_atomic(path, data) is True)
        check("read returns the same data", load_json_safe(path) == data)
        check("leaves no .tmp file behind", not path.with_suffix(".json.tmp").exists())

        path.write_text("{uszkodzony json", encoding="utf-8")
        recovered = load_json_safe(path, default={"fallback": True})
        check("corrupt file does not crash the app", isinstance(recovered, dict))

        check("missing file returns the default",
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
    print("\n[5] On-disk data consistency")
    jobs = load_json_safe(ROOT / "jobs_database.json", default=[])
    decisions = load_json_safe(ROOT / "user_decisions.json", default={})

    if not jobs:
        print("  jobs_database.json missing - skipping")
        return

    links = [j.get("link", "") for j in jobs]
    check("no duplicate links in the database",
          len(links) == len(set(links)), f"{len(links) - len(set(links))} duplikatów")
    check("all links are in canonical form",
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
        print(f"  manual ratings: {len(rated)}, outside database: {len(orphans)}, "
              f"in archive: {len(orphans) - len(truly_lost)}")
        check("decision keys are canonical",
              all(canonical_link(k) == k for k in decisions),
              f"{sum(1 for k in decisions if canonical_link(k) != k)} nieznormalizowanych")


def main():
    print("=" * 62)
    print("  INTEGRATION TESTS (no API calls)")
    print("=" * 62)

    for test in (test_canonical_link, test_text_cleaning, test_stale_detection,
                 test_safe_io, test_data_consistency):
        try:
            test()
        except Exception as e:
            FAILED.append(test.__name__)
            print(f"  {test.__name__} raised an exception: {e}")

    print("\n" + "=" * 62)
    print(f"  PASSED: {len(PASSED)}   FAILED: {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print(f"    - {f}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
