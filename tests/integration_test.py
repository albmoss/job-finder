"""
Testy integralności pipeline'u - bez zużywania limitu API.

Sprawdzają rzeczy, które faktycznie psuły się w praktyce: spójność linków,
czyszczenie opisów, wykrywanie nieaktualnych wyników, rotację modeli i kluczy,
zapisy na dysk, scrapery i kontrakt HTTP interfejsu. Wszystkie pliki robocze
powstają w katalogach tymczasowych - prywatne dane w katalogu projektu nie są czytane.

Uruchomienie:  python tests/integration_test.py
"""

import json
import os
import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
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




def test_safe_io():
    print("\n[4] Safe writes")
    tmp = Path(tempfile.mkdtemp(prefix="test_safe_io_"))
    path = tmp / "safe_io.json"
    try:
        data = {"a": 1, "ą": "ę"}
        check("atomic write reports success", save_json_atomic(path, data) is True)
        check("read returns the same data", load_json_safe(path) == data)
        check("leaves no .tmp file behind", not path.with_suffix(".json.tmp").exists())

        path.write_text("{uszkodzony json", encoding="utf-8")
        recovered = load_json_safe(path, default={"fallback": True})
        check("corrupt file does not crash the app", isinstance(recovered, dict))

        check("missing file returns the default",
              load_json_safe(tmp / "brak.json", default={"d": 1}) == {"d": 1})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)




def test_llm_providers():
    """
    Pula kluczy i rodzaje błędów dostawców modeli. Od rodzaju błędu zależy reakcja
    kaskady: limit albo zły klucz -> następny klucz, urwany JSON -> podział paczki.
    """
    print("\n[6] Model providers: key pool and error kinds")
    from pydantic import BaseModel
    from utils import llm

    # Pusta pula oznaczała, że pętla po kluczach nie wykonywała się ani razu,
    # każdy batch kończył się "porażką na wszystkich modelach", a etap analizy
    # spał po 5 minut i próbował w nieskończoność.
    s = llm.settings_from_env({"GEMINI_API_KEY_PRIMARY": "g-glowny"})
    check("the primary key alone makes a usable pool",
          s.api_keys == ["g-glowny"] and not s.error, str(s.api_keys))
    s = llm.settings_from_env({"LLM_PROVIDER": "Anthropic", "GEMINI_API_KEY_PRIMARY": "g",
                               "ANTHROPIC_API_KEY": "a-1", "ANTHROPIC_API_KEY_1": "a-1",
                               "ANTHROPIC_API_KEY_2": "YOUR_KEY_HERE", "ANTHROPIC_API_KEY_3": "a-2"})
    check("the pool holds only the chosen provider's keys, no duplicates or placeholders",
          s.api_keys == ["a-1", "a-2"], str(s.api_keys))
    s = llm.settings_from_env({"LLM_PROVIDER": "opneai", "GEMINI_API_KEY_PRIMARY": "g"})
    check("a typo in LLM_PROVIDER sends nothing to another provider",
          s.api_keys == [] and "opneai" in s.error, str(s.api_keys))
    s = llm.settings_from_env({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k",
                               "OPENAI_MODELS": "qwen3, qwen3,llama4"})
    check("custom models replace the defaults for scoring and the profile",
          s.models == ["qwen3", "llama4"], str(s.models))

    check("Gemini 429 is a rate limit", llm._classify_gemini("429 RESOURCE_EXHAUSTED") == "rate_limit")
    check("Gemini rejected key moves on to the next key",
          llm._classify_gemini("400 INVALID_ARGUMENT. {'reason': 'API_KEY_INVALID'}") == "invalid_key")
    check("truncated JSON asks for a smaller batch",
          llm._classify_gemini("Unterminated string starting at") == "truncated")
    check("an unknown error is none of these", llm._classify_gemini("connection reset") == "other")

    class Ocena(BaseModel):
        id: int
        match_percentage: int

    class Odp:
        def __init__(self, status, payload):
            self.status_code, self._payload = status, payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    kolejka, wyslane = [], []

    def fake_post(url, headers, body):
        wyslane.append(body)
        return kolejka.pop(0)

    def rodzaj(settings, *odpowiedzi):
        kolejka[:] = odpowiedzi
        try:
            llm.ask_json(settings, "m", "k", "prompt", Ocena)
        except llm.LLMError as e:
            return e.kind
        return "ok"

    openai = llm.settings_from_env({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k",
                                    "OPENAI_BASE_URL": "http://test.invalid/v1"})
    anthropic = llm.settings_from_env({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"})
    lista = [{"id": 1, "match_percentage": 80}]
    ok_openai = {"choices": [{"finish_reason": "stop",
                              "message": {"content": json.dumps({"evaluations": lista})}}]}
    ok_anthropic = {"stop_reason": "end_turn",
                    "content": [{"type": "text", "text": json.dumps({"evaluations": lista})}]}
    prawdziwy_post = llm._post
    try:
        llm._post = fake_post
        for settings, limit in ((openai, 429), (anthropic, 529)):
            nazwa = settings.provider.id
            check(f"{nazwa}: HTTP {limit} is a rate limit", rodzaj(settings, Odp(limit, {})) == "rate_limit")
            check(f"{nazwa}: HTTP 401 is a rejected key", rodzaj(settings, Odp(401, {})) == "invalid_key")
        check("openai: output cut at the token limit asks for a smaller batch",
              rodzaj(openai, Odp(200, {"choices": [{"finish_reason": "length",
                                                    "message": {"content": '{"evalu'}}]})) == "truncated")
        check("anthropic: output cut at the token limit asks for a smaller batch",
              rodzaj(anthropic, Odp(200, {"stop_reason": "max_tokens", "content": []})) == "truncated")
        kolejka[:] = [Odp(200, ok_anthropic)]
        check("anthropic: the wrapped reply is unwrapped into the list",
              llm.ask_json(anthropic, "m", "k", "prompt", Ocena).data == lista)

        # Serwer bez json_schema (np. DeepSeek) dostaje json_object - i odrzucony
        # tryb nie jest wysyłany ponownie przy każdej kolejnej paczce.
        kolejka[:] = [Odp(400, {"error": "response_format json_schema is not supported"}),
                      Odp(200, ok_openai)]
        wynik = llm.ask_json(openai, "m", "k", "prompt", Ocena).data
        check("openai: a server without json_schema still returns the list", wynik == lista, str(wynik))
        kolejka[:] = [Odp(200, ok_openai)]
        wyslane.clear()
        llm.ask_json(openai, "m", "k", "prompt", Ocena)
        check("the rejected schema mode is not retried on the next batch",
              [b["response_format"]["type"] for b in wyslane] == ["json_object"], str(wyslane))
    finally:
        llm._post = prawdziwy_post
        llm._json_object_only.discard((openai.base_url, "m"))

def test_record_scrape():
    print("\n[7] Recording a scrape run")
    from utils.data_models import Job, JobDatabase

    tmp = Path(tempfile.mkdtemp(prefix="test_record_scrape_"))
    path = tmp / "jobs.json"

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
        shutil.rmtree(tmp, ignore_errors=True)


def test_scraper_health():
    print("\n[8] Silent scraper failure")
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
          health.check("praca.pl", 200,
                       jobs=[dict(ok_job, title="Kucharz &amp; pomoc")] * 3)["verdict"] == "degraded")
    check("links leaving the portal's domain are caught",
          health.check("praca.pl", 200, jobs=[dict(ok_job, link="https://reklama.example/x")] * 3,
                       domain="praca.pl")["verdict"] == "degraded")

    # NoFluffJobs API oddaje tytuł z encjami; przebieg z 25.09 padł na tym w fazie 1.
    from dataclasses import asdict
    from scrapers.nofluff_scraper import NoFluffScraper
    nfj = NoFluffScraper.__new__(NoFluffScraper)._parse_posting(
        {"title": "IT Systems &amp; Infrastructure Administrator", "name": "Kowalski &amp; Syn",
         "url": "it-admin-kowalski-warszawa"}, {})
    check("NoFluffJobs titles and companies arrive decoded",
          (nfj.title, nfj.company) == ("IT Systems & Infrastructure Administrator", "Kowalski & Syn"),
          (nfj.title, nfj.company))
    check("so the health check has nothing to flag",
          health.check("NoFluffJobs", 38, jobs=[asdict(nfj)] * 3,
                       domain="nofluffjobs.com") is None)

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
    check("pojedyncze blokady nie robia alarmu",
          health.check("OLX Praca", 1272,
                       enrich={"ok": 900, "expired": 0, "error": 10, "blocked": 40}) is None)
    check("opisy ze stanu chronia przed falszywym alarmem awarii",
          health.check("OLX Praca", 423,
                       enrich={"ok": 0, "expired": 0, "error": 3, "blocked": 0, "ze_stanu": 420}) is None)
    check("przewaga 403 z opisami ze stanu nadal jest awaria",
          (health.check("OLX Praca", 423,
                        enrich={"ok": 0, "expired": 0, "error": 1, "blocked": 2, "ze_stanu": 420}) or {})
          .get("verdict") == "broken")


def test_idempotent_writes():
    """
    Etapy bazodanowe nie przepisują pliku, gdy nie mają czego zmienić.

    Przed poprawką `clean_db`, `migrate_normalize_links` i `deduplicate_db`
    zapisywały oba pliki (63,7 MB) razem z kopią zapasową przy KAŻDYM
    przebiegu, także wtedy, gdy liczba zmian wynosiła zero - czyli ~380 MB
    ruchu na dysku po to, żeby odtworzyć pliki bajt w bajt.
    """
    print("\n[9] Zapis tylko przy realnej zmianie")
    import clean_db
    import deduplicate_db
    import migrate_normalize_links as migrate

    tmp = Path(tempfile.mkdtemp(prefix="test_idempotent_"))
    path = tmp / "jobs.json"
    bdir = tmp / "backups"

    def kopie():
        return len(list(bdir.glob(f"{path.name}.*.bak"))) if bdir.exists() else 0

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
        deduplicate_db._apply(path, keep, {}, set())
        check("deduplicate nie przepisuje pliku bez duplikatow", kopie() == przed,
              f"kopii przybylo: {kopie() - przed}")

        # A gdy zmiana JEST, zapis ma nastąpić.
        brudne = list(czyste) + [{"link": "https://a.pl/of/1?utm_source=x", "title": "A",
                                  "company": "F", "location": "Warszawa",
                                  "description": "Duplikat tej samej oferty."}]
        save_json_atomic(path, brudne)
        przed = kopie()
        deduplicate_db._apply(path, keep, {}, set())
        check("deduplicate zapisuje, gdy duplikat faktycznie jest", kopie() > przed)
        check("duplikat zniknal z pliku", len(load_json_safe(path, default=[])) == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_olx_tempo_przy_blokadzie():
    """
    Odstęp między wejściami musi obowiązywać także wtedy, gdy portal odmawia.

    `time.sleep(OPIS_PRZERWA)` stał na końcu pętli, za wszystkimi `continue`,
    więc każde 403 pomijało przerwę. Pierwsza blokada kasowała odstęp, kolejne
    wejścia szły ~20 razy na sekundę i blokada się utrwalała: przebieg
    z 1 września 2026 zrobił 1164 zapytania w 58 s i skończył na 1137 blokadach.
    Odstęp działał tylko tam, gdzie nie był potrzebny.
    """
    print(chr(10) + "[10] OLX: tempo dociagania opisow")

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
    print(chr(10) + "[11] Oferty zdjete z portalu")

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


def test_olx_fetch_rownolegly():
    """
    Opisy ida przez `fetch` w stronie, a blokada nadal przerywa dociąganie.

    Przebieg z 6 września 2026 dociągał opisy przez `page.goto` na każdą ofertę
    z osobna: 50 ofert/min i godzina ciszy w logu. `fetch` wołany wewnątrz
    otwartej strony OLX daje 68 ofert/min przy tym samym odstępie 0,5 s
    i identycznej treści opisu (porównane znak w znak). Zmierzone 7 września
    2026; przy 6 równoległych i 0,3 s odstępu portal odciął 20 na 20, więc
    odstęp zostaje granicą, nie pokrętłem.
    """
    print(chr(10) + "[12] OLX: dociaganie opisow przez fetch")

    import scrapers.olx_scraper as olx
    from utils.data_models import Job
    import utils.known_links as kl

    # OLX wkleja stan strony jako JSON w JSON-ie - parser czyta wlasnie to
    stan = {"jobAd": {"job": {
        "status": "active",
        "description": "<p>Praca w sklepie. Kasa fiskalna, wykladanie towaru.</p>",
        "postingComponent": {"companyName": "Sklep Test"},
        "addedAt": "2026-09-07T10:00:00+02:00",
    }}}
    OPIS = ("<html><script>window.__PRERENDERED_STATE__ = "
            + json.dumps(json.dumps(stan)) + ";</script></html>")

    class StronaFetch:
        """Strona, ktora umie `evaluate` - czyli droga podstawowa."""
        url = "https://www.olx.pl/praca/warszawa/"

        def __init__(self, kody):
            self.kody = kody          # kod HTTP zwracany po kolei
            self.zapytania = []
            self.odstepy = []

        def route(self, *a, **k):
            pass

        def unroute(self, *a, **k):
            pass

        def goto(self, *a, **k):
            raise AssertionError("droga fetch nie ma prawa wchodzic przez goto")

        def evaluate(self, js, arg):
            adresy, rownolegle, odstep = arg
            self.odstepy.append(odstep)
            wyniki = []
            for adres in adresy:
                self.zapytania.append(adres)
                kod = self.kody(len(self.zapytania))
                wyniki.append({"status": kod, "html": OPIS if kod == 200 else ""})
            return wyniki

    def zbuduj(ile):
        return [Job(title=f"t{i}", company="c", location="Warszawa",
                    link=f"https://www.olx.pl/oferta/x-ID{i}.html",
                    description="x", source="OLX Praca")
                for i in range(ile)]

    prawdziwe_known = kl.known_links
    try:
        kl.known_links = lambda: set()

        # 1. wszystko sie udaje - opisy wchodza, goto nie jest wolane
        scraper = object.__new__(olx.OLXScraper)
        scraper.page = StronaFetch(lambda n: 200)
        scraper.timeout = 1000
        scraper.seen_again_links = []
        oferty = zbuduj(30)
        wynik = scraper.enrich_descriptions(oferty)

        check("fetch dociaga opisy zamiast wchodzic na kazda oferte",
              scraper.enrich_stats["ok"] == 30, str(scraper.enrich_stats))
        check("opis trafia do oferty",
              all("kasa fiskalna" in (j.description or "").lower() for j in wynik),
              (wynik[0].description or "")[:60])
        check("firma nadpisana z ogloszenia",
              wynik[0].company == "Sklep Test", wynik[0].company)
        check("paczkowanie nie gubi ofert",
              len(scraper.page.zapytania) == 30, str(len(scraper.page.zapytania)))

        # 2. portal odmawia - po serii blokad dociaganie ma sie przerwac
        scraper = object.__new__(olx.OLXScraper)
        scraper.page = StronaFetch(lambda n: 403)
        scraper.timeout = 1000
        scraper.seen_again_links = []
        scraper.enrich_descriptions(zbuduj(200))

        check("blokady licza sie osobno, nie jako bledy",
              scraper.enrich_stats["blocked"] > 0 and scraper.enrich_stats["error"] == 0,
              str(scraper.enrich_stats))
        check("po serii blokad fetch przerywa dociaganie",
              len(scraper.page.zapytania) <= olx.PACZKA * 2,
              f"{len(scraper.page.zapytania)} zapytan na 200 ofert")
        # 3. portal odmawia czesciowo - odstep ma urosnac, a nie ciagnac dalej
        #    tym samym tempem; przy pelnej blokadzie liczy sie przerwanie (2.)
        scraper = object.__new__(olx.OLXScraper)
        scraper.page = StronaFetch(lambda n: 403 if n <= 10 else 200)
        scraper.timeout = 1000
        scraper.seen_again_links = []
        scraper.enrich_descriptions(zbuduj(75))

        check("odstep rosnie po paczce z blokadami",
              len(scraper.page.odstepy) > 1
              and scraper.page.odstepy[1] > scraper.page.odstepy[0],
              str(scraper.page.odstepy))
        check("odstep wraca do granicy, gdy portal przestal odmawiac",
              scraper.page.odstepy[-1] == int(olx.OPIS_PRZERWA * 1000),
              str(scraper.page.odstepy))
        check("odstep nie przekracza sufitu",
              all(o <= olx.BLOKADA_SUFIT * 1000 for o in scraper.page.odstepy),
              str(scraper.page.odstepy))
        check("czesciowa blokada nie przerywa calosci",
              scraper.enrich_stats["ok"] == 65,
              str(scraper.enrich_stats))
    finally:
        kl.known_links = prawdziwe_known


def test_olx_opis_z_listingu():
    """
    Opis ma przyjść ze strony kategorii, nie z 975 osobnych wejść.

    Strona listingu niesie cały listing jako JSON (`__PRERENDERED_STATE__`),
    z pełnym opisem każdego ogłoszenia - scraper i tak ją pobiera. Sprawdzone
    7 września 2026: opis złożony ze stanu i opis ze strony oferty mają
    identyczną długość co do znaku (2186 = 2186). Wcześniej ten sam opis
    kosztował osobne wejście na każdą ofertę: kwadrans na przebieg.
    """
    print(chr(10) + "[13] OLX: opis prosto z listingu")

    import scrapers.olx_scraper as olx
    from utils.data_models import Job
    import utils.known_links as kl

    ad = {
        "url": "https://www.olx.pl/oferta/praca/kasjer-CID4-ID9aa.html",
        "status": "active",
        "title": "Kasjer/ka",
        "description": "<p>Obsluga kasy, wykladanie towaru, praca zmianowa.</p>",
        "createdTime": "2026-09-07T08:30:00+02:00",
        "user": {"name": "Market Test"},
        "params": [{"name": "Wymiar pracy", "value": "Pelny etat"}],
    }
    stan = {"listing": {"listing": {"ads": [ad]}}}
    html = ("<html><script>window.__PRERENDERED_STATE__ = "
            + json.dumps(json.dumps(stan)) + ";</script></html>")

    scraper = object.__new__(olx.OLXScraper)
    mapa = scraper._ogloszenia_ze_stanu(html)
    check("stan listingu daje mape ogloszen", len(mapa) == 1, str(list(mapa)[:1]))

    job = Job(title="Kasjer/ka", company="OLX", location="Warszawa",
              link=ad["url"], description="Oferta z OLX (kategoria: sprzedaz)",
              source="OLX Praca")
    wzialo = scraper._z_ogloszenia(job, mapa.get(ad["url"]))
    check("opis z listingu trafia do oferty", wzialo and "kasy" in job.description.lower(),
          (job.description or "")[:60])
    check("firma z ogloszenia, nie zaslepka OLX", job.company == "Market Test", job.company)
    check("data wystawienia przepisana", (job.posted_date or "").startswith("2026-09-07"),
          str(job.posted_date))
    check("parametry trafiaja do pol strukturalnych", job.schedules == ["full_time"],
          str(job.schedules))
    check("opis nie zawiera doklejonych parametrow", "Pelny etat" not in job.description,
          job.description)

    check("ogloszenie zdjete nie nadpisuje opisu",
          scraper._z_ogloszenia(job, dict(ad, status="removed_by_user")) is False)
    check("brak stanu na stronie to nie blad",
          scraper._ogloszenia_ze_stanu("<html>nic tu nie ma</html>") == {})

    # oferta z opisem z listingu nie ma po co wchodzic na wlasna strone
    class StronaLicznik:
        url = "https://www.olx.pl/praca/warszawa/"

        def __init__(self):
            self.wejscia = 0

        def route(self, *a, **k):
            pass

        def unroute(self, *a, **k):
            pass

        def goto(self, *a, **k):
            self.wejscia += 1
            raise AssertionError("oferta z opisem nie ma po co wchodzic na strone")

    prawdziwe_known = kl.known_links
    try:
        kl.known_links = lambda: set()
        scraper.page = StronaLicznik()
        scraper.timeout = 1000
        scraper.seen_again_links = []
        scraper.opisy_ze_stanu = {job.link}
        wynik = scraper.enrich_descriptions([job])
        check("oferta z listingu omija dociaganie",
              len(wynik) == 1 and scraper.page.wejscia == 0,
              f"{len(wynik)} ofert, {scraper.page.wejscia} wejsc")
        check("statystyka liczy opisy z listingu osobno",
              scraper.enrich_stats.get("ze_stanu") == 1, str(scraper.enrich_stats))
    finally:
        kl.known_links = prawdziwe_known


def test_pipeline_final_status():
    """PIPELINE COMPLETE moze powstac tylko wtedy, gdy kazdy etap sie udal."""
    print(chr(10) + "[14] Koncowy status pipeline'u")

    import run_final_pipeline as pipeline

    output = io.StringIO()
    with redirect_stdout(output):
        result = pipeline._finish({"scraping": True, "analysis": True})
    text = output.getvalue()
    check("komplet etapow daje status COMPLETE",
          result is True and "PIPELINE COMPLETE" in text and "INCOMPLETE" not in text)

    output = io.StringIO()
    with redirect_stdout(output):
        result = pipeline._finish({"scraping": True, "analysis": False})
    text = output.getvalue()
    check("blad etapu daje status INCOMPLETE i niezerowy wynik",
          result is False and "PIPELINE INCOMPLETE" in text and "PIPELINE COMPLETE" not in text)

    check("etap bez jawnego wyniku pozostaje zgodny wstecz",
          pipeline._phase("legacy", lambda: None) is True)
    check("niezerowy kod etapu jest bledem",
          pipeline._phase("failed", lambda: 1) is False)

    import main_scraper

    results = {
        "zdrowy": {"success": True, "status": "scraped"},
        "blad": {"success": False, "status": "failed"},
        "pominiety": {"success": True, "status": "skipped"},
    }
    findings = [{"source": "uszkodzony", "verdict": "degraded", "detail": "brak pola"}]
    blocking = main_scraper._blocking_scrape_sources(results, findings)
    check("awaria i zdegradowane dane blokuja sukces scrapingu",
          blocking == ["blad", "uszkodzony"], str(blocking))

    warnings = [{"source": "maly", "verdict": "weak", "detail": "mniej ofert"}]
    check("slaby wynik ostrzega, ale nie udaje awarii technicznej",
          main_scraper._blocking_scrape_sources(
              {"zdrowy": {"success": True, "status": "scraped"}}, warnings
          ) == [])




def test_windows_process_safety():
    print("\n[17] Windows process identity and child management")
    from pipeline_manager import _get_process_creation_time, _is_pid_alive

    pid = os.getpid()
    ctime = _get_process_creation_time(pid)
    check("can read process creation time for own PID", ctime is not None and ctime > 0)
    check("is_pid_alive returns True with correct expected creation time",
          _is_pid_alive(pid, expected_create_time=ctime))
    check("is_pid_alive returns False with incorrect expected creation time (prevents recycled PID kill)",
          not _is_pid_alive(pid, expected_create_time=ctime + 999999))
    check("is_pid_alive returns False for invalid PID",
          not _is_pid_alive(-1))

def test_pipeline_stop_and_resume_lifecycle():
    print("\n[18] Comprehensive stop-resume lifecycle and edge case regressions")
    import run_final_pipeline as pipeline
    import matching.run as matching_run
    import pipeline_manager as sapp
    from pipeline_manager import PipelineProcessManager, load_json_safe
    import tempfile, shutil, time, sys, io
    from unittest.mock import patch
    from contextlib import redirect_stdout

    old_cwd = os.getcwd()
    temp_dir = tempfile.mkdtemp(prefix="test_lifecycle_")
    temp_path = Path(temp_dir)

    # Isolate all module state paths to temporary directory
    t_state_lock = temp_path / "pipeline_run_state.json"
    t_stop_flag = temp_path / "pipeline_stop_requested.flag"
    t_checkpoint = temp_path / "pipeline_checkpoint.json"

    patches = [
        patch.object(sapp, "STATE_LOCK_FILE", t_state_lock),
        patch.object(sapp, "STOP_FLAG_FILE", t_stop_flag),
        patch.object(sapp, "CHECKPOINT_FILE", t_checkpoint),
        patch.object(pipeline, "STOP_FLAG_FILE", t_stop_flag),
        patch.object(pipeline, "CHECKPOINT_FILE", t_checkpoint),
        patch.object(matching_run, "STOP_FLAG_FILE", t_stop_flag),
        patch("utils.cv_profile.ensure_profile", return_value={"seniority": "mid", "city": "Warszawa", "skills": ["python"]}),
    ]

    for p in patches:
        p.start()

    try:
        os.chdir(temp_dir)

        # 1. Real run_pipeline lifecycle: initial checkpoint inside phase0 and stop before phase4
        phase0_saw_checkpoint = {"seen": False, "has_options": False}
        phase4_called = {"called": False}

        def fake_purge():
            # Executed during Phase 0: verify initial checkpoint was already persisted!
            if t_checkpoint.exists():
                phase0_saw_checkpoint["seen"] = True
                cp = load_json_safe(t_checkpoint, default={})
                if cp.get("options", {}).get("skip_scraping") is True and cp.get("current_stage") == "phase0":
                    phase0_saw_checkpoint["has_options"] = True
            return True
        def fake_matching_stopping(*args, **kwargs):
            # Simulate stop requested at matching boundary
            t_stop_flag.touch()
            return 1

        def fake_eval():
            phase4_called["called"] = True
            return True

        with patch.object(pipeline, "purge_stale_offers") as mock_p0, \
             patch.object(pipeline, "migrate_normalize_links") as mock_p1_5, \
             patch.object(pipeline, "deduplicate_db") as mock_p2, \
             patch.object(pipeline, "clean_db") as mock_p2_5, \
             patch.object(pipeline, "JobDatabase") as mock_db_cls, \
             patch("matching.run.main", side_effect=fake_matching_stopping), \
             patch("eval_ranking.main", side_effect=fake_eval):

            mock_p0.main = fake_purge
            mock_p1_5.main = lambda: True
            mock_p2.run = lambda: True
            mock_p2_5.run = lambda: True
            mock_db_instance = mock_db_cls.return_value
            mock_db_instance.load_jobs.return_value = [{"title": "Job 1", "link": "http://x"}]

            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                res = pipeline.run_pipeline(skip_scraping=True, rescore_all=True)
            run_output = out_buf.getvalue()

        check("initial checkpoint observed inside phase0", phase0_saw_checkpoint["seen"] is True)
        check("initial checkpoint preserves options inside phase0", phase0_saw_checkpoint["has_options"] is True)
        check("run_pipeline returned False on user stop", res is False)
        check("run_pipeline printed PIPELINE STOPPED", "PIPELINE STOPPED" in run_output)
        check("phase4 was NOT called after matching stop", phase4_called["called"] is False)

        cp_after_stop = load_json_safe(t_checkpoint, default={})
        check("checkpoint marks stopped is True", cp_after_stop.get("stopped") is True)
        check("checkpoint preserves completed stages before stop", "phase2_5" in cp_after_stop.get("completed_stages", []))

        # 2. Resuming run_pipeline skips completed stages
        phase0_rerun = {"called": False}
        phase4_resumed = {"called": False}

        def fake_purge_rerun():
            phase0_rerun["called"] = True
            return True

        def fake_matching_success(*args, **kwargs):
            return 0

        def fake_eval_resume(args):
            phase4_resumed["called"] = True
            return True

        with patch.object(pipeline, "purge_stale_offers") as mock_p0, \
             patch.object(pipeline, "migrate_normalize_links") as mock_p1_5, \
             patch.object(pipeline, "deduplicate_db") as mock_p2, \
             patch.object(pipeline, "clean_db") as mock_p2_5, \
             patch.object(pipeline, "JobDatabase") as mock_db_cls, \
             patch("matching.run.main", side_effect=fake_matching_success), \
             patch("eval_ranking.main", side_effect=fake_eval_resume):

            mock_p0.main = fake_purge_rerun
            mock_p1_5.main = lambda: True
            mock_p2.run = lambda: True
            mock_p2_5.run = lambda: True
            mock_db_instance = mock_db_cls.return_value
            mock_db_instance.load_jobs.return_value = [{"title": "Job 1", "link": "http://x"}]

            out_resume = io.StringIO()
            with redirect_stdout(out_resume):
                res_resume = pipeline.run_pipeline(resume=True)
            resume_output = out_resume.getvalue()

        check("resumed pipeline returned True on completion", res_resume is True)
        check("resumed pipeline skipped completed phase0", phase0_rerun["called"] is False)
        check("resumed pipeline executed phase4", phase4_resumed["called"] is True)
        check("resumed pipeline output contains PIPELINE COMPLETE", "PIPELINE COMPLETE" in resume_output)

        # 3. Slow cooperative stop (>1.5s grace period) and subsequent clean start without immediate stop
        mgr = PipelineProcessManager()
        # Child runs for 2.0s - deliberately exceeding the manager's 1.5s cooperative polling window!
        slow_cmd = [sys.executable, "-c", "import time; time.sleep(2.0)"]
        started, _ = mgr.start_pipeline(mode="full", cmd=slow_cmd, is_resume=False)
        check("manager started slow child process", started is True)

        # Issue cooperative stop: polling loop waits 1.5s, then returns while process is still running
        t0 = time.time()
        stop_ok, stop_msg = mgr.stop_pipeline(force=False)
        duration = time.time() - t0
        check("stop_pipeline returned without auto-force-kill", stop_ok is True)
        check("stop_pipeline polling window observed (~1.5s)", 1.3 <= duration <= 2.2)
        check("manager status is stopping", mgr.get_state().get("status") in ("stopping", "stopped"))

        # Wait for the child process to finish its 2.0s sleep and exit
        if mgr._thread:
            mgr._thread.join(timeout=4.0)
        check("child process exited cleanly", not mgr.is_running())

        # A leftover stop flag may have remained if polling expired before process exit.
        # Simulate leftover flag on disk:
        t_stop_flag.touch()
        check("stop flag exists on disk before new start", t_stop_flag.exists())

        # Next start_pipeline or resume MUST safely clear the stale flag and NOT stop immediately!
        quick_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]
        next_started, _ = mgr.start_pipeline(mode="full", cmd=quick_cmd, is_resume=False)
        check("new start_pipeline started successfully", next_started is True)
        check("stale stop flag cleared by start_pipeline", not t_stop_flag.exists())

        if mgr._thread:
            mgr._thread.join(timeout=3.0)
        next_state = mgr.get_state()
        check("new run completed successfully instead of immediately stopping", next_state.get("status") == "completed")

        # 4. Standalone tool exit semantics: code 0 = completed, code 1 = failed
        mgr_sa = PipelineProcessManager()
        sa_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]
        ok_sa, _ = mgr_sa.start_pipeline(mode="standalone", cmd=sa_cmd, is_resume=False, stage="phase3")
        running_ids = [s["id"] for s in mgr_sa._stages if s["status"] == "running"]
        check("standalone step runs as its own stage, the rest skipped",
              running_ids == ["phase3"] and mgr_sa._active_stage_idx == 5
              and all(s["status"] == "skipped" for s in mgr_sa._stages if s["id"] != "phase3"), running_ids)
        if mgr_sa._thread:
            mgr_sa._thread.join(timeout=3.0)
        sa_state = mgr_sa.get_state()
        check("standalone exit code 0 marked as completed", sa_state.get("status") == "completed")
        check("standalone exit code 0 marked as success", sa_state.get("success") is True)
        check("standalone step stage done on success",
              next(s for s in sa_state["stages"] if s["id"] == "phase3")["status"] == "done")

        mgr_fa = PipelineProcessManager()
        fa_cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
        ok_fa, _ = mgr_fa.start_pipeline(mode="full", cmd=fa_cmd, is_resume=False)
        if mgr_fa._thread:
            mgr_fa._thread.join(timeout=3.0)
        fa_state = mgr_fa.get_state()
        check("standalone exit code 1 marked as failed", fa_state.get("status") == "failed")

    finally:
        for p in patches:
            p.stop()
        os.chdir(old_cwd)
        shutil.rmtree(temp_dir, ignore_errors=True)
def test_http_security_and_dns_rebinding_protection():
    print("\n[19] HTTP local binding, Host validation & DNS rebinding protection")
    from starlette.testclient import TestClient
    import server

    client = TestClient(server.app, base_url="http://127.0.0.1:8501")

    # 1. Localhost allowed on GET endpoints
    res_local_ip = client.get("/api/cv", headers={"host": "127.0.0.1:8501"})
    check("local 127.0.0.1 allowed on GET /api/cv", res_local_ip.status_code == 200)

    res_local_name = client.get("/api/cv", headers={"host": "localhost:8501"})
    check("local localhost allowed on GET /api/cv", res_local_name.status_code == 200)

    # 2. Foreign Host headers (DNS rebinding simulation) rejected across GET endpoints
    res_rebind_cv = client.get("/api/cv", headers={"host": "attacker.com:8501"})
    check("foreign host rejected on GET /api/cv with 400 Bad Request", res_rebind_cv.status_code == 400)

    res_rebind_boot = client.get("/api/bootstrap", headers={"host": "malicious.site:8501"})
    check("foreign host rejected on GET /api/bootstrap with 400 Bad Request", res_rebind_boot.status_code == 400)

    res_rebind_offers = client.get("/api/offers", headers={"host": "evil.org"})
    check("foreign host rejected on GET /api/offers with 400 Bad Request", res_rebind_offers.status_code == 400)

    # 3. Cross-origin mutation protection rejects foreign Origin on POST
    res_csrf = client.post("/api/offers/decision",
                           json={"link": "https://example.com/test", "status": "save", "rating": 5},
                           headers={"origin": "http://evil.com"})
    check("cross-origin mutation rejected with 403 Forbidden", res_csrf.status_code == 403)

    # 4. Klucze z .env nie wychodzą jawnym tekstem przez /api/env-keys
    from app_services import _read_env_file
    res_keys = client.get("/api/env-keys", headers={"host": "127.0.0.1:8501"})
    check("env-keys endpoint responds with 200", res_keys.status_code == 200)
    sekrety = [v for k, v in _read_env_file().items()
               if ("KEY" in k or "SECRET" in k) and len(v) > 8]
    check("no configured secret appears in the /api/env-keys payload",
          not any(s in res_keys.text for s in sekrety))


def test_external_data_change_automatic_invalidation():
    print("\n[20] External data change automatic invalidation & cache efficiency")
    import tempfile, shutil, json, time
    from pathlib import Path
    from starlette.testclient import TestClient
    import app_services
    import server

    temp_dir = tempfile.mkdtemp(prefix="test_auto_inval_")
    temp_path = Path(temp_dir)

    t_jobs = temp_path / "jobs_database.json"
    t_matches = temp_path / "match_results.json"
    t_decisions = temp_path / "user_decisions.json"

    orig_jobs = app_services.JOBS_DATABASE_PATH
    orig_matches = app_services.MATCH_RESULTS_PATH
    orig_dec = app_services.USER_DECISIONS_PATH

    app_services.JOBS_DATABASE_PATH = t_jobs
    app_services.MATCH_RESULTS_PATH = t_matches
    app_services.USER_DECISIONS_PATH = t_decisions
    try:
        # Initial disk state: 1 job, 1 match, 0 decisions
        job1 = {"title": "Initial Dev", "company": "Corp A", "link": "https://corp-a.com/job1",
                "description": "Python backend microservices experience required over 20 chars", "source": "Test"}
        match1 = {"percent": 88, "reason": "Good Python fit"}

        t_jobs.write_text(json.dumps([job1], ensure_ascii=False), encoding="utf-8")
        t_matches.write_text(json.dumps({job1["link"]: match1}, ensure_ascii=False), encoding="utf-8")
        t_decisions.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

        client = TestClient(server.app, base_url="http://127.0.0.1:8501")

        # 1. Initial query: consumer observes 1 offer
        res1 = client.get("/api/offers?tab=Dopasowane", headers={"host": "127.0.0.1:8501"})
        check("initial offers query returns 200", res1.status_code == 200)
        data1 = res1.json()
        check("initial offers count is 1", data1.get("total") == 1)
        check("initial offer title is observed", len(data1.get("items", [])) > 0 and data1["items"][0]["title"] == "Initial Dev")

        # 2. Simulate background pipeline writing newly scraped & analyzed offer to disk
        time.sleep(0.05)
        job2 = {"title": "Background Pipeline Dev", "company": "Corp B", "link": "https://corp-b.com/job2",
                "description": "React TypeScript full stack role over 20 chars", "source": "Test"}
        match2 = {"percent": 94, "reason": "High React fit"}

        t_jobs.write_text(json.dumps([job1, job2], ensure_ascii=False), encoding="utf-8")
        t_matches.write_text(json.dumps({job1["link"]: match1, job2["link"]: match2}, ensure_ascii=False), encoding="utf-8")

        # 3. Next consumer poll without manual reload: observes updated 2 offers automatically
        res2 = client.get("/api/offers?tab=Dopasowane", headers={"host": "127.0.0.1:8501"})
        check("subsequent poll returns 200 without manual reload", res2.status_code == 200)
        data2 = res2.json()
        check("background file update automatically reflected in total count (total: 2)", data2.get("total") == 2)
        titles2 = [it["title"] for it in data2.get("items", [])]
        check("newly written offer title observed by consumer", "Background Pipeline Dev" in titles2)

        # 4. Simulate external decision write to disk (e.g. concurrent CLI or external sync)
        time.sleep(0.05)
        decision_data = {"https://corp-a.com/job1": {"status": "save", "rating": 9, "stage": "save"}}
        t_decisions.write_text(json.dumps(decision_data, ensure_ascii=False), encoding="utf-8")

        # 5. Consumer polls Zapisane tab without manual reload: observes saved offer automatically
        res3 = client.get("/api/offers?tab=Zapisane", headers={"host": "127.0.0.1:8501"})
        check("Zapisane poll returns 200", res3.status_code == 200)
        data3 = res3.json()
        check("external decision write automatically reflected in Zapisane tab (total: 1)", data3.get("total") == 1)
        links3 = [it["link"] for it in data3.get("items", [])]
        check("saved offer link observed in Zapisane items", "https://corp-a.com/job1" in links3)

        # 6. Consumer polls Dopasowane tab: decided offer moved out
        res4 = client.get("/api/offers?tab=Dopasowane", headers={"host": "127.0.0.1:8501"})
        data4 = res4.json()
        check("decided offer automatically excluded from Dopasowane tab (total: 1)", data4.get("total") == 1)
        titles4 = [it["title"] for it in data4.get("items", [])]
        check("only undecided offer remains in Dopasowane", "Background Pipeline Dev" in titles4 and "Initial Dev" not in titles4)

    finally:
        app_services.JOBS_DATABASE_PATH = orig_jobs
        app_services.MATCH_RESULTS_PATH = orig_matches
        app_services.USER_DECISIONS_PATH = orig_dec
        app_services.job_data_service.reload()
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_playwright_chromium_prerequisites():
    print("\n[21] Playwright Chromium prerequisite detector & asyncio safety")
    import app_services
    import asyncio
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        browser_exe = tmp_path / "fake_chrome.exe"
        browser_exe.write_text("placeholder", encoding="utf-8")
        missing_exe = tmp_path / "nonexistent_chrome.exe"

        def _mock_resolver(target_path: Path):
            def _resolver():
                try:
                    asyncio.get_running_loop()
                    raise AssertionError("Helper was invoked directly on an active asyncio event loop thread")
                except RuntimeError:
                    pass
                return str(target_path)
            return _resolver

        # 1. Existing browser executable file returns ready (bool check)
        with patch.object(app_services, "_get_chromium_executable_path", side_effect=_mock_resolver(browser_exe)):
            ok_ready, _ = app_services.check_playwright_chromium()
            check("existing browser executable returns ready", ok_ready is True)

        # 2. Missing browser executable file returns not ready without install (bool check)
        with patch.object(app_services, "_get_chromium_executable_path", side_effect=_mock_resolver(missing_exe)):
            ok_missing, _ = app_services.check_playwright_chromium()
            check("missing browser executable returns not ready without install", ok_missing is False)

        # 3. Invocation from inside an active asyncio event loop
        async def _async_call():
            with patch.object(app_services, "_get_chromium_executable_path", side_effect=_mock_resolver(browser_exe)):
                return app_services.check_playwright_chromium()

        async_ok, _ = asyncio.run(_async_call())
        check("check_playwright_chromium succeeds when called from active asyncio loop", async_ok is True)

def test_pipeline_skipped_stage_progress():
    print("\n[22] Pipeline progress calculation with skipped stages")
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    import pipeline_manager
    from pipeline_manager import PipelineProcessManager

    # Stan przebiegu w katalogu tymczasowym: wcześniej test kasował prawdziwy
    # pipeline_run_state.json i z arkusza znikał log przerwanego przebiegu.
    temp_dir = Path(tempfile.mkdtemp(prefix="test_pipeline_progress_"))
    with patch.object(pipeline_manager, "STATE_LOCK_FILE", temp_dir / "pipeline_run_state.json"), \
         patch.object(pipeline_manager, "CHECKPOINT_FILE", temp_dir / "pipeline_checkpoint.json"), \
         patch.object(pipeline_manager, "STOP_FLAG_FILE", temp_dir / "pipeline_stop_requested.flag"):
        _check_skipped_stage_progress(PipelineProcessManager)
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def _check_skipped_stage_progress(PipelineProcessManager):
    class DummyProc:
        pid = 12345
        def poll(self): return None

    class ExitedProc:
        pid = 12345
        def poll(self): return 0

    mgr = PipelineProcessManager()
    mgr._cmd = ["test"]
    mgr._process = DummyProc()

    # 1. Completed state with 1 skipped + 6 done:
    mgr._status = "completed"
    mgr._stages = [
        {"name": "scrape", "title": "Scraping portali", "status": "skipped"},
        {"name": "extract", "title": "Ekstrakcja", "status": "done"},
        {"name": "enrich", "title": "Wzbogacanie", "status": "done"},
        {"name": "cluster", "title": "Klasteryzacja", "status": "done"},
        {"name": "match", "title": "Dopasowanie", "status": "done"},
        {"name": "ranking", "title": "Ranking", "status": "done"},
        {"name": "report", "title": "Raport", "status": "done"},
    ]
    mgr._active_stage_idx = 6
    mgr._exit_code = 0
    mgr._pipeline_complete_seen = True

    state_completed = mgr.get_state()
    check("completed with skipped stage reports 100.0% progress", state_completed["progress_percent"] == 100.0)
    check("completed with skipped stage labels 7/7 resolved stages", "7/7" in state_completed["progress_label"])
    check("completed pipeline cannot be resumed", state_completed["can_resume"] is False)

    # 2. Running state when stage 0 is skipped and stage 1 is active (stages 1-6 pending):
    mgr._stages = [
        {"name": "scrape", "title": "Scraping portali", "status": "skipped"},
        {"name": "extract", "title": "Ekstrakcja", "status": "running"},
        {"name": "enrich", "title": "Wzbogacanie", "status": "pending"},
        {"name": "cluster", "title": "Klasteryzacja", "status": "pending"},
        {"name": "match", "title": "Dopasowanie", "status": "pending"},
        {"name": "ranking", "title": "Ranking", "status": "pending"},
        {"name": "report", "title": "Raport", "status": "pending"},
    ]
    mgr._status = "running"
    mgr._active_stage_idx = 1
    state_running = mgr.get_state()
    check("running with 1 skipped stage reflects resolved stage in progress_percent",
          state_running["progress_percent"] == round((1 / 7) * 100, 1))
    check("running state indicates active stage", "Etap 2 z 7" in state_running["progress_label"])

    # 3. Stopped state with unfinished stages:
    mgr._process = ExitedProc()
    mgr._status = "stopped"
    state_stopped = mgr.get_state()
    check("stopped pipeline with remaining stages allows resume", state_stopped["can_resume"] is True)


def test_pipeline_stage_stats_and_eta():
    print("\n[23] Stage numbers and scoring pace parsed from the run log")
    from pipeline_manager import PipelineProcessManager

    mgr = PipelineProcessManager()
    lines = [
        "   REMOVED (stale):  37",
        "  jobs_database.json: 1250 -> 1240 (scalono 10)",
        "2026-09-23 10:02:12,123 - deduplicate_db - INFO - jobs_database.json: 1240 -> 1192 (removed 48)",
        "2026-09-23 10:02:13,000 - clean_db - INFO -    Before: 1,000,000 chars",
        "2026-09-23 10:02:13,000 - clean_db - INFO -    After:  1,000,000 chars",
        "2026-09-23 10:02:14,000 - clean_db - INFO -    Before: 2,000,000 chars",
        "2026-09-23 10:02:14,000 - clean_db - INFO -    After:  1,070,000 chars",
    ]
    for line in lines:
        mgr._parse_telemetry(line)
    stages = mgr._telemetry_snapshot()["stages"]
    check("purge: stale offers removed from the jobs database", stages.get("phase0") == {"removed": 37})
    check("normalisation: links and merged count",
          stages.get("phase1_5") == {"links": 1240, "merged": 10}, stages.get("phase1_5"))
    check("dedup: jobs database count, purge line ignored",
          stages.get("phase2") == {"removed": 48}, stages.get("phase2"))
    check("token diet: characters summed over every Before/After pair",
          stages.get("phase2_5") == {"chars_before": 3_000_000, "chars_after": 2_070_000}, stages.get("phase2_5"))

    mgr._parse_telemetry("2026-09-23 10:02:15,000 - deduplicate_db - INFO - jobs_database.json: 1192 ofert, brak duplikatow - plik bez zmian")
    check("dedup without duplicates reports zero", mgr._telemetry_snapshot()["stages"]["phase2"] == {"removed": 0})

    mgr._parse_telemetry("Prefilter: 12 rejected {'miasto': 10, 'poziom': 2} | to score: 38")
    check("scoring: prefilter rejected, reasons and to_score parsed",
          mgr._telemetry_snapshot()["stages"]["phase3"]
          == {"prefilter_rejected": 12, "prefilter_reasons": {"miasto": 10, "poziom": 2}, "to_score": 38})
    check("scoring: prefilter records to_score and total",
          mgr._telemetry["scoring"]["to_score"] == 38 and mgr._telemetry["scoring"]["prefilter_rejected"] == 12)

    mgr._parse_telemetry("   Scored 10/38 (5.0/s)")
    check("scoring: progress line sets rate, scored and report time",
          mgr._telemetry["scoring"]["last_done_at"] is not None
          and mgr._telemetry["scoring"]["rate"] == 5.0 and mgr._telemetry["scoring"]["scored"] == 10)

    mgr._parse_telemetry("   Scored 25/38 (5.2/s)")
    sc = mgr._telemetry_snapshot()["scoring"]
    check("scoring: progress update updates scored and rate",
          sc["scored"] == 25 and sc["rate"] == 5.2)
    mgr._parse_telemetry("   Jev error: HTTP 500")
    check("scoring: Jev errors are counted live", mgr._telemetry["scoring"]["errors"] == 1)
    mgr._parse_telemetry("Matching done: 36 scored now, 2 errors, 140 offers with a percent.")
    phase3 = mgr._telemetry_snapshot()["stages"]["phase3"]
    check("scoring: final summary sets errors and offers with a percent",
          (phase3["errors"], phase3["with_percent"], mgr._telemetry["scoring"]["errors"]) == (2, 140, 2), phase3)

    mgr._parse_telemetry("Of those, with an AI score:         66")
    mgr._parse_telemetry("Spearman correlation (AI score vs rating): +0.551  (umiarkowana)")
    check("evaluation: Spearman and common count parsed",
          mgr._telemetry_snapshot()["stages"]["phase4"] == {"common": 66, "rho": 0.551})
    fresh = PipelineProcessManager()
    fresh._parse_telemetry("No manual ratings - nothing to evaluate.")
    check("evaluation: missing ratings reported as insufficient",
          fresh._telemetry_snapshot()["stages"]["phase4"] == {"insufficient": True})
    for line in [
        "2026-09-25 18:14:00,000 - main_scraper - INFO - Running aplikuj.pl scraper...",
        "2026-09-25 18:14:01,000 - main_scraper - INFO - Running OLX Praca scraper...",
        "2026-09-25 18:20:00,000 - scrapers.ldjson_scraper_base - INFO - aplikuj.pl: 1200/3482 descriptions - 240/min, ~10 min left",
        "2026-09-25 18:20:01,000 - scrapers.olx_scraper - INFO - OLX: 50/300 opisow (ok: 48, wygasle: 2, bledy: 0, blokady: 0) - 30 ofert/min, zostalo ~8 min",
        "2026-09-25 18:20:02,000 - scrapers.ldjson_scraper_base - INFO - GoWork.pl: 100/200 descriptions - 60/min, ~2 min left",
    ]:
        mgr._parse_telemetry(line)
    by_name = {s["name"]: s for s in mgr._telemetry_snapshot()["sources"]}
    check("details: ld+json progress lands on its source",
          (by_name["aplikuj.pl"]["details_done"], by_name["aplikuj.pl"]["details_total"]) == (1200, 3482))
    check("details: OLX short log name maps to the OLX Praca source",
          (by_name["OLX Praca"]["details_done"], by_name["OLX Praca"]["details_total"]) == (50, 300))
    check("details: progress of an unannounced source creates no row", "GoWork.pl" not in by_name)
    mgr._parse_telemetry("2026-09-25 18:29:00,000 - scrapers.ldjson_scraper_base - INFO - "
                         "aplikuj.pl: fetched 3400/3482 descriptions, dropped 12 (out of scope) -> 966 offers")
    src = next(s for s in mgr._telemetry_snapshot()["sources"] if s["name"] == "aplikuj.pl")
    check("details: final summary fills the tile even when some pages had no JobPosting",
          src["details_done"] == src["details_total"] == 3482, src)

def test_offer_api_contract():
    print("\n[24] Fresh offers, next step and decisions over HTTP")
    import tempfile, shutil, json
    from pathlib import Path
    from starlette.testclient import TestClient
    import app_services
    import server

    temp_path = Path(tempfile.mkdtemp(prefix="test_offer_api_"))
    paths = {
        "JOBS_DATABASE_PATH": temp_path / "jobs_database.json",
        "MATCH_RESULTS_PATH": temp_path / "match_results.json",
        "USER_DECISIONS_PATH": temp_path / "user_decisions.json",
        "LAST_SCRAPE_RUN_PATH": temp_path / "last_scrape_run.json",
    }
    originals = {name: getattr(app_services, name) for name in paths}
    for name, path in paths.items():
        setattr(app_services, name, path)

    def job(n, scraped_at):
        return {"title": f"Analityk {n}", "company": "Corp", "link": f"https://corp.example/job{n}",
                "description": "Analiza danych w SQL i raporty dla zarządu, ponad 20 znaków", "source": "Test",
                "scraped_at": scraped_at}

    jobs = [job(1, "2026-09-20T18:00:00"), job(2, "2026-09-20T19:30:00"), job(3, "2026-09-20T19:40:00")]
    matches = {
        jobs[0]["link"]: {"percent": 80},
        jobs[1]["link"]: {"percent": 70},
        jobs[2]["link"]: {"percent": 75},
    }
    decisions = {jobs[0]["link"]: "save", jobs[2]["link"]: "reject"}
    paths["JOBS_DATABASE_PATH"].write_text(json.dumps(jobs), encoding="utf-8")
    paths["MATCH_RESULTS_PATH"].write_text(json.dumps(matches), encoding="utf-8")
    paths["USER_DECISIONS_PATH"].write_text(json.dumps(decisions), encoding="utf-8")
    paths["LAST_SCRAPE_RUN_PATH"].write_text(json.dumps({"started_at": "2026-09-20T19:00:00"}), encoding="utf-8")

    try:
        app_services.job_data_service.reload()
        client = TestClient(server.app, base_url="http://127.0.0.1:8501")

        all_offers = client.get("/api/offers?tab=Wszystkie").json()
        new_links = sorted(row["link"] for row in all_offers["items"] if row["is_new"])
        check("offers scraped after the last run start are new",
              new_links == [jobs[1]["link"], jobs[2]["link"]])
        check("fresh_count counts the whole list", all_offers.get("fresh_count") == 2)

        detail0 = client.get("/api/offers/detail", params={"link": jobs[0]["link"]}).json()
        check("detail exposes match percentage", detail0["match_percentage"] == 80)
        detail1 = client.get("/api/offers/detail", params={"link": jobs[1]["link"]}).json()
        check("detail exposes second match percentage", detail1["match_percentage"] == 70)

        bad_due = client.post("/api/offers/next-step", json={"link": jobs[0]["link"], "label": "Rozmowa", "due": "2026-13-01"})
        check("next step rejects an invalid date", bad_due.status_code == 400)
        undecided = client.post("/api/offers/next-step", json={"link": jobs[1]["link"], "label": "Rozmowa", "due": None})
        check("next step requires a decision", undecided.status_code == 400)

        saved = client.post("/api/offers/next-step",
                            json={"link": jobs[0]["link"], "label": "  Rozmowa   z HR ", "due": "2026-09-26 10:00"})
        check("next step on a legacy string decision is saved",
              saved.status_code == 200 and saved.json()["offer"]["next_step"] == {"label": "Rozmowa z HR", "due": "2026-09-26 10:00"})
        on_disk = json.loads(paths["USER_DECISIONS_PATH"].read_text(encoding="utf-8"))[jobs[0]["link"]]
        check("legacy decision becomes an object that keeps its status",
              isinstance(on_disk, dict) and on_disk["status"] == "save")

        client.post("/api/offers/decision", json={"link": jobs[0]["link"], "status": "apply", "rating": 8, "stage": "interview"})
        board = client.get("/api/applications").json()
        card = next((i for i in board["items"] if i["link"] == jobs[0]["link"]), None)
        check("next step survives a stage change and reaches the board",
              card is not None and card["next_step"] == {"label": "Rozmowa z HR", "due": "2026-09-26 10:00"})

        cleared = client.post("/api/offers/next-step", json={"link": jobs[0]["link"], "label": "", "due": None})
        check("empty label removes the next step", cleared.status_code == 200 and cleared.json()["offer"]["next_step"] is None)
    finally:
        for name, path in originals.items():
            setattr(app_services, name, path)
        app_services.job_data_service.reload()
        shutil.rmtree(temp_path, ignore_errors=True)


def test_foreign_run_visible_to_server():
    print("\n[25] Run started outside the server is visible and stoppable from the server")
    import tempfile, shutil, time
    from unittest.mock import patch
    import pipeline_manager as pm
    from pipeline_manager import PipelineProcessManager, load_json_safe

    temp_path = Path(tempfile.mkdtemp(prefix="test_foreign_run_"))
    state_file = temp_path / "pipeline_run_state.json"
    stop_flag = temp_path / "pipeline_stop_requested.flag"
    # Nazwa jak prawdziwy punkt wejścia: menedżer rozpoznaje po niej przebieg pipeline'u.
    child = temp_path / "run_final_pipeline.py"
    child.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "flag = Path(sys.argv[1])\n"
        "print('── PHASE 3: Matching (prefilter + Jev)', flush=True)\n"
        "print('Prefilter: 10 rejected {\"tech\": 10} | to score: 150', flush=True)\n"
        "print('   Scored 1/150 (2.5/s)', flush=True)\n"
        "deadline = time.time() + 15\n"
        "while not flag.exists() and time.time() < deadline:\n"
        "    time.sleep(0.05)\n"
        "print('PIPELINE STOPPED', flush=True)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    with patch.object(pm, "STATE_LOCK_FILE", state_file), \
         patch.object(pm, "STOP_FLAG_FILE", stop_flag), \
         patch.object(pm, "CHECKPOINT_FILE", temp_path / "pipeline_checkpoint.json"):
        try:
            server = PipelineProcessManager()
            server.start_pipeline(mode="standalone", cmd=[sys.executable, "-c", "pass"])
            server.wait()
            check("server's own earlier run finished", server.get_state()["status"] == "completed")

            echoed = []
            owner = PipelineProcessManager()
            started, _ = owner.start_pipeline(
                mode="full", cmd=[sys.executable, "-u", str(child), str(stop_flag)], echo=echoed.append)
            check("terminal run started", started is True)

            deadline = time.time() + 10
            state = server.get_state()
            while time.time() < deadline and not (
                    state.get("running") and state["stages"][5]["status"] == "running"
                    and (state["telemetry"].get("scoring") or {}).get("scored") == 1):
                time.sleep(0.1)
                state = server.get_state()
            check("server sees the terminal run as running", state.get("running") is True and state["status"] == "running")
            check("server shows the terminal run's stage", state["stages"][5]["status"] == "running")
            check("server shows the terminal run's scoring progress",
                  (state["telemetry"].get("scoring") or {}).get("scored") == 1
                  and (state["telemetry"].get("scoring") or {}).get("to_score") == 150)
            check("server shows the terminal run's log", any("PHASE 3" in line for line in state["logs"]))
            refused, _ = server.start_pipeline(mode="full", cmd=[sys.executable, "-c", "pass"])
            check("server refuses a second run while the terminal run lives", refused is False)

            owner_pid = load_json_safe(state_file, default={}).get("pid")
            stop_ok, _ = server.stop_pipeline(force=False)
            check("server accepts a stop request for the terminal run", stop_ok is True)
            check("stop request leaves the owner's state file intact",
                  load_json_safe(state_file, default={}).get("pid") == owner_pid)
            stopping = server.get_state()["status"]

            code = owner.wait()
            final = server.get_state()
            check("server shows stopping until the run exits", stopping in ("stopping", "stopped"))
            check("terminal run exits after the stop flag", code == 1)
            check("server shows the terminal run as stopped", final["running"] is False and final["status"] == "stopped")
            check("stopped terminal run can be resumed from the server", final["can_resume"] is True)
            check("terminal output is echoed", "PIPELINE STOPPED" in echoed)
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)


def main():
    print("=" * 62)
    print("  INTEGRATION TESTS (no API calls)")
    print("=" * 62)

    for test in (test_canonical_link, test_text_cleaning,
                 test_safe_io, test_llm_providers,
                 test_record_scrape, test_scraper_health,
                 test_idempotent_writes, test_olx_tempo_przy_blokadzie,
                 test_zdjete_z_portalu, test_olx_fetch_rownolegly,
                 test_olx_opis_z_listingu, test_pipeline_final_status,
                 test_windows_process_safety,
                 test_pipeline_stop_and_resume_lifecycle,
                 test_http_security_and_dns_rebinding_protection,
                 test_external_data_change_automatic_invalidation,
                 test_playwright_chromium_prerequisites,
                 test_pipeline_skipped_stage_progress,
                 test_pipeline_stage_stats_and_eta,
                 test_offer_api_contract,
                 test_foreign_run_visible_to_server):
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
