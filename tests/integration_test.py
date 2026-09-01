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


def test_scraper_health():
    print("\n[9] Silent scraper failure")
    from utils import scraper_health as health

    history = [{"date": "2026-08-1%d" % i, "count": c}
               for i, c in enumerate([1200, 1100, 1250, 1180, 1300])]
    ok_job = {"title": "Kucharz", "company": "Bar Mleczny",
              "description": "opis", "link": "https://praca.pl/of/1"}

    check("zero results after a productive history is a failure",
          health.check("praca.pl", 0, history=history)["verdict"] == "broken")
    check("a run far below the median is flagged",
          health.check("praca.pl", 12, jobs=[ok_job], history=history)["verdict"] == "weak")
    check("a healthy run says nothing",
          health.check("praca.pl", 1200, jobs=[ok_job] * 3, history=history,
                       domain="praca.pl") is None)
    check("a young source is not judged against a median it has not got",
          health.check("nowy.pl", 3, jobs=[ok_job], history=history[:2]) is None)

    empty_company = dict(ok_job, company="")
    check("an empty field in every record is a broken parser",
          health.check("praca.pl", 200, jobs=[empty_company] * 3)["verdict"] == "degraded")
    check("a single record without a company is not",
          health.check("praca.pl", 1200, jobs=[ok_job, ok_job, empty_company],
                       history=history) is None)
    check("undecoded entities in the title are caught",
          "HTML" in health.check("praca.pl", 200,
                                 jobs=[dict(ok_job, title="Kucharz &amp; pomoc")] * 3)["detail"])
    check("links leaving the portal's domain are caught",
          health.check("praca.pl", 200, jobs=[dict(ok_job, link="https://reklama.example/x")] * 3,
                       domain="praca.pl")["verdict"] == "degraded")

    check("a rate limit is never read as breakage",
          health.check("Indeed", 0, error="HTTP 429 Too Many Requests")["verdict"] == "inconclusive")
    check("a parser exception is",
          health.check("Indeed", 0, error="AttributeError: NoneType")["verdict"] == "broken")

    check("a healthy run produces no report at all",
          health.format_report([]) == "")
    check("a source skipped on purpose is still reported",
          "Adzuna" in health.format_report([], skipped_no_key=["Adzuna"]))

    check("dociaganie opisow bez ani jednego opisu to awaria",
          (health.check("OLX Praca", 1272,
                        enrich={"ok": 0, "expired": 0, "error": 0, "blocked": 1272}) or {})
          .get("verdict") == "broken")
    check("403 na wszystkich podstronach jest nazwane wprost",
          "403" in (health.check("OLX Praca", 1272,
                                 enrich={"ok": 0, "expired": 0, "error": 0, "blocked": 9}) or {})
          .get("detail", ""))
    check("czesc opisow pobrana - to nie awaria",
          health.check("OLX Praca", 1272,
                       enrich={"ok": 900, "expired": 10, "error": 362, "blocked": 0}) is None)
    check("brak statystyk opisow niczego nie psuje",
          health.check("OLX Praca", 1272, enrich=None) is None)

    # Warunek pytal wylacznie o zero, wiec 24 opisy na 1137 blokad przechodzily
    # bez jednej linijki w raporcie - dokladnie ta cicha awaria, przed ktora
    # ten modul mial chronic.
    check("przewaga blokad to awaria, nawet gdy czesc opisow wrocila",
          (health.check("OLX Praca", 1338,
                        enrich={"ok": 24, "expired": 0, "error": 3, "blocked": 1137}) or {})
          .get("verdict") == "broken")
    check("raport podaje, ile zapytan odrzucono",
          "1137 z 1164" in (health.check("OLX Praca", 1338,
                                         enrich={"ok": 24, "expired": 0, "error": 3,
                                                 "blocked": 1137}) or {}).get("detail", ""))
    check("pojedyncze blokady nie robia alarmu",
          health.check("OLX Praca", 1272,
                       enrich={"ok": 900, "expired": 0, "error": 10, "blocked": 40}) is None)


def test_idempotent_writes(tmp_name="_test_idempotent.json"):
    """
    Etapy bazodanowe nie przepisują pliku, gdy nie mają czego zmienić.

    Przed poprawką `clean_db`, `migrate_normalize_links` i `deduplicate_db`
    zapisywały oba pliki (63,7 MB) razem z kopią zapasową przy KAŻDYM
    przebiegu, także wtedy, gdy liczba zmian wynosiła zero - czyli ~380 MB
    ruchu na dysku po to, żeby odtworzyć pliki bajt w bajt.
    """
    print("\n[10] Zapis tylko przy realnej zmianie")
    import clean_db
    import deduplicate_db
    import migrate_normalize_links as migrate

    path = ROOT / tmp_name
    bdir = ROOT / "backups"

    def kopie():
        return len(list(bdir.glob(f"{tmp_name}.*.bak"))) if bdir.exists() else 0

    # Rekordy już czyste i już znormalizowane - nie ma czego poprawiać.
    czyste = [{"link": "https://a.pl/of/1", "title": "A", "company": "F",
               "location": "Warszawa", "description": "Opis oferty bez smieci."},
              {"link": "https://a.pl/of/2", "title": "B", "company": "G",
               "location": "Kraków", "description": "Drugi opis, tez czysty."}]
    try:
        save_json_atomic(path, czyste)
        przed = kopie()

        clean_db.reduce_file(path)
        check("clean_db nie przepisuje juz czystego pliku", kopie() == przed,
              f"kopii przybylo: {kopie() - przed}")

        stare_jobs = migrate.JOBS_DB
        migrate.JOBS_DB = str(path)
        try:
            migrate.migrate_jobs()
        finally:
            migrate.JOBS_DB = stare_jobs
        check("migrate nie przepisuje znormalizowanych linkow", kopie() == przed,
              f"kopii przybylo: {kopie() - przed}")

        keep = {canonical_link(j["link"]) for j in czyste}
        deduplicate_db._apply(path, False, keep, {}, set())
        check("deduplicate nie przepisuje pliku bez duplikatow", kopie() == przed,
              f"kopii przybylo: {kopie() - przed}")

        # A gdy zmiana JEST, zapis ma nastąpić.
        brudne = list(czyste) + [{"link": "https://a.pl/of/1?utm_source=x", "title": "A",
                                  "company": "F", "location": "Warszawa",
                                  "description": "Duplikat tej samej oferty."}]
        save_json_atomic(path, brudne)
        przed = kopie()
        deduplicate_db._apply(path, False, keep, {}, set())
        check("deduplicate zapisuje, gdy duplikat faktycznie jest", kopie() > przed)
        check("duplikat zniknal z pliku", len(load_json_safe(path, default=[])) == 2)
    finally:
        for f in (path, Path(str(path) + ".tmp")):
            if f.exists():
                f.unlink()
        if bdir.exists():
            for stale in bdir.glob(f"{tmp_name}.*.bak"):
                stale.unlink()


def test_olx_tempo_przy_blokadzie():
    """
    Odstęp między wejściami musi obowiązywać także wtedy, gdy portal odmawia.

    `time.sleep(OPIS_PRZERWA)` stał na końcu pętli, za wszystkimi `continue`,
    więc każde 403 pomijało przerwę. Pierwsza blokada kasowała odstęp, kolejne
    wejścia szły ~20 razy na sekundę i blokada się utrwalała: przebieg
    z 1 września 2026 zrobił 1164 zapytania w 58 s i skończył na 1137 blokadach.
    Odstęp działał tylko tam, gdzie nie był potrzebny.
    """
    print(chr(10) + "[11] OLX: tempo dociagania opisow")

    import scrapers.olx_scraper as olx
    from utils.data_models import Job

    class FikcyjnaOdpowiedz:
        def __init__(self, status):
            self.status = status

    class FikcyjnaStrona:
        """Zawsze 403 - dokładnie ten stan, który kasował przerwę."""
        def __init__(self):
            self.wejscia = 0

        def route(self, *a, **k):
            pass

        def unroute(self, *a, **k):
            pass

        def goto(self, url, **k):
            self.wejscia += 1
            return FikcyjnaOdpowiedz(403)

        def content(self):
            return ""

    drzemki = []
    prawdziwy_sleep = olx.time.sleep
    prawdziwe_known = None
    import utils.known_links as kl
    prawdziwe_known = kl.known_links
    try:
        olx.time.sleep = lambda s: drzemki.append(s)
        kl.known_links = lambda: set()

        scraper = object.__new__(olx.OLXScraper)
        scraper.page = FikcyjnaStrona()
        scraper.timeout = 1000
        scraper.seen_again_links = []

        oferty = [Job(title=f"t{i}", company="c", location="Warszawa",
                      link=f"https://www.olx.pl/oferta/x-ID{i}.html",
                      description="x", source="OLX Praca")
                  for i in range(40)]
        scraper.enrich_descriptions(oferty)
    finally:
        olx.time.sleep = prawdziwy_sleep
        kl.known_links = prawdziwe_known

    wejscia = scraper.page.wejscia
    check("przerwa nie jest pomijana przy 403",
          len(drzemki) >= wejscia - 1,
          f"{len(drzemki)} drzemek na {wejscia} wejsc")
    check("odstep rosnie po kolejnych blokadach",
          len(drzemki) > 1 and drzemki[-1] > drzemki[0],
          f"{drzemki[0]} -> {drzemki[-1]}")
    check("odstep nie przekracza sufitu",
          all(d <= olx.BLOKADA_SUFIT for d in drzemki))
    check("po serii blokad dociaganie sie przerywa",
          wejscia <= olx.BLOKADY_LIMIT,
          f"{wejscia} wejsc przy limicie {olx.BLOKADY_LIMIT}")
    check("nie probuje wszystkich 40 ofert",
          wejscia < 40, f"wejsc: {wejscia}")



def test_zdjete_z_portalu():
    """
    Oferta pominięta przez najnowszy przebieg swojego źródła jest zdjęta.

    `purge_stale_offers` patrzy wyłącznie na wiek, więc oferta zdjęta z portalu
    trzy dni po zescrapowaniu zostawała w bazie razem ze swoją oceną. 1 września
    2026 połowa pierwszej dziesiątki „Dopasowanych" była martwa. Sprawdzone
    wtedy na dwóch portalach: 18 z 18 ofert pominiętych przez ostatni przebieg
    było zdjętych, 18 z 18 widzianych - żywych.
    """
    print(chr(10) + "[12] Oferty zdjete z portalu")

    from utils.liveness import zdjete_z_portalu, dni_scrapowania

    baza = [
        {"link": "a", "source": "X", "last_seen": "2026-09-01T10:00:00"},
        {"link": "b", "source": "X", "last_seen": "2026-08-23T10:00:00"},
        {"link": "c", "source": "X", "last_seen": None},
        {"link": "d", "source": "Y", "last_seen": "2026-08-23T10:00:00"},
        {"link": "e", "source": "Z", "last_seen": None},
    ]
    zdjete = zdjete_z_portalu(baza)

    check("oferta z ostatniego przebiegu zostaje", "a" not in zdjete)
    check("oferta pominieta przez nowszy przebieg jest zdjeta", "b" in zdjete)
    check("brak last_seen przy historii zrodla tez liczy sie jako zdjeta",
          "c" in zdjete)
    check("zrodlo z jednym przebiegiem nie kasuje wlasnych ofert",
          "d" not in zdjete, "Y ma tylko 2026-08-23")
    check("zrodlo bez ani jednego stempla nie da sie ocenic",
          "e" not in zdjete)

    dni = dni_scrapowania(baza)
    check("dni scrapowania czytane z ofert, nie z historii scrapera",
          dni["X"] == {"2026-09-01", "2026-08-23"} and dni["Y"] == {"2026-08-23"},
          str(dict(dni)))
    check("zrodlo bez stempli nie ma wpisu", "Z" not in dni)


def main():
    print("=" * 62)
    print("  INTEGRATION TESTS (no API calls)")
    print("=" * 62)

    for test in (test_canonical_link, test_text_cleaning, test_stale_detection,
                 test_safe_io, test_data_consistency, test_model_rotation,
                 test_api_keys_configured, test_record_scrape, test_scraper_health,
                 test_idempotent_writes, test_olx_tempo_przy_blokadzie,
                 test_zdjete_z_portalu):
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
