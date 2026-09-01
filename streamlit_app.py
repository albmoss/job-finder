"""
Job Finder - interfejs Streamlit.

Przegląd ofert z ocenami dopasowania, sterowanie pipeline'em i śledzenie aplikacji.
"""

import streamlit as st
import logging
from pathlib import Path
import os
import json
import hashlib
import math
import time
import subprocess
import sys
import re
import html
from datetime import datetime

def get_key(prefix, link):
    return f"{prefix}_{hashlib.md5(link.encode()).hexdigest()[:10]}"

from config import (
    JOBS_DATABASE_PATH,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    AI_CONFIG,
    UI_CONFIG
)
from utils.cv_parser import CVParser
from utils.data_models import JobDatabase, Job, JobMatch
from utils.text_cleaner import detect_work_mode, strip_html
from utils.safe_io import save_json_atomic, load_json_safe
from utils.links import canonical_link
from utils.liveness import zdjete_z_portalu
from utils.offer_age import ghost_signals, ghost_label
import skill_gaps
import ui_theme

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PAGE_SIZE = UI_CONFIG.get('page_size', 25)

st.set_page_config(
    page_title="Job Search Analytics — AI Matcher",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="collapsed"
)

def inject_custom_css():
    """
    Warstwa wizualna. Kolory, kroje i promienie siedzą w .streamlit/config.toml -
    tutaj zostaje tylko to, czego natywny motyw nie potrafi: rytm pionowy,
    karta oferty z paskiem oceny i zwężenie kontrolek.

    Zasady, które trzymają ten plik w ryzach:
      * zero JavaScriptu - poprzednia wersja przemalowywała każdy przycisk
        inline co 150 ms, przez co przyciski nie mogły mieć hierarchii,
      * reguły zawężone klasą st-key-* do konkretnego komponentu, a nie
        do globalnego testid Streamlita, który łapie każdy blok w aplikacji,
      * !important tylko tam, gdzie Streamlit wstawia styl inline.
    """
    st.markdown(f"<style>{ui_theme.css_tokens()}</style>", unsafe_allow_html=True)
    st.markdown("""
        <style>
        /* ---------- rytm pionowy ----------
           Poprzednia wersja zerowała marginesy wszystkich nagłówków i
           akapitów, przez co cała aplikacja zlewała się w jeden blok.
           Tutaj rytm jest odbudowany jawną skalą. */
        [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {
            font-stretch: var(--head-stretch);
            letter-spacing: -0.015em;
            margin: 0 0 0.35rem 0;
        }
        [data-testid="stMarkdownContainer"] p { margin: 0 0 0.5rem 0; }
        [data-testid="stMarkdownContainer"] p:last-child { margin-bottom: 0; }

        /* Dymki podpowiedzi.
           Streamlit kompensuje domyślny dolny margines akapitu ujemnym
           marginesem -15px na kontenerze markdown w dymku. Reset rytmu
           powyżej ten margines zdejmuje, więc kompensata zostaje bez pary
           i ściąga bąbel z 32px do 15px - tekst zostaje ucięty w pół linii,
           bo dymek ma overflow:auto. Zerujemy ujemny margines tam, gdzie
           nie ma już czego kompensować. */
        [data-testid="stTooltipContent"] [data-testid="stMarkdownContainer"] {
            margin-bottom: 0 !important;
        }

        [data-testid="stHeading"] { font-stretch: var(--head-stretch); }

        /* Chrome Streamlita: chowamy menu i deploy. Uchwytow panelu bocznego
           nie przywracamy - panelu nie ma, nawigacja siedzi u gory ekranu
           (patrz inject_workspace_css). */
        #MainMenu,
        [data-testid="stStatusWidget"],
        header [data-testid="stAppDeployButton"] { display: none; }

        /* Po wyłączeniu menu, deploya, statusu i uchwytów panelu bocznego
           w pasku nagłówka nie zostało już nic - a sam pasek dalej zajmował
           56 px u góry ekranu i kładł się przezroczystą płytą na treści.
           Zaszłość po układzie z panelem bocznym; usuwamy cały pasek, nie
           samo tło. */
        [data-testid="stHeader"] { display: none; }

        /* Arkusze stylów idą przez st.markdown, więc każdy z nich dostaje
           własny kontener elementu. Kontenery mają zerową wysokość, ale są
           pełnoprawnymi dziećmi flexa i każdy dokładał swoje 15 px przerwy -
           trzy arkusze to 45 px pustki nad paskiem z marką, których nie dało
           się znaleźć w żadnym marginesie. display:none wyjmuje je z układu;
           reguły w <style> działają niezależnie od tego, czy rodzic jest
           rysowany. */
        [data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style),
        [data-testid="stElementContainer"]:has(> div > [data-testid="stMarkdown"] style) {
            display: none;
        }

        [data-testid="stMainBlockContainer"] {
            padding-top: 2.6rem;
            padding-bottom: 4rem;
            max-width: 1400px;
        }

        /* ---------- nagłówek widoku ---------- */
        .vh { margin: 0 0 1.15rem 0; }

        /* ---------- listwa stanu ----------
           Kontekst, nie treść: cztery liczby w jednej linii z włosowymi
           separatorami. Świadomie nie są to kafle z gradientem. */
        .rail {
            display: flex;
            flex-wrap: wrap;
            align-items: baseline;
            gap: 0;
            border-top: 1px solid var(--line);
            border-bottom: 1px solid var(--line);
            padding: 0.6rem 0;
            margin-bottom: 1.6rem;
        }

        .rail-val.is-on  { color: var(--moss); }
        .rail-val.is-off { color: var(--clay); }

        /* ---------- karta oferty ----------
           Układ wzorowany na JustJoin.it i NoFluffJobs, gdzie ta sama lista
           jest przeglądana setki razy dziennie. Oba portale trzymają się
           tej samej dyscypliny: kolorowe logo po lewej, jedna kolorowa
           metryka po prawej, cała reszta szara. Kolor niesie tam wyłącznie
           to, po czym się decyduje - u nich zarobki, u nas dopasowanie. */


        /* Pasek przy krawędzi: wysokość wypełnienia = procent dopasowania.
           Portale tego nie mają, bo nie mają czego rankingować - u nas
           pozwala przelecieć wzrokiem po lewej krawędzi i zobaczyć ranking
           bez czytania liczb. Cieńszy niż wcześniej, bo procent ma teraz
           własny kafel i pasek nie musi go dublować. */
        .jc-rail {
            position: absolute;
            left: 0; top: 12px; bottom: 12px;
            width: 2px;
            background: var(--line-soft);
            border-radius: 0 2px 2px 0;
        }

        /* Odpowiednik logo firmy. Na portalach to właśnie logotypy sprawiają,
           że lista nie wygląda jak jednolity blok - każda pozycja ma inny
           punkt zaczepienia dla oka. Nie mamy logotypów, więc odcień bierze
           się z nazwy firmy: stały dla danej firmy, różny między firmami. */
        .jc-avatar {
            flex-shrink: 0;
            width: 2.6rem;
            height: 2.6rem;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: var(--font-mono);
            font-size: var(--fs-body);
            font-weight: 600;
            letter-spacing: 0.02em;
            border: 1px solid;
        }

        /* Ogłoszenie, które wisi zbyt długo. Ten sam kształt co .jc-new, ale
           wyciszony - to ostrzeżenie, nie alarm, bo sygnał jest poszlakowy. */
        .jc-stale {
            font-size: var(--fs-micro);
            font-weight: 600;
            letter-spacing: 0.06em;
            color: var(--muted);
            background: color-mix(in srgb, var(--muted) 10%, transparent);
            border: 1px solid color-mix(in srgb, var(--muted) 26%, transparent);
            border-radius: 3px;
            padding: 0.08rem 0.32rem;
        }

        /* Meta pod tytułem, drobna i szara - jak na obu portalach.
           Nazwa firmy jaśniejsza, bo to ona identyfikuje ofertę. */
        .jc-meta {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.4rem;
            margin-top: 0.28rem;
            font-size: var(--fs-small);
            color: var(--muted);
        }

        /* Ta sama oferta na innych portalach - jeden element flexa, żeby
           etykieta i linki nie rozjechały się po gapie .jc-meta */
        .jc-alt-wrap { color: var(--muted); }

        /* Metryka po prawej - miejsce, w którym oba portale trzymają widełki
           płacowe. U nas mieszka tam procent dopasowania. */
        .jc-score {
            flex-shrink: 0;
            align-self: center;
            min-width: 5rem;
            text-align: center;
            padding: 0.5rem 0.7rem;
            border: 1px solid transparent;
            border-radius: 7px;
        }

        /* --- chipy: tekstura wiersza, celowo szare ---
           Na portalach stos technologiczny jest szary właśnie po to, żeby nie
           konkurował z jedyną kolorową liczbą. Ta sama zasada tutaj. */
        .jc-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.32rem;
            margin-top: 0.7rem;
            margin-left: 3.45rem;
        }
        .jc-chip {
            font-size: var(--fs-small);
            letter-spacing: 0.01em;
            color: color-mix(in srgb, var(--text) 62%, transparent);
            background: rgba(255, 255, 255, 0.028);
            border: 1px solid var(--line);
            border-radius: 4px;
            padding: 0.12rem 0.42rem;
            white-space: nowrap;
        }
        .jc-chip.is-gap { color: var(--clay); border-color: color-mix(in srgb, var(--clay) 34%, transparent); }
        .jc-chip.is-good { color: var(--accent-dim); border-color: var(--accent-edge); }

        /* Werdykt AI - nasza jedyna treść, której portale nie mają.
           Dostaje własne tło, żeby nie czytał się jak dalszy ciąg opisu. */
        .jc-reason {
            margin-top: 0.65rem;
            margin-left: 3.45rem;
            max-width: 88ch;
            padding: 0.4rem 0.65rem;
            background: var(--accent-soft);
            border-left: 2px solid var(--accent-dim);
            border-radius: 0 4px 4px 0;
            font-size: var(--fs-body);
            line-height: 1.5;
            color: color-mix(in srgb, var(--text) 82%, transparent);
        }

        /* Miara wiersza, nie cała szerokość karty. Przy ~1300 px tekst leciał
           jednym sznurkiem przez cały ekran i oko gubiło początek następnej
           linii. 72-80 znaków to długość, przy której czyta się bez wysiłku. */
        .jc-desc {
            margin-top: 0.55rem;
            margin-left: 3.45rem;
            max-width: 78ch;
            font-size: var(--fs-body);
            line-height: 1.65;
            color: color-mix(in srgb, var(--text) 58%, transparent);
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        [class*="st-key-save_"] [data-testid="stIconMaterial"] { color: color-mix(in srgb, var(--slate) 84%, white); }

        [class*="st-key-save_"] button:hover {
            border-color: color-mix(in srgb, var(--slate) 55%, transparent);
            background: color-mix(in srgb, var(--slate) 14%, transparent); }

        /* ---------- sekcje panelu sterowania ----------
           Numeracja kroków niesie informację: to jest realna sekwencja,
           w której kolejność ma znaczenie, a nie ozdobnik. */
        .sec-label {
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--faint);
            margin: 0 0 0.7rem 0;
        }
        /* Krok panelu: siatka zamiast flexa z ręcznym wcięciem opisu.
           Wcześniej opis miał `margin-left: 2.3rem` dobrane na oko, żeby trafić
           pod tytuł - ale numer jest w innym kroju niż tytuł, więc przy każdej
           zmianie fontu albo skali tekst się rozjeżdżał. Grid wyrównuje kolumnę
           opisu do kolumny tytułu z definicji. */
        .step {
            display: grid;
            grid-template-columns: 2.2rem 1fr;
            column-gap: 0.6rem;
            align-items: baseline;
            margin: 0 0 0.9rem 0;
        }

        /* Podpowiedź "co teraz" - jedno zdanie, od którego zaczyna ktoś,
           kto wchodzi tu pierwszy raz i nie zna pipeline'u. */
        .next-up {
            display: flex;
            align-items: baseline;
            gap: 0.6rem;
            background: var(--raised);
            border: 1px solid var(--line);
            border-left: 3px solid var(--accent-dim);
            border-radius: 6px;
            padding: 0.75rem 0.95rem;
            margin: 0 0 1.4rem 0;
        }

        /* Komunikat stanu w panelu. Własny, bo st.warning/st.info rysują się
           w kolorach Streamlita - żółtym i niebieskim - które nie należą do
           palety aplikacji. Tu wystarczą dwa warianty z --moss i --clay. */
        .panel-note {
            font-size: var(--fs-body);
            line-height: 1.5;
            border: 1px solid var(--line);
            border-left: 3px solid var(--muted);
            border-radius: 6px;
            padding: 0.7rem 0.9rem;
            margin: 0 0 0.9rem 0;
            color: var(--text);
            background: var(--raised);
        }
        .panel-note.is-ok    { border-left-color: var(--moss); }

        /* ---------- pasek filtrów ---------- */

        /* ---------- kulka portalu ----------
           Znacznik źródła oferty. Stoi zawsze bezpośrednio przy nazwie
           portalu - w wierszu listy, w szczegółach, na karcie i w logu
           pobrań - więc czyta się jako etykieta tej nazwy, a nie jako
           kolejny stan wiersza. Świadomie mniejsza od kropki decyzji
           (.wr-dot / .jc-dot) i zawsze w parze z tekstem: obie rodziny
           kropek bywają w jednym wierszu i nie mogą się mylić. */
        .src-dot {
            display: inline-block;
            width: 5px;
            height: 5px;
            border-radius: 50%;
            margin-right: 0.34rem;
            vertical-align: 0.05em;
            flex: 0 0 auto;
        }
        .src-name { white-space: nowrap; }


        /* ---------- podłoga jakości ---------- */
        :focus-visible {
            outline: 2px solid var(--accent);
            outline-offset: 2px;
        }
        /* Swiadomie bez @media (prefers-reduced-motion: reduce).
           Ta regula kasowala wszystkie animacje i przejscia gwiazdka, a
           Windows z wylaczonymi efektami (MinAnimate=0) zglasza wlasnie
           "reduce" - przez co zamowiony wjazd wierszy nie odpalal sie w ogole.
           Ruch w tym ukladzie jest czescia projektu, nie ozdobnikiem. */
        @media (max-width: 900px) {

        }
        </style>
    """, unsafe_allow_html=True)


# =============================================================================
# PREZENTACJA - wspólne elementy
# =============================================================================

def _esc(value) -> str:
    """
    Tytuły i nazwy firm pochodzą ze scrapowanych stron i trafiają do HTML.
    Bez tego pojedynczy znak < w tytule rozwala układ całej karty.
    """
    return html.escape(str(value)) if value is not None else ""


def src_dot(name, label=True) -> str:
    """
    Kulka barwna oznaczająca portal, z którego pochodzi oferta.

    Jedna funkcja na całą aplikację, bo kulka ma wyglądać tak samo i stać
    w tym samym miejscu wszędzie: w wierszu listy, w szczegółach oferty,
    na karcie i w logu pobrań. Kulka trzyma się nazwy źródła - nie kolumny,
    nie krawędzi wiersza - więc niezależnie od układu czyta się jako
    "to jest znacznik tego portalu".

    Barwa siedzi w ui_theme.source_color(), razem z resztą palety.
    """
    if not name:
        return ""
    color = ui_theme.source_color(name)
    dot = (f'<span class="src-dot" style="background:{color}" '
           f'title="{_esc(name)}"></span>')
    return f'{dot}<span class="src-name">{_esc(name)}</span>' if label else dot


def fmt_n(value) -> str:
    """
    Liczba z odstępem tysięcy, tak jak na górnej listwie. Bez tego ta sama
    wielkość pojawiała się w dwóch zapisach naraz - "15 700" na listwie
    i "15519 z 15562" w nagłówku listy pod spodem.
    """
    return f"{int(value):,}".replace(",", " ")


def score_color(percentage: int) -> str:
    """Sekwencyjna rampa: jeden odcień, zmienna intensywność = wielkość."""
    return score_style(percentage)[0]


# Kolor tekstu, tło i obrys kafla oceny. Trzymane razem, bo muszą pochodzić
# z tego samego pasma rampy - inaczej kafel i pasek rozjeżdżają się kolorem.


def score_style(percentage: int):
    """Zwraca (kolor, tło, obrys) dla danego procentu dopasowania."""
    high, mid, low = ui_theme.score_bands()
    thresholds = UI_CONFIG['match_color_thresholds']
    if percentage >= thresholds['high']:
        return high
    if percentage >= thresholds['medium']:
        return mid
    return low


def format_description(text: str, drop_prefix: str = ""):
    """
    Opisy przychodzą ze scraperów jako tekst z twardymi złamaniami linii
    w środku zdań - to artefakt sklejania elementów inline. W HTML te złamania
    znikają bez śladu i tekst zlewa się w jeden ciąg, a po złamaniu przed
    kropką zostaje osierocona interpunkcja ("pracujących .").

    Pusta linia = akapit. Pojedyncze złamanie = zwykła spacja.

    `drop_prefix` ucina tytuł oferty powtórzony na początku opisu - część
    portali wkleja go tam, przez co pierwsze linijki podglądu marnowały się
    na powtórzenie tego, co i tak stoi wyżej w nagłówku karty.
    """
    text = strip_html(text or "")

    if drop_prefix and len(drop_prefix) > 8:
        # Jedne portale wklejają sam tytuł, inne poprzedzają go słowem
        # ("Stanowisko <tytuł>"), dlatego szukamy go w pierwszych 60 znakach
        # zamiast porównywać sam początek.
        pos = text[:60 + len(drop_prefix)].casefold().find(drop_prefix.casefold())
        if pos != -1:
            text = text[pos + len(drop_prefix):].lstrip(" -–—:.\n")

    paragraphs = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = re.sub(r"\s*\n\s*", " ", chunk)
        chunk = re.sub(r"\s+([.,;:!?])", r"\1", chunk)
        chunk = re.sub(r"\s{2,}", " ", chunk).strip()
        if chunk:
            paragraphs.append(chunk)
    return paragraphs


# Stan decyzji niesie barwę kategorialną - przygaszoną, żeby nie konkurowała
# z oceną dopasowania.
# Barwy stanów pochodzą z ui_theme, żeby nie rozjechały się z ikonami
# decyzji, które używają tych samych tokenów w CSS.
# Stany, po których oferta ma już swoją zakładkę - "Dopasowane" pokazuje
# wyłącznie to, czego jeszcze nie tknąłeś.
DECIDED_STATUSES = frozenset({"reject", "save", "apply", "rated", "aspirational"})

DECISION_STYLE = {
    "apply":        (ui_theme.STATES["moss"], "wysłane"),
    "save":         (ui_theme.STATES["slate"], "zapisane"),
    "aspirational": (ui_theme.STATES["amber"], "aspiruję"),
    "reject":       (ui_theme.STATES["clay"], "odrzucone"),
    "rated":        (ui_theme.STATES["grey"], "ocenione"),
}


# --- WCZYTYWANIE DANYCH ---

USER_DECISIONS_PATH = Path("user_decisions.json")

def load_user_decisions():
    """Load user decisions from JSON file (z fallbackiem na backup przy uszkodzeniu)"""
    return load_json_safe(USER_DECISIONS_PATH, default={})

def save_user_decisions(decisions):
    """
    Zapis decyzji: atomowy + rotacyjny backup.
    To jedyne dane w systemie, których nie da się odtworzyć - setki ręcznych ocen.
    """
    if not save_json_atomic(USER_DECISIONS_PATH, decisions, backup=True, keep=20):
        st.error("⚠️ Nie udało się zapisać decyzji! Sprawdź logi.")

def update_decision(link, status, rating, stage=None):
    """Update a decision and move it to the end to maintain chronological order."""
    # Link jest kluczem łączącym decyzje z ofertami - musi być kanoniczny,
    # inaczej ocena "odkleja się" od oferty (patrz utils/links.py)
    link = canonical_link(link)
    if link in st.session_state.user_decisions:
        old_val = st.session_state.user_decisions.pop(link)
    else:
        old_val = {}
    
    existing_stage = old_val.get('stage') if isinstance(old_val, dict) else None
    new_stage = stage if stage else (existing_stage or status)

    decision_data = {"status": status, "rating": rating, "stage": new_stage,
                     "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if status == "apply" or new_stage == "apply":
        decision_data["applied_at"] = old_val.get("applied_at") if isinstance(old_val, dict) and old_val.get("applied_at") else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
    st.session_state.user_decisions[link] = decision_data
    save_user_decisions(st.session_state.user_decisions)

def get_decision(link):
    """Obsługuje oba formaty decyzji naraz: stary string i nowy słownik."""
    decisions = st.session_state.user_decisions
    val = decisions.get(link)
    if val is None:
        val = decisions.get(canonical_link(link))
    if isinstance(val, dict):
        return val.get('status'), val.get('rating')
    elif isinstance(val, str):
        return val, None
    return None, None


def init_session_state():
    if 'cv_text' not in st.session_state: st.session_state.cv_text = None
    if 'analyzed_matches' not in st.session_state: st.session_state.analyzed_matches = []
    if 'raw_jobs' not in st.session_state: st.session_state.raw_jobs = []
    
    if 'user_decisions' not in st.session_state:
        st.session_state.user_decisions = load_user_decisions()
        
    if 'data_loaded' not in st.session_state: st.session_state.data_loaded = False
    if 'current_cv_path' not in st.session_state: st.session_state.current_cv_path = "cv.pdf"
    if 'active_view' not in st.session_state: st.session_state.active_view = "Dopasowane przez AI"
    
    # Indeksy po linku - budowane raz przy wczytaniu danych, patrz _load_data_impl
    if 'job_lookup' not in st.session_state: st.session_state.job_lookup = {}
    if 'match_lookup' not in st.session_state: st.session_state.match_lookup = {}
    if 'zdjete' not in st.session_state: st.session_state.zdjete = set()

    # Pulpit: ktora zakladka i ktora oferta jest otwarta w lewym panelu
    if 'ws_view' not in st.session_state: st.session_state.ws_view = "Dopasowane"
    if 'ws_selected' not in st.session_state: st.session_state.ws_selected = None

def load_data():
    if st.session_state.data_loaded:
        return
    with st.spinner("Ładowanie bazy danych ofert..."):
        _load_data_impl()

def _load_data_impl():
    # 1. Oferty z ocenami z analizy kaskadowej
    deep_path = Path("analyzed_jobs_waterfall.json")
    if deep_path.exists():
        try:
            with open(deep_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            seen = set()
            unique_matches = []
            for d in data:
                if 'job' in d and 'link' in d['job']:
                    link = d['job']['link']
                    if link not in seen:
                        unique_matches.append(JobMatch.from_dict(d))
                        seen.add(link)
            st.session_state.analyzed_matches = unique_matches
        except Exception as e:
            st.error(f"Błąd ładowania wyników analizy AI: {e}")

    # 2. Surowa baza ofert
    if JOBS_DATABASE_PATH.exists():
        try:
            db = JobDatabase(str(JOBS_DATABASE_PATH))
            all_jobs = db.load_jobs()
            seen_raw = set()
            unique_raw = []
            for j in all_jobs:
                if j.link not in seen_raw:
                    unique_raw.append(j)
                    seen_raw.add(j.link)
            st.session_state.raw_jobs = unique_raw
        except Exception as e:
            st.error(f"Błąd ładowania bazy surowej: {e}")
    
    # 3. Indeksy po linku: oferta i jej ocena.
    #    Ocen jest ~17 tys., a szukały ich trzy miejsca w interfejsie przez
    #    `next(m for m in analyzed_matches ...)` - liniowo, przy każdym
    #    przeładowaniu strony i raz na każdą ofertę na tablicy etapów.
    lookup = {}
    matches = {}
    for m in st.session_state.analyzed_matches:
        lookup[m.job.link] = m.job
        matches[m.job.link] = m
    for j in st.session_state.raw_jobs:
        if j.link not in lookup:
            lookup[j.link] = j
    st.session_state.job_lookup = lookup
    st.session_state.match_lookup = matches
    # Oferty, ktorych portal juz nie wystawia. Nie kasujemy ich - schodza na
    # dol listy z plakietka, bo 1 wrzesnia 2026 polowa pierwszej dziesiatki
    # "Dopasowanych" byla martwa i to ona zjadala uwage.
    st.session_state.zdjete = zdjete_z_portalu(st.session_state.raw_jobs)

    st.session_state.data_loaded = True

def find_job_obj(link):
    """Wyszukanie oferty w czasie stałym, po gotowym indeksie."""
    return st.session_state.job_lookup.get(link)


def find_match(link):
    """Ocena AI dla oferty, w czasie stałym."""
    return st.session_state.match_lookup.get(link)


def _delete_job_permanent(job_link: str):
    """Usuwa ofertę ze wszystkich plików z danymi."""
    try:
        db = JobDatabase(str(JOBS_DATABASE_PATH))
        db.remove_job(job_link)
    except Exception as e:
        logger.error(f"Error deleting from DB: {e}")

    try:
        analyze_path = Path("analyzed_jobs_waterfall.json")
        if analyze_path.exists():
            with open(analyze_path, 'r', encoding='utf-8') as f:
                analyzed_data = json.load(f)
            new_analyzed = [d for d in analyzed_data
                           if not ('job' in d and 'link' in d['job'] and d['job']['link'] == job_link)]
            if len(new_analyzed) < len(analyzed_data):
                temp_path = str(analyze_path) + ".tmp"
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(new_analyzed, f, ensure_ascii=False, indent=2)
                os.replace(temp_path, str(analyze_path))
    except Exception as e:
        logger.error(f"Error deleting from analyzed JSON: {e}")

    st.session_state.raw_jobs = [j for j in st.session_state.raw_jobs if j.link != job_link]
    st.session_state.analyzed_matches = [m for m in st.session_state.analyzed_matches if m.job.link != job_link]

    st.session_state.job_lookup.pop(job_link, None)
    st.session_state.match_lookup.pop(job_link, None)

    if job_link in st.session_state.user_decisions:
        del st.session_state.user_decisions[job_link]
        save_user_decisions(st.session_state.user_decisions)


# =============================================================================
# PULPIT: OPERACJE NA DANYCH I PIPELINE
# =============================================================================


def _run_step(cmd, label, done_label, tail=14):
    """
    Uruchom etap pipeline'u i pokazuj jego wyjście NA ŻYWO.

    Wcześniej było `subprocess.run(capture_output=True)`, czyli interfejs stał
    zamrożony bez jednego znaku informacji - przy pełnym scrapowaniu nawet
    45 minut. Popen i czytanie linia po linii pokazuje, co się właśnie dzieje.

    Flaga `-u` jest konieczna: Python przy przekierowanym wyjściu buforuje je
    blokowo i bez niej podgląd na żywo pokazywałby pustkę aż do końca procesu.
    """
    from collections import deque

    with st.status(label, expanded=True) as status:
        view = st.empty()
        lines = deque(maxlen=tail)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", *cmd],
                cwd=str(Path(__file__).parent),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    lines.append(line)
                    view.code("\n".join(lines), language=None)
            code = proc.wait()
        except Exception as e:
            status.update(label=f"{label} — błąd", state="error")
            st.error(str(e))
            return False

        if code == 0:
            status.update(label=done_label, state="complete", expanded=False)
            return True

        status.update(label=f"{label} — nie powiodło się", state="error", expanded=True)
        st.error("Ostatnie linie:\n\n" + "\n".join(lines) if lines else f"Kod wyjścia {code}")
        return False


# =============================================================================
# PULPIT: WARSTWA WIZUALNA
# =============================================================================

def inject_workspace_css():
    """
    Warstwa wizualna pulpitu. Trzymana osobno od inject_custom_css, bo dotyczy
    wyłącznie nowego układu - kartę oferty z listy zostawiamy w spokoju.

    Zasady te same co wyżej: zero JavaScriptu, zawężanie klasą st-key-*,
    !important tylko przy stylach inline Streamlita.

    Rytm pionowy: odstępy między blokami panelu ustawia CSS, nie Streamlit.
    Dlatego treść panelu idzie jednym st.markdown - gdy szła kilkoma, do moich
    marginesów dokładały się domyślne przerwy między elementami i odstępy
    wychodziły 44 / 42 / 16 / 13 px zamiast jednej skali.
    """
    st.markdown("""
        <style>
        /* ---------- panel boczny nie istnieje: nawigacja jest u góry ----------
           Sam collapse nie wystarczy - zostaje wąski uchwyt ze strzałkami,
           który wygląda jak niedokończona nawigacja. Chowamy panel i uchwyt. */
        [data-testid="stSidebar"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stSidebarCollapseButton"],
        [data-testid="stExpandSidebarButton"],
        [data-testid="collapsedControl"] { display: none !important; }

        /* Pulpit mieści się w oknie i nic się nie przewija na poziomie strony.
           Pasek, zakładki i podpowiedź mają swoją wysokość, para paneli bierze
           dokładnie to, co zostało. Wcześniej góra zjadała 194 px, z czego
           102 px to były same przerwy między trzema paskami tekstu, a panele
           rosły z treści - przy 864 px wysokości okna wychodziły 240 px poza
           ekran i trzeba było scrollować, żeby kliknąć cokolwiek na dole. */
        [data-testid="stMain"] { overflow: hidden !important; }
        [data-testid="stMainBlockContainer"] {
            height: 100vh;
            max-height: 100vh;
            padding-top: 0.85rem;
            padding-bottom: 0.9rem;
            max-width: 1680px;
            display: flex;
            flex-direction: column;
        }
        [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
            flex: 1 1 auto;
            min-height: 0;
            gap: 0.55rem;
        }
        /* Wiersz z panelami zabiera całą resztę wysokości. Streamlit wkłada
           między kontener a nasz blok dwa bezimienne opakowania i drugi blok
           pionowy - każde z nich jest elementem flex o `flex: 0 1 auto`, więc
           wystarczy jedno takie ogniwo, żeby wysokość nie doszła do paneli.
           `:has` bierze cały łańcuch przodków naraz, bez wypisywania klas
           z hashem, które i tak zmieniają się między wersjami. */
        [data-testid="stMainBlockContainer"] *:has([class*="st-key-wscols"]),
        [class*="st-key-wscols"],
        [class*="st-key-wscols"] > div,
        [class*="st-key-wscols"] > div > [data-testid="stHorizontalBlock"] {
            /* Tylko pozwolenie na zwężenie, bez wymuszania rozciągania:
               domyślne `min-height: auto` nie daje elementowi flex zejść
               poniżej treści. Rozciągania tu NIE chcemy - inaczej "Dodaj
               z linku" znów rysuje 885 px ramki wokół 150 px treści. */
            min-height: 0 !important;
        }
        /* `st.columns` ustawia wierszowi `align-items: flex-start`, więc kolumna
           nie rozciąga się na wysokość wiersza, tylko rośnie z własnej treści -
           i miała 901 px w rodzicu, który miał 699. */
        [class*="st-key-wscols"] > div > [data-testid="stHorizontalBlock"] {
            align-items: stretch !important;
        }
        /* To samo w drugą stronę: między kolumną a panelem też siedzi
           bezimienne opakowanie i to ono nie pozwalało panelowi ZEJŚĆ poniżej
           treści. Krótkie zakładki rosły do okna, a długie zostawały przy 901
           i wyłaziły poza ekran. */
        [class*="st-key-wscols"] [data-testid="stColumn"],
        [data-testid="stColumn"] *:has([class*="st-key-wsleft"]),
        [data-testid="stColumn"] *:has([class*="st-key-wsright"]) {
            flex: 1 1 auto !important;
            min-height: 0 !important;
        }

        /* ---------- pasek u góry ----------
           Marka i liczby w jednym bloku HTML, nie w kolumnach Streamlita:
           kolumny mierzyły własną wysokość inaczej niż treść i włosowa kreska
           przechodziła przez cyfry zamiast pod nimi. */
        .wt-bar {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 2rem;
            flex-wrap: wrap;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--line-soft);
        }
        /* Marka: szeryf, małe litery, bez rozstrzelenia. Wcześniej szła
           tym samym mono-wersalikiem co zakładki, etykiety i log - czyli
           niczym się nie różniła od podpisu kolumny. */
        .wt-brand {
            font-family: var(--font-head);
            font-size: var(--fs-num);
            font-weight: 600;
            letter-spacing: -0.015em;
            line-height: 1;
            color: var(--text-bright);
            white-space: nowrap;
        }
        .wt-brand span { color: var(--faint); font-weight: 400; }
        .wt-rail { display: flex; gap: 2.2rem; flex-wrap: wrap; }

        /* Liczba nad podpisem, nie obok. Wcześniej obie części szły w jednej
           linii tym samym mono-wersalikiem i cały róg czytał się jak jeden
           ciąg znaków - trzeba go było rozszyfrowywać zamiast rzucić okiem.
           Teraz liczba jest szeryfowa i duża, podpis drobny i cichy: dwa
           różne tony zamiast jednego. */
        .wt-item {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 0.1rem;
            line-height: 1;
        }
        .wt-item b {
            font-family: var(--font-head);
            font-size: var(--fs-num);
            font-weight: 600;
            letter-spacing: -0.02em;
            line-height: 1;
            font-variant-numeric: tabular-nums;
            color: var(--text-bright);
        }
        .wt-item span {
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            font-weight: 500;
            letter-spacing: 0.01em;
            color: var(--faint);
        }

        /* Lejek aplikacji. Świadomie o stopień cichszy od liczb obok: tamte
           mówią, jak duża jest baza, ten - co się dzieje z garstką ofert,
           w które naprawdę wszedłeś. Gdyby miał tę samą wagę, pięć małych
           liczb przykryłoby dwie duże. */
        .wt-sep {
            width: 1px;
            align-self: stretch;
            background: var(--line-soft);
        }
        .wt-funnel { display: flex; gap: 1.4rem; align-items: flex-end; }
        .wt-stage {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 0.15rem;
            line-height: 1;
        }
        .wt-stage b {
            font-family: var(--font-mono);
            font-size: var(--fs-body);
            font-weight: 500;
            font-variant-numeric: tabular-nums;
            color: var(--text);
        }
        .wt-stage span {
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            letter-spacing: 0.01em;
            color: var(--faint);
        }
        .wt-stage.is-off b { color: var(--accent-ash); }

        /* ---------- zakładki ---------- */
        [class*="st-key-wstabs"] { margin: 0; }
        [class*="st-key-wstabs"] [data-baseweb="button-group"] { gap: 0.1rem; }
        [class*="st-key-wstabs"] button {
            border: 0 !important;
            background: transparent !important;
            border-radius: 0 !important;
            border-bottom: 2px solid transparent !important;
            padding: 0.34rem 0.7rem !important;
            font-family: var(--font-body) !important;
            font-size: var(--fs-body) !important;
            font-weight: 500 !important;
            letter-spacing: 0 !important;
            text-transform: none !important;
            color: var(--muted) !important;
            transition: color .18s ease, border-color .18s ease;
        }
        [class*="st-key-wstabs"] button:hover { color: var(--text) !important; }
        [class*="st-key-wstabs"] button[aria-checked="true"],
        [class*="st-key-wstabs"] button[kind="segmented_controlActive"] {
            color: var(--text-bright) !important;
            font-weight: 600 !important;
            border-bottom-color: var(--accent) !important;
        }

        .wp-hint-top {
            font-size: var(--fs-small);
            color: var(--faint);
            margin: 0;
        }

        /* ---------- skala tekstu ----------
           Wcześniej w tym pliku było 41 różnych rozmiarów pisma na 69 deklaracji -
           sąsiednie stopnie różniły się o 0,01 rem, czyli o nic. Skala ma siedem
           stopni i to jest jej całe zadanie: różnica ma być widoczna bez mierzenia,
           a nowy element ma nie mieć gdzie wcisnąć czterdziestego drugiego rozmiaru. */
        :root {
            --fs-micro: 0.68rem;   /* dane w mono: czas, liczniki, numer wiersza */
            --fs-small: 0.78rem;   /* metryki wiersza, podpowiedzi, drugi plan */
            --fs-body:  0.88rem;   /* tytuł oferty, treść, zakładki */
            --fs-lead:  1.05rem;   /* tytuły paneli, wynik przy wierszu */
            --fs-num:   1.32rem;   /* marka i liczby zbiorcze u góry */
            --fs-head:  1.55rem;   /* tytuł otwartej oferty */
            --fs-score: 2.2rem;    /* wynik dopasowania w podglądzie */
        }
        /* Akapit Streamlita ma własne 0.875rem, czyli 0.075 px obok --fs-body.
           Różnicy nie widać, ale to dokładnie ten rozjazd, przez który skala
           przestaje być jedynym źródłem prawdy. Bierzemy tylko akapity bez
           klasy - własne bloki (.wd-p) trzymają rozmiar swojego kontekstu. */
        .stMainBlockContainer [data-testid="stMarkdownContainer"] p:not([class]) {
            font-size: var(--fs-body);
        }
        /* Streamlit odsyła znak zapytania na prawy skraj widgetu (inline
           `justify-content: flex-end; width: 100%`), więc przy szerokim panelu
           wisiał pół metra od podpisu, który tłumaczy. */
        [data-testid="stWidgetLabel"] { font-size: var(--fs-body); }
        [data-testid="stWidgetLabel"] [data-testid="stTooltipIcon"] {
            margin-right: auto;   /* rodzic jest wyrównany do prawej krawędzi */
            margin-left: 0.45rem;
        }

        /* ---------- panele ---------- */
        [class*="st-key-wscols"] { margin-top: 0.35rem; }
        /* Kolumna i wszystko w niej rozciąga się na wysokość wiersza, żeby
           `height: 100%` na panelu miało się do czego odnieść. Sam procent nie
           wystarcza: pośrednie kontenery Streamlita mają wysokość `auto`,
           a procent liczony od `auto` nie robi nic. */
        [class*="st-key-wscols"] [data-testid="stColumn"] { display: flex !important; }
        [class*="st-key-wscols"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
        [class*="st-key-wscols"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"]
            > [data-testid="stLayoutWrapper"] {
            flex: 1 1 auto !important;
        }
        [class*="st-key-wsleft"],
        [class*="st-key-wsright"] {
            background: var(--raised);
            border: 1px solid var(--line-soft);
            border-radius: 0.8rem;
            padding: 0.95rem 1.1rem 1.15rem;
            /* Dolna granica, zeby para paneli nie zapadla sie do paska.
               Gorna bierze sie z okna, nie z tresci - stad `min-height: 0`
               w lancuchu wyzej i przewijanie w srodku panelu zamiast
               przewijania calej strony. */
            min-height: 11rem;
            /* Sufit liczony od okna, a nie od rodzica: 152 px zajmuje góra
               (pasek, zakładki, podpowiedź), reszta to dolny oddech. Procentu
               użyć się tu nie da - łańcuch opakowań Streamlita nie przenosi
               wysokości, a wymuszenie jej rozciąga panele także tam, gdzie
               treści jest na 150 px. Panel trzyma się treści i zatrzymuje
               na krawędzi ekranu. */
            max-height: calc(100vh - 168px);
            overflow-y: auto;
            overflow-x: hidden;
            /* Oba panele biorą wysokość wiersza, czyli tego z większą treścią.
               Bez tego krótszy kończył się wyżej i zestawienie się rozjeżdżało
               inaczej w każdej zakładce - a liczba wierszy na stronie nigdy nie
               będzie pasować do każdej zawartości lewego panelu. */
            height: 100%;
        }
        /* Jedna skala odstępów w obu panelach - reszta rytmu siedzi
           w marginesach bloków HTML, nie w domyślnych przerwach Streamlita. */
        [class*="st-key-wsleft"],
        [class*="st-key-wsright"] {
            gap: 0.7rem;
        }
        /* Stopka siada na dnie panelu - to podpis pod tym, co nad nią stoi
           (legenda kolumn, licznik), więc ma trzymać się dolnej krawędzi tak
           samo w każdej zakładce. Podpowiedź NIE: to zwykła proza i pchnięta
           na dno zostawiała dziurę w środku panelu, czyli gorzej niż pas
           pustki pod spodem. Wolne miejsce zbiera się na dole, w jednym
           kawałku. */
        [class*="st-key-wsleft"] > div:last-child:has(.wp-foot),
        [class*="st-key-wsright"] > div:last-child:has(.wp-foot) {
            margin-top: auto !important;
        }

        /* Jeden nagłówek panelu - ten sam prostokąt niezależnie od tego,
           czy w środku stoi sam napis, czy napis z przyciskami. Wysokość jest
           sztywna, więc tytuł lewego i prawego panelu leży na tej samej linii;
           wcześniej ten z przyciskami schodził 12 px niżej, bo Streamlit
           zwijał kontener napisu do wysokości tekstu i centrował już tylko
           jego. Para paneli wyglądała przez to na przekrzywioną. */
        .wp-head:not(.is-mid),
        [class*="st-key-wshead"],
        [class*="st-key-wslisthead"] {
            height: 2.9rem;
            min-height: 2.9rem;
            flex: 0 0 auto;
            box-sizing: border-box;
            border-bottom: 1px solid var(--line-soft);
        }
        .wp-head.is-mid {
            min-height: 2.6rem;
            border-bottom: 1px solid var(--line-soft);
        }
        [class*="st-key-wslisthead"] > div,
        [class*="st-key-wslistbar"] { height: 100% !important; }
        /* Streamlit zwija .stMarkdown w pasku do 6 px i napis wisi na górnej
           krawędzi tej zapadniętej ramki - a centrowanie paska centruje wtedy
           ramkę, nie tekst. Rozciągamy cały łańcuch od kontenera elementu do
           samego bloku, żeby tytuł i licznik stron liczyły środek względem
           paska. To ta sama przyczyna dla obu: podpisu panelu i "1 / 1741". */
        [class*="st-key-wslistbar"] > [data-testid="stElementContainer"]:first-child,
        [class*="st-key-wshead"] > [data-testid="stElementContainer"]:first-child,
        [class*="st-key-wspager"] > [data-testid="stElementContainer"] {
            align-self: stretch !important;
        }
        [class*="st-key-wslistbar"] > [data-testid="stElementContainer"]:first-child
            div:not(.wp-label):not(.wp-count),
        [class*="st-key-wshead"] > [data-testid="stElementContainer"]:first-child
            div:not(.wp-label):not(.wp-count),
        [class*="st-key-wspager"] > [data-testid="stElementContainer"] div {
            height: 100% !important;
            /* Wewnetrzny margines 7 px zsuwal caly napis w dol - to on, a nie
               centrowanie, odpowiadal za tytul o wiersz nizej niz w panelu obok. */
            margin: 0 !important;
        }
        /* Podpis i licznik w jednej linii, gdy nagłówek jest kontenerem
           Streamlita, a nie gotowym blokiem .wp-head. */
        .wp-headline {
            display: flex;
            align-items: center;
            gap: 0.6rem;
            height: 100%;
            min-width: 0;
            overflow: hidden;
        }
        .wp-headline .wp-label {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .wp-headline .wp-count { flex: 0 0 auto; }
        .wp-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
        }
        [class*="st-key-wshead"] { align-items: center !important; }
        .wp-head.is-mid { margin-top: 0.55rem; }
        /* Tytuł panelu. Wcześniej był mikro-wersalikiem w mono - czyli
           dokładnie tym samym kształtem co etykieta operacji w logu i co
           podpis liczby w rogu. Trzy różne rzeczy wyglądały identycznie
           i nagłówki ginęły. Teraz to jedyne szeryfowe napisy w panelu:
           większe, zwykłą wielkością liter, w kolorze tekstu.
           Zdania w kodzie są pisane małą literą, więc wielką dokłada CSS -
           dzięki temu nie trzeba pamiętać o niej w każdym wywołaniu. */
        .wp-label {
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: var(--fs-lead);
            font-weight: 600;
            letter-spacing: -0.01em;
            color: var(--text);
            line-height: 1.35;
        }
        .wp-label::first-letter { text-transform: uppercase; }
        /* Naglowek panelu jest jasniejszy niz cokolwiek pod nim - inaczej
           tytul sekcji i tytul kroku czytaly sie mocniej niz sam panel. */
        .wp-head:not(.is-mid) .wp-label { color: var(--text-bright); }
        .wp-head.is-mid .wp-label { font-size: var(--fs-body); }

        .wp-count {
            font-family: var(--font-mono);
            font-size: var(--fs-small);
            color: var(--faint);
            font-variant-numeric: tabular-nums;
        }
        .wp-empty {
            padding: 1.4rem 0;
            font-size: var(--fs-small);
            color: var(--faint);
        }
        .wp-foot {
            padding-top: 0.8rem;
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            letter-spacing: 0.02em;
            color: var(--faint);
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .wp-hint {
            margin-top: 1.3rem;
            padding-top: 0.85rem;
            border-top: 1px solid var(--line-soft);
            font-size: var(--fs-small);
            line-height: 1.55;
            color: var(--faint);
        }
        .wp-note {
            font-size: var(--fs-small);
            line-height: 1.5;
            color: var(--muted);
            padding: 0.6rem 0.85rem;
            border-left: 2px solid var(--line);
            background: rgba(255, 255, 255, 0.018);
            border-radius: 0 0.4rem 0.4rem 0;
        }
        .wp-note.is-ok { border-left-color: var(--accent); }
        .wp-note.is-warn { border-left-color: var(--amber); }
        .wp-note em { color: var(--text); font-style: normal; }

        [class*="st-key-wslisthead"] [data-testid="stTextInput"] input {
            font-size: var(--fs-small);
        }
        [class*="st-key-wslisthead"] > [data-testid="stVerticalBlock"] { gap: 0.55rem; }
        /* Rozkład nagłówka: podpis zabiera całą wolną przestrzeń, lupka i
           stronicowanie trzymają się prawej krawędzi. Streamlit domyślnie
           rozciąga OSTATNI element w rzędzie, przez co sterowanie lądowało
           w połowie panelu z pustką po prawej. */
        [class*="st-key-wslistbar"] { flex-wrap: nowrap !important; }
        [class*="st-key-wslistbar"] > [data-testid="stElementContainer"]:first-child {
            flex: 1 1 0 !important;
            /* Tytuł panelu nie schodzi poniżej własnego napisu. Przy 1152 px
               pole szukania spychało go do zera: z "Oferty 17 407" zostawało
               "O", a licznik wypadał POD nagłówek, na pierwszą ofertę. */
            min-width: 4.6rem !important;
        }
        [class*="st-key-wslistbar"] > *:not(:first-child) {
            flex: 0 0 auto !important;
        }
        /* Zagnieżdżony kontener (lupka, stronicowanie) przychodzi ze
           Streamlita z szerokością 100% rodzica - jako element rzędu zjadał
           całą szerokość i spychał podpis panelu do zera. Ma być tak szeroki,
           jak jego zawartość. */
        [class*="st-key-wslistbar"] > [data-testid="stLayoutWrapper"],
        [class*="st-key-wspager"] {
            width: max-content !important;
            flex: 0 0 auto !important;
        }
        /* Szukanie jest jedynym elementem paska, który wolno zwężać - reszta
           to ikony o stałym rozmiarze i licznik stron, którego nie da się
           skrócić bez utraty sensu. */
        [class*="st-key-wssearch"] {
            width: auto !important;
            flex: 0 1 auto !important;
            min-width: 0 !important;
            /* Bez tego pole i krzyzyk lamia sie na dwa wiersze, gdy pasek
               jest ciasny - a naglowek ma sztywne 2,9 rem, wiec drugi wiersz
               wychodzi ponad ramke panelu i klada sie na pierwszej ofercie. */
            flex-wrap: nowrap !important;
        }
        [class*="st-key-wssearch"] > [data-testid="stElementContainer"]:last-child {
            flex: 0 0 auto !important;
        }
        /* Kazde ogniwo lancucha musi wolno zwezac - jedno `min-width: auto`
           po drodze wystarczy, zeby pasek wyszedl poza ramke panelu. Przy
           1152 px strzalka "nastepna strona" lezala 50 px ZA krawedzia. */
        [class*="st-key-wssearch"] > [data-testid="stElementContainer"]:first-child {
            flex: 0 1 auto !important;
            min-width: 0 !important;
        }
        [class*="st-key-wssearch"] > [data-testid="stElementContainer"]:first-child
            [data-testid="stTextInput"] { min-width: 0 !important; }
        /* Regula wyzej ustawia KAZDEMU elementowi paska poza pierwszym
           `flex: 0 0 auto` - razem z bezimienna otoczka, w ktorej Streamlit
           trzyma zagniezdzony kontener. Szukanie musi z niej wyjsc, bo to
           jedyna rzecz w pasku, ktora wolno zwezic. */
        [class*="st-key-wslistbar"] > *:has([class*="st-key-wssearch"]) {
            flex: 0 1 auto !important;
            min-width: 0 !important;
        }
        /* Stronicowanie: kontener napisu zwija się do wysokości tekstu,
           więc "1 / 1741" centrowało się względem własnej ramki, a nie
           względem strzałek - stąd wrażenie, że numer stron stoi krzywo. */
        .wp-pager { display: flex; align-items: center; justify-content: center; }

        /* Przyciski nagłówka bez podpisu: kwadratowe, sama ikona. Podpis przy
           lupce i strzałkach dopowiadał to, co ikona mówi sama. */
        [class*="st-key-wslistbar"] .stButton button,
        [class*="st-key-wshead"] .stButton button {
            min-height: 1.9rem;
            height: 1.9rem;
            width: 1.9rem;
            padding: 0;
            border-radius: 0.4rem;
        }
        [class*="st-key-wslistbar"] .stButton button p,
        [class*="st-key-wshead"] .stButton button p { display: none; }
        [class*="st-key-wslistbar"] [data-testid="stIconMaterial"],
        [class*="st-key-wshead"] [data-testid="stIconMaterial"] {
            font-size: var(--fs-lead) !important;
            margin: 0 !important;
        }

        /* Pole szukania wjeżdża z szerokości przycisku, w którego miejscu staje. */
        @keyframes ws-search-in {
            from { width: 1.9rem; opacity: 0.4; }
            to   { width: 15rem; opacity: 1; }
        }
        [class*="st-key-wssearch"] [data-testid="stTextInput"] {
            width: 15rem;
            max-width: 100%;
            animation: ws-search-in .2s cubic-bezier(.16, 1, .3, 1);
        }
        [class*="st-key-wssearch"] input { font-size: var(--fs-small); }

        .wp-pager {
            font-family: var(--font-mono);
            font-size: var(--fs-small);
            color: var(--muted);
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
            min-width: 3.6rem;
            text-align: center;
        }
        .wp-pager span { color: var(--faint); margin: 0 0.2rem; }

        /* Krzyżyk zamykający podświetla się na glinę - tę samą barwę, którą
           w całym interfejsie mają decyzje odmowne. */
        [class*="st-key-ws_close"] button:hover {
            color: var(--clay) !important;
            border-color: var(--clay) !important;
            background: rgba(180, 112, 92, 0.10) !important;
        }
        [class*="st-key-ws_close"] button:hover [data-testid="stIconMaterial"] {
            color: var(--clay) !important;
        }

        /* ---------- wiersz oferty ---------- */
        [class*="st-key-wsrow_"],
        [class*="st-key-wsdec_"] {
            position: relative;
            border-bottom: 1px solid var(--line-soft);
            transition: background-color .16s ease;
        }
        /* Wiersze rozbierają między siebie wolną wysokość panelu. Wysokość
           bierze się z lewej strony i nigdy nie wyjdzie równym wielokrotnością
           wiersza, więc bez tego na dole zostawał pas pustki - raz 40 px,
           raz 90 px, zależnie od zakładki. Sufit pilnuje, żeby strona z
           trzema wynikami szukania nie zrobiła z wierszy kafli. */
        /* Klucz siedzi na kontenerze wiersza, ale w kolumnie panelu stoi
           bezimienna otoczka Streamlita - to ona musi rosnac, inaczej
           `flex` na wierszu nie ma na czym zadzialac. */
        [class*="st-key-wsleft"] > div:has(> [class*="st-key-wsrow_"]),
        [class*="st-key-wsright"] > div:has(> [class*="st-key-wsrow_"]),
        [class*="st-key-wsrow_"] {
            flex: 1 1 auto;
            min-height: 2.9rem;
            max-height: 4.6rem;
        }
        /* To samo dla decyzji po lewej: na niskim oknie sie sciskaja, na
           wysokim rozchodza. Dzieki temu nie trzeba wybierac miedzy pusta
           przestrzenia a przewijaniem w srodku panelu. */
        [class*="st-key-wsleft"] > div:has(> [class*="st-key-wsdec_"]),
        [class*="st-key-wsdec_"] {
            flex: 1 1 auto;
            min-height: 2.1rem;
            max-height: 3.2rem;
        }
        [class*="st-key-wsdec_"] .wl-dec { height: 100%; align-content: center; }
        [class*="st-key-wsrow_"] .wr-row { height: 100%; align-content: center; }
        /* Lewy panel ma trzymać JEDNĄ wagę kreski. Wiersze decyzji brały
           mocniejszą (--line-soft) niż linijki logu tuż nad nimi, a do tego
           każda plakietka statusu ma własny obrys - w sumie trzy różne kreski
           na jednej wysokości i cały panel wyglądał na rozjechany. */
        [class*="st-key-wsdec_"] {
            border-bottom-color: rgba(255, 255, 255, 0.03);
        }
        /* Streamlit dokłada -15 px marginesu pod otoczkę markdownu, żeby
           skasować dolny margines akapitu. Nasze bloki nie są akapitami i nie
           mają czego kasować, więc te 15 px zjadają im wysokość: kontener
           wiersza miał 20 px przy 34 px treści, a kreska - rysowana na dole
           kontenera - szła przez środek plakietki i przez tekst zamiast pod
           nimi. Dotyczylo to 15 rodzajow blokow i ~66 elementow na kazdej
           zakladce: kazdy nasz blok byl o 15 px nizszy niz jego tresc, wiec
           odstepy w CSS nie znaczyly tego, co pisza - 0,7 rem przerwy minus
           15 px dawalo naklada­nie sie o 4,5 px. Zerujemy dla wszystkich
           naszych prefiksow (wp-, wl-, wr-, wg-, wx-, wt-, wd-), nie dla
           akapitow - tam ten margines robi swoje. */
        div:has(> [class^="wp-"]),
        div:has(> [class^="wl-"]),
        div:has(> [class^="wr-"]),
        div:has(> [class^="wg-"]),
        div:has(> [class^="wx-"]),
        div:has(> [class^="wt-"]),
        div:has(> [class^="wd-"]) {
            margin-bottom: 0 !important;
        }
        [class*="st-key-wsrow_"]:hover,
        [class*="st-key-wsdec_"]:hover { background: rgba(255, 255, 255, 0.028); }
        [class*="st-key-wsrow_"] [data-testid="stVerticalBlock"],
        [class*="st-key-wsdec_"] [data-testid="stVerticalBlock"] { gap: 0 !important; }
        [class*="st-key-wsrow_"] [data-testid="stElementContainer"],
        [class*="st-key-wsdec_"] [data-testid="stElementContainer"] { margin: 0 !important; }
        /* Klucz przycisku ląduje na kontenerze elementu, nie na samym
           przycisku - i to ten kontener ma zerową wysokość. Rozciągamy go
           na cały wiersz, dzięki czemu klikalne jest całe pole, a nie
           piętnastopikselowy pasek. */
        [class*="st-key-btn_wsrow_"],
        [class*="st-key-btn_wsdec_"] {
            position: absolute !important;
            top: 0 !important;
            left: 0 !important;
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            z-index: 4;
        }
        [class*="st-key-btn_wsrow_"] .stButton,
        [class*="st-key-btn_wsrow_"] .stButton button,
        [class*="st-key-btn_wsdec_"] .stButton,
        [class*="st-key-btn_wsdec_"] .stButton button {
            width: 100% !important;
            height: 100% !important;
            min-height: 0 !important;
            padding: 0 !important;
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
            cursor: pointer;
        }
        [class*="st-key-btn_wsrow_"] .stButton button,
        [class*="st-key-btn_wsdec_"] .stButton button { opacity: 0; }

        .wr-row {
            display: grid;
            grid-template-columns: 1.6rem minmax(0, 1fr) 3.2rem;
            grid-template-rows: auto auto auto;
            align-items: center;
            column-gap: 0.75rem;
            padding: 0.62rem 0.15rem 0.58rem;
        }
        .wr-rank {
            grid-row: 1 / span 2;
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            color: var(--faint);
        }
        .wr-main { grid-column: 2; min-width: 0; }
        .wr-title {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: var(--fs-body);
            font-weight: 500;
            line-height: 1.3;
            color: var(--text-bright);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .wr-dot { width: 6px; height: 6px; border-radius: 50%; flex: 0 0 auto; }
        .wr-meta {
            margin-top: 0.2rem;
            /* O dwa stopnie niżej niż tytuł, nie o jeden: firma, portal i miasto
               stoją w wierszu tuż pod nim i przy jednym stopniu różnicy czytały
               się jak druga linijka tytułu. */
            font-size: var(--fs-micro);
            line-height: 1.3;
            color: var(--faint);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .wr-org { color: var(--muted); }
        /* Nazwa portalu idzie krojem otoczenia, nie mono. W mono świeciła
           mocniej niż nazwa firmy stojąca obok, a to firma jest ważniejsza -
           hierarchia stawała na głowie. Rozpoznanie portalu niesie teraz
           kulka, więc sam napis może być cichy. */
        .wr-src { color: var(--muted); }
        .wr-sep { margin: 0 0.35rem; opacity: 0.45; }
        .wr-score {
            grid-column: 3;
            grid-row: 1 / span 2;
            text-align: right;
            font-family: var(--font-mono);
            font-size: var(--fs-lead);
            font-weight: 600;
            letter-spacing: -0.01em;
        }
        .wr-score small { font-size: var(--fs-micro); margin-left: 0.06rem; opacity: 0.7; }
        .wr-score.is-none { color: var(--faint); font-weight: 400; }
        .wr-bar {
            grid-column: 2 / span 2;
            grid-row: 3;
            height: 2px;
            margin-top: 0.5rem;
            background: var(--line-soft);
        }
        .wr-bar i {
            display: block;
            height: 2px;
            opacity: 0.42;
            transform-origin: left center;
        }
        [class*="st-key-wsrow_"]:hover .wr-bar i,
        .wr-row.is-active .wr-bar i { opacity: 0.85; }
        .wr-row.is-active {
            background: var(--accent-soft);
            box-shadow: inset 2px 0 0 var(--accent);
        }
        .wr-stage {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            margin: 1.1rem 0 0;
            padding-bottom: 0.35rem;
            border-bottom: 1px solid var(--line-soft);
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            font-weight: 600;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--faint);
        }

        /* ---------- log po lewej ---------- */
        .wl-block { display: flex; flex-direction: column; }
        .wl-line {
            display: grid;
            grid-template-columns: 9.6rem 6.2rem minmax(0, 1fr) auto;
            align-items: baseline;
            gap: 0.7rem;
            padding: 0.4rem 0.15rem;
            font-size: var(--fs-small);
            min-width: 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }
        .wl-time {
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            color: var(--faint);
            white-space: nowrap;
        }
        /* Rodzaj operacji to etykieta, nie dana - stąd krój interfejsu
           i mocno ścięte rozstrzelenie. Ma być najcichszą warstwą wiersza. */
        .wl-op {
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: var(--faint);
            white-space: nowrap;
        }
        .wl-op.is-bad { color: var(--clay); }
        .wl-what {
            font-family: var(--font-mono);
            font-size: var(--fs-small);
            color: var(--text);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .wl-detail {
            color: var(--faint);
            font-size: var(--fs-small);
            white-space: nowrap;
            text-align: right;
        }
        .wl-dec {
            display: grid;
            grid-template-columns: 5.6rem minmax(0, 1fr) auto;
            align-items: baseline;
            gap: 0.7rem;
            padding: 0.46rem 0.15rem;
            min-width: 0;
        }
        .wl-badge {
            justify-self: start;
            padding: 0.06rem 0.4rem;
            border: 1px solid;
            border-radius: 0.3rem;
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            font-weight: 600;
            letter-spacing: 0.02em;
            white-space: nowrap;
        }
        .wl-title {
            color: var(--text);
            font-size: var(--fs-small);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
        }
        .wl-org {
            color: var(--faint);
            font-size: var(--fs-small);
            white-space: nowrap;
            text-align: right;
        }

        /* ---------- szczegóły oferty ----------
           Cały blok idzie jednym markdownem, więc te marginesy są jedynym,
           co rozdziela sekcje. Skala: 1.05rem między blokami, 0.85rem wewnątrz. */
        .wd-state {
            display: inline-block;
            margin-bottom: 0.6rem;
            padding: 0.1rem 0.45rem;
            border: 1px solid;
            border-radius: 0.3rem;
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .wd-title {
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: var(--fs-head);
            line-height: 1.18;
            letter-spacing: -0.018em;
            color: var(--text-bright);
            text-wrap: balance;
        }
        .wd-meta {
            margin-top: 0.4rem;
            font-size: var(--fs-small);
            line-height: 1.5;
            color: var(--faint);
        }
        .wd-sep { margin: 0 0.4rem; opacity: 0.45; }

        /* Ocena dostaje własny pas: liczba, podpis i szyna, która pokazuje
           ją jeszcze raz - oko łapie proporcję szybciej niż cyfrę. */
        .wd-gauge {
            display: grid;
            grid-template-columns: auto minmax(0, 1fr);
            align-items: center;
            column-gap: 1.1rem;
            margin-top: 1.05rem;
            padding: 0.8rem 0;
            border-top: 1px solid var(--line-soft);
            border-bottom: 1px solid var(--line-soft);
        }
        .wd-score {
            font-family: var(--font-mono);
            font-size: var(--fs-score);
            font-weight: 600;
            line-height: 1;
            letter-spacing: -0.03em;
        }
        .wd-score small { font-size: var(--fs-small); margin-left: 0.08rem; opacity: 0.65; }
        .wd-gauge-lbl {
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            font-weight: 600;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--faint);
        }
        .wd-rail {
            margin-top: 0.5rem;
            height: 3px;
            background: var(--line-soft);
            border-radius: 2px;
            overflow: hidden;
        }
        .wd-rail i {
            display: block;
            height: 3px;
            border-radius: 2px;
            transform-origin: left center;
        }
        .wd-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            margin-top: 1.05rem;
        }
        .wd-reason {
            margin-top: 1.05rem;
            padding: 0.65rem 0.9rem;
            border-left: 2px solid var(--accent);
            background: var(--accent-soft);
            border-radius: 0 0.4rem 0.4rem 0;
            font-size: var(--fs-small);
            line-height: 1.55;
            color: var(--text);
        }

        /* Opis: własne pole przewijania z wygaszeniem u dołu, żeby ucięcie
           tekstu wyglądało na zamierzone, a nie na przypadkowe. */
        .wd-descwrap { position: relative; margin-top: 0.9rem; }
        .wd-descwrap::after {
            content: "";
            position: absolute;
            left: 0; right: 0; bottom: 0;
            height: 2.6rem;
            pointer-events: none;
            background: linear-gradient(to bottom,
                        rgba(0, 0, 0, 0), var(--raised) 88%);
        }
        /* Opis dostaje to, co zostaje w oknie, a nie sztywne 15,5 rem.
           Reszta podglądu - tytuł, wynik, plakietki, nota i cała decyzja -
           zajmuje stałe ~565 px plus 167 px górnej części strony; stąd 732.
           Przy oknie 864 px opis ma 146 px, przy 1080 - 362. Sztywna wartość
           wypychała panel 86 px poza ekran, a rozciąganie flexem sprawiało,
           że opis wylewał się na sekcję "Decyzja" - łańcuch otoczek Streamlita
           nie przenosi wysokości i procent liczył się od treści. */
        .wd-desc {
            max-height: calc(100vh - 732px);
            min-height: 6rem;
            overflow-y: auto;
            padding: 0 1rem 1.8rem 0;
            font-size: var(--fs-small);
            line-height: 1.62;
            color: var(--muted);
        }
        .wd-desc::-webkit-scrollbar { width: 5px; }
        .wd-desc::-webkit-scrollbar-track { background: transparent; }
        .wd-desc::-webkit-scrollbar-thumb { background: var(--line); border-radius: 3px; }
        .wd-p { margin: 0 0 0.7rem; }
        .wd-lead {
            margin: 0.85rem 0 0.4rem;
            color: var(--text);
            font-size: var(--fs-small);
        }
        .wd-list { margin: 0 0 0.8rem; padding: 0; list-style: none; }
        .wd-list li {
            position: relative;
            padding-left: 0.95rem;
            margin-bottom: 0.3rem;
        }
        .wd-list li::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0.62em;
            width: 5px;
            height: 1px;
            background: var(--accent);
            opacity: 0.6;
        }

        /* ---------- przyciski ----------
           Bez wersalików i bez rozstrzelenia. Wcześniej dwa słowa zajmowały
           181 px i krzyczały mocniej niż tytuł oferty; teraz przycisk jest
           tak szeroki, jak jego napis. */
        [class*="st-key-wsactions"] button,
        [class*="st-key-wsactions"] a[kind],
        [class*="st-key-wshead"] button,
        [class*="st-key-wshead"] a[kind],
        [class*="st-key-wstool"] button,
        [class*="st-key-wstool"] a[kind] {
            background: transparent !important;
            border: 1px solid var(--line) !important;
            border-radius: 0.4rem !important;
            color: var(--muted) !important;
            font-family: var(--font-body) !important;
            font-size: var(--fs-small) !important;
            font-weight: 400 !important;
            letter-spacing: 0 !important;
            text-transform: none !important;
            padding: 0.3rem 0.6rem !important;
            min-height: 0 !important;
            line-height: 1.45 !important;
            box-shadow: none !important;
            transition: color .16s ease, border-color .16s ease,
                        background-color .16s ease;
        }
        [class*="st-key-wsactions"] button:hover,
        [class*="st-key-wsactions"] a[kind]:hover,
        [class*="st-key-wshead"] button:hover,
        [class*="st-key-wshead"] a[kind]:hover,
        [class*="st-key-wstool"] button:hover,
        [class*="st-key-wstool"] a[kind]:hover {
            color: var(--text-bright) !important;
            border-color: var(--accent-edge) !important;
            background: var(--accent-soft) !important;
        }
        [class*="st-key-wsactions"] button:disabled,
        [class*="st-key-wstool"] button:disabled {
            opacity: 0.35 !important;
            background: transparent !important;
            border-color: var(--line-soft) !important;
        }
        [class*="st-key-wsactions"] button [data-testid="stIconMaterial"],
        [class*="st-key-wshead"] button [data-testid="stIconMaterial"],
        [class*="st-key-wshead"] a [data-testid="stIconMaterial"],
        [class*="st-key-wstool"] button [data-testid="stIconMaterial"] {
            font-size: var(--fs-body) !important;
            opacity: 0.75;
        }

        /* ---------- rząd decyzji ----------
           Cztery decyzje to nie cztery akcje - to jeden wybór stanu, w którym
           tylko jedna opcja może być prawdziwa. Wcześniej wyglądały jak cztery
           identyczne szare obwódki: nic nie mówiło, czym się różnią, a "Usuń"
           pod spodem miał dokładnie ten sam fason co "Zapisz".

           Każda dostaje więc swoją barwę - dokładnie tę, którą ten stan ma
           już na plakietce w liście i na kropce przy wierszu (--slate,
           --moss, --amber, --clay z ui_theme). Kolor nie jest tu ozdobą,
           tylko tym samym kodem, którym reszta interfejsu opisuje stan. */
        [class*="st-key-ws_save_"] button,
        [class*="st-key-ws_app_"] button,
        [class*="st-key-ws_asp_"] button,
        [class*="st-key-ws_rej_"] button {
            border-radius: 0.55rem !important;
            padding: 0.44rem 0.85rem !important;
            font-size: var(--fs-body) !important;
            font-weight: 500 !important;
            color: var(--text-bright) !important;
            background: color-mix(in srgb, var(--barwa) 11%, transparent) !important;
            border: 1px solid color-mix(in srgb, var(--barwa) 30%, transparent) !important;
            transition: background-color .16s ease, border-color .16s ease,
                        transform .12s ease;
        }
        [class*="st-key-ws_save_"] button:hover,
        [class*="st-key-ws_app_"] button:hover,
        [class*="st-key-ws_asp_"] button:hover,
        [class*="st-key-ws_rej_"] button:hover {
            background: color-mix(in srgb, var(--barwa) 20%, transparent) !important;
            border-color: color-mix(in srgb, var(--barwa) 55%, transparent) !important;
            /* Jeden piksel w górę - tyle, żeby kliknięcie miało odpowiedź,
               i nie więcej, bo rząd czterech skaczących kafli to jarmark. */
            transform: translateY(-1px);
        }
        [class*="st-key-ws_save_"] button:active,
        [class*="st-key-ws_app_"] button:active,
        [class*="st-key-ws_asp_"] button:active,
        [class*="st-key-ws_rej_"] button:active { transform: translateY(0); }
        /* Ikona w pełnej barwie stanu - to ona niesie znaczenie, napis je
           tylko nazywa. Wcześniej obie były przygaszone do 0,75. */
        [class*="st-key-ws_save_"] button [data-testid="stIconMaterial"],
        [class*="st-key-ws_app_"] button [data-testid="stIconMaterial"],
        [class*="st-key-ws_asp_"] button [data-testid="stIconMaterial"],
        [class*="st-key-ws_rej_"] button [data-testid="stIconMaterial"] {
            color: var(--barwa) !important;
            opacity: 1 !important;
            font-size: var(--fs-lead) !important;
        }
        /* "Ocen" nie jest decyzja - zapisuje samo dopasowanie i zostawia
           oferte na liscie. Neutralny, ale w tych samych proporcjach co
           cztery obok, zeby caly blok czytal sie jak jeden zestaw. */
        [class*="st-key-ws_conf_"] button {
            border-radius: 0.55rem !important;
            padding: 0.44rem 0.85rem !important;
            font-size: var(--fs-body) !important;
            font-weight: 500 !important;
            color: var(--text) !important;
            border-color: var(--line) !important;
        }
        [class*="st-key-ws_conf_"] button:hover {
            color: var(--text-bright) !important;
            border-color: var(--accent-edge) !important;
            background: var(--accent-soft) !important;
        }

        [class*="st-key-ws_save_"] { --barwa: var(--slate); }
        [class*="st-key-ws_app_"]  { --barwa: var(--moss); }
        [class*="st-key-ws_asp_"]  { --barwa: var(--amber); }
        [class*="st-key-ws_rej_"]  { --barwa: var(--clay); }

        /* Rząd drugoplanowy jest cichy z założenia: "Przywróć" i "Usuń" nie
           są jedną z czterech decyzji, tylko cofnięciem i zniszczeniem.
           Bez obrysu, dopiero pod kursorem pokazują, czym są. */
        [class*="st-key-ws_rest_"] button,
        [class*="st-key-ws_del_"] button {
            border-color: transparent !important;
            color: var(--faint) !important;
        }
        [class*="st-key-ws_rest_"] button:hover {
            color: var(--text) !important;
            border-color: var(--line) !important;
            background: var(--accent-soft) !important;
        }
        /* Usunięcie i potwierdzenie usunięcia - barwa konsekwencji. */
        [class*="st-key-ws_del_"] button:hover,
        [class*="st-key-ws_delyes_"] button,
        [class*="st-key-wstool_recalc"] button:hover {
            color: var(--clay) !important;
            border-color: color-mix(in srgb, var(--clay) 45%, transparent) !important;
            background: color-mix(in srgb, var(--clay) 10%, transparent) !important;
        }
        /* Kroki pipeline'u to jedyne przyciski na pełną szerokość - są
           głównym działaniem swojej sekcji, nie jednym z czterech. */
        [class*="st-key-wstool_steps"] button { padding: 0.42rem 0.7rem !important; }

        /* Rząd drugoplanowy (Przywróć / Usuń) oddziela włosowa linia
           własnego kontenera - pusty .wp-head robił za separator, a po
           ujednoliceniu wysokości nagłówków urósł do 2,45rem. */
        [class*="st-key-wssecond"] {
            border-top: 1px solid var(--line-soft);
            padding-top: 0.65rem;
            margin-top: 0.35rem;
        }
        .wd-ratelbl {
            font-family: var(--font-body);
            font-size: var(--fs-micro);
            font-weight: 600;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--faint);
            white-space: nowrap;
        }
        [class*="st-key-wsactions"] [data-testid="stSliderTickBarMin"],
        [class*="st-key-wsactions"] [data-testid="stSliderTickBarMax"] { display: none; }
        [class*="st-key-wsactions"] [data-testid="stSlider"] { padding-top: 0; }
        /* Suwak rozciagniety na cala szerokosc odsuwal "Ocen" o 400 px od
           liczby, ktora ten przycisk zatwierdza - dwie polowy jednej
           czynnosci na dwoch koncach panelu. Ograniczyc trzeba KONTENER
           elementu (`flex: 1 1 120px` od Streamlita), bo to on jest
           elastycznym dzieckiem rzedu; samo zwezenie widzetu w srodku
           niczego nie przesuwa. */
        [class*="st-key-wsactions"]
            [data-testid="stElementContainer"]:has([data-testid="stSlider"]) {
            flex: 0 1 18rem !important;
        }
        [class*="st-key-wsactions"] [data-testid="stThumbValue"] {
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            color: var(--accent);
        }
        [class*="st-key-wsactions"] > [data-testid="stVerticalBlock"] { gap: 0.5rem; }

        /* ---------- widoki narzędziowe w tym samym fasonie ---------- */
        .wx-step {
            padding: 0.7rem 0 0.45rem;
            border-top: 1px solid var(--line-soft);
        }
        .wx-step.is-first { border-top: 0; padding-top: 0.15rem; }
        .wx-num {
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            letter-spacing: 0.1em;
            color: var(--accent);
            font-variant-numeric: tabular-nums;
        }
        /* Krok pipeline'u to podsekcja, więc czyta się jak podsekcja: ten sam
           stopień co "co się ostatnio działo" obok. Wcześniej brał --fs-lead
           I rozszerzony krój, czyli był większy ORAZ mocniejszy niż "Pipeline"
           nad nim - podrzędne wygrywało z nadrzędnym. */
        .wx-title {
            margin-top: 0.2rem;
            font-family: var(--font-head);
            font-size: var(--fs-body);
            font-weight: 600;
            letter-spacing: -0.01em;
            color: var(--text);
        }
        .wx-desc {
            margin-top: 0.3rem;
            font-size: var(--fs-small);
            line-height: 1.45;
            color: var(--muted);
        }
        .wx-meta {
            margin-top: 0.35rem;
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            line-height: 1.4;
            color: var(--faint);
        }
        .wx-stat {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: baseline;
            gap: 0.7rem;
            padding: 0.44rem 0.15rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }
        .wx-stat-lbl { font-size: var(--fs-small); color: var(--muted); }
        .wx-stat-val {
            font-family: var(--font-mono);
            font-size: var(--fs-body);
            font-weight: 600;
            color: var(--text-bright);
        }
        .wx-stat-val.is-on { color: var(--accent); }
        .wx-stat-val.is-off { color: var(--clay); }
        /* ---------- luki kompetencyjne ----------
           Pasek jest tu miarą częstości, nie oceny, więc świadomie nie używa
           palety wyników (zielony/żółty/czerwony): nic tu nie jest „dobre"
           ani „złe", jedno jest tylko częstsze od drugiego. */
        .wg-row {
            display: grid;
            grid-template-columns: 1.6rem minmax(0, 1fr) 6.5rem 2.2rem 2.6rem;
            align-items: center;
            column-gap: 0.75rem;
            padding: 0.5rem 0.15rem;
            border-bottom: 1px solid var(--line-soft);
        }
        .wg-rank {
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            color: var(--faint);
        }
        .wg-name {
            font-size: var(--fs-body);
            color: var(--text);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .wg-track {
            height: 0.28rem;
            border-radius: 0.14rem;
            background: var(--line-soft);
            overflow: hidden;
        }
        .wg-track i {
            display: block;
            height: 100%;
            background: var(--accent-dim);
            transform-origin: left center;
        }
        .wg-n, .wg-avg {
            font-family: var(--font-mono);
            font-size: var(--fs-small);
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .wg-n { color: var(--text); }
        .wg-avg { color: var(--faint); }
        .wg-row.is-fresh .wg-track i {
            animation: ws-bar-grow .8s cubic-bezier(.16, 1, .3, 1) forwards;
            animation-delay: calc(var(--i, 0) * 34ms + 130ms);
        }

        .wx-file {
            font-family: var(--font-mono);
            font-size: var(--fs-small);
            color: var(--text);
            word-break: break-all;
        }
        .wx-path {
            margin-top: 0.15rem;
            font-family: var(--font-mono);
            font-size: var(--fs-micro);
            color: var(--faint);
            word-break: break-all;
        }

        /* ---------- wejście: rozciąganie i wjazd ----------
           Świadomie bez @media (prefers-reduced-motion) - ten ruch jest
           zamówioną częścią układu, a nie ozdobnikiem. */
        @keyframes ws-row-in {
            from { opacity: 0; transform: translateX(14px); }
            to   { opacity: 1; transform: none; }
        }
        @keyframes ws-bar-grow {
            from { transform: scaleX(0); }
            to   { transform: scaleX(1); }
        }
        @keyframes ws-fade-in {
            from { opacity: 0; transform: translateY(7px); }
            to   { opacity: 1; transform: none; }
        }
        /* Oferta, ktorej portal juz nie wystawia. Przygaszona i na dole listy,
           ale widoczna - regula jest heurystyka (patrz utils/liveness.py),
           wiec chowanie takiej oferty kosztowaloby okazje, a nie tylko halas. */
        .wr-row.is-gone { opacity: 0.5; }
        .wr-row.is-gone .wr-score,
        .wr-row.is-gone .wr-title { text-decoration: line-through; }
        .wr-gone {
            font-size: var(--fs-micro);
            color: var(--clay);
            border: 1px solid color-mix(in srgb, var(--clay) 34%, transparent);
            border-radius: 0.3rem;
            padding: 0 0.28rem;
            white-space: nowrap;
        }
        .wr-row.is-fresh,
        .wl-line.is-fresh,
        .wl-dec.is-fresh,
        .wg-row.is-fresh,
        .wx-step.is-fresh {
            opacity: 0;
            animation: ws-row-in .44s cubic-bezier(.16, 1, .3, 1) forwards;
            animation-delay: calc(var(--i, 0) * 34ms);
        }
        .wr-row.is-fresh .wr-bar i {
            animation: ws-bar-grow .8s cubic-bezier(.16, 1, .3, 1) forwards;
            animation-delay: calc(var(--i, 0) * 34ms + 130ms);
        }
        .wd-part {
            opacity: 0;
            animation: ws-fade-in .42s cubic-bezier(.16, 1, .3, 1) forwards;
            animation-delay: calc(var(--i, 0) * 55ms);
        }
        .wd-rail i {
            animation: ws-bar-grow .85s cubic-bezier(.16, 1, .3, 1) forwards;
            animation-delay: 240ms;
        }

        /* ---------- węższe okna ---------- */
        @media (max-width: 1100px) {
            [class*="st-key-wscols"] [data-testid="stHorizontalBlock"] {
                flex-direction: column;
            }
            [class*="st-key-wscols"] [data-testid="stColumn"] {
                width: 100% !important;
                flex: 1 1 100% !important;
            }
            [class*="st-key-wsleft"],
            [class*="st-key-wsright"] { min-height: 0; }
            [class*="st-key-wsleft"] { margin-bottom: 0.8rem; }
        }
        @media (max-width: 820px) {
            .wl-line { grid-template-columns: minmax(0, 1fr) auto; }
            .wl-time, .wl-op { grid-column: 1 / -1; }
        }
        @media (max-width: 640px) {
            .wt-rail { gap: 1.1rem; }
            .wr-row { grid-template-columns: 1.3rem minmax(0, 1fr) 2.8rem; }
            .wd-score { font-size: var(--fs-head); }
            .wd-title { font-size: var(--fs-num); }
        }
        </style>
    """, unsafe_allow_html=True)

# =============================================================================
# PULPIT: NAWIGACJA U GORY + DWA PANELE
# =============================================================================

WS_TABS = ["Dopasowane", "Wszystkie", "Ocenione", "Zapisane", "Aspiracyjne", "Odrzucone"]
WS_TOOLS = ["Czego brakuje", "Dodaj z linku", "Panel sterowania"]
WS_ALL = WS_TABS + WS_TOOLS

WS_TAB_HINT = {
    "Dopasowane": "ocenione przez AI, jeszcze nietknięte przez Ciebie",
    "Wszystkie":  "cała baza prosto ze skraperów",
    "Ocenione":   "wystawiłeś ocenę i zostawiłeś na później",
    "Zapisane":   "zapisane oraz te, gdzie aplikacja już poszła",
    "Aspiracyjne": "za wysoko na teraz, ale w tę stronę celujesz",
    "Odrzucone":  "odrzucone - profil uczy się, czego nie chcesz",
    "Czego brakuje": "umiejętności, przez które odpadają oferty skądinąd dopasowane",
    "Dodaj z linku": "wklej adres oferty; po lewej podgląd tego, co wpadnie do bazy",
    "Panel sterowania": "po lewej stan danych, po prawej to, co go zmienia",
}

WS_STAGE_NAMES = {
    "save":      "Zapisane",
    "apply":     "Wysłane",
    "interview": "Rozmowa",
    "offer":     "Oferta",
    "archive":   "Archiwum",
}
# Stare zapisy używały innych nazw etapu
WS_STAGE_LEGACY = {"saved": "save", "reject": "archive", "rejected": "archive"}


def _ws_touch():
    """
    Podbija licznik zmian danych.

    Lista ofert dla zakładki jest pamiętana między przebiegami - bez tego
    każde kliknięcie przeliczało piętnaście tysięcy ofert od nowa. Licznik
    jest jedynym sygnałem, że pamięć trzeba wyrzucić.
    """
    st.session_state["_ws_rev"] = st.session_state.get("_ws_rev", 0) + 1


def _ws_fingerprint():
    return (len(st.session_state.raw_jobs),
            len(st.session_state.analyzed_matches),
            len(st.session_state.user_decisions),
            st.session_state.get("_ws_rev", 0))


def _ws_status_dot(status):
    if status in DECISION_STYLE:
        color, label = DECISION_STYLE[status]
        return color, label
    return None, None


def ws_collect(tab, search):
    """
    Zwraca listę krotek (job, match, status, rating) dla zakładki.

    Jedno miejsce, w którym zakładka zamienia się w zbiór ofert - dzięki temu
    prawy panel nie wie nic o tym, skąd dane pochodzą, a dołożenie zakładki
    to dopisanie jednej gałęzi tutaj.
    """
    matches_by_link = st.session_state.match_lookup
    out = []

    if tab == "Dopasowane":
        for m in st.session_state.analyzed_matches:
            # Jedno odpytanie o decyzję na ofertę. Poprzednia wersja pytała dwa
            # razy - raz na filtr, raz na krotkę - a przy 17 tys. ofert to samo
            # w sobie było połową kosztu przeliczenia zakładki.
            status, rating = get_decision(m.job.link)
            if status in DECIDED_STATUSES:
                continue
            out.append((m.job, m, status, rating))
        zdjete = st.session_state.zdjete
        out.sort(key=lambda t: (t[0].link not in zdjete,
                                t[1].match_percentage if t[1] else -1), reverse=True)

    elif tab == "Wszystkie":
        for j in st.session_state.raw_jobs:
            status, rating = get_decision(j.link)
            out.append((j, matches_by_link.get(j.link), status, rating))
        zdjete = st.session_state.zdjete
        out.sort(key=lambda t: (t[0].link not in zdjete,
                                t[1].match_percentage if t[1] else -1,
                                getattr(t[0], "scraped_at", "") or ""), reverse=True)

    else:
        wanted = {
            "Ocenione": ('rated',),
            "Zapisane": ('save', 'apply'),
            "Aspiracyjne": ('aspirational',),
            "Odrzucone": ('reject',),
        }[tab]
        # Kolejność w user_decisions jest chronologiczna - update_decision
        # przenosi wpis na koniec. Odwracamy, żeby najświeższe były u góry.
        for link in reversed(list(st.session_state.user_decisions.keys())):
            status, rating = get_decision(link)
            if status not in wanted:
                continue
            job = find_job_obj(link)
            if job is None:
                continue
            out.append((job, matches_by_link.get(link), status, rating))

    if search:
        q = search.lower()
        out = [t for t in out
               if q in (t[0].title or "").lower() or q in (t[0].company or "").lower()]
    return out


def ws_collect_cached(tab, search):
    """ws_collect z pamięcią na czas życia sesji - patrz _ws_touch."""
    sig = (tab, search) + _ws_fingerprint()
    cache = st.session_state.setdefault("_ws_cache", {})
    if cache.get("sig") != sig:
        cache["sig"] = sig
        cache["items"] = ws_collect(tab, search)
    return cache["items"]


def ws_panel_head(label, right="", mid=False):
    cls = "wp-head is-mid" if mid else "wp-head"
    right_html = f'<div class="wp-count">{_esc(right)}</div>' if right != "" else ''
    st.markdown(f'<div class="{cls}"><div class="wp-label">{_esc(label)}</div>'
                f'{right_html}</div>', unsafe_allow_html=True)


def _wd_body_html(paragraphs):
    """
    Opis oferty jako czytelny blok, a nie ciąg osieroconych linijek.

    Scrapery gubią znaczniki listy: wymagania przychodzą jako osobne akapity
    bez kropek, przez co panel wyglądał na rozsypany. Krótki fragment bez
    kropki na końcu (albo dowolny fragment po linii kończącej się dwukropkiem)
    wraca na swoje miejsce jako punkt listy.
    """
    parts, bullets = [], []

    def flush():
        if bullets:
            parts.append('<ul class="wd-list">'
                         + "".join(f'<li>{_esc(b)}</li>' for b in bullets)
                         + '</ul>')
            bullets.clear()

    listing = False
    for raw in paragraphs:
        text = (raw or "").strip()
        if not text:
            continue
        short = len(text) <= 140
        if short and text.endswith(":"):
            flush()
            listing = True
            parts.append(f'<p class="wd-lead">{_esc(text)}</p>')
            continue
        if short and (listing or not text.endswith((".", "!", "?"))):
            listing = True
            bullets.append(text)
            continue
        flush()
        listing = False
        parts.append(f'<p class="wd-p">{_esc(text)}</p>')
    flush()
    return "".join(parts)


def ws_render_rows(page_items, fresh, selected, rank=True):
    """Prawy panel: lista ofert w rytmie wiersza, nie kafla."""
    for idx, (job, match, status, rating) in page_items:
        pct = int(match.match_percentage) if match else None
        color = score_color(pct) if pct is not None else "var(--faint)"

        dot_color, dot_label = _ws_status_dot(status)
        dot_html = (f'<span class="wr-dot" style="background:{dot_color}" '
                    f'title="{_esc(dot_label)}"></span>') if dot_color else ''

        score_html = (f'<div class="wr-score" style="color:{color}">{pct}'
                      f'<small>%</small></div>') if pct is not None else \
                     '<div class="wr-score is-none">—</div>'

        bar_html = (f'<div class="wr-bar"><i style="width:{pct}%;background:{color}"></i>'
                    f'</div>') if pct is not None else '<div class="wr-bar"></div>'

        zdjeta = job.link in st.session_state.zdjete
        source = getattr(job, "source", "") or "—"
        location = getattr(job, "location", "") or "Warszawa"

        meta = (f'<span class="wr-org">{_esc(job.company)}</span>'
                f'<span class="wr-sep">·</span>'
                f'<span class="wr-src">{src_dot(source)}</span>'
                f'<span class="wr-sep">·</span>'
                f'<span>{_esc(location)}</span>'
                + ('<span class="wr-sep">·</span>'
                   '<span class="wr-gone" title="portal nie wystawia jej '
                   'w najnowszym listingu">zdjęta</span>' if zdjeta else ''))

        cls = "wr-row"
        if zdjeta:
            cls += " is-gone"
        if fresh:
            cls += " is-fresh"
        if selected == job.link:
            cls += " is-active"

        rank_html = f'<div class="wr-rank">{idx + 1:02d}</div>' if rank else \
                    '<div class="wr-rank"></div>'

        row_key = f"wsrow_{hashlib.md5(job.link.encode()).hexdigest()[:12]}"
        with st.container(key=row_key):
            st.markdown(
                f'<div class="{cls}" style="--i:{min(idx, 24)}">'
                f'{rank_html}'
                f'<div class="wr-main">'
                f'<div class="wr-title">{dot_html}{_esc(job.title)}</div>'
                f'<div class="wr-meta">{meta}</div>'
                f'</div>'
                f'{score_html}'
                f'{bar_html}'
                f'</div>',
                unsafe_allow_html=True
            )
            if st.button("Pokaż szczegóły", key=f"btn_{row_key}"):
                st.session_state.ws_selected = job.link
                st.rerun(scope="fragment")


def _ws_search_toggle(open_: bool):
    """Rozwija lupkę albo ją chowa; schowanie czyści frazę, żeby lista wróciła."""
    st.session_state.ws_search_open = open_
    if not open_:
        st.session_state.ws_search = ""


def _ws_page_step(page_key: str, delta: int, total: int):
    """Przewija stronę o jedną w bok, nie wychodząc poza zakres."""
    st.session_state[page_key] = max(1, min(total, st.session_state.get(page_key, 1) + delta))


def ws_render_list_panel(tab):
    """Prawy panel w całości: pasek narzędzi, wiersze, stronicowanie."""
    search = st.session_state.get("ws_search", "") or ""
    items = ws_collect_cached(tab, search)

    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE)) if items else 1
    page_key = f"ws_page_{tab}"
    if st.session_state.get(page_key, 1) > total_pages:
        st.session_state[page_key] = total_pages

    # Lupka rozsuwa się w polu tekstowym w tym samym miejscu nagłówka, zamiast
    # otwierać drugi wiersz: pole stoi dokładnie tam, gdzie przed chwilą był
    # przycisk, więc wzrok nie musi go szukać. Sam wjazd robi CSS (wg-search-in),
    # bo Streamlit rysuje widget od nowa, a nie zmienia jego szerokość.
    open_search = bool(st.session_state.get("ws_search_open") or search)
    page = max(1, min(st.session_state.get(page_key, 1), total_pages))
    st.session_state[page_key] = page

    with st.container(key="wslisthead"):
        with st.container(key="wslistbar", horizontal=True,
                          vertical_alignment="center", gap="small"):
            # Przy otwartym szukaniu licznik znika: miejsca w pasku starcza
            # na tytuł ALBO na liczbę, a liczba i tak mówiłaby wtedy o wyniku
            # szukania, nie o zakładce - czyli o czym innym niż podpis obok.
            count_html = ('' if open_search else
                          f'<div class="wp-count">{fmt_n(len(items))}</div>')
            st.markdown(f'<div class="wp-headline"><div class="wp-label">oferty</div>'
                        f'{count_html}</div>',
                        unsafe_allow_html=True, width="stretch")

            # Przełącznik przez on_click, nie przez zwracaną wartość: callback
            # wykonuje się PRZED przebiegiem skryptu, więc pole pojawia się
            # w tym samym kliknięciu. Odczytanie wyniku st.button wymagałoby
            # jawnego st.rerun w środku rysowania nagłówka.
            if open_search:
                with st.container(key="wssearch", horizontal=True,
                                  vertical_alignment="center", gap="small"):
                    st.text_input("Szukaj", key="ws_search",
                                  placeholder="Stanowisko lub firma…",
                                  label_visibility="collapsed")
                    st.button("", icon=":material/close:", key="ws_search_hide",
                              help="Zamyka szukanie i czyści frazę",
                              on_click=_ws_search_toggle, args=(False,))
            else:
                st.button("", icon=":material/search:", key="ws_search_show",
                          help="Szukanie po stanowisku albo firmie",
                          on_click=_ws_search_toggle, args=(True,))

            # Stronicowanie strzałkami: pole liczbowe wymagało kliknięcia w
            # mikroskopijne +/- albo wpisania numeru, czyli świadomej wiedzy,
            # ile jest stron.
            with st.container(key="wspager", horizontal=True,
                              vertical_alignment="center", gap="small"):
                st.button("", icon=":material/chevron_left:", key=f"ws_prev_{tab}",
                          help="Poprzednia strona", disabled=page <= 1,
                          on_click=_ws_page_step, args=(page_key, -1, total_pages))
                st.markdown(f'<div class="wp-pager">{page}<span>/</span>'
                            f'{total_pages}</div>', unsafe_allow_html=True)
                st.button("", icon=":material/chevron_right:", key=f"ws_next_{tab}",
                          help="Następna strona", disabled=page >= total_pages,
                          on_click=_ws_page_step, args=(page_key, 1, total_pages))

    if not items:
        st.markdown('<div class="wp-empty">Nic tu nie ma. '
                    'Zmień zakładkę albo wyczyść szukanie.</div>',
                    unsafe_allow_html=True)
        return

    sig = f"{tab}|{search}|{page}|{len(items)}"
    fresh = st.session_state.get("_ws_sig") != sig
    st.session_state["_ws_sig"] = sig

    start = (page - 1) * PAGE_SIZE
    # enumerate na wycinku, nie na całości - numer wiersza i tak zaczyna się
    # od `start`, a lista potrafi mieć kilkanaście tysięcy pozycji.
    page_items = list(enumerate(items[start:start + PAGE_SIZE], start=start))
    ws_render_rows(page_items, fresh, st.session_state.get("ws_selected"))

    # Numer strony stoi w nagłówku panelu; powtórka na dole nie dokładała nic
    # poza pasem, który odbierał wierszom wysokość.


# --- lewy panel: log -------------------------------------------------------

def ws_activity_rows(limit=6):
    """
    Co się ostatnio działo, zebrane z tego, co faktycznie leży na dysku.

    Każdy wpis niesie prawdziwy znacznik czasu, a nie gotowy napis - inaczej
    sortowanie porównuje teksty i "08.17" lądowało przed "17.08". Do tego
    nazwa operacji, bo sama nazwa portalu nie mówi, czy to było pobieranie,
    czy ocenianie.
    """
    rows = []

    runs = load_json_safe(Path("scraper_status.json"), default={}) or {}
    for source, info in runs.items():
        if not isinstance(info, dict):
            continue
        raw = info.get("timestamp") or info.get("last_run_date") or ""
        try:
            when = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        count = info.get("jobs_count")
        ok = info.get("status") == "success"
        detail = (f"{fmt_n(count)} ofert" if isinstance(count, int)
                  else str(info.get("status", "")))
        rows.append((when, "pobieranie", source, detail, not ok))

    analyzed_path = Path("analyzed_jobs_waterfall.json")
    if analyzed_path.exists():
        rows.append((datetime.fromtimestamp(analyzed_path.stat().st_mtime),
                     "ocena AI", "waterfall_analysis.py",
                     f"{fmt_n(len(st.session_state.analyzed_matches))} ocen", False))

    profile_path = Path("preference_profile.json")
    if profile_path.exists():
        profile = load_json_safe(profile_path, default={}) or {}
        built = profile.get("_metadata", {}).get("total_decisions_analyzed", 0) or 0
        rows.append((datetime.fromtimestamp(profile_path.stat().st_mtime),
                     "profil", "generate_preference_profile.py",
                     f"z {fmt_n(built)} decyzji", False))

    rows.sort(key=lambda r: r[0], reverse=True)
    return rows[:limit]


def ws_render_activity():
    """Lewy panel, gdy nic nie jest wybrane: log i ostatnie decyzje."""
    ws_panel_head("co się ostatnio działo")

    # Szesc wpisow, nie dziewiec: przy oknie 1536x864 dziewiec wypychalo
    # panel 108 px poza ekran, a log jest tu kontekstem, nie praca.
    rows = ws_activity_rows()
    if not rows:
        st.markdown('<div class="wp-empty">Pusto - nic jeszcze nie chodziło. '
                    'Zajrzyj do „Panel sterowania”.</div>', unsafe_allow_html=True)
    else:
        out = ['<div class="wl-block">']
        for i, (when, op, what, detail, bad) in enumerate(rows):
            out.append(
                f'<div class="wl-line is-fresh" style="--i:{i}">'
                f'<span class="wl-time">{when.strftime("%Y-%m-%d %H:%M:%S")}</span>'
                f'<span class="wl-op{" is-bad" if bad else ""}">{_esc(op)}</span>'
                f'<span class="wl-what">'
                f'{src_dot(what) if op == "pobieranie" else _esc(what)}</span>'
                f'<span class="wl-detail">{_esc(detail)}</span>'
                f'</div>'
            )
        out.append('</div>')
        st.markdown("".join(out), unsafe_allow_html=True)

    ws_panel_head("ostatnie decyzje", mid=True)

    shown = 0
    for link in reversed(list(st.session_state.user_decisions.keys())):
        # Siedem, nie dziewiec - z tego samego powodu co log wyzej.
        if shown >= 7:
            break
        status, rating = get_decision(link)
        color, label = _ws_status_dot(status)
        if not color:
            continue
        job = find_job_obj(link)
        if job is None:
            continue
        if status == 'rated' and rating:
            label = f"ocena {rating}/10"

        # Prawa kolumna trzyma jedną rzecz - firmę, tak samo jak w liście ofert.
        # Wcześniej wchodziła tu data, gdy decyzja miała stempel, więc w jednym
        # miejscu raz stała firma, raz godzina i nic tego nie rozróżniało.
        raw = st.session_state.user_decisions.get(link)
        stamp = raw.get("decided_at") or raw.get("applied_at") if isinstance(raw, dict) else None
        right = _esc(getattr(job, "company", "") or "")
        stamp_attr = f' title="{_esc(stamp)}"' if stamp else ''

        row_key = f"wsdec_{hashlib.md5(link.encode()).hexdigest()[:12]}"
        with st.container(key=row_key):
            st.markdown(
                f'<div class="wl-dec is-fresh" style="--i:{shown}"{stamp_attr}>'
                f'<span class="wl-badge" style="color:{color};border-color:{color}44">'
                f'{_esc(label)}</span>'
                f'<span class="wl-title">{_esc(job.title)}</span>'
                f'<span class="wl-org">{right}</span>'
                f'</div>',
                unsafe_allow_html=True
            )
            if st.button("Otwórz ofertę", key=f"btn_{row_key}"):
                st.session_state.ws_selected = link
                st.rerun(scope="fragment")
        shown += 1

    if shown == 0:
        st.markdown('<div class="wp-empty">Jeszcze nic nie oceniłeś.</div>',
                    unsafe_allow_html=True)

    st.markdown('<div class="wp-hint">Kliknij ofertę - z listy obok albo '
                'z decyzji wyżej - żeby zobaczyć szczegóły i ocenić ją tutaj.</div>',
                unsafe_allow_html=True)


# --- lewy panel: szczegoly oferty ------------------------------------------

def ws_render_detail(link, stage_ctl=False):
    """
    Lewy panel, gdy oferta jest wybrana: pełne dane i wszystkie decyzje.

    Treść idzie jednym blokiem HTML - rytm pionowy jest wtedy w całości
    w CSS, zamiast sumować moje marginesy z domyślnymi przerwami Streamlita.
    """
    job = find_job_obj(link)
    if job is None:
        st.session_state.ws_selected = None
        st.markdown('<div class="wp-empty">Tej oferty nie ma już w bazie.</div>',
                    unsafe_allow_html=True)
        return

    match = find_match(link)
    status, rating = get_decision(link)

    # Nagłówek: podpis po lewej, dwa drobne przyciski po prawej, wszystko na
    # jednej linii środka. Kontener ma własną włosową kreskę, tej samej
    # wysokości co nagłówek prawego panelu.
    with st.container(key="wshead", horizontal=True, vertical_alignment="center",
                      gap="small"):
        # Ten sam blok, co w nagłówku listy ofert - .wp-headline centruje
        # napis względem paska. Goły .wp-label wisiałby na górnej krawędzi.
        st.markdown('<div class="wp-headline"><div class="wp-label">'
                    'szczegóły oferty</div></div>',
                    unsafe_allow_html=True, width="stretch")
        st.link_button("Otwórz", job.link, icon=":material/open_in_new:",
                       help="Otwiera ofertę na portalu, w nowej karcie")
        if st.button("", icon=":material/close:", key="ws_close",
                     help="Wraca do logu ostatnich zdarzeń"):
            st.session_state.ws_selected = None
            st.rerun(scope="fragment")

    pct = int(match.match_percentage) if match else None
    color = score_color(pct) if pct is not None else "var(--faint)"

    state_html = ''
    if status in DECISION_STYLE:
        dot_color, state_label = DECISION_STYLE[status]
        if status == 'rated' and rating:
            state_label = f"ocena {rating}/10"
        state_html = (f'<div class="wd-state" style="color:{dot_color};'
                      f'border-color:{dot_color}44">{_esc(state_label)}</div>')

    work_mode = detect_work_mode(job.location or "", job.description or "", job.title or "")
    meta_bits = [_esc(getattr(job, "company", "") or "—"),
                 _esc(getattr(job, "location", "") or "Warszawa"),
                 _esc(work_mode["label"])]
    # Portal idzie osobno, bo niesie kulkę - jego tekst jest już zescapowany.
    if getattr(job, "source", ""):
        meta_bits.append(f'<span class="wd-src">{src_dot(job.source)}</span>')
    meta_html = '<span class="wd-sep">·</span>'.join(
        f'<span>{b}</span>' for b in meta_bits if b)

    blocks = [f'<div class="wd-part" style="--i:0">{state_html}'
              f'<div class="wd-title">{_esc(job.title)}</div>'
              f'<div class="wd-meta">{meta_html}</div></div>']

    if pct is not None:
        blocks.append(
            f'<div class="wd-gauge wd-part" style="--i:1">'
            f'<div class="wd-score" style="color:{color}">{pct}<small>%</small></div>'
            f'<div><div class="wd-gauge-lbl">dopasowanie do Twojego CV</div>'
            f'<div class="wd-rail"><i style="width:{pct}%;background:{color}"></i></div>'
            f'</div></div>')

    chips = []
    if match:
        if getattr(match, "industry", None):
            chips.append(f'<span class="jc-chip">{_esc(match.industry)}</span>')
        if match.is_entry_level:
            chips.append('<span class="jc-chip is-good">junior / staż</span>')
        if match.learnable_in_month:
            chips.append('<span class="jc-chip is-good">nauka ≤ 1 mc</span>')
        for skill in (getattr(match, "missing_skills", None) or [])[:5]:
            chips.append(f'<span class="jc-chip is-gap">brak: {_esc(skill)}</span>')
    if chips:
        blocks.append(f'<div class="wd-chips wd-part" style="--i:2">'
                      f'{"".join(chips)}</div>')

    if match and getattr(match, "reason", None):
        blocks.append(f'<div class="wd-reason wd-part" style="--i:3">'
                      f'{_esc(match.reason)}</div>')

    paragraphs = format_description(job.description, drop_prefix=job.title)
    if paragraphs:
        blocks.append(f'<div class="wd-descwrap wd-part" style="--i:4">'
                      f'<div class="wd-desc">{_wd_body_html(paragraphs)}</div></div>')

    st.markdown("".join(blocks), unsafe_allow_html=True)

    if stage_ctl and status:
        ws_panel_head("etap rekrutacji", mid=True)
        with st.container(key="wstool_stage"):
            raw = st.session_state.user_decisions.get(canonical_link(link), {})
            current = raw.get("stage") if isinstance(raw, dict) else None
            current = WS_STAGE_LEGACY.get(current, current)
            if current not in WS_STAGE_NAMES:
                current = status if status in WS_STAGE_NAMES else "save"
            keys = list(WS_STAGE_NAMES)
            new_stage = st.selectbox("Etap", options=keys,
                                     index=keys.index(current),
                                     format_func=lambda x: WS_STAGE_NAMES[x],
                                     key=get_key("ws_stage", link),
                                     label_visibility="collapsed")
            if new_stage != current:
                update_decision(link, status, rating or 5, stage=new_stage)
                _ws_touch()
                st.rerun(scope="fragment")

    ws_panel_head("decyzja", mid=True)

    with st.container(key="wsactions"):
        with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
            st.markdown('<div class="wd-ratelbl">ocena</div>',
                        unsafe_allow_html=True, width="content")
            rating_val = st.slider("Ocena", 1, 10, rating if rating else 5,
                                   key=get_key("ws_slider", link),
                                   label_visibility="collapsed", width="stretch")
            if st.button("Oceń", key=get_key("ws_conf", link),
                         help="Zapisuje samą ocenę - oferta zostaje na liście"):
                update_decision(link, "rated", rating_val)
                _ws_touch()
                st.rerun(scope="fragment")

        with st.container(horizontal=True, gap="small"):
            if st.button("Zapisz", icon=":material/bookmark:",
                         key=get_key("ws_save", link),
                         help="Trafi do zakładki Zapisane"):
                update_decision(link, "save", rating_val)
                _ws_touch()
                st.rerun(scope="fragment")
            if st.button("Wysłane", icon=":material/send:",
                         key=get_key("ws_app", link),
                         help="Aplikacja poszła - zapisuje dzisiejszą datę"):
                update_decision(link, "apply", rating_val)
                _ws_touch()
                st.rerun(scope="fragment")
            if st.button("Aspiruję", icon=":material/trending_up:",
                         key=get_key("ws_asp", link),
                         help="Za wysoko na teraz, ale w tę stronę celujesz"):
                update_decision(link, "aspirational", rating_val)
                _ws_touch()
                st.rerun(scope="fragment")
            if st.button("Odrzuć", icon=":material/close:",
                         key=get_key("ws_rej", link),
                         help="Do kosza - profil uczy się, czego nie chcesz"):
                update_decision(link, "reject", min(rating_val, 3))
                _ws_touch()
                st.rerun(scope="fragment")

        with st.container(key="wssecond", horizontal=True, gap="small"):
            if status:
                if st.button("Przywróć", icon=":material/undo:",
                             key=get_key("ws_rest", link),
                             help="Wraca do bazy jako nieoceniona"):
                    st.session_state.user_decisions.pop(link, None)
                    st.session_state.user_decisions.pop(canonical_link(link), None)
                    save_user_decisions(st.session_state.user_decisions)
                    _ws_touch()
                    st.rerun(scope="fragment")

            confirm_key = f"ws_confirm_{hashlib.md5(link.encode()).hexdigest()[:10]}"
            if st.session_state.get(confirm_key):
                if st.button("Na pewno usunąć?", icon=":material/delete_forever:",
                             key=get_key("ws_delyes", link),
                             help="Kliknij, żeby usunąć bezpowrotnie"):
                    st.session_state.pop(confirm_key, None)
                    _delete_job_permanent(link)
                    st.session_state.ws_selected = None
                    _ws_touch()
                    st.toast("Oferta usunięta z bazy.")
                    st.rerun(scope="fragment")
            else:
                if st.button("Usuń", icon=":material/delete:",
                             key=get_key("ws_del", link),
                             help="Usuwa z bazy, z wyników AI i z Twoich decyzji"):
                    st.session_state[confirm_key] = True
                    st.rerun(scope="fragment")


# --- widoki narzedziowe: ten sam fason, ten sam uklad dwoch paneli ---------

def ws_board_stages():
    """Oferty rozłożone na etapy rekrutacji. Zwraca (etap -> lista krotek)."""
    stages = {key: [] for key in WS_STAGE_NAMES}

    for link, ddata in st.session_state.user_decisions.items():
        if isinstance(ddata, dict):
            status, stage = ddata.get("status"), ddata.get("stage")
        else:
            status, stage = ddata, None

        stage = WS_STAGE_LEGACY.get(stage, stage)
        # Na tablicy ląduje tylko to, w czym coś się dzieje. Odrzucenie przy
        # przeglądaniu listy nie jest etapem rekrutacji - takich ofert były
        # setki i zalewały "Archiwum", przez co tablica nic nie pokazywała.
        if stage in stages:
            target = stage
        elif status in ("save", "apply"):
            target = status
        else:
            continue

        job = find_job_obj(link)
        if job is None:
            continue
        match = find_match(job.link)
        rating = ddata.get("rating") if isinstance(ddata, dict) else None
        stages[target].append((job, match, status, rating))

    return stages


def ws_gaps_data(threshold):
    """
    Braki policzone z ocen, które interfejs i tak trzyma w pamięci.

    `skill_gaps.py` czyta te same dane z dysku - tutaj wchodzą gotowe obiekty
    JobMatch, więc zamiast 36 MB JSON-a przy każdym przeładowaniu strony
    przepisujemy je na kształt, którego oczekuje `collect`. Logika liczenia
    zostaje jedna, w skill_gaps: dwie kopie rozjechałyby się po pierwszej
    poprawce, a wtedy raport z konsoli i zakładka pokazywałyby co innego.
    """
    # Przepisanie 17 tys. ocen kosztuje ułamek sekundy, ale suwak progu wywołuje
    # przeliczenie przy każdym drgnięciu - stąd pamięć na ostatni wynik.
    sig = (threshold,) + _ws_fingerprint()
    cache = st.session_state.setdefault("_ws_gap_cache", {})
    if cache.get("sig") != sig:
        entries = [
            {
                "job": {"link": m.job.link},
                "match_percentage": m.match_percentage,
                "missing_skills": m.missing_skills,
                "learnable_in_month": m.learnable_in_month,
            }
            for m in st.session_state.analyzed_matches
        ]
        cache["sig"] = sig
        cache["data"] = skill_gaps.collect(entries, threshold, skill_gaps.dismissed_links())
    return cache["data"]


def ws_tool_gaps(left, right):
    threshold = st.session_state.get("ws_gap_threshold", 50)
    gaps, considered, skipped = ws_gaps_data(threshold)
    rows = skill_gaps.rank(gaps, 14)

    with right:
        with st.container(key="wsright"):
            ws_panel_head("czego brakuje", fmt_n(len(gaps)))

            if not rows:
                st.markdown(
                    '<div class="wp-note">Nic nad tym progiem. Obniż go po lewej '
                    'albo poczekaj, aż pipeline oceni świeże oferty.</div>',
                    unsafe_allow_html=True)
            else:
                top = rows[0]["count"]
                out = []
                for i, row in enumerate(rows):
                    # Szerokość paska względem pierwszej pozycji, nie względem
                    # liczby ofert - inaczej przy 18 trafieniach na 1240 ofert
                    # wszystkie paski byłyby niewidoczną kreską.
                    width = max(4, round(100 * row["count"] / top))
                    out.append(
                        f'<div class="wg-row is-fresh" style="--i:{min(i, 24)}">'
                        f'<div class="wg-rank">{i + 1:02d}</div>'
                        f'<div class="wg-name">{_esc(row["skill"])}</div>'
                        f'<div class="wg-track"><i style="width:{width}%"></i></div>'
                        f'<div class="wg-n">{row["count"]}</div>'
                        f'<div class="wg-avg">{row["mean_match"]:.0f}%</div>'
                        f'</div>'
                    )
                st.markdown("".join(out), unsafe_allow_html=True)
                st.markdown('<div class="wp-foot">ile ofert &middot; '
                            'średnie dopasowanie</div>', unsafe_allow_html=True)

    with left:
        with st.container(key="wsleft"):
            ws_panel_head("jak to czytać")
            st.markdown(
                '<div class="wp-note">Ranking mówi, w co aplikować dzisiaj. '
                'To zestawienie odpowiada na inne pytanie: <em>czego się nauczyć, '
                'żeby ranking miał z czego wybierać</em>. Liczy się tylko to, czego '
                'zabrakło w ofertach, które poza tym pasowały.</div>',
                unsafe_allow_html=True)

            st.slider("Próg dopasowania", min_value=30, max_value=80, step=5,
                      value=50, key="ws_gap_threshold", format="%d%%",
                      help="Od jakiego dopasowania oferta wchodzi do zestawienia")

            st.markdown(
                f'<div class="wx-stat"><span class="wx-stat-lbl">ofert nad progiem'
                f'</span><span class="wx-stat-val is-on">{fmt_n(considered)}</span></div>'
                f'<div class="wx-stat"><span class="wx-stat-lbl">pominiętych jako '
                f'odrzucone</span><span class="wx-stat-val'
                f'{" is-off" if skipped == 0 else ""}">{fmt_n(skipped)}</span></div>'
                f'<div class="wx-stat"><span class="wx-stat-lbl">różnych braków'
                f'</span><span class="wx-stat-val">{fmt_n(len(gaps))}</span></div>',
                unsafe_allow_html=True)

            st.markdown(
                '<div class="wp-hint">Próg ma znaczenie i dlatego nie da się go '
                'pominąć. Bez niego na czoło wychodzi „wykształcenie medyczne” '
                'i „uprawnienia SEP” - braki prawdziwe, tylko względem ofert, '
                'których i tak nie tkniesz.<br><br>Odrzucone przez Ciebie oferty '
                'nie wchodzą do rachunku: ich braki nie mówią nic o kierunku nauki, '
                'bo tę drogę już świadomie odrzuciłeś.</div>',
                unsafe_allow_html=True)


def ws_tool_add(left, right):
    if 'manual_job_data' not in st.session_state:
        st.session_state.manual_job_data = None
    if 'manual_job_url' not in st.session_state:
        st.session_state.manual_job_url = ""

    data = st.session_state.manual_job_data

    with right:
        with st.container(key="wsright"):
            ws_panel_head("dodaj z linku")
            with st.container(key="wstool_add"):
                url_input = st.text_input(
                    "URL oferty", value=st.session_state.manual_job_url,
                    placeholder="https://…", label_visibility="collapsed")

                with st.container(horizontal=True, gap="small"):
                    fetch = st.button("Pobierz dane", icon=":material/download:",
                                      key="wstool_fetch")
                    if data and st.button("Wyczyść", icon=":material/backspace:",
                                          key="wstool_clear"):
                        st.session_state.manual_job_data = None
                        st.session_state.manual_job_url = ""
                        st.rerun(scope="fragment")

                if fetch and url_input:
                    with st.spinner("Czytam stronę oferty…"):
                        from utils.link_fetcher import extract_job_info_from_url
                        fetched = extract_job_info_from_url(url_input)
                        fetched['link'] = url_input
                        st.session_state.manual_job_data = fetched
                        st.session_state.manual_job_url = url_input
                        st.rerun(scope="fragment")

                if data:
                    ws_panel_head("sprawdź przed zapisem", mid=True)
                    c1, c2 = st.columns(2)
                    with c1:
                        title = st.text_input("Tytuł stanowiska",
                                              value=data.get('title', ''))
                    with c2:
                        company = st.text_input("Firma", value=data.get('company', ''))
                    c3, c4 = st.columns(2)
                    with c3:
                        location = st.text_input("Lokalizacja",
                                                 value=data.get('location', ''))
                    with c4:
                        source = st.text_input("Portal", value=data.get('source', ''))
                    description = st.text_area("Opis", value=data.get('description', ''),
                                               height=140)

                    data.update({'title': title, 'company': company,
                                 'location': location, 'source': source,
                                 'description': description})

                    exists = data['link'] in st.session_state.job_lookup
                    if st.button("Zapisz w bazie" if not exists else "Nadpisz w bazie",
                                 icon=":material/save:", key="wstool_save",
                                 disabled=not (title and company)):
                        ws_save_manual_job(data)
                        st.session_state.ws_selected = data['link']
                        _ws_touch()
                        st.toast("Oferta zapisana w bazie.")
                        st.rerun(scope="fragment")
                    if not (title and company):
                        st.markdown('<div class="wp-note is-warn">Tytuł i firma są '
                                    'wymagane - bez nich oferta nie da się odnaleźć '
                                    'na liście.</div>', unsafe_allow_html=True)

    with left:
        with st.container(key="wsleft"):
            selected = st.session_state.get("ws_selected")
            if selected and selected in st.session_state.job_lookup:
                ws_render_detail(selected)
            elif data:
                ws_panel_head("podgląd")
                meta = '<span class="wd-sep">·</span>'.join(
                    f'<span>{_esc(b)}</span>' for b in
                    [data.get('company') or "—", data.get('location') or "—",
                     data.get('source') or "—"] if b)
                st.markdown(
                    f'<div class="wd-part" style="--i:0">'
                    f'<div class="wd-title">{_esc(data.get("title") or "Bez tytułu")}'
                    f'</div><div class="wd-meta">{meta}</div></div>',
                    unsafe_allow_html=True)
                paragraphs = format_description(data.get('description', ''),
                                                drop_prefix=data.get('title', ''))
                if paragraphs:
                    st.markdown(
                        f'<div class="wd-descwrap wd-part" style="--i:1">'
                        f'<div class="wd-desc">{_wd_body_html(paragraphs)}</div></div>',
                        unsafe_allow_html=True)
                st.markdown('<div class="wp-hint">Tak oferta wejdzie do bazy. '
                            'Popraw pola po prawej, jeśli portal coś przekręcił.</div>',
                            unsafe_allow_html=True)
            else:
                ws_panel_head("po co to")
                st.markdown(
                    '<div class="wp-note">Skrapery nie sięgają wszędzie. Jeśli '
                    'trafisz na ofertę poza nimi - z LinkedIna kogoś znajomego, '
                    'ze strony firmy - wklej adres po prawej. Aplikacja przeczyta '
                    'stronę, wypełni pola, a Ty je poprawisz przed zapisem.</div>',
                    unsafe_allow_html=True)
                st.markdown('<div class="wp-hint">Oferta dodana ręcznie zachowuje '
                            'się jak każda inna: można ją ocenić, zapisać i '
                            'odrzucić.</div>', unsafe_allow_html=True)


def ws_save_manual_job(data):
    """
    Zapis ręcznie dodanej oferty - do sesji i na dysk.

    Wcześniej działo się to przy samym rysowaniu podglądu, więc oferta lądowała
    w bazie, zanim ktokolwiek nacisnął cokolwiek. Teraz zapisuje wyłącznie
    przycisk.
    """
    job = Job(title=data['title'], company=data['company'], link=data['link'],
              description=data.get('description', ''), source=data.get('source', ''),
              location=data.get('location', ''))

    existing = st.session_state.job_lookup.get(job.link)
    if existing is not None:
        for field in ("title", "company", "location", "source", "description"):
            setattr(existing, field, getattr(job, field))
    else:
        st.session_state.raw_jobs.append(job)
        st.session_state.job_lookup[job.link] = job

    try:
        db = JobDatabase(str(JOBS_DATABASE_PATH))
        jobs = [j for j in db.load_jobs() if j.link != job.link]
        jobs.append(job)
        db.save_jobs(jobs)
    except Exception as e:
        logger.warning(f"Nie udało się dopisać oferty do bazy: {e}")
        st.error(f"Oferta jest w sesji, ale zapis do pliku nie wyszedł: {e}")


def ws_tool_pipeline(left, right):
    base = Path(__file__).parent
    analyzed_path = base / "analyzed_jobs_waterfall.json"
    profile_path = base / "preference_profile.json"

    # Liczymy na ZBIORACH linków, nie na długościach list. Poprzednia wersja
    # robiła `baza - oceny_AI - decyzje`, a decyzje dotyczą ofert, które już
    # mają ocenę AI - ta sama oferta była odejmowana dwa razy i "czeka na ocenę"
    # potrafiło pokazać 0 przy tysiącach nieprzeanalizowanych ofert.
    # Te same dane siedza juz w pamieci sesji - wczytane raz przy starcie.
    # Wczesniej kazde przeladowanie tej zakladki parsowalo 64 MB JSON-a
    # z dysku (0,56 s zmierzone), zeby policzyc dlugosci trzech zbiorow.
    # Po kroku pipeline'u UI i tak przeladowuje dane, wiec liczby sa swieze.
    db_links = {canonical_link(j.link) for j in st.session_state.raw_jobs}
    analyzed_links = {canonical_link(m.job.link) for m in st.session_state.analyzed_matches}
    decided_links = {canonical_link(l) for l in st.session_state.user_decisions}

    db_count = len(db_links)
    analyzed_count = len(analyzed_links)
    decisions_count = len(decided_links)
    pending = len(db_links - analyzed_links)

    profile = load_json_safe(profile_path, default={}) or {}
    profile_exists = bool(profile)
    profile_built_from = profile.get("_metadata", {}).get("total_decisions_analyzed", 0) or 0
    new_decisions = max(0, decisions_count - profile_built_from)

    if db_count == 0:
        next_up = ("Zacznij od kroku 01 - baza jest pusta, więc nie ma czego "
                   "oceniać. Pobranie ofert niczego nie kasuje.")
    elif pending > 0:
        next_up = (f"Przejdź do kroku 03 - {fmt_n(pending)} ofert czeka na ocenę AI. "
                   f"Bez tego nie pojawią się w „Dopasowane”.")
    elif not profile_exists and decisions_count >= 10:
        next_up = (f"Przejdź do kroku 02 - masz {decisions_count} ocen, z których "
                   f"da się zbudować profil i trafniej oceniać kolejne oferty.")
    elif new_decisions >= 25:
        next_up = (f"Warto odświeżyć krok 02 - od zbudowania profilu doszło "
                   f"{new_decisions} nowych decyzji.")
    else:
        next_up = ("Wszystko policzone. Wróć do „Dopasowane” i oceniaj oferty - "
                   "każda Twoja ocena poprawia kolejne.")

    # ---------------- lewy panel: stan i historia ----------------
    with left:
        with st.container(key="wsleft"):
            ws_panel_head("stan danych")
            stats = [
                ("ofert w bazie", fmt_n(db_count), ""),
                ("z oceną AI", fmt_n(analyzed_count), ""),
                ("czeka na ocenę", fmt_n(pending), "is-off" if pending else "is-on"),
                ("Twoich decyzji", fmt_n(decisions_count), ""),
                ("profil preferencji", "gotowy" if profile_exists else "brak",
                 "is-on" if profile_exists else "is-off"),
            ]
            for label, val, cls in stats:
                st.markdown(
                    f'<div class="wx-stat"><span class="wx-stat-lbl">{_esc(label)}'
                    f'</span><span class="wx-stat-val {cls}">{_esc(val)}</span></div>',
                    unsafe_allow_html=True)

            st.markdown(f'<div class="wp-note is-ok" style="margin-top:0.9rem">'
                        f'{_esc(next_up)}</div>', unsafe_allow_html=True)

            ws_panel_head("co się ostatnio działo", mid=True)
            rows = ws_activity_rows(limit=4)
            if not rows:
                st.markdown('<div class="wp-empty">Nic jeszcze nie chodziło.</div>',
                            unsafe_allow_html=True)
            else:
                out = ['<div class="wl-block">']
                for i, (when, op, what, detail, bad) in enumerate(rows):
                    out.append(
                        f'<div class="wl-line is-fresh" style="--i:{i}">'
                        f'<span class="wl-time">{when.strftime("%Y-%m-%d %H:%M:%S")}</span>'
                        f'<span class="wl-op{" is-bad" if bad else ""}">{_esc(op)}</span>'
                        f'<span class="wl-what">'
                        f'{src_dot(what) if op == "pobieranie" else _esc(what)}</span>'
                        f'<span class="wl-detail">{_esc(detail)}</span></div>')
                out.append('</div>')
                st.markdown("".join(out), unsafe_allow_html=True)

            ws_panel_head("aplikacja", mid=True)
            cv_path = Path(st.session_state.current_cv_path)
            st.markdown(
                f'<div class="wx-file">{_esc(cv_path.name)}</div>'
                f'<div class="wx-path">{_esc(str(cv_path.absolute().parent))}</div>',
                unsafe_allow_html=True)
            with st.container(key="wstool_cv", horizontal=True, gap="small"):
                if st.button("Otwórz CV", icon=":material/description:",
                             key="wstool_cv_open"):
                    if cv_path.exists():
                        try:
                            os.startfile(str(cv_path))
                            st.toast("Otwieram CV…")
                        except OSError as e:
                            st.error(f"Nie udało się otworzyć pliku: {e}")
                    else:
                        st.error("Nie znaleziono pliku")
                if st.button("Zmień CV", icon=":material/edit:",
                             key="wstool_cv_pick"):
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    root.wm_attributes('-topmost', 1)
                    picked = filedialog.askopenfilename(
                        title="Wybierz plik CV",
                        filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")])
                    root.destroy()
                    if picked:
                        st.session_state.current_cv_path = picked
                        st.session_state.cv_text = None
                        st.rerun(scope="app")
                if st.button("Odśwież", icon=":material/refresh:",
                             key="wstool_reload",
                             help="Czyta pliki z dysku od nowa. Nic nie pobiera "
                                  "z internetu i niczego nie usuwa."):
                    st.session_state.data_loaded = False
                    _ws_touch()
                    st.toast("Wczytano dane z dysku od nowa.")
                    st.rerun(scope="app")

    # ---------------- prawy panel: to, co zmienia stan ----------------
    with right:
        with st.container(key="wsright"):
            ws_panel_head("pipeline", "3 kroki")
            with st.container(key="wstool_steps"):
                st.markdown(
                    '<div class="wx-step is-first is-fresh" style="--i:0">'
                    '<div class="wx-num">01</div>'
                    '<div class="wx-title">Pobierz oferty z portali</div>'
                    '<div class="wx-desc">Odwiedza wszystkie portale i dopisuje do '
                    'bazy oferty, których jeszcze nie masz. Istniejących ofert ani '
                    'Twoich ocen nie rusza.</div>'
                    '<div class="wx-meta">Pracuj.pl · OLX · aplikuj.pl · GoWork.pl · '
                    'praca.pl · NoFluffJobs · JustJoin.it · RocketJobs · SOLID.Jobs · '
                    'LinkedIn · Indeed<br>od kilku minut do ok. 45 min, zależnie od '
                    'liczby nowych ofert</div></div>',
                    unsafe_allow_html=True)
                if st.button("Pobierz oferty", icon=":material/download:",
                             width="stretch", key="run_scrapers_btn"):
                    if _run_step(["main_scraper.py"], "Pobieram oferty z portali…",
                                 "Pobieranie zakończone"):
                        _run_step(["deduplicate_db.py"], "Scalam duplikaty…",
                                  "Duplikaty scalone", tail=6)
                        _run_step(["clean_db.py"], "Skracam opisy…",
                                  "Opisy skrócone", tail=4)
                        st.session_state.data_loaded = False
                        _ws_touch()
                        st.rerun(scope="app")

                st.markdown(
                    '<div class="wx-step is-fresh" style="--i:1">'
                    '<div class="wx-num">02</div>'
                    '<div class="wx-title">Przebuduj profil preferencji</div>'
                    '<div class="wx-desc">Czyta Twoje oceny i wyciąga z nich wzorzec: '
                    'jakie role i branże Ci pasują, a co odrzucasz. Profil trafia do '
                    'polecenia dla AI, więc kolejne oferty są oceniane trafniej.</div>'
                    '<div class="wx-meta">uruchom po każdej większej porcji ocen<br>'
                    'trwa poniżej minuty</div></div>',
                    unsafe_allow_html=True)

                if profile_exists:
                    gen_at = profile.get("_metadata", {}).get("generated_at", "")
                    when = gen_at[:16].replace("T", ", ") if gen_at else "nieznana data"
                    note = (f"Obecny profil: {when}, zbudowany z "
                            f"{profile_built_from} Twoich decyzji.")
                    if new_decisions >= 25:
                        st.markdown(
                            f'<div class="wp-note is-warn">{_esc(note)} '
                            f'<em>Od tego czasu doszło {new_decisions} nowych ocen - '
                            f'przebudowanie poprawi trafność kolejnych.</em></div>',
                            unsafe_allow_html=True)
                    else:
                        st.markdown(f'<div class="wp-note is-ok">{_esc(note)}</div>',
                                    unsafe_allow_html=True)
                    with st.expander("Co jest w profilu", expanded=False, type="compact"):
                        def _list(key):
                            return ", ".join(profile.get(key, [])) or "—"
                        st.markdown(f"**Podsumowanie:** {profile.get('summary', '—')}")
                        st.markdown(f"**Preferowane role:** {_list('preferred_role_types')}")
                        st.markdown(f"**Preferowane branże:** {_list('preferred_industries')}")
                        st.markdown(f"**Przyciąga Cię:** {_list('attractive_keywords')}")
                        st.markdown(f"**Odrzucasz:** {_list('red_flags')}")
                elif decisions_count < 10:
                    st.markdown(
                        f'<div class="wp-note">Masz {decisions_count} ocen. Profil '
                        f'zbuduje się sensownie od jakichś dziesięciu - oceniaj dalej '
                        f'oferty na liście.</div>', unsafe_allow_html=True)

                if st.button("Przebuduj profil", icon=":material/autorenew:",
                             width="stretch", key="gen_profile_btn"):
                    if _run_step(["generate_preference_profile.py"],
                                 "Buduję profil z Twoich ocen…",
                                 "Profil przebudowany", tail=8):
                        st.rerun(scope="app")

                st.markdown(
                    f'<div class="wx-step is-fresh" style="--i:2">'
                    f'<div class="wx-num">03</div>'
                    f'<div class="wx-title">Oceń oferty przez AI</div>'
                    f'<div class="wx-desc">Wysyła do Gemini oferty, które nie mają '
                    f'jeszcze oceny, i dla każdej liczy procent dopasowania oraz '
                    f'uzasadnienie. Dopiero po tym kroku oferta pojawia się '
                    f'w „Dopasowane”.</div>'
                    f'<div class="wx-meta">do policzenia teraz: {fmt_n(pending)} ofert<br>'
                    f'około {max(1, round(pending / 180))} min · zużywa limit Gemini'
                    f'</div></div>',
                    unsafe_allow_html=True)
                if st.button("Oceń oferty", icon=":material/play_arrow:",
                             width="stretch", key="run_analysis_btn",
                             disabled=pending == 0):
                    if _run_step(["waterfall_analysis.py"], "Oceniam oferty przez AI…",
                                 "Ocenianie zakończone"):
                        st.session_state.data_loaded = False
                        _ws_touch()
                        st.rerun(scope="app")
                if pending == 0 and db_count:
                    st.markdown('<div class="wp-note is-ok">Wszystkie oferty w bazie '
                                'mają już ocenę AI.</div>', unsafe_allow_html=True)

            ws_panel_head("rzadziej używane", mid=True)

            # Przeliczenie od zera jest nieodwracalne i kosztuje cały limit
            # Gemini, więc siedzi osobno, a nie jako pole wyboru obok zwykłego
            # przycisku, gdzie łatwo je kliknąć przez pomyłkę.
            with st.expander("Policz wszystkie oceny AI od nowa", expanded=False,
                             type="compact"):
                with st.container(key="wstool_recalc"):
                    st.markdown(
                        f"Kasuje **{fmt_n(analyzed_count)}** dotychczasowych ocen AI "
                        f"i liczy je od zera. Twoje własne oceny i decyzje zostają "
                        f"nietknięte, ale przeliczenie zużyje limit Gemini na "
                        f"wszystkie oferty w bazie (około "
                        f"{max(1, round(db_count / 180))} min). Ma sens po zmianie "
                        f"polecenia dla AI albo modelu.")
                    confirm = st.text_input("Wpisz PRZELICZ, żeby odblokować",
                                            key="confirm_recalc", placeholder="PRZELICZ")
                    if st.button("Skasuj oceny AI i policz od nowa",
                                 icon=":material/warning:", key="recalc_all_btn",
                                 disabled=confirm.strip().upper() != "PRZELICZ"):
                        save_json_atomic(str(analyzed_path), [], backup=True)
                        st.toast("Skasowano dotychczasowe oceny AI (kopia w backups/).")
                        if _run_step(["waterfall_analysis.py"], "Liczę wszystko od nowa…",
                                     "Przeliczone"):
                            st.session_state.data_loaded = False
                            _ws_touch()
                            st.rerun(scope="app")

            with st.expander("Klucze API", expanded=False, type="compact"):
                with st.container(key="wstool_keys"):
                    ws_render_env_keys()


def ws_render_env_keys():
    """Edycja kluczy w .env. Wartości nie opuszczają dysku."""
    fields = [
        ("GEMINI_API_KEY_PRIMARY", "Gemini - klucz główny", True),
        ("GEMINI_API_KEY_1", "Gemini - zapas 1", True),
        ("GEMINI_API_KEY_2", "Gemini - zapas 2", True),
        ("GEMINI_API_KEY_3", "Gemini - zapas 3", True),
        ("GEMINI_API_KEY_4", "Gemini - zapas 4", True),
        ("ADZUNA_APP_ID", "Adzuna App ID", False),
        ("ADZUNA_APP_KEY", "Adzuna App Key", True),
        ("JOOBLE_API_KEY", "Jooble API Key", True),
        ("CAREERJET_API_KEY", "Careerjet API Key", True),
    ]
    env_path = Path(__file__).parent / ".env"
    current = {name: "" for name, _, _ in fields}
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in current:
                        current[k.strip()] = v.strip()
        except OSError as e:
            logger.warning(f"Nie udało się odczytać .env: {e}")

    st.markdown('<div class="wx-meta">Klucze 1-4 służą do rotacji przy limitach. '
                'Portale pracy są opcjonalne - skraper włącza się sam po ustawieniu '
                'klucza.</div>', unsafe_allow_html=True)

    new_vals = {}
    for name, label, secret in fields:
        new_vals[name] = st.text_input(label, value=current[name],
                                       type="password" if secret else "default",
                                       key=f"env_{name}")

    if st.button("Zapisz klucze", icon=":material/save:", key="save_env_keys_btn"):
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        written, out = set(), []
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in new_vals:
                    out.append(f"{k}={new_vals[k]}")
                    written.add(k)
                    continue
            out.append(line)
        for k, v in new_vals.items():
            if k not in written:
                out.append(f"{k}={v}")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        st.toast("Zapisano klucze w .env")


# --- pasek u gory i sklejenie calosci --------------------------------------

def ws_render_topbar(active):
    """Pasek u góry: marka, listwa liczb, lejek aplikacji, zakładki."""
    n_raw = len(st.session_state.raw_jobs)
    n_analyzed = len(st.session_state.analyzed_matches)

    # Lejek stoi tutaj, a nie w osobnej zakładce: "Tablica" pokazywała te same
    # oferty, które są w zakładkach obok, tylko pogrupowane - a jedyne, czego
    # nie dało się odczytać skądinąd, to ile ich jest na którym etapie.
    stages = ws_board_stages()
    funnel = "".join(
        f'<div class="wt-stage{"" if stages[key] else " is-off"}">'
        f'<b>{len(stages[key])}</b><span>{_esc(label.lower())}</span></div>'
        for key, label in WS_STAGE_NAMES.items()
    )

    # Marka i liczby jednym blokiem: kolumny Streamlita mierzyly wlasna
    # wysokosc inaczej niz tresc i wlosowa kreska przechodzila przez cyfry.
    st.markdown(
        f'<div class="wt-bar">'
        f'<div class="wt-brand">job<span>finder</span></div>'
        f'<div class="wt-rail">'
        f'<div class="wt-item"><b>{fmt_n(n_raw)}</b><span>w bazie</span></div>'
        f'<div class="wt-item"><b>{fmt_n(n_analyzed)}</b><span>ocen AI</span></div>'
        f'<div class="wt-sep"></div>'
        f'<div class="wt-funnel">{funnel}</div>'
        f'</div></div>',
        unsafe_allow_html=True
    )

    with st.container(key="wstabs"):
        chosen = st.segmented_control(
            "Widok", WS_ALL, default=active, key="ws_nav",
            label_visibility="collapsed"
        )
    return chosen or active


@st.fragment
def render_workspace():
    """
    Cały pulpit: pasek u góry i dwa panele pod nim.

    Fragment, a nie zwykła funkcja: kliknięcie oferty czy zmiana zakładki
    przelicza wyłącznie ten kawałek, zamiast całego skryptu razem
    z wczytywaniem danych z dysku. Stąd st.rerun(scope="app") przy tych
    nielicznych działaniach, które faktycznie zmieniają dane pod spodem.
    """
    active = st.session_state.get("ws_view", "Dopasowane")
    chosen = ws_render_topbar(active)

    if chosen != active:
        st.session_state.ws_view = chosen
        st.session_state.ws_selected = None
        active = chosen

    st.markdown(f'<div class="wp-hint-top">{_esc(WS_TAB_HINT.get(active, ""))}</div>',
                unsafe_allow_html=True)

    with st.container(key="wscols"):
        left, right = st.columns([1, 1.08], gap="medium")

        if active == "Czego brakuje":
            ws_tool_gaps(left, right)
        elif active == "Dodaj z linku":
            ws_tool_add(left, right)
        elif active == "Panel sterowania":
            ws_tool_pipeline(left, right)
        else:
            # Prawy panel liczy się pierwszy, choć stoi po prawej: to on
            # przyjmuje kliknięcie w ofertę, a lewy ma o nim wiedzieć już
            # w tym samym przebiegu.
            with right:
                with st.container(key="wsright"):
                    ws_render_list_panel(active)
            with left:
                with st.container(key="wsleft"):
                    selected = st.session_state.get("ws_selected")
                    if selected:
                        # Etap rekrutacji był wcześniej wyłącznie na Tablicy.
                        # Sam pilnuje, żeby pokazać się tylko dla oferty,
                        # która ma jakąkolwiek decyzję.
                        ws_render_detail(selected, stage_ctl=True)
                    else:
                        ws_render_activity()


# =============================================================================
# APLIKACJA
# =============================================================================

def main():
    inject_custom_css()
    inject_workspace_css()
    init_session_state()
    load_data()

    # Nawigacja jest wylacznie na gornym pasku. Panelu bocznego nie ma wcale -
    # to, co w nim zostawalo (plik CV, ponowny odczyt z dysku), siedzi teraz
    # w "Panel sterowania", czyli tam, gdzie reszta ustawien aplikacji.
    render_workspace()


if __name__ == "__main__":
    main()
