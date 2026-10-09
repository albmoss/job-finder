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

from utils import candidates
from utils.links import canonical_link
from utils.safe_io import load_json_safe, save_json_atomic
from utils.text_cleaner import clean_job_description, strip_html

ROOT = Path(__file__).parent.parent
PASSED, FAILED = [], []
candidates.ROOT = Path(tempfile.mkdtemp(prefix="test_candidates_")) / "candidates"


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

    from app_services import format_description
    title = "Junior Frontend Developer"
    check("title repeated as a heading is dropped from the description",
          format_description(f"{title}\n\nPraca z React i TypeScript.", drop_prefix=title)
          == ["Praca z React i TypeScript."])
    mid = "Do zespołu poszukujemy Junior Frontend Developera z wiedzą o HTML."
    check("title inside the first sentence keeps the whole sentence",
          format_description(mid, drop_prefix=title) == [mid])


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

        # Zaślepka zamiast opisu: następne pobranie wstawia treść, prawdziwego opisu nie rusza.
        placeholder = "Oferta z JustJoinIT: Tytuł"
        db.record_scrape([Job(title="Tytuł", company="Firma", link="https://a.pl/4",
                              description=placeholder, source="test")])
        db.record_scrape([Job(title="Tytuł", company="Firma", link="https://a.pl/4",
                              description=placeholder, source="test")])
        db.record_scrape([job("https://a.pl/4")])
        db.record_scrape([Job(title="Tytuł", company="Firma", link="https://a.pl/4",
                              description="Inny, późniejszy opis tej samej oferty.", source="test")])
        rec = [r for r in on_disk() if r["link"] == "https://a.pl/4"][0]
        check("a placeholder description is replaced once, a real one is kept",
              rec["description"] == "Opis oferty wystarczająco długi, żeby przeszedł.", rec["description"])

        db.record_scrape([job("https://a.pl/6")])
        db.record_scrape([job("https://a.pl/6", logo_url="https://cdn.example.pl/logo-a.png")])
        db.record_scrape([job("https://a.pl/6", logo_url="https://cdn.example.pl/logo-b.png")])
        rec = [r for r in on_disk() if r["link"] == "https://a.pl/6"][0]
        check("a missing logo is filled in on a known offer, an existing one is left alone",
              rec["logo_url"] == "https://cdn.example.pl/logo-a.png", str(rec["logo_url"]))

        import config
        import utils.known_links as kl
        db.record_scrape([Job(title="Tytuł", company="Firma", link="https://a.pl/5",
                              description="Oferta z OLX (kategoria: sprzedaz)", source="test")])
        real_path = config.JOBS_DATABASE_PATH
        try:
            config.JOBS_DATABASE_PATH = path
            kl.reset_cache()
            known = kl.known_links()
        finally:
            config.JOBS_DATABASE_PATH = real_path
            kl.reset_cache()
        check("offers with a placeholder are not known, so scrapers fetch them again",
              "https://a.pl/5" not in known and "https://a.pl/4" in known, sorted(known))
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
    Oferta pominięta przez nowszy przebieg jest zdjęta tylko przy pełnym listingu
    źródła i tylko wtedy, gdy była widziana już przy obecnym zakresie.

    4 października 2026 sama reguła „nie było w nowszym przebiegu" oznaczyła 63%
    listy: żywe oferty spoza nowego zakresu kategorii i spoza limitu stron.
    """
    print(chr(10) + "[11] Oferty zdjete z portalu")

    from utils.liveness import zdjete_z_portalu, dni_scrapowania

    baza = [
        {"link": "a", "source": "X", "last_seen": "2026-09-08T10:00:00"},
        {"link": "b", "source": "X", "last_seen": "2026-09-05T10:00:00"},
        {"link": "c", "source": "X", "last_seen": "2026-08-23T10:00:00"},
        {"link": "d", "source": "X", "last_seen": None},
        {"link": "e", "source": "Y", "last_seen": "2026-09-08T10:00:00"},
        {"link": "f", "source": "Y", "last_seen": "2026-08-23T10:00:00"},
    ]
    status = {"X": {"liveness": {"scope": "warszawa", "since": "2026-09-01"}}, "Y": {"history": []}}
    zdjete = zdjete_z_portalu(baza, status)

    check("oferta z ostatniego przebiegu zostaje", "a" not in zdjete)
    check("oferta pominieta przez nowszy przebieg przy tym samym zakresie jest zdjeta", "b" in zdjete)
    check("oferta widziana ostatnio przed zmiana zakresu nie jest zdjeta", "c" not in zdjete)
    check("oferta bez last_seen nie jest zdjeta", "d" not in zdjete)
    check("zrodlo bez pelnego listingu nie oznacza ofert jako zdjetych",
          "e" not in zdjete and "f" not in zdjete)

    dni = dni_scrapowania(baza)
    check("dni scrapowania czytane z ofert, nie z historii scrapera",
          dni["X"] == {"2026-09-08", "2026-09-05", "2026-08-23"} and dni["Y"] == {"2026-09-08", "2026-08-23"},
          str(dict(dni)))

    from utils.liveness import wygasle
    terminy = [
        {"link": "w1", "source": "JustJoinIT", "valid_through": "2026-10-04T23:59:59+02:00",
         "last_seen": "2026-10-02T10:00:00"},
        {"link": "w2", "source": "JustJoinIT", "valid_through": "2026-10-04T23:59:59+02:00",
         "last_seen": "2026-10-06T10:00:00"},
        {"link": "w3", "source": "JustJoinIT", "valid_through": "2026-10-07T00:00:00Z",
         "last_seen": "2026-10-02T10:00:00"},
        {"link": "w4", "source": "aplikuj.pl", "valid_through": "2026-09-20T23:59:59+02:00",
         "last_seen": "2026-09-19T10:00:00"},
        {"link": "w5", "source": "Pracuj.pl", "valid_through": None, "last_seen": "2026-09-01T10:00:00"},
    ]
    check("oferta po terminie waznosci, niewidziana od terminu, jest zdjeta",
          wygasle(terminy, "2026-10-07") == {"w1"}, str(wygasle(terminy, "2026-10-07")))

    from utils.data_models import ScraperStatusManager
    tmp = Path(tempfile.mkdtemp(prefix="test_liveness_"))
    try:
        mgr = ScraperStatusManager(str(tmp / "status.json"))
        mgr.record_liveness_scope("X", "warszawa|junior")
        first = mgr.load_status()["X"]["liveness"]
        mgr._write({"X": {"liveness": dict(first, since="2026-09-01")}})
        mgr.record_liveness_scope("X", "warszawa|junior")
        check("ten sam zakres nie przesuwa daty since",
              mgr.load_status()["X"]["liveness"]["since"] == "2026-09-01")
        mgr.record_liveness_scope("X", "warszawa|junior,mid")
        check("zmiana zakresu zaczyna liczenie od dzisiaj",
              mgr.load_status()["X"]["liveness"]["since"] != "2026-09-01")
        mgr.record_liveness_scope("X", None)
        check("niepelny listing usuwa wpis", "liveness" not in mgr.load_status()["X"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sprawdzenie_ofert():
    """
    Znaczniki zdjętej oferty per portal (wycinki z prawdziwych stron, 05.10.2026)
    i ważność wpisów w `offer_checks.json`. Bez sieci.
    """
    print(chr(10) + "[11b] Sprawdzenie ofert na portalu")

    from datetime import datetime, timedelta
    import utils.offer_check as oc

    ld = '<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Junior"}</script>'
    jjit_paczka = '"expiredOfferAlert\\":\\"Offer expired\\",'
    rj_paczka = '"expiredOfferAlert\\":\\"Oferta archiwalna\\",'
    przypadki = [
        ("praca.pl", "https://www.praca.pl/junior-front-end-developer_10000001.html", 200,
         '<div class="offer-archive">Oferta pracy jest nieaktualna, zobacz podobne</div>' + ld, oc.GONE),
        ("praca.pl", "https://www.praca.pl/doradca-ds-serwisu_10000002.html", 200, ld, oc.ALIVE),
        ("GoWork", "https://www.gowork.pl/oferta/specjalista,AbCdEfGhIjKlMnOpQrSt01,warszawa", 200,
         '<div class="g-job-offer-not-active" data-v-3d739413><span>Oferta pracy wygasła 9 dni temu. '
         'Obejrzyj inne oferty - </span></div>' + ld, oc.GONE),
        ("GoWork", "https://www.gowork.pl/oferta/kosztorysant,AbCdEfGhIjKlMnOpQrSt02,warszawa", 200, ld, oc.ALIVE),
        ("aplikuj.pl 410", "https://www.aplikuj.pl/oferta/1000001/pomoc-nauczyciela", 410, "", oc.GONE),
        ("aplikuj.pl", "https://www.aplikuj.pl/oferta/1000002/recepcjonista", 200,
         '<div class="font-medium text-gray-900">\n    Pracodawca zakończył zbieranie CV na tę ofertę.\n</div>', oc.GONE),
        ("aplikuj.pl", "https://www.aplikuj.pl/oferta/1000003/asystentka-stomatologiczna", 200, ld, oc.ALIVE),
        ("SOLID.Jobs", "https://solid.jobs/offer-not-found/10001/forma-studio-platny-staz", 200,
         "<title>SOLID.Jobs – Platforma rekrutacyjna dla specjalistów</title>", oc.GONE),
        ("SOLID.Jobs", "https://solid.jobs/offer/10002/north-labs-ruby-on-rails-developer", 200, ld, oc.ALIVE),
        ("JustJoinIT", "https://justjoin.it/job-offer/forma-studio-fullstack-developer-python-react--krakow-python", 200,
         jjit_paczka + '<div class="MuiPaper-root MuiAlert-root MuiAlert-colorInfo" role="alert" title="Offer expired">',
         oc.GONE),
        ("JustJoinIT", "https://justjoin.it/job-offer/north-labs-machine-learning-engineer-warszawa-ai", 200,
         jjit_paczka + ld, oc.ALIVE),
        ("JustJoinIT 404", "https://justjoin.it/job-offer/usunieta-oferta-warszawa-java", 404, "", oc.GONE),
        ("RocketJobs", "https://rocketjobs.pl/oferta/forma-studio-mlodszy-konsultant-warszawa-logistyka", 200,
         rj_paczka + '<div class="MuiPaper-root MuiAlert-root" role="alert" title="Oferta archiwalna" '
         'style="--Paper-shadow:none">', oc.GONE),
        ("RocketJobs", "https://rocketjobs.pl/oferta/north-labs-mlodszy-specjalista-ds-marketingu-warszawa", 200,
         rj_paczka + ld, oc.ALIVE),
        ("NoFluffJobs", "https://nofluffjobs.com/pl/job/junior-project-manager-forma-studio-warszawa-2", 200,
         '{"status":"EXPIRED","postingUrl":"junior-project-manager-forma-studio-warszawa-2"}'
         '<p class="tw-mb-0"> Oferta pracy Junior Project Manager wygasła. </p>', oc.GONE),
        ("NoFluffJobs", "https://nofluffjobs.com/pl/job/junior-devops-engineer-north-labs-remote-2", 200,
         '{"status":"DISABLED","postingUrl":"junior-devops-engineer-north-labs-remote-2"}', oc.GONE),
        ("NoFluffJobs", "https://nofluffjobs.com/pl/job/product-owner-k-m-forma-studio-warszawa", 200,
         '{"status":"PUBLISHED","postingUrl":"product-owner-k-m-forma-studio-warszawa"}', oc.ALIVE),
        ("RocketJobs 429", "https://rocketjobs.pl/oferta/x-warszawa", 429, "", oc.UNKNOWN),
        ("praca.pl 403", "https://www.praca.pl/x_1.html", 403, "", oc.UNKNOWN),
        ("GoWork 404", "https://www.gowork.pl/oferta/x,1,warszawa", 404, "", oc.UNKNOWN),
        ("JustJoinIT captcha", "https://justjoin.it/job-offer/x", 200, "<title>Just a moment...</title>", oc.UNKNOWN),
    ]
    for nazwa, url, status, html, oczekiwany in przypadki:
        stan = oc.stan_strony(oc.portal(url), status, url, html)
        check(f"{nazwa} {status}: {oczekiwany}", stan == oczekiwany, stan)

    check("sama paczka tlumaczen z 'Oferta archiwalna' nie oznacza zdjetej",
          oc.stan_strony("candidate_api", 200, "https://rocketjobs.pl/oferta/x", rj_paczka + ld) == oc.ALIVE)

    prawdziwy_get = oc.requests.get
    zapytania = []
    oc.requests.get = lambda *a, **k: zapytania.append(a)
    try:
        bez_zapytan = [oc.check_offer(link) for link in (
            "https://www.pracuj.pl/praca/junior,oferta,1000",
            "https://www.olx.pl/oferta/praca/kasjer-CID4-IDx.html",
            "https://pl.linkedin.com/jobs/view/1",
            "https://jobs.lever.co/firma/1",
        )]
        check("Pracuj/OLX/LinkedIn/ATS: unknown bez zapytania",
              bez_zapytan == [oc.UNKNOWN] * 4 and not zapytania, str(bez_zapytan))
    finally:
        oc.requests.get = prawdziwy_get

    teraz = datetime(2026, 10, 5, 12, 0)
    check("zdjeta zostaje zdjeta na zawsze",
          oc.aktualny({"state": "gone", "checked_at": "2026-01-01T00:00:00"}, teraz))
    check("zywa sprzed 23 h nie jest sprawdzana ponownie",
          oc.aktualny({"state": "alive", "checked_at": (teraz - timedelta(hours=23)).isoformat()}, teraz))
    check("zywa sprzed 25 h jest sprawdzana ponownie",
          not oc.aktualny({"state": "alive", "checked_at": (teraz - timedelta(hours=25)).isoformat()}, teraz))
    check("nieznany stan sprzed 25 h jest sprawdzany ponownie",
          not oc.aktualny({"state": "unknown", "checked_at": (teraz - timedelta(hours=25)).isoformat()}, teraz))
    check("brak wpisu = do sprawdzenia", not oc.aktualny(None, teraz))

    tmp = Path(tempfile.mkdtemp(prefix="test_offer_checks_"))
    prawdziwa_sciezka, prawdziwy_check = oc.OFFER_CHECKS_PATH, oc.check_offer
    sprawdzone = []
    stany = {"https://www.praca.pl/a_1.html": oc.GONE, "https://www.praca.pl/b_2.html": oc.ALIVE}
    oc.OFFER_CHECKS_PATH = tmp / "offer_checks.json"
    oc.check_offer = lambda link: sprawdzone.append(link) or stany[link]
    try:
        linki = ["https://www.praca.pl/a_1.html?utm_source=x", "https://www.praca.pl/b_2.html",
                 "https://www.pracuj.pl/praca/c,oferta,3"]
        zdjete = oc.sprawdz_oferty(linki, teraz)
        check("zwraca podany link zdjetej oferty", zdjete == {linki[0]}, str(zdjete))
        check("Pracuj.pl nie trafia do sprawdzania", "https://www.pracuj.pl/praca/c,oferta,3" not in sprawdzone)
        zapis = load_json_safe(oc.OFFER_CHECKS_PATH)
        check("pamiec trzyma kanoniczny link ze stanem i czasem",
              zapis.get("https://www.praca.pl/a_1.html", {}).get("state") == oc.GONE
              and "checked_at" in zapis.get("https://www.praca.pl/b_2.html", {}), str(zapis))
        sprawdzone.clear()
        oc.sprawdz_oferty(linki, teraz + timedelta(hours=2))
        check("w ciagu doby nic nie jest sprawdzane drugi raz", sprawdzone == [], str(sprawdzone))
        oc.sprawdz_oferty(linki, teraz + timedelta(hours=25))
        check("po dobie wraca tylko zywa", sprawdzone == ["https://www.praca.pl/b_2.html"], str(sprawdzone))
        check("zdjete ze sprawdzen do plakietki", oc.zdjete_ze_sprawdzen() == {"https://www.praca.pl/a_1.html"})
    finally:
        oc.OFFER_CHECKS_PATH, oc.check_offer = prawdziwa_sciezka, prawdziwy_check
        shutil.rmtree(tmp, ignore_errors=True)


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

        class StronaPadajaca(StronaFetch):
            def __init__(self, kody, padnij_przy):
                super().__init__(kody)
                self.padnij_przy = padnij_przy
                self.zamknieta = False

            def is_closed(self):
                return self.zamknieta

            def close(self):
                self.zamknieta = True

            def evaluate(self, js, arg):
                if len(self.odstepy) + 1 == self.padnij_przy:
                    self.odstepy.append(None)
                    raise RuntimeError("Page.evaluate: Target crashed")
                return super().evaluate(js, arg)

        class Kontekst:
            def __init__(self):
                self.strony = []

            def new_page(self):
                strona = StronaPadajaca(lambda n: 200, padnij_przy=0)
                strona.set_default_timeout = lambda t: None
                self.strony.append(strona)
                return strona

        scraper = object.__new__(olx.OLXScraper)
        scraper.page = StronaPadajaca(lambda n: 200, padnij_przy=2)
        scraper.context = Kontekst()
        scraper.timeout = 1000
        scraper.seen_again_links = []
        scraper.enrich_descriptions(zbuduj(60))
        check("po padnieciu karty fetch rusza na nowej i nie gubi ofert",
              scraper.enrich_stats["ok"] == 60 and len(scraper.context.strony) == 1,
              f"{scraper.enrich_stats}, nowych kart: {len(scraper.context.strony)}")

        class StronaListingu:
            def __init__(self, padnieta):
                self.padnieta = padnieta

            def is_closed(self):
                return False

            def close(self):
                pass

            def goto(self, *a, **k):
                if self.padnieta:
                    raise RuntimeError("Page.goto: Page crashed")
                return type("R", (), {"status": 200, "text": lambda s: "<html>ok</html>"})()

        class KontekstListingu:
            def new_page(self):
                strona = StronaListingu(padnieta=False)
                strona.set_default_timeout = lambda t: None
                return strona

        scraper = object.__new__(olx.OLXScraper)
        scraper.page = StronaListingu(padnieta=True)
        scraper.context = KontekstListingu()
        scraper.timeout = 1000
        scraper.max_retries = 3
        scraper.delay = 0
        check("listing po padnieciu karty wchodzi na nowej",
              scraper._wejdz_i_wez_html("https://www.olx.pl/praca/x/") == "<html>ok</html>")

        class Strona404(StronaListingu):
            wejscia = 0

            def goto(self, *a, **k):
                Strona404.wejscia += 1
                return type("R", (), {"status": 404, "text": lambda s: "<html>Ups</html>"})()

        scraper.page = Strona404(padnieta=False)
        check("martwy slug (404) to porazka listingu, bez ponawiania",
              scraper._wejdz_i_wez_html("https://www.olx.pl/praca/x/") is None
              and Strona404.wejscia == 1, f"wejsc: {Strona404.wejscia}")

        import utils.scraper_health as sh
        werdykt = sh.check("OLX Praca", 500, failed_parts=["praktyki-staze"])
        check("kategoria bez listingu nie jest cichym sukcesem",
              bool(werdykt) and werdykt["verdict"] == "degraded"
              and "praktyki-staze" in werdykt["detail"], str(werdykt))
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

    zdrowy = {"success": True, "status": "scraped"}
    blad = {"success": False, "status": "failed"}
    pominiety = {"success": True, "status": "skipped"}
    check("jedno zepsute zrodlo nie zatrzymuje przebiegu",
          main_scraper._scrape_failed({"zdrowy": zdrowy, "blad": blad, "pominiety": pominiety},
                                      [{"source": "blad", "verdict": "broken", "detail": "wyjatek"}]) is False)
    check("same awarie zatrzymuja przebieg",
          main_scraper._scrape_failed({"blad": blad, "pominiety": pominiety}, []) is True)
    check("zrodlo z zerem ofert przy dawnych wynikach nie liczy sie jako dzialajace",
          main_scraper._scrape_failed({"pusty": zdrowy, "blad": blad},
                                      [{"source": "pusty", "verdict": "broken", "detail": "zero ofert"}]) is True)
    check("przebieg z samymi pominietymi zrodlami nie jest awaria",
          main_scraper._scrape_failed({"pominiety": pominiety}, []) is False)


def test_stop_during_scraping():
    print("\n[14b] Stop during scraping saves collected offers and leaves the source unfinished")
    from unittest.mock import patch
    import main_scraper
    from utils import stop

    temp = Path(tempfile.mkdtemp(prefix="test_stop_scraping_"))
    flag = temp / "pipeline_stop_requested.flag"
    ran, saved, status_calls = [], [], []

    def fake_scraper(name, stops=False):
        class Fake:
            def __init__(self, config):
                self.seen_again_links = []
                self.liveness_scope = "scope"

            def get_source_name(self):
                return name

            def run(self):
                ran.append(name)
                if stops:
                    flag.touch()
                return [object()] * 6
        return Fake

    class Status:
        def is_scraped_today(self, name): return False
        def get_history(self, name): return []
        def record_yield(self, *args): status_calls.append(("yield",) + args)
        def mark_as_completed(self, *args): status_calls.append(("completed",) + args)
        def record_liveness_scope(self, *args): status_calls.append(("liveness",) + args)

    class Db:
        def __init__(self, path): pass
        def record_scrape(self, jobs, seen): saved.append(len(jobs)); return len(jobs), 0
        def load_jobs(self): return []

    names = ["PracujOptimizedScraper", "OLXScraper", "RocketJobsScraper", "LinkedInScraper", "NoFluffScraper",
             "JustJoinScraper", "SolidJobsAPIScraper", "AdzunaAPIScraper", "JoobleAPIScraper",
             "CareerjetAPIScraper", "PracaPlScraper", "AplikujScraper", "GoWorkScraper", "ATSFeedsScraper",
             "IndeedScraper"]
    fakes = {cls: fake_scraper(f"Src {cls}") for cls in names}
    fakes["PracujOptimizedScraper"] = fake_scraper("Alpha", stops=True)
    fakes["OLXScraper"] = fake_scraper("Omega")
    try:
        with patch.multiple(main_scraper, ScraperStatusManager=Status, JobDatabase=Db, **fakes), \
             patch.object(stop, "STOP_FLAG_FILE", flag), redirect_stdout(io.StringIO()):
            jobs = main_scraper.run_all_scrapers(only=["alpha", "omega"])
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    check("stopped scraping returns what it collected instead of failing", len(jobs) == 6, len(jobs))
    check("sources not started before the stop never run", ran == ["Alpha"], ran)
    check("offers collected before the stop are saved", saved == [6], saved)
    check("a stopped source is not marked as scraped today and reports no full listing",
          status_calls == [], status_calls)


def test_listing_completeness():
    print("\n[14c] ld+json listing counts as full only when pagination ends on its own")
    from scrapers.gowork_scraper import GoWorkScraper

    def collect(pages, max_pages=5, max_offers=400):
        scraper = object.__new__(GoWorkScraper)
        scraper.max_pages, scraper.max_offers = max_pages, max_offers
        scraper.build_listing_url = lambda page: page
        scraper.extract_offer_links = lambda html: html.split()
        scraper._fetch = lambda page: pages[page - 1] if page <= len(pages) else ""
        links = scraper._collect_links()
        return scraper.listing_complete, len(links)

    page = lambda *ids: " ".join(f"https://www.gowork.pl/oferta/{i}" for i in ids)
    check("pagination ending on an empty page is a full listing", collect([page(1, 2), page(3)]) == (True, 3))
    check("a portal repeating its pages is a full listing", collect([page(1), page(1), page(1)]) == (True, 1))
    check("a page that failed to load is not a full listing", collect([page(1, 2), None, page(3)]) == (False, 2))
    check("hitting the offer limit is not a full listing", collect([page(1, 2), page(3, 4)], max_offers=3) == (False, 3))
    check("hitting the page limit is not a full listing",
          collect([page(1), page(2), page(3)], max_pages=2) == (False, 2))


def test_category_fallback():
    print("\n[14d] Category pick falls back to the previous choice when Jev fails")
    from unittest.mock import patch
    from matching.jev import JevError
    from utils import candidate_scope, cv_profile, portal_categories

    def fail(*args, **kwargs):
        raise JevError("HTTP 500")

    catalog = {"it": "IT", "office": "Biuro", "new": "Nowa"}
    path = candidates.path(candidates.CATEGORY_SCOPE)
    with patch.object(candidate_scope, "load_profile", return_value={"seniority": "junior", "skills": ["SQL"]}), \
         patch.object(cv_profile, "cv_text", return_value="CV"), \
         patch.object(portal_categories, "_ask_jev", fail):
        path.unlink(missing_ok=True)
        check("no previous choice: search without a category filter",
              portal_categories.pick_categories("demo", catalog) is None)
        path.write_text(json.dumps({"demo": {"profile_fp": "old", "catalog_fp": "old",
                                             "picked": ["it", "gone"]}}), encoding="utf-8")
        check("previous choice is reused, minus categories the portal dropped",
              portal_categories.pick_categories("demo", catalog) == ["it"])
    path.unlink(missing_ok=True)


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
    import pipeline_manager as sapp
    from utils import stop
    from pipeline_manager import PipelineProcessManager, load_json_safe
    import tempfile, shutil, time, sys, io
    from unittest.mock import patch
    from contextlib import redirect_stdout
    import matching.run

    old_cwd = os.getcwd()
    temp_dir = tempfile.mkdtemp(prefix="test_lifecycle_")
    temp_path = Path(temp_dir)

    # Isolate all module state paths to temporary directory
    t_state_lock = temp_path / "pipeline_run_state.json"
    t_stop_flag = temp_path / "pipeline_stop_requested.flag"
    t_checkpoint = temp_path / "pipeline_checkpoint.json"

    patches = [
        patch.object(sapp, "STATE_LOCK_FILE", t_state_lock),
        patch.object(sapp, "CHECKPOINT_FILE", t_checkpoint),
        patch.object(stop, "STOP_FLAG_FILE", t_stop_flag),
        patch.object(pipeline, "CHECKPOINT_FILE", t_checkpoint),
        patch("utils.cv_profile.ensure_profile", return_value={"seniority": "mid", "city": "Warszawa", "skills": ["python"]}),
    ]

    for p in patches:
        p.start()

    try:
        os.chdir(temp_dir)

        # 1. Real run_pipeline lifecycle: initial checkpoint inside phase0 and stop during matching
        phase0_saw_checkpoint = {"seen": False, "has_options": False}

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

        with patch.object(pipeline, "purge_stale_offers") as mock_p0, \
             patch.object(pipeline, "migrate_normalize_links") as mock_p1_5, \
             patch.object(pipeline, "deduplicate_db") as mock_p2, \
             patch.object(pipeline, "clean_db") as mock_p2_5, \
             patch.object(pipeline, "JobDatabase") as mock_db_cls, \
             patch("matching.run.main", side_effect=fake_matching_stopping):

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

        cp_after_stop = load_json_safe(t_checkpoint, default={})
        check("checkpoint marks stopped is True", cp_after_stop.get("stopped") is True)
        check("checkpoint preserves completed stages before stop", "phase2_5" in cp_after_stop.get("completed_stages", []))

        # 2. Resuming run_pipeline skips completed stages
        phase0_rerun = {"called": False}
        matching_resumed = {"called": False}

        def fake_purge_rerun():
            phase0_rerun["called"] = True
            return True

        def fake_matching_success(*args, **kwargs):
            matching_resumed["called"] = True
            return 0

        with patch.object(pipeline, "purge_stale_offers") as mock_p0, \
             patch.object(pipeline, "migrate_normalize_links") as mock_p1_5, \
             patch.object(pipeline, "deduplicate_db") as mock_p2, \
             patch.object(pipeline, "clean_db") as mock_p2_5, \
             patch.object(pipeline, "JobDatabase") as mock_db_cls, \
             patch("matching.run.main", side_effect=fake_matching_success):

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
        check("resumed pipeline executed phase3", matching_resumed["called"] is True)
        check("resumed pipeline output contains PIPELINE COMPLETE", "PIPELINE COMPLETE" in resume_output)

        # 3. Slow cooperative stop (>1.5s grace period) and subsequent clean start without immediate stop
        mgr = PipelineProcessManager()
        # Child runs for 2.0s - deliberately exceeding the manager's 1.5s cooperative polling window!
        slow_cmd = [sys.executable, "-c", "import time; time.sleep(2.0)"]
        started, _ = mgr.start_pipeline(mode="full", cmd=slow_cmd)
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
        next_started, _ = mgr.start_pipeline(mode="full", cmd=quick_cmd)
        check("new start_pipeline started successfully", next_started is True)
        check("stale stop flag cleared by start_pipeline", not t_stop_flag.exists())

        if mgr._thread:
            mgr._thread.join(timeout=3.0)
        next_state = mgr.get_state()
        check("new run completed successfully instead of immediately stopping", next_state.get("status") == "completed")

        # 4. Standalone tool exit semantics: code 0 = completed, code 1 = failed
        mgr_sa = PipelineProcessManager()
        sa_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]
        ok_sa, _ = mgr_sa.start_pipeline(mode="standalone", cmd=sa_cmd, stage="phase3")
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
        ok_fa, _ = mgr_fa.start_pipeline(mode="full", cmd=fa_cmd)
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

    res_rebind_offers = client.get("/api/offers", headers={"host": "evil.org"})
    check("foreign host rejected on GET /api/offers with 400 Bad Request", res_rebind_offers.status_code == 400)

    # 3. Cross-origin mutation protection rejects foreign Origin on POST
    res_csrf = client.post("/api/offers/decision",
                           json={"link": "https://example.com/test", "status": "save"},
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

    orig_jobs = app_services.JOBS_DATABASE_PATH
    orig_root = candidates.ROOT

    app_services.JOBS_DATABASE_PATH = t_jobs
    candidates.ROOT = temp_path / "candidates"
    t_matches = candidates.path(candidates.MATCH_RESULTS)
    t_decisions = candidates.path(candidates.DECISIONS)
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
        decision_data = {"https://corp-a.com/job1": {"status": "save", "stage": "save"}}
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
        candidates.ROOT = orig_root
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
         patch("utils.stop.STOP_FLAG_FILE", temp_dir / "pipeline_stop_requested.flag"):
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

    # 1. Completed state with 1 skipped + 5 done:
    mgr._status = "completed"
    mgr._stages = [
        {"name": "scrape", "title": "Scraping portali", "status": "skipped"},
        {"name": "extract", "title": "Ekstrakcja", "status": "done"},
        {"name": "enrich", "title": "Wzbogacanie", "status": "done"},
        {"name": "cluster", "title": "Klasteryzacja", "status": "done"},
        {"name": "match", "title": "Dopasowanie", "status": "done"},
        {"name": "report", "title": "Raport", "status": "done"},
    ]
    mgr._active_stage_idx = 5
    mgr._exit_code = 0
    mgr._pipeline_complete_seen = True

    state_completed = mgr.get_state()
    check("completed with skipped stage reports 100.0% progress", state_completed["progress_percent"] == 100.0)
    check("completed with skipped stage labels 6/6 resolved stages", "6/6" in state_completed["progress_label"])
    check("completed pipeline cannot be resumed", state_completed["can_resume"] is False)

    # 2. Running state when stage 0 is skipped and stage 1 is active (stages 2-5 pending):
    mgr._stages = [
        {"name": "scrape", "title": "Scraping portali", "status": "skipped"},
        {"name": "extract", "title": "Ekstrakcja", "status": "running"},
        {"name": "enrich", "title": "Wzbogacanie", "status": "pending"},
        {"name": "cluster", "title": "Klasteryzacja", "status": "pending"},
        {"name": "match", "title": "Dopasowanie", "status": "pending"},
        {"name": "report", "title": "Raport", "status": "pending"},
    ]
    mgr._status = "running"
    mgr._active_stage_idx = 1
    state_running = mgr.get_state()
    check("running with 1 skipped stage reflects resolved stage in progress_percent",
          state_running["progress_percent"] == round((1 / 6) * 100, 1))
    check("running state indicates active stage", "Etap 2 z 6" in state_running["progress_label"])

    # 3. Stopped state with unfinished stages:
    mgr._process = ExitedProc()
    mgr._status = "stopped"
    state_stopped = mgr.get_state()
    check("stopped pipeline with remaining stages allows resume", state_stopped["can_resume"] is True)


def test_pipeline_events():
    print("\n[23] Stage track and run numbers from utils.telemetry events")
    import io
    from contextlib import redirect_stdout
    from pipeline_manager import PipelineProcessManager
    from utils import telemetry

    out = io.StringIO()
    with redirect_stdout(out):
        print("2026-09-23 10:02:12,123 - Pipeline - INFO - ── PHASE 0: Drop offers older than 14 days")
        telemetry.emit("stage", id="phase0", state="running")
        telemetry.emit("stage_stats", id="phase0", removed=37)
        telemetry.emit("stage", id="phase0", state="done")
        telemetry.emit("stage", id="phase0_5", state="running")
    lines = out.getvalue().splitlines()
    check("a human log line is not an event", telemetry.parse(lines[0]) is None)

    mgr = PipelineProcessManager()
    mgr._init_stages("full")
    for line in lines[1:]:
        mgr._apply_event(telemetry.parse(line))
    by_id = {s["id"]: s for s in mgr._stages}
    check("stage 00 keeps running from the purge into the CV profile",
          by_id["phase0"]["status"] == "running" and mgr._active_stage_idx == 0, by_id["phase0"])
    check("stage numbers land on their stage", mgr._telemetry_snapshot()["stages"].get("phase0") == {"removed": 37})

    for ev in [{"event": "stage", "id": "phase0_5", "state": "done"},
               {"event": "stage", "id": "phase1", "state": "cached"},
               {"event": "stage", "id": "phase1_5", "state": "running"},
               {"event": "stage", "id": "phase1_5", "state": "failed"}]:
        mgr._apply_event(ev)
    check("stage 00 ends with the CV profile", by_id["phase0"]["status"] == "done")
    check("a stage done in an earlier run is done without a start time",
          by_id["phase1"]["status"] == "done" and by_id["phase1"]["started_at"] is None, by_id["phase1"])
    check("a failed stage is failed and active",
          by_id["phase1_5"]["status"] == "failed" and mgr._active_stage_idx == 2, by_id["phase1_5"])

    for ev in [{"event": "prefilter", "rejected": 12, "reasons": {"miasto": 10, "poziom": 2}, "to_score": 38},
               {"event": "scored", "done": 10, "total": 38, "rate": 5.0},
               {"event": "jev_error"},
               {"event": "scored", "done": 25, "total": 38, "rate": 5.2}]:
        mgr._apply_event(ev)
    sc = mgr._telemetry_snapshot()["scoring"]
    check("scoring: progress, pace and live Jev errors",
          (sc["scored"], sc["to_score"], sc["rate"], sc["errors"], sc["prefilter_rejected"]) == (25, 38, 5.2, 1, 12)
          and sc["last_done_at"] is not None, sc)
    mgr._apply_event({"event": "matching_done", "scored_now": 36, "errors": 2, "with_percent": 140})
    phase3 = mgr._telemetry_snapshot()["stages"]["phase3"]
    check("scoring: final summary sets errors and offers with a percent",
          (phase3["errors"], phase3["with_percent"], mgr._telemetry["scoring"]["errors"]) == (2, 140, 2), phase3)

    for ev in [{"event": "source", "name": "aplikuj.pl", "state": "running"},
               {"event": "source_details", "name": "aplikuj.pl", "done": 1200, "total": 3482},
               {"event": "source_details", "name": "GoWork.pl", "done": 100, "total": 200},
               {"event": "source", "name": "GoWork.pl", "state": "failed", "error": "403 Client Error: Forbidden"},
               {"event": "source_health", "name": "GoWork.pl", "verdict": "inconclusive", "detail": "portal odmówił"}]:
        mgr._apply_event(ev)
    by_name = {s["name"]: s for s in mgr._telemetry_snapshot()["sources"]}
    check("details: progress lands on its source",
          (by_name["aplikuj.pl"]["details_done"], by_name["aplikuj.pl"]["details_total"]) == (1200, 3482))
    check("details: progress of an unannounced source creates no row",
          by_name["GoWork.pl"]["details_done"] is None, by_name["GoWork.pl"])
    check("failed source keeps its error and the health verdict",
          by_name["GoWork.pl"]["state"] == "failed" and by_name["GoWork.pl"]["error"] == "403 Client Error: Forbidden"
          and by_name["GoWork.pl"]["health"] == {"verdict": "inconclusive", "detail": "portal odmówił"})
    kinds = [ev["event"] for ev in mgr._events]
    check("progress events stay out of the activity feed",
          "scored" not in kinds and "source_details" not in kinds and "source" in kinds and "stage" in kinds, kinds)


def test_offer_api_contract():
    print("\n[24] Fresh offers, tabs, notes, next step and decisions over HTTP")
    import tempfile, shutil, json
    from pathlib import Path
    from starlette.testclient import TestClient
    import app_services
    import server

    temp_path = Path(tempfile.mkdtemp(prefix="test_offer_api_"))
    orig_jobs, orig_root = app_services.JOBS_DATABASE_PATH, candidates.ROOT
    app_services.JOBS_DATABASE_PATH = temp_path / "jobs_database.json"
    candidates.ROOT = temp_path / "candidates"
    paths = {
        "JOBS_DATABASE_PATH": app_services.JOBS_DATABASE_PATH,
        "MATCH_RESULTS_PATH": candidates.path(candidates.MATCH_RESULTS),
        "USER_DECISIONS_PATH": candidates.path(candidates.DECISIONS),
        "LAST_SCRAPE_RUN_PATH": candidates.path(candidates.LAST_SCRAPE_RUN),
    }

    def job(n, scraped_at):
        return {"title": f"Analityk {n}", "company": "Corp", "link": f"https://corp.example/job{n}",
                "description": "Analiza danych w SQL i raporty dla zarządu, ponad 20 znaków", "source": "Test",
                "scraped_at": scraped_at}

    jobs = [job(1, "2026-09-20T18:00:00"), job(2, "2026-09-20T19:30:00"), job(3, "2026-09-20T19:40:00"),
            job(4, "2026-09-20T10:00:00"), job(5, "2026-09-20T10:00:00"), job(6, "2026-09-20T10:00:00"),
            job(7, "2026-09-20T10:00:00")]
    matches = {
        jobs[0]["link"]: {"percent": 80},
        jobs[1]["link"]: {"percent": 70},
        jobs[2]["link"]: {"percent": 75},
        jobs[3]["link"]: {"percent": 3},
        jobs[4]["link"]: {"percent": 0},
        jobs[5]["link"]: {"percent": None, "filtered": "miasto"},
    }
    decisions = {jobs[0]["link"]: "save", jobs[2]["link"]: "reject",
                 jobs[3]["link"]: {"status": "rated", "rating": 7}}
    paths["JOBS_DATABASE_PATH"].write_text(json.dumps(jobs), encoding="utf-8")
    paths["MATCH_RESULTS_PATH"].write_text(json.dumps(matches), encoding="utf-8")
    paths["USER_DECISIONS_PATH"].write_text(json.dumps(decisions), encoding="utf-8")
    paths["LAST_SCRAPE_RUN_PATH"].write_text(json.dumps({"started_at": "2026-09-20T19:00:00"}), encoding="utf-8")

    try:
        app_services.job_data_service.reload()
        client = TestClient(server.app, base_url="http://127.0.0.1:8501")

        matched = client.get("/api/offers?tab=Dopasowane").json()
        check("Dopasowane: every undecided offer with a percent, legacy rated entries included, best first",
              [row["link"] for row in matched["items"]] == [jobs[1]["link"], jobs[3]["link"], jobs[4]["link"]],
              [row["link"] for row in matched["items"]])
        check("offers scraped after the last run start are new",
              [row["link"] for row in matched["items"] if row["is_new"]] == [jobs[1]["link"]])
        check("fresh_count counts the whole list", matched.get("fresh_count") == 1)
        hidden = client.get("/api/offers?tab=Ukryte").json()
        check("hidden offers: rejected ones, counted for the filter",
              [row["link"] for row in hidden["items"]] == [jobs[2]["link"]] and matched["hidden_count"] == 1)
        saved_tab = client.get("/api/offers?tab=Zapisane").json()
        check("Zapisane holds saved offers", [row["link"] for row in saved_tab["items"]] == [jobs[0]["link"]])
        check("unknown tab is rejected", client.get("/api/offers?tab=Wszystkie").status_code == 400)

        client.post("/api/offers/decision", json={"link": jobs[3]["link"], "status": "save"})
        noted = client.post("/api/offers/note", json={"link": jobs[3]["link"], "note": " Wysłać po portfolio "})
        on_disk3 = json.loads(paths["USER_DECISIONS_PATH"].read_text(encoding="utf-8"))[jobs[3]["link"]]
        check("saving drops the legacy rating key and the note is trimmed",
              "rating" not in on_disk3 and noted.json()["offer"]["note"] == "Wysłać po portfolio", on_disk3)
        check("note needs a decision",
              client.post("/api/offers/note", json={"link": jobs[1]["link"], "note": "x"}).status_code == 400)
        stats = client.get("/api/stats").json()
        check("stats split the base into scored, filtered and not yet seen",
              (stats["scored_count"], stats["filtered_count"],
               stats["pending_scoring_count"], stats["fresh_count"]) == (5, 1, 1, 2), stats)

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

        client.post("/api/offers/decision", json={"link": jobs[0]["link"], "status": "apply", "stage": "interview"})
        board = client.get("/api/applications").json()
        card = next((i for i in board["items"] if i["link"] == jobs[0]["link"]), None)
        check("next step survives a stage change and reaches the board",
              card is not None and card["next_step"] == {"label": "Rozmowa z HR", "due": "2026-09-26 10:00"})
        check("board column follows the stage", card is not None and card["stage"] == "interview"
              and board["counts"]["interview"] == 1, board["counts"])
        check("unknown stage is rejected", client.post(
            "/api/offers/decision", json={"link": jobs[0]["link"], "status": "apply", "stage": "save"}).status_code == 400)

        cleared = client.post("/api/offers/next-step", json={"link": jobs[0]["link"], "label": "", "due": None})
        check("empty label removes the next step", cleared.status_code == 200 and cleared.json()["offer"]["next_step"] is None)
    finally:
        app_services.JOBS_DATABASE_PATH = orig_jobs
        candidates.ROOT = orig_root
        app_services.job_data_service.reload()
        shutil.rmtree(temp_path, ignore_errors=True)


def test_foreign_run_visible_to_server():
    print("\n[25] Run started outside the server is visible and stoppable from the server")
    import tempfile, shutil, time
    from unittest.mock import patch
    import pipeline_manager as pm
    from pipeline_manager import PipelineProcessManager, load_json_safe
    from utils import stop, telemetry

    temp_path = Path(tempfile.mkdtemp(prefix="test_foreign_run_"))
    state_file = temp_path / "pipeline_run_state.json"
    stop_flag = temp_path / "pipeline_stop_requested.flag"
    # Nazwa jak prawdziwy punkt wejścia: menedżer rozpoznaje po niej przebieg pipeline'u.
    child = temp_path / "run_final_pipeline.py"
    child.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "flag = Path(sys.argv[1])\n"
        f"event = lambda **e: print({telemetry.PREFIX!r} + json.dumps(e), flush=True)\n"
        "print('── PHASE 3: Matching (prefilter + Jev)', flush=True)\n"
        "event(event='stage', at=0, id='phase3', state='running')\n"
        "event(event='prefilter', at=0, rejected=10, reasons={'tech': 10}, to_score=150)\n"
        "event(event='scored', at=0, done=1, total=150, rate=2.5)\n"
        "deadline = time.time() + 15\n"
        "while not flag.exists() and time.time() < deadline:\n"
        "    time.sleep(0.05)\n"
        "print('PIPELINE STOPPED', flush=True)\n"
        "event(event='pipeline', at=0, result='stopped')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    with patch.object(pm, "STATE_LOCK_FILE", state_file), \
         patch.object(stop, "STOP_FLAG_FILE", stop_flag), \
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
            check("terminal output is echoed without event lines",
                  "PIPELINE STOPPED" in echoed and not any(line.startswith(telemetry.PREFIX) for line in echoed))
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)


def test_company_logos():
    print("\n[26] Company logos from the data scrapers already download")
    from bs4 import BeautifulSoup
    from utils.data_models import Job
    from utils.links import logo_url

    check("protocol-relative logo becomes https", logo_url("//cdn.example.pl/l.png") == "https://cdn.example.pl/l.png")
    check("http logo is upgraded to https", logo_url("http://cdn.example.pl/l.png") == "https://cdn.example.pl/l.png")
    check("a non-http logo is rejected", logo_url("data:image/png;base64,AAAA") is None)
    check("a relative logo without a base is rejected", logo_url("/media/l.png") is None)

    from scrapers.justjoin_scraper import JustJoinScraper
    jj = object.__new__(JustJoinScraper)
    jj.config = {}
    thumb = ("https://imgproxy.justjoinit.tech/Sig123/h:200/w:200/plain/"
             "https://public.justjoin.it/companies/logos/original/fikcyjna.jpg")
    offer = {"title": "Junior Tester", "companyName": "Fikcyjna Sp. z o.o.", "slug": "fikcyjna-junior-tester",
             "city": "Warszawa", "companyLogoThumbUrl": thumb}
    check("JustJoin/RocketJobs: logo from companyLogoThumbUrl",
          jj._parse(offer, {}).logo_url == thumb, str(jj._parse(offer, {}).logo_url))
    check("JustJoin/RocketJobs: no logo gives None",
          jj._parse(dict(offer, companyLogoThumbUrl=None), {}).logo_url is None)

    from scrapers.nofluff_scraper import NoFluffScraper
    nfj = NoFluffScraper.__new__(NoFluffScraper)
    posting = {"title": "Junior Analyst", "name": "Fikcyjna Analityka", "url": "junior-analyst-fikcyjna-warszawa",
               "logo": {"original": "companies/logos/original/fikcyjna_20260101.png",
                        "jobs_listing": "companies/logos/jobs_listing/fikcyjna_20260101.png",
                        "jobs_details": "companies/logos/jobs_details/fikcyjna_20260101.png"}}
    got = nfj._parse_posting(posting, {}).logo_url
    check("NoFluffJobs: relative logo path gets the static host and the 100px variant",
          got == "https://static.nofluffjobs.com/companies/logos/jobs_details/fikcyjna_20260101.png", str(got))
    check("NoFluffJobs: no logo gives None",
          nfj._parse_posting(dict(posting, logo=None), {}).logo_url is None)

    from scrapers.pracuj_optimized_scraper import PracujOptimizedScraper
    pracuj = object.__new__(PracujOptimizedScraper)
    pracuj.city = "Warszawa"
    grouped = [
        {"jobTitle": "Asystent biura", "companyName": "Fikcyjne Biuro",
         "companyLogoUri": "https://logos.gpcdn.pl/loga-firm/1/fikcyjne_280x280.png",
         "offers": [{"offerAbsoluteUri": "https://www.pracuj.pl/praca/asystent-biura,oferta,1001"}]},
        {"jobTitle": "Magazynier", "companyName": "Fikcyjny Magazyn", "companyLogoUri": None,
         "offers": [{"offerAbsoluteUri": "https://www.pracuj.pl/praca/magazynier,oferta,1002"}]},
    ]
    next_data = {"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": ["jobOffers"], "state": {"data": {"groupedOffers": grouped}}}]}}}}
    pracuj.fetch_page_html = lambda page: ('<html><script id="__NEXT_DATA__" type="application/json">'
                                          + json.dumps(next_data) + "</script></html>")
    items = pracuj.parse_listing_page(1)
    check("Pracuj.pl: logo from companyLogoUri, None when the offer has none",
          [i.get("logo_url") for i in items]
          == ["https://logos.gpcdn.pl/loga-firm/1/fikcyjne_280x280.png", None], str(items))

    from scrapers.gowork_scraper import GoWorkScraper
    from scrapers.aplikuj_scraper import AplikujScraper

    def ld_page(org, body=""):
        posting = {"@type": "JobPosting", "title": "Pracownik biurowy", "description": "Opis oferty testowej.",
                   "hiringOrganization": org}
        return ('<html><script type="application/ld+json">' + json.dumps(posting)
                + "</script>" + body + "</html>")

    def detail(scraper_cls, page, link):
        scraper = object.__new__(scraper_cls)
        scraper._fetch = lambda url, attempts=2: page
        return scraper._fetch_detail(link)[0].logo_url

    gw_link = "https://www.gowork.pl/oferta/pracownik-biurowy,abc,warszawa"
    got = detail(GoWorkScraper, ld_page({"@type": "Organization", "name": "Fikcyjna Firma",
                                         "logo": "https://www.gowork.pl/media/cache/job_company_logo/f.png"}), gw_link)
    check("ld+json: hiringOrganization.logo string", got == "https://www.gowork.pl/media/cache/job_company_logo/f.png", str(got))
    got = detail(GoWorkScraper, ld_page({"name": "Fikcyjna Firma",
                                         "logo": {"@type": "ImageObject", "url": "/media/logo/f.png"}}), gw_link)
    check("ld+json: ImageObject with a relative url resolves against the offer page",
          got == "https://www.gowork.pl/media/logo/f.png", str(got))
    got = detail(GoWorkScraper, ld_page({"@type": "Organization", "name": "Fikcyjna Firma"}), gw_link)
    check("ld+json: organization without a logo gives None", got is None, str(got))

    ap_link = "https://www.aplikuj.pl/oferta/1001/pracownik-biurowy"
    company = "Fikcyjna & Syn Sp. z o.o."
    body = ('<img src="/build/logo/logo-blue.svg" alt="Aplikuj.pl">'
            '<img loading="lazy" src="/media/offerTemplate/2026/baner.avif" alt="Pracownik biurowy">'
            '<img class="inline-block" src="/media/SygnaturaFirmy&amp;x=1" alt="Fikcyjna &amp; Syn Sp. z o.o.">'
            '<img class="offer-card-company-logo__img" src="/media/InnaFirma" alt="Inna Firma">')
    got = detail(AplikujScraper, ld_page({"@type": "Organization", "name": company}, body), ap_link)
    check("aplikuj.pl: logo is the page image captioned with the hiring company",
          got == "https://www.aplikuj.pl/media/SygnaturaFirmy&x=1", str(got))
    got = detail(AplikujScraper, ld_page({"@type": "Organization", "name": "Bez Logo SA"}, body), ap_link)
    check("aplikuj.pl: no image of this company gives None", got is None, str(got))

    from scrapers.solid_jobs_api import SolidJobsAPIScraper
    solid = object.__new__(SolidJobsAPIScraper)
    solid.config = {}
    solid_offer = {"title": "Junior HR", "company": "Fikcyjne HR", "url": "https://solid.jobs/offer/1/junior-hr",
                   "locations": ["Warszawa"],
                   "companyLogoUrl": "https://cdn.solid.jobs/companies/logos/fikcyjne.png"}
    check("SOLID.Jobs: logo from companyLogoUrl",
          solid._parse_offer(solid_offer).logo_url == "https://cdn.solid.jobs/companies/logos/fikcyjne.png")
    check("SOLID.Jobs: no logo gives None",
          solid._parse_offer(dict(solid_offer, companyLogoUrl=None)).logo_url is None)

    import scrapers.olx_scraper as olx
    olx_scraper = object.__new__(olx.OLXScraper)
    ad = {"url": "https://www.olx.pl/oferta/praca/kasjer-CID4-IDfik.html", "status": "active",
          "description": "<p>Obsluga kasy w sklepie testowym.</p>",
          "user": {"name": "Fikcyjny Sklep", "logo": None,
                   "logo_ad_page": "https://img-resizer.example.olx.org/img-eu-olxpl-production/1_1_261x203.jpg"}}

    def olx_logo(ad_):
        job = Job(title="Kasjer", company="OLX", link=ad_["url"],
                  description="Oferta z OLX (kategoria: sprzedaz)", source="OLX Praca")
        olx_scraper._z_ogloszenia(job, ad_)
        return job.logo_url

    check("OLX: logo of the business account from the listing state",
          olx_logo(ad) == "https://img-resizer.example.olx.org/img-eu-olxpl-production/1_1_261x203.jpg")
    check("OLX: private account without a logo gives None",
          olx_logo(dict(ad, user={"name": "Jan", "logo": None, "logo_ad_page": None})) is None)

    from scrapers.linkedin_scraper import LinkedInScraper
    card = BeautifulSoup(
        '<li><img class="artdeco-entity-image artdeco-entity-image--square-4" '
        'data-delayed-url="https://media.licdn.com/dms/image/v2/X/company-logo_100_100/0/1/fikcyjna_logo?e=2147483647&amp;v=beta" '
        'data-ghost-url="https://static.licdn.com/aero-v1/sc/h/ghost" alt=""></li>', "html.parser")
    check("LinkedIn: logo from the card's data-delayed-url",
          LinkedInScraper._card_logo(card)
          == "https://media.licdn.com/dms/image/v2/X/company-logo_100_100/0/1/fikcyjna_logo?e=2147483647&v=beta")
    ghost = BeautifulSoup('<li><img class="artdeco-entity-image" '
                          'data-ghost-url="https://static.licdn.com/aero-v1/sc/h/ghost" alt=""></li>', "html.parser")
    check("LinkedIn: the ghost placeholder is not a logo", LinkedInScraper._card_logo(ghost) is None)


def test_candidates():
    print("\n[27] Candidates: migration, isolation, shared offers and CV change without rescoring")
    import hashlib
    from unittest.mock import patch
    from starlette.testclient import TestClient
    import app_services
    import matching.run as matching_run
    import purge_stale_offers
    import server
    from utils import cv_profile
    from utils.cv_profile import profile_fingerprint

    temp_path = Path(tempfile.mkdtemp(prefix="test_candidates_flow_"))
    orig_jobs, orig_root, old_cwd = app_services.JOBS_DATABASE_PATH, candidates.ROOT, os.getcwd()
    cv = "Analityk danych. SQL, Python, raporty dla zarządu. Warszawa."
    profile = {"city": "Warszawa", "seniority": "junior", "years_experience": 1.0, "skills": ["SQL"],
               "languages": [{"name": "polish", "level": "C2"}], "roles": ["Analityk"],
               "_metadata": {"cv_sha256": hashlib.sha256(cv.encode("utf-8")).hexdigest()}}

    def job(n, last_seen="2099-01-01"):
        return {"title": f"Analityk {n}", "company": "Corp", "link": f"https://corp.example/job{n}",
                "location": "Warszawa", "description": "Analiza danych w SQL i raporty dla zarządu.",
                "source": "Test", "scraped_at": "2026-09-20T10:00:00", "last_seen": last_seen}

    jobs = [job(1), job(2), job(3, last_seen="2020-01-01")]
    (temp_path / "jobs_database.json").write_text(json.dumps(jobs), encoding="utf-8")
    (temp_path / "final_cv_text.txt").write_text(cv, encoding="utf-8")
    (temp_path / "candidate_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (temp_path / "user_decisions.json").write_text(json.dumps({jobs[0]["link"]: "save"}), encoding="utf-8")
    (temp_path / "match_results.json").write_text(json.dumps({jobs[0]["link"]: {"percent": 81}}), encoding="utf-8")

    try:
        app_services.JOBS_DATABASE_PATH = temp_path / "jobs_database.json"
        candidates.ROOT = temp_path / "candidates"
        listing = candidates.listing()
        check("existing single-user data becomes the first candidate",
              listing["active"] == "k1" and listing["items"][0]["has_cv"]
              and (candidates.ROOT / "k1" / "user_decisions.json").exists()
              and not (temp_path / "user_decisions.json").exists(), listing)

        client = TestClient(server.app, base_url="http://127.0.0.1:8501")
        app_services.job_data_service.reload()
        check("first candidate sees its saved offer",
              [r["link"] for r in client.get("/api/offers?tab=Zapisane").json()["items"]] == [jobs[0]["link"]])

        created = client.post("/api/candidates", json={"name": "  Druga   osoba "}).json()
        check("new candidate becomes active with a cleaned name",
              created["active"] == "k2" and created["items"][1]["name"] == "Druga osoba", created)
        candidates.adopt_name("Jan Kowalski")
        check("a name given by the user is not replaced by the name from the CV",
              candidates.listing()["items"][1]["name"] == "Druga osoba")
        check("new candidate starts without CV, scores or decisions",
              client.get("/api/cv").json()["ready"] is False
              and client.get("/api/offers?tab=Zapisane").json()["total"] == 0
              and client.get("/api/offers?tab=Dopasowane").json()["total"] == 0)
        check("the active candidate cannot be deleted",
              client.post("/api/candidates/delete", json={"id": "k2"}).status_code == 400)
        with patch.object(server.PipelineProcessManager.get_instance(), "is_running", return_value=True):
            check("switching is blocked during a run",
                  client.post("/api/candidates/activate", json={"id": "k1"}).status_code == 409)

        (candidates.ROOT / "k1" / "user_decisions.json").write_text(
            json.dumps({jobs[0]["link"]: "save", jobs[2]["link"]: "reject"}), encoding="utf-8")
        os.chdir(temp_path)
        with redirect_stdout(io.StringIO()):
            purge_stale_offers.main()
        kept = [j["link"] for j in load_json_safe(temp_path / "jobs_database.json", default=[])]
        check("a stale offer decided by an inactive candidate survives the purge",
              jobs[2]["link"] in kept and jobs[0]["link"] in kept, kept)

        client.post("/api/candidates/activate", json={"id": "k1"})
        check("switching back restores the first candidate's lists",
              [r["link"] for r in client.get("/api/offers?tab=Zapisane").json()["items"]] == [jobs[0]["link"]])

        named = dict(profile, _metadata=dict(profile["_metadata"], name="Anna Nowak"))
        with patch("utils.cv_profile.build_profile", return_value=named):
            cv_profile.ensure_profile(force=True)
        check("a candidate with a default name takes the name from the CV profile",
              candidates.listing()["items"][0]["name"] == "Anna Nowak", candidates.listing())
        client.post("/api/candidates/rename", json={"id": "k1", "name": "Ania"})
        with patch("utils.cv_profile.build_profile", return_value=named):
            cv_profile.ensure_profile(force=True)
        check("after a manual rename the CV name no longer overrides it",
              candidates.listing()["items"][0]["name"] == "Ania")

        results_path = candidates.path(candidates.MATCH_RESULTS)
        loaded = {j.link: j for j in matching_run.JobDatabase(str(temp_path / "jobs_database.json")).load_jobs()}
        old_fp = profile_fingerprint(profile)
        results_path.write_text(json.dumps({jobs[0]["link"]: {
            "percent": 81, "filtered": None, "answers": {}, "offer_fp": matching_run.offer_fingerprint(loaded[jobs[0]["link"]]),
            "profile_fp": old_fp, "model": "m", "scored_at": "2026-09-20T10:00:00"}}), encoding="utf-8")
        changed = dict(profile, skills=["SQL", "Power BI"])
        candidates.path(candidates.PROFILE).write_text(json.dumps(changed), encoding="utf-8")

        asked = []

        def fake_triage(items, *a, **k):
            asked.extend(link for link, _ in items)
            return {link: 1.0 for link, _ in items}, 0, 0, False

        with patch.object(matching_run.jev, "api_key", return_value="test"), \
             patch.object(matching_run, "JOBS_PATH", temp_path / "jobs_database.json"), \
             patch.object(matching_run.triage, "triage", fake_triage), \
             patch.object(matching_run.jev, "ask", return_value={"answers": {}, "model": "m"}), \
             patch.object(matching_run.jev, "percent", return_value=55), \
             redirect_stdout(io.StringIO()):
            code = matching_run.main([])
        after = json.loads(results_path.read_text(encoding="utf-8"))
        check("after a CV change only unscored offers go to Jev",
              code == 0 and sorted(asked) == sorted([jobs[1]["link"], jobs[2]["link"]]), asked)
        check("the offer scored with the previous CV keeps its score and profile",
              after[jobs[0]["link"]]["percent"] == 81 and after[jobs[0]["link"]]["profile_fp"] == old_fp)
        check("new offers are scored with the current profile",
              after[jobs[1]["link"]]["percent"] == 55
              and after[jobs[1]["link"]]["profile_fp"] == profile_fingerprint(changed), after[jobs[1]["link"]])

        removed = client.post("/api/candidates/delete", json={"id": "k2"}).json()
        check("deleting an inactive candidate removes its data",
              [c["id"] for c in removed["items"]] == ["k1"] and not (candidates.ROOT / "k2").exists())
    finally:
        os.chdir(old_cwd)
        app_services.JOBS_DATABASE_PATH = orig_jobs
        candidates.ROOT = orig_root
        app_services.job_data_service.reload()
        shutil.rmtree(temp_path, ignore_errors=True)


def main():
    print("=" * 62)
    print("  INTEGRATION TESTS (no API calls)")
    print("=" * 62)

    for test in (test_canonical_link, test_text_cleaning,
                 test_safe_io, test_llm_providers,
                 test_record_scrape, test_scraper_health,
                 test_idempotent_writes, test_olx_tempo_przy_blokadzie,
                 test_zdjete_z_portalu, test_sprawdzenie_ofert, test_olx_fetch_rownolegly,
                 test_olx_opis_z_listingu, test_pipeline_final_status, test_stop_during_scraping,
                 test_listing_completeness, test_category_fallback,
                 test_windows_process_safety,
                 test_pipeline_stop_and_resume_lifecycle,
                 test_http_security_and_dns_rebinding_protection,
                 test_external_data_change_automatic_invalidation,
                 test_playwright_chromium_prerequisites,
                 test_pipeline_skipped_stage_progress,
                 test_pipeline_events,
                 test_offer_api_contract,
                 test_foreign_run_visible_to_server,
                 test_company_logos,
                 test_candidates):
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
