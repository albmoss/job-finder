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


def test_model_rotation():
    print("\n[6] Model and key rotation")
    import waterfall_analysis as wa

    models, keys = wa.MODELS, wa.API_KEYS
    try:
        wa.MODELS = ["m0", "m1", "m2"]
        wa.API_KEYS = ["k0", "k1"]

        pairs = list(wa._rotation(0, 0))
        check("rotation visits every model x key pair once",
              len(pairs) == 6 and len(set(pairs)) == 6, str(pairs))
        check("keys are exhausted before the model is downgraded",
              [m for m, _ in pairs] == [0, 0, 1, 1, 2, 2], str(pairs))

        resumed = list(wa._rotation(1, 1))
        check("rotation resumes from the pair that last worked",
              resumed[0] == (1, 1), str(resumed[:2]))
        check("after a downgrade the key pool restarts at 0",
              resumed[1] == (1, 0), str(resumed[:3]))
        check("rotation terminates instead of looping",
              len(resumed) == 6 and len(set(resumed)) == 6, str(resumed))

        wa.MODELS, wa.API_KEYS = ["only"], ["single"]
        check("a single model and key still terminate",
              list(wa._rotation(0, 0)) == [(0, 0)])
    finally:
        wa.MODELS, wa.API_KEYS = models, keys

    # Rozróżnienie decyduje, czy szukamy innego klucza, czy dzielimy batch
    check("429 is read as a rate limit", wa._classify("429 RESOURCE_EXHAUSTED") == "rate_limit")
    check("truncated JSON asks for a smaller batch",
          wa._classify("Unterminated string starting at") == "truncated")
    check("an unknown error is neither", wa._classify("connection reset") == "other")


def test_api_keys_configured():
    print("\n[7] API key pool")
    from config import GEMINI_API_KEY, GEMINI_API_KEYS

    # Pusta pula oznaczała, że pętla po kluczach nie wykonywała się ani razu,
    # każdy batch kończył się "porażką na wszystkich modelach", a etap analizy
    # spał po 5 minut i próbował w nieskończoność.
    check("at least one Gemini key is configured", bool(GEMINI_API_KEYS),
          "uzupełnij GEMINI_API_KEY_PRIMARY w .env")
    check("the primary key is part of the rotation",
          not GEMINI_API_KEY or GEMINI_API_KEY in GEMINI_API_KEYS)
    check("no empty entries in the pool", all(GEMINI_API_KEYS))
    check("no duplicate keys in the pool",
          len(set(GEMINI_API_KEYS)) == len(GEMINI_API_KEYS))


def test_record_scrape(tmp_name="_test_record_scrape.json"):
    print("\n[8] Recording a scrape run")
    from utils.data_models import Job, JobDatabase

    path = ROOT / tmp_name

    def job(link, **kw):
        return Job(title="Tytuł", company="Firma", link=link,
                   description="Opis oferty wystarczająco długi, żeby przeszedł.",
                   source="test", **kw)

    def on_disk():
        return load_json_safe(path, default=[])

    try:
        db = JobDatabase(str(path))

        added, touched = db.record_scrape([job("https://a.pl/1"), job("https://a.pl/2")])
        check("new offers are appended", (added, touched) == (2, 0), f"{added}, {touched}")

        added, _ = db.record_scrape([job("https://a.pl/1")])
        check("a known offer is not duplicated", added == 0 and len(on_disk()) == 2)

        rec = [r for r in on_disk() if r["link"] == "https://a.pl/1"][0]
        # Bez tego zapisu sygnał "wisi od X dni" nigdy by nie ruszył - dla
        # źródeł bez posted_date to jedyna miara wieku oferty.
        check("re-scraping a known offer bumps times_seen",
              rec["times_seen"] == 2, str(rec["times_seen"]))

        _, touched = db.record_scrape([], ["https://a.pl/2"])
        check("seen_again refreshes an offer whose page was skipped", touched == 1)

        added, touched = db.record_scrape([], ["https://a.pl/brak", "", None])
        check("unknown links create no phantom records",
              (added, touched) == (0, 0) and len(on_disk()) == 2)

        added, touched = db.record_scrape([job("https://a.pl/3")], ["https://a.pl/1"])
        check("one call handles new offers and re-seen ones together",
              (added, touched) == (1, 1) and len(on_disk()) == 3, f"{added}, {touched}")

        db.record_scrape([job("https://a.pl/3", posted_date="2026-08-01")])
        db.record_scrape([job("https://a.pl/3", posted_date="2026-01-01")])
        rec = [r for r in on_disk() if r["link"] == "https://a.pl/3"][0]
        check("a missing date is filled in, an existing one is left alone",
              rec["posted_date"] == "2026-08-01", str(rec["posted_date"]))

        db.record_scrape([job("https://a.pl/1?utm_source=x")])
        check("a tracking parameter does not create a second record",
              len(on_disk()) == 3, str(len(on_disk())))
    finally:
        for p in (path, Path(str(path) + ".tmp")):
            if p.exists():
                p.unlink()
        bdir = ROOT / "backups"
        if bdir.exists():
            for stale in bdir.glob(f"{tmp_name}.*.bak"):
                stale.unlink()


def main():
    print("=" * 62)
    print("  INTEGRATION TESTS (no API calls)")
    print("=" * 62)

    for test in (test_canonical_link, test_text_cleaning, test_stale_detection,
                 test_safe_io, test_data_consistency, test_model_rotation,
                 test_api_keys_configured, test_record_scrape):
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
