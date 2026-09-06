"""
OLX Praca Scraper

Listing zbierany przez Playwright - OLX renderuje wyniki po stronie klienta.

Opisy nie ida ani zwyklym HTTP (OLX odpowiada 403 na kazdy adres, blokada
siedzi na odcisku TLS), ani przez wejscie na kazda oferte osobno - tylko przez
`fetch` wolany WEWNATRZ otwartej strony OLX. To samo origin, te same
ciasteczka, ten sam odcisk, a przy okazji zadnego renderowania i zadnych
30-sekundowych timeoutow nawigacji. Rozbieranie HTML-a zostaje jedno,
w utils/olx_details.

Wczesniej opis byl zaslepka "Oferta z OLX (kategoria: X)", przez co AI ocenialo
te oferty wylacznie po tytule.
"""

import json
import logging
import re
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from utils.data_models import Job
from utils.olx_details import fetch_offer_details, normalize_olx_link

# Odstep miedzy podstronami ofert. Zmierzone na 15 ofertach kazde:
#   0,50 s -> 14 opisow, 0 blokad
#   0,25 s -> 9 opisow, juz 1 blokada
#   0,00 s -> 0 opisow, 15 blokad (portal odcina natychmiast)
# Ponizej 0,5 s OLX zaczyna odmawiac takze przegladarce, wiec to nie jest
# pokretlo do krecenia "dla szybkosci" - to granica, ktora portal wyznaczyl.
OPIS_PRZERWA = 0.5
# Po blokadzie odstep rosnie geometrycznie; po BLOKADY_LIMIT z rzedu
# przerywamy dociaganie, bo kazde kolejne wejscie tylko pograza sesje.
BLOKADA_MNOZNIK = 2.0
BLOKADA_SUFIT = 8.0
BLOKADY_LIMIT = 12
# Co ile ofert petla melduje, gdzie jest. Bez tego dociaganie 975 opisow to
# godzina ciszy w logu i nie da sie odroznic pracy od zawieszenia - przebieg
# z 6 wrzesnia 2026 stal 60 minut na jednej linijce "fetching descriptions".
POSTEP_CO = 50
# Opisy leca teraz przez `fetch` wywolany WEWNATRZ otwartej strony OLX, a nie
# przez `page.goto` na kazda oferte. Zysk jest podwojny: nie placimy za render
# (goto parsuje HTML, odpala skrypty i czeka na domcontentloaded), a nieudane
# wejscie wraca od razu, zamiast wisiec do 30-sekundowego timeoutu nawigacji.
# Zmierzone 7 wrzesnia 2026 na tej samej puli ofert, po 20-30 na proba:
#   goto sekwencyjnie, 0.5 s   ->  50 ofert/min, 0 blokad
#   fetch 3 naraz,     0.5 s   ->  68 ofert/min, 0 blokad
#   fetch 8 naraz,     0.5 s   ->  68 ofert/min, 0 blokad  (wiecej nie pomaga)
#   fetch 6 naraz,     0.3 s   ->  20 blokad na 20 ofert   (portal odcina)
# Rownoleglosc powyzej 3 nic nie daje - waskim gardlem jest OLX, nie my.
# ODSTEP ZOSTAJE 0.5 s: to ta sama granica portalu co przy goto, sprawdzona
# ponownie przy fetchu. Tresc opisu obiema drogami jest identyczna (porownane
# znak w znak na ofertach po 3218 i 5340 znakow).
KARTY = 3
# Ile ofert idzie w jednym wywolaniu `evaluate`. Kazda to ~200 kB HTML-a
# przechodzacego przez CDP, wiec paczka wiekszej mocy nie przyspiesza,
# a zjada pamiec. Przy okazji wyznacza takt meldunkow o postepie.
PACZKA = 25

logger = logging.getLogger(__name__)

# Strona kategorii niesie w sobie caly listing jako JSON - z pelnym opisem
# kazdego ogloszenia. Scraper i tak te strone pobiera, wiec opis jest gratis:
# sprawdzone 7 wrzesnia 2026, opis zlozony ze stanu i opis ze strony oferty
# maja identyczna dlugosc co do znaku (2186 = 2186) i te sama tresc.
# Dlatego wchodzenie na kazda oferte z osobna zniknelo - bylo 975 zapytan
# i kwadrans, jest zero zapytan i tyle, ile trwa sam listing.
STAN_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")')

# Pobieranie idzie z wnetrza strony OLX, wiec z tego samego origin, z tymi
# samymi ciasteczkami i tym samym odciskiem TLS co przegladarka. `ctx.request`
# i gole `requests` dostaja 403 wlasnie dlatego, ze tego nie maja.
# `nastepny` jest wspolnym zegarem wszystkich robotnikow: odstep obowiazuje
# miedzy ZAPYTANIAMI, nie w kazdym watku z osobna.
JS_OPISY = """
async ([adresy, rownolegle, odstep]) => {
  const wyniki = [];
  let i = 0, nastepny = 0;
  async function slot() {
    const teraz = Date.now();
    const czekaj = Math.max(0, nastepny - teraz);
    nastepny = Math.max(teraz, nastepny) + odstep;
    if (czekaj) await new Promise(r => setTimeout(r, czekaj));
  }
  async function robotnik() {
    while (i < adresy.length) {
      const nr = i++;
      await slot();
      try {
        const odp = await fetch(adresy[nr], {credentials: 'include'});
        wyniki[nr] = {status: odp.status, html: await odp.text()};
      } catch (e) {
        wyniki[nr] = {status: 0, html: '', blad: String(e)};
      }
    }
  }
  await Promise.all(Array.from({length: rownolegle}, robotnik));
  return wyniki;
}
"""


class OLXScraper(BaseScraper):
    """Scraper portalu OLX Praca."""
    
    BASE_URL = "https://www.olx.pl"
    
    def __init__(self, config: dict):
        super().__init__(config)
        # Linki ofert, których stron nie pobierano - main_scraper odnotuje na
        # nich last_seen/times_seen (patrz JobDatabase.record_scrape).
        self.seen_again_links = []
        # Linki ofert, które dostały opis prosto z listingu - te nie mają po co
        # wchodzić na własną stronę.
        self.opisy_ze_stanu = set()

    def _wejdz_i_wez_html(self, url: str):
        """
        Wejdź na listing i oddaj HTML, który przyszedł z serwera.

        `page.content()` tu nie wystarcza: OLX po hydracji **usuwa z DOM-u
        skrypt ze stanem**. Zmierzone 7 września 2026 na tej samej stronie:
        przy `domcontentloaded` stan ma 46 ogłoszeń, dwie sekundy później
        zero, a `window.__PRERENDERED_STATE__` nie zostaje w żadnej zmiennej
        globalnej. `navigate_with_retry` czeka po wejściu `self.delay` sekund,
        więc czytanie DOM-u po nim daje zawsze pustą mapę - i właśnie dlatego
        pierwszy przebieg z opisami z listingu wrócił do drogi zapasowej.
        Surowa odpowiedź nawigacji ma stan zawsze.

        Zwraca `None`, gdy wejście się nie udało (wtedy kategoria leci dalej),
        i pusty napis, gdy weszło, ale treści nie ma.
        """
        for proba in range(1, self.max_retries + 1):
            try:
                logger.info(f"{self.get_source_name()}: Navigating to {url} "
                            f"(attempt {proba}/{self.max_retries})")
                resp = self.page.goto(url, wait_until="domcontentloaded",
                                      timeout=self.timeout)
                # Treść czytamy od razu - dopiero potem przerwa na dorysowanie listy.
                html = resp.text() if resp else ""
                time.sleep(self.delay)
                return html
            except Exception as e:
                logger.warning(f"{self.get_source_name()}: Navigation attempt {proba} failed: {e}")
                if proba < self.max_retries:
                    time.sleep(self.delay * proba)
        logger.error(f"{self.get_source_name()}: All navigation attempts failed")
        return None

    def _ogloszenia_ze_stanu(self, html: str) -> dict:
        """
        Mapa `link -> ogloszenie` z JSON-a wbudowanego w strone listingu.

        Pusta mapa nie jest bledem - znaczy tylko, ze tej strony nie da sie
        przeczytac tym sposobem i opisy pojda droga zapasowa.
        """
        m = STAN_RE.search(html or "")
        if not m:
            return {}
        try:
            stan = json.loads(json.loads(m.group(1)))
        except ValueError as e:
            logger.debug(f"OLX: nie rozbieram stanu listingu: {e}")
            return {}
        ogloszenia = ((stan.get("listing") or {}).get("listing") or {}).get("ads") or []
        mapa = {}
        for ad in ogloszenia:
            adres = ad.get("url") or ""
            if adres:
                mapa[normalize_olx_link(adres)] = ad
        return mapa

    def _z_ogloszenia(self, job, ad) -> bool:
        """Przepisz opis, firme i date z ogloszenia w listingu. False = nie da sie."""
        from utils.olx_details import build_description, extract_company
        from utils.text_cleaner import clean_job_description

        if not ad or ad.get("status") not in (None, "active"):
            return False
        opis = build_description(ad)
        if not opis.strip():
            return False
        # Opis podmieniamy po utworzeniu obiektu, wiec __post_init__ juz nie
        # zadziala - czyscimy recznie, tak samo jak przy drodze zapasowej.
        job.description = clean_job_description(opis)
        firma = extract_company(ad, fallback="")
        if firma:
            job.company = firma
        data = ad.get("createdTime") or ad.get("lastRefreshTime")
        if data:
            job.posted_date = data
        return True

    def get_source_name(self) -> str:
        return "OLX Praca"
    
    def build_search_url(self) -> str:
        """Build search URL with 'bez doświadczenia' filter using configured location and radius"""
        from config import SCRAPER_CONFIG
        
        # OLX filter: bez-doswiadczenia (without experience) - gets ALL entry-level jobs
        experience = SCRAPER_CONFIG["olx_praca"]["experience_filter"]
        location = SCRAPER_CONFIG.get("location", "Warszawa").lower()
        radius = SCRAPER_CONFIG.get("radius_km", 15)
        
        # OLX URL structure: /praca/bez-doswiadczenia/warszawa/?search[dist]=15
        url = f"{self.BASE_URL}/praca/{experience}/{location}/?search[dist]={radius}"
        
        logger.info(f"OLX Praca: no experience required, {location.capitalize()} +{radius}km")
        return url
    
    def scrape_jobs(self) -> List[Job]:
        """Scrape jobs using optimized OLX filter URL splitting by categories to bypass 25-page limit"""
        jobs = []
        seen_links = set()
        
        categories = [
            "administracja-biurowa", "badania-i-rozwoj", "bankowosc", "bhp-ochrona-srodowiska",
            "budowa-remonty", "dostawca-kurier-miejski", "e-commerce-handel-internetowy",
            "edukacja", "finanse-ksiegowosc", "franczyza-wlasna-firma", 
            "fryzjerstwo-kosmetyka", "gastronomia", "hr", "hostessa-roznoszenie-ulotek",
            "hotelarstwo", "inzynieria", "it-telekomunikacja", "kierowca", "logistyka-zakupy-spedycja",
            "marketing-pr", "mechanika-lakiernictwo", "montaz-serwis", "obsluga-klienta-call-center",
            "ochrona", "opieka", "organizacja-obsluga-imprez", "praca-magazynowe", "pracownik-sklepu",
            "produkcja", "rolnictwo-ogrodnictwo", "sprzatanie", "sprzedaz", "ubezpieczenia",
            "wykladanie-ekspozycja-towaru", "zdrowie", "praktyki-staze", "pozostale-oferty-pracy"
        ]
        
        import os, json
        state_file = "olx_category_state.json"
        
        completed_categories = []
        if getattr(self, "force", False) and os.path.exists(state_file):
            # Przy --force to wlasnie checkpoint blokuje ponowne przejscie:
            # znacznik "zescrapowane dzisiaj" flaga omija, a ten - nie.
            logger.info("OLX: --force - kasuje checkpoint kategorii")
            try:
                os.remove(state_file)
            except OSError as e:
                logger.warning(f"OLX: nie udalo sie skasowac {state_file}: {e}")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    # Stan kasowany raz na dobę
                    import datetime
                    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                    if state.get("date") == today_str:
                        completed_categories = state.get("completed", [])
                    else:
                        completed_categories = []
            except Exception:
                pass

        logger.info(f"Loaded {len(completed_categories)} completed categories from previous runs.")

        for category in categories:
            if category in completed_categories:
                logger.info(f"Skipping already scraped category: {category}")
                continue

            # Lokalizacja i promień z configu
            from config import SCRAPER_CONFIG
            location = SCRAPER_CONFIG.get("location", "Warszawa").lower()
            radius = SCRAPER_CONFIG.get("radius_km", 15)
            search_url = f"https://www.olx.pl/praca/{category}/{location}/?search%5Border%5D=created_at%3Adesc&search%5Bdist%5D={radius}"
            logger.info(f"Navigating to OLX Category URL (Sorted by Newest): {search_url}")
            
            html_strony = self._wejdz_i_wez_html(search_url)
            if html_strony is None:
                logger.error(f"{self.get_source_name()}: Failed to navigate to {category}")
                continue
                
            # Zgoda na ciasteczka przy pierwszym wejściu - bez długiego czekania
            try:
                cookie_button = self.page.locator('button[id="onetrust-accept-btn-handler"]')
                if cookie_button.count() > 0 and cookie_button.is_visible(timeout=1000):
                    cookie_button.click(timeout=1000)
                    self.page.wait_for_timeout(500)
            except Exception as e:
                pass

            try:
                empty_state = self.page.query_selector('h3[data-testid="no-results-header"]')
                if empty_state:
                    logger.info(f"No results for {category}, skipping.")
                    completed_categories.append(category)
                    continue
            except Exception:
                pass
                
            page_num = 1
            max_pages = 5 # Reduced to 5 as requested to focus on most recent offers
            
            while page_num <= max_pages:
                logger.info(f"{self.get_source_name()} | {category}: Scraping page {page_num}")
                
                try:
                    self.page.wait_for_selector('a[href*="/oferta/"]', timeout=3000)
                except Exception:
                    break  # Zwykle znaczy koniec ofert albo timeout
                
                # Stan strony niesie opisy - czytamy go raz na strone
                ogloszenia = self._ogloszenia_ze_stanu(html_strony)
                cards = self.page.query_selector_all('div[data-cy="l-card"]')
                page_found = 0
                
                for card in cards:
                    try:
                        link_elem = card.query_selector('a[href*="/oferta/"]')
                        if not link_elem: continue
                        
                        title_elem = link_elem.query_selector('h6')
                        title = title_elem.inner_text().strip() if title_elem else link_elem.inner_text().strip()
                        href = link_elem.get_attribute('href')
                        
                        # Kilka selektorów naraz - klasy CSS-modules zmieniają się przy każdym wdrożeniu
                        company_elem = (
                            card.query_selector('p.css-w5qju7') or
                            card.query_selector('[data-testid="company-name"]') or
                            card.query_selector('div[data-cy="l-card"] p:nth-of-type(2)')
                        )
                        company = company_elem.inner_text().strip() if company_elem else "OLX"
                        
                        if not href or not title: continue
                        if not href.startswith('http'): href = self.BASE_URL + href

                        # Utnij parametry śledzące - inaczej ta sama oferta trafia
                        # do bazy wielokrotnie pod różnymi URL-ami
                        href = normalize_olx_link(href)

                        if href in seen_links: continue
                        seen_links.add(href)

                        job = Job(
                            title=title,
                            company=company,
                            link=href,
                            description=f"Oferta z OLX (kategoria: {category})",
                            source=self.get_source_name(),
                            location="Warszawa"
                        )
                        # Opis prosto z listingu; jesli go tam nie ma, oferta
                        # zostaje z zaslepka i dobiera ja enrich_descriptions.
                        if self._z_ogloszenia(job, ogloszenia.get(href)):
                            self.opisy_ze_stanu.add(href)
                        jobs.append(job)
                        page_found += 1
                    except Exception:
                        pass
                
                logger.info(f"{category} | Page {page_num}: Collected {page_found} new jobs")
                
                if page_found == 0: break
                    
                # Wejscie na kolejna strone URL-em, nie kliknieciem: klikniecie
                # przerysowuje liste po stronie klienta i stan strony zostaje
                # ten z pierwszej - a to w nim siedza opisy.
                try:
                    if not self.page.query_selector('a[data-cy="pagination-forward"]'):
                        break  # Koniec listy
                    page_num += 1
                    html_strony = self._wejdz_i_wez_html(f"{search_url}&page={page_num}")
                    if html_strony is None:
                        break
                except Exception:
                    break
                    
            # Kategoria przerobiona do końca
            completed_categories.append(category)
            logger.info(f"Completed category {category}. Total cumulative jobs: {len(jobs)}")
            
            # Stan zapisywany po każdej kategorii
            try:
                import datetime
                with open(state_file, 'w') as f:
                    json.dump({
                        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                        "completed": completed_categories
                    }, f)
            except Exception as e:
                logger.error(f"Could not save OLX category state: {e}")
                
        logger.info(f"{self.get_source_name()}: Total jobs collected across all categories: {len(jobs)}")

        jobs = self.enrich_descriptions(jobs)
        return jobs

    def enrich_descriptions(self, jobs: List[Job]) -> List[Job]:
        """
        Dociągnij pełne opisy z podstron ofert (przez przeglądarkę - patrz niżej).
        Oferty wygasłe są odrzucane - i tak nie da się na nie zaaplikować.

        Oferty, które są już w bazie, pomijamy w całości: `record_scrape` nie
        nadpisuje istniejących rekordów, więc pobranie ich opisu (0.4 s każde,
        1272 oferty w przebiegu z 23.08) nie zmieniało niczego poza czasem.
        Ich linki idą do `seen_again_links` - main_scraper odnotowuje na nich
        `last_seen`, czyli jedyny sygnał wieku oferty na OLX.
        """
        if not jobs:
            return jobs

        from utils.known_links import known_links

        known = known_links()
        self.seen_again_links = [j.link for j in jobs if j.link in known]
        jobs = [j for j in jobs if j.link not in known]
        if self.seen_again_links:
            logger.info(
                f"OLX: {len(self.seen_again_links)} offers already in the database "
                f"(their pages are skipped)"
            )
        if not jobs:
            return []

        # Oferty z opisem z listingu nie maja po co wchodzic na strone.
        ze_stanu = getattr(self, "opisy_ze_stanu", set())
        gotowe = [j for j in jobs if j.link in ze_stanu]
        jobs = [j for j in jobs if j.link not in ze_stanu]
        if gotowe:
            logger.info(f"OLX: {len(gotowe)} opisow prosto z listingu (bez wchodzenia na oferty)")
        if not jobs:
            self.enrich_stats = {"ok": 0, "expired": 0, "error": 0, "blocked": 0,
                                 "ze_stanu": len(gotowe)}
            return gotowe

        logger.info(f"OLX: fetching descriptions for {len(jobs)} offers...")
        if not self.page:
            logger.warning("OLX: brak otwartej przegladarki - pomijam opisy")
            return jobs

        from utils.olx_details import parse_offer_html
        from utils.text_cleaner import clean_job_description

        # Opisy ida przez przegladarke, nie przez `requests`: OLX odpowiada
        # golemu klientowi 403 na kazdy adres, takze na strone glowna, i nie
        # ratuja tego pelne naglowki - blokada siedzi nizej, na odcisku TLS.
        # Skutkiem byly przebiegi typu "ok: 0, errors: 1272" konczace sie
        # napisem "Successfully scraped", a w bazie 1819 ofert z zaslepka.
        # Przegladarka wchodzi bez problemu; zeby nie placic za to czasem,
        # odcinamy obrazy, media i czcionki - opis siedzi w HTML.
        stats = {"ok": 0, "expired": 0, "error": 0, "blocked": 0}
        page = self.page
        try:
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
        except Exception as e:
            logger.debug(f"OLX: nie udalo sie odciac zasobow: {e}")

        # Droga podstawowa to fetch z wnetrza strony; `goto` zostaje jako
        # zapasowa - wchodzi, gdy fetch padnie w polowie albo gdy strona nie
        # umie `evaluate` (tak wchodzi tu test tempa).
        enriched = []
        pozostale = jobs
        if hasattr(page, "evaluate"):
            enriched, pozostale = self._opisy_przez_fetch(page, jobs, stats)
        if pozostale:
            enriched += self._opisy_przez_goto(page, pozostale, stats,
                                               zrobione=len(jobs) - len(pozostale),
                                               wszystkich=len(jobs))

        try:
            page.unroute("**/*")
        except Exception:
            pass

        stats["ze_stanu"] = len(gotowe)
        self.enrich_stats = stats
        enriched = gotowe + enriched
        logger.info(
            f"OLX: descriptions - z listingu: {stats['ze_stanu']}, dociagniete: {stats['ok']}, "
            f"expired (dropped): {stats['expired']}, errors: {stats['error']}, "
            f"blocked (403): {stats['blocked']}"
        )
        if stats["blocked"] and stats["ok"] == 0:
            logger.error(
                "OLX: kazde zapytanie o opis dostalo 403 - portal blokuje ten "
                "sposob pobierania. Oferty ida do bazy z zaslepka."
            )
        return enriched

    def _melduj(self, zrobione, wszystkich, stats, start):
        """
        Gdzie jest dociaganie opisow.

        Przebieg z 6 wrzesnia 2026 stal godzine na jednej linijce "fetching
        descriptions": petla nie wypisywala nic az do konca, wiec z zewnatrz
        nie dalo sie odroznic pracy od zawieszenia. Zgadywanie po zuzyciu CPU
        procesu przegladarki nie jest odpowiedzia.
        """
        minelo = time.monotonic() - start
        tempo = (zrobione / minelo) if minelo else 0
        zostalo = ((wszystkich - zrobione) / tempo) if tempo else 0
        logger.info(
            f"OLX: {zrobione}/{wszystkich} opisow "
            f"(ok: {stats['ok']}, wygasle: {stats['expired']}, "
            f"bledy: {stats['error']}, blokady: {stats['blocked']}) - "
            f"{tempo * 60:.0f} ofert/min, zostalo ~{zostalo / 60:.0f} min"
        )

    def _zapisz_opis(self, job, wynik, stats, enriched):
        """Wspolne dla obu drog: co zrobic z rozebranym HTML-em oferty."""
        from utils.text_cleaner import clean_job_description

        status = wynik.get("status")
        if status == "ok":
            # Podmieniamy opis po utworzeniu obiektu, wiec __post_init__
            # (ktore czysci opis) juz sie nie wykona - czyscimy recznie
            job.description = clean_job_description(wynik["description"])
            if wynik.get("company"):
                job.company = wynik["company"]
            if wynik.get("posted_date"):
                job.posted_date = wynik["posted_date"]
            stats["ok"] += 1
            enriched.append(job)
            return True
        if status == "expired":
            stats["expired"] += 1
            return False
        stats["error"] += 1
        enriched.append(job)
        return False

    def _opisy_przez_fetch(self, page, jobs, stats):
        """
        Opisy przez `fetch` wolany w otwartej stronie OLX - patrz JS_OPISY.

        Zwraca `(uzupelnione, do_zrobienia_inaczej)`. Druga lista jest niepusta
        tylko wtedy, gdy fetch przestal dzialac technicznie; przy blokadzie
        portalu jest pusta, bo dobijanie sie tam jeszcze przez `goto` nie ma sensu.
        """
        from utils.olx_details import parse_offer_html

        # fetch musi wyjsc z origin olx.pl, inaczej przegladarka utnie go na CORS
        try:
            if not (page.url or "").startswith("https://www.olx.pl"):
                page.goto(f"{self.BASE_URL}/praca/", wait_until="domcontentloaded",
                          timeout=self.timeout)
        except Exception as e:
            logger.warning(f"OLX: nie wchodze na strone do fetchowania ({e}) - wracam do goto")
            return [], jobs

        enriched = []
        przerwa = OPIS_PRZERWA
        blokady_z_rzedu = 0
        start = time.monotonic()
        for poczatek in range(0, len(jobs), PACZKA):
            paczka = jobs[poczatek:poczatek + PACZKA]
            try:
                wyniki = page.evaluate(
                    JS_OPISY, [[j.link for j in paczka], KARTY, int(przerwa * 1000)]
                ) or []
            except Exception as e:
                logger.warning(
                    f"OLX: fetch w stronie padl po {poczatek} ofertach "
                    f"({type(e).__name__}: {e}) - reszta idzie przez goto"
                )
                return enriched, jobs[poczatek:]

            blokady_w_paczce = 0
            for job, wynik in zip(paczka, wyniki):
                wynik = wynik or {}
                kod = wynik.get("status")
                html = wynik.get("html") or ""
                if kod == 404:
                    stats["expired"] += 1
                    continue
                if kod == 403:
                    # Osobny licznik: to jedyny stan znaczacy "przestalo dzialac
                    # w ogole", i nie moze ginac w worku "error".
                    stats["blocked"] += 1
                    blokady_z_rzedu += 1
                    blokady_w_paczce += 1
                    continue
                if kod != 200 or not html:
                    stats["error"] += 1
                    enriched.append(job)
                    continue
                if self._zapisz_opis(job, parse_offer_html(html), stats, enriched):
                    blokady_z_rzedu = 0

            # Odstep rosnie geometrycznie, dopoki portal odmawia, i wraca do
            # zmierzonej granicy, gdy przestal.
            przerwa = (min(przerwa * BLOKADA_MNOZNIK, BLOKADA_SUFIT)
                       if blokady_w_paczce else OPIS_PRZERWA)
            self._melduj(min(poczatek + PACZKA, len(jobs)), len(jobs), stats, start)
            if blokady_z_rzedu >= BLOKADY_LIMIT:
                logger.warning(
                    f"OLX: {blokady_z_rzedu} blokad z rzedu - przerywam dociaganie "
                    f"opisow po {poczatek + len(paczka)} z {len(jobs)} ofert"
                )
                break
        return enriched, []

    def _opisy_przez_goto(self, page, jobs, stats, zrobione=0, wszystkich=None):
        """Droga zapasowa: wejscie na kazda oferte osobno. Wolniejsza o ~1/3."""
        from utils.olx_details import parse_offer_html

        wszystkich = wszystkich or len(jobs)
        enriched = []
        przerwa = OPIS_PRZERWA
        blokady_z_rzedu = 0
        start = time.monotonic()
        for nr, job in enumerate(jobs):
            if nr and nr % POSTEP_CO == 0:
                self._melduj(zrobione + nr, wszystkich, stats, start)
            # Odstep PRZED wejsciem, nie po udanym: wczesniej `time.sleep` stal
            # na koncu petli, za wszystkimi `continue`, wiec przy 403 byl
            # pomijany. Pierwsza blokada kasowala odstep, kolejne wejscia szly
            # 20 razy na sekunde i blokada sie utrwalala - przebieg z 1 wrzesnia
            # 2026 zrobil 1164 zapytania w 58 s i skonczyl na 1137 blokadach.
            if nr:
                time.sleep(przerwa)
            html, status_http = "", None
            try:
                resp = page.goto(job.link, wait_until="domcontentloaded",
                                 timeout=self.timeout)
                status_http = resp.status if resp else None
                if status_http == 404:
                    stats["expired"] += 1
                    continue
                if status_http == 403:
                    # Osobny licznik, zeby blokada nie ginela w worku "error" -
                    # to jedyny stan, ktory znaczy "przestalo dzialac w ogole".
                    stats["blocked"] += 1
                    blokady_z_rzedu += 1
                    przerwa = min(przerwa * BLOKADA_MNOZNIK, BLOKADA_SUFIT)
                    if blokady_z_rzedu >= BLOKADY_LIMIT:
                        logger.warning(
                            f"OLX: {blokady_z_rzedu} blokad z rzedu - przerywam "
                            f"dociaganie opisow po {zrobione + nr + 1} z {wszystkich} ofert"
                        )
                        break
                    continue
                if status_http != 200:
                    stats["error"] += 1
                    enriched.append(job)
                    continue
                html = page.content()
            except Exception as e:
                logger.debug(f"OLX: {job.link} - {type(e).__name__}: {e}")
                stats["error"] += 1
                enriched.append(job)
                continue

            if self._zapisz_opis(job, parse_offer_html(html), stats, enriched):
                blokady_z_rzedu = 0
                przerwa = OPIS_PRZERWA

        return enriched

    def get_job_description(self, job_url: str) -> str:
        """
        Pojedynczy opis - używane przez ręczne dodawanie ofert.

        Przez przeglądarkę, jeśli jest otwarta; `fetch_offer_details` po
        HTTP zostaje jako zapasowa droga, choć dziś OLX odpowiada na nią 403.
        """
        if self.page:
            try:
                from utils.olx_details import parse_offer_html
                resp = self.page.goto(job_url, wait_until="domcontentloaded",
                                      timeout=self.timeout)
                if resp and resp.status == 200:
                    result = parse_offer_html(self.page.content())
                    if result.get("status") == "ok":
                        return result.get("description", "")
            except Exception as e:
                logger.debug(f"OLX: pojedynczy opis przez przegladarke nie wyszedl: {e}")

        result = fetch_offer_details(job_url)
        return result.get("description", "") if result.get("status") == "ok" else ""


    def scrape_current_page(self) -> List[Job]:
        return []  # Nieużywane
        
    def go_to_next_page(self) -> bool:
        return False  # Nieużywane
