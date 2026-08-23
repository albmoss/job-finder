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
from utils.offer_age import ghost_signals, ghost_label
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
        .vh-eyebrow {
            font-family: var(--font-mono);
            font-size: 0.68rem;
            font-weight: 500;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--faint);
            margin-bottom: 0.3rem;
        }
        .vh-title {
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: 1.65rem;
            font-weight: 600;
            letter-spacing: -0.02em;
            color: var(--text);
            line-height: 1.15;
        }
        .vh-sub {
            font-size: 0.86rem;
            color: var(--muted);
            margin-top: 0.3rem;
            max-width: 62ch;
        }

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
        .rail-item {
            display: flex;
            align-items: baseline;
            gap: 0.45rem;
            padding: 0 1.15rem;
            border-right: 1px solid var(--line-soft);
        }
        .rail-item:first-child { padding-left: 0; }
        .rail-item:last-child { border-right: none; }
        .rail-val {
            font-family: var(--font-mono);
            font-size: 1.02rem;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
            color: var(--text);
        }
        .rail-lbl {
            font-size: 0.74rem;
            color: var(--muted);
            letter-spacing: 0.02em;
        }
        .rail-val.is-on  { color: var(--moss); }
        .rail-val.is-off { color: var(--clay); }

        /* ---------- karta oferty ----------
           Układ wzorowany na JustJoin.it i NoFluffJobs, gdzie ta sama lista
           jest przeglądana setki razy dziennie. Oba portale trzymają się
           tej samej dyscypliny: kolorowe logo po lewej, jedna kolorowa
           metryka po prawej, cała reszta szara. Kolor niesie tam wyłącznie
           to, po czym się decyduje - u nich zarobki, u nas dopasowanie. */
        [class*="st-key-jc_"] {
            position: relative;
            background: var(--raised);
            border: 1px solid var(--line);
            border-radius: 11px;
            padding: 0.95rem 1.15rem 0.75rem 1.35rem;
            margin-bottom: 0.75rem;
            transition: background 0.13s ease, border-color 0.13s ease;
        }
        [class*="st-key-jc_"]:hover {
            background: var(--raised-hi);
            border-color: color-mix(in srgb, var(--line) 70%, var(--text));
        }

        [class*="st-key-jc_"] > [data-testid="stElementContainer"]:first-child {
            position: static;
        }

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
        .jc-rail-fill {
            position: absolute;
            bottom: 0; left: 0;
            width: 100%;
            border-radius: 0 2px 2px 0;
        }
        .jc-rail.is-empty {
            background: repeating-linear-gradient(
                to bottom, var(--line) 0 3px, transparent 3px 7px);
        }

        /* --- górny rząd: znacznik firmy | tytuł i meta | dopasowanie --- */
        .jc-top {
            display: flex;
            align-items: flex-start;
            gap: 0.85rem;
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
            font-size: 0.82rem;
            font-weight: 600;
            letter-spacing: 0.02em;
            border: 1px solid;
        }

        .jc-id { min-width: 0; flex: 1; }
        .jc-titleline {
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
            flex-wrap: wrap;
        }
        .jc-title {
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: 1.14rem;
            font-weight: 600;
            color: var(--text-bright);
            line-height: 1.25;
            letter-spacing: -0.015em;
        }
        .jc-new {
            font-size: 0.6rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--moss);
            background: color-mix(in srgb, var(--moss) 13%, transparent);
            border: 1px solid color-mix(in srgb, var(--moss) 34%, transparent);
            border-radius: 3px;
            padding: 0.08rem 0.32rem;
        }

        /* Ogłoszenie, które wisi zbyt długo. Ten sam kształt co .jc-new, ale
           wyciszony - to ostrzeżenie, nie alarm, bo sygnał jest poszlakowy. */
        .jc-stale {
            font-size: 0.6rem;
            font-weight: 600;
            letter-spacing: 0.06em;
            color: var(--muted);
            background: color-mix(in srgb, var(--muted) 10%, transparent);
            border: 1px solid color-mix(in srgb, var(--muted) 26%, transparent);
            border-radius: 3px;
            padding: 0.08rem 0.32rem;
        }
        .jc-stale.is-ghost {
            color: var(--clay);
            background: color-mix(in srgb, var(--clay) 12%, transparent);
            border-color: color-mix(in srgb, var(--clay) 32%, transparent);
        }

        /* Meta pod tytułem, drobna i szara - jak na obu portalach.
           Nazwa firmy jaśniejsza, bo to ona identyfikuje ofertę. */
        .jc-meta {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.4rem;
            margin-top: 0.28rem;
            font-size: 0.78rem;
            color: var(--muted);
        }
        .jc-org { color: color-mix(in srgb, var(--text) 78%, transparent); font-weight: 500; }
        .jc-sep { color: var(--line); }
        /* Ta sama oferta na innych portalach - jeden element flexa, żeby
           etykieta i linki nie rozjechały się po gapie .jc-meta */
        .jc-alt-wrap { color: var(--muted); }
        .jc-alt {
            color: inherit;
            text-decoration: underline dotted;
            text-underline-offset: 2px;
        }
        .jc-alt:hover { color: color-mix(in srgb, var(--text) 85%, transparent); }
        .jc-state {
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
            font-size: 0.69rem;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }
        .jc-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }

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
        .jc-score-val {
            font-family: var(--font-mono);
            font-size: 1.5rem;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
            line-height: 1;
            letter-spacing: -0.03em;
        }
        .jc-score-pct { font-size: 0.58em; font-weight: 500; opacity: 0.6; }

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
            font-size: 0.71rem;
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
            font-size: 0.825rem;
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
            font-size: 0.815rem;
            line-height: 1.65;
            color: color-mix(in srgb, var(--text) 58%, transparent);
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .jc-full { max-height: 24rem; overflow-y: auto; padding-right: 0.6rem; }
        .jc-full-p {
            max-width: 80ch;
            font-size: 0.85rem;
            line-height: 1.7;
            color: color-mix(in srgb, var(--text) 80%, transparent);
            margin: 0 0 0.7rem 0;
        }
        .jc-full-p:last-child { margin-bottom: 0; }
        .jc-full::-webkit-scrollbar { width: 4px; }
        .jc-full::-webkit-scrollbar-thumb { background: var(--line); border-radius: 4px; }

        /* ---------- kontrolki w karcie ---------- */
        [class*="st-key-jc_"] [data-testid="stSlider"] { padding-top: 0.1rem; }
        [class*="st-key-jc_"] [data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {
            height: 13px; width: 13px;
        }
        [class*="st-key-jc_"] [data-testid="stSliderTickBarMin"],
        [class*="st-key-jc_"] [data-testid="stSliderTickBarMax"] { display: none; }

        [class*="st-key-jc_"] [data-testid="stExpander"] details {
            border: none;
            background: transparent;
            margin-top: 0.35rem;
            margin-left: 3.45rem;
        }
        [class*="st-key-jc_"] [data-testid="stExpander"] summary {
            padding-left: 0;
            font-size: 0.76rem;
            color: var(--faint);
        }
        [class*="st-key-jc_"] [data-testid="stExpander"] summary:hover { color: var(--muted); }

        /* ---------- rząd akcji ---------- */
        [class*="st-key-ja_jc_"] {
            border-top: 1px solid var(--line-soft);
            padding-top: 0.6rem;
            margin-top: 0.6rem;
        }
        .jc-rate-lbl {
            font-size: 0.71rem;
            color: var(--faint);
            white-space: nowrap;
        }
        /* Suwak rozpychał się na całą kolumnę i wypychał przycisk
           zatwierdzenia na jej przeciwległy koniec. Ograniczyć trzeba
           kontener elementu, bo to on jest elastycznym dzieckiem rzędu -
           samo zwężenie widżetu w środku niczego nie zmienia. */
        [class*="st-key-ja_jc_"] [data-testid="stElementContainer"]:has([data-testid="stSlider"]) {
            flex: 0 0 10.5rem;
            max-width: 10.5rem;
        }
        [class*="st-key-ja_jc_"] [data-testid="stSlider"] { max-width: 10.5rem; }

        /* Pionowa kreska oddziela decyzje od "otwórz" i "więcej" - inaczej
           sześć ikon w rzędzie czyta się jak jeden ciąg. */
        .jc-actions-sep {
            width: 1px;
            height: 1.35rem;
            background: var(--line);
            margin: 0 0.15rem;
        }

        /* Przyciski decyzji są bez podpisów - kształt ikony i jej barwa mają
           powiedzieć, co robią, zanim zdążysz przeczytać. Każda decyzja ma
           własny odcień, ten sam, którym oznaczony jest jej stan na karcie
           i w nazwie widoku. */
        [class*="st-key-jc_"] .stButton button,
        [class*="st-key-jc_"] .stLinkButton a {
            font-size: 0.79rem;
            padding: 0.3rem 0.55rem;
            min-height: 0;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--line);
            color: color-mix(in srgb, var(--text) 86%, transparent);
        }
        [class*="st-key-jc_"] .stButton button:hover,
        [class*="st-key-jc_"] .stLinkButton a:hover {
            background: rgba(255, 255, 255, 0.07);
            border-color: color-mix(in srgb, var(--line) 60%, var(--text));
        }
        [class*="st-key-jc_"] [data-testid="stIconMaterial"] { font-size: 1.15rem; }

        [class*="st-key-save_"] [data-testid="stIconMaterial"] { color: color-mix(in srgb, var(--slate) 84%, white); }
        [class*="st-key-app_"] [data-testid="stIconMaterial"] { color: color-mix(in srgb, var(--moss) 84%, white); }
        [class*="st-key-asp_"] [data-testid="stIconMaterial"] { color: color-mix(in srgb, var(--amber) 84%, white); }
        [class*="st-key-rej_"] [data-testid="stIconMaterial"] { color: color-mix(in srgb, var(--clay) 84%, white); }
        [class*="st-key-conf_"] [data-testid="stIconMaterial"] { color: var(--muted); }

        /* Usuwanie jest jedynym nieodwracalnym działaniem w rzędzie - stoi
           obok reszty, ale wygasza się do szarości i dopiero pod kursorem
           pokazuje, czym jest. Drugi klik ("Na pewno?") świeci od razu. */
        [class*="st-key-del_"] [data-testid="stIconMaterial"] { color: var(--faint); }
        [class*="st-key-del_"] button:hover {
            border-color: color-mix(in srgb, var(--clay) 55%, transparent);
            background: color-mix(in srgb, var(--clay) 14%, transparent); }
        [class*="st-key-del_"] button:hover [data-testid="stIconMaterial"] {
            color: color-mix(in srgb, var(--clay) 84%, white); }

        [class*="st-key-delyes_"] button {
            border-color: color-mix(in srgb, var(--clay) 60%, transparent) !important;
            background: color-mix(in srgb, var(--clay) 20%, transparent) !important;
            color: color-mix(in srgb, var(--clay) 88%, white) !important; }
        [class*="st-key-delyes_"] [data-testid="stIconMaterial"] {
            color: color-mix(in srgb, var(--clay) 88%, white); }

        [class*="st-key-save_"] button:hover {
            border-color: color-mix(in srgb, var(--slate) 55%, transparent);
            background: color-mix(in srgb, var(--slate) 14%, transparent); }
        [class*="st-key-app_"] button:hover {
            border-color: color-mix(in srgb, var(--moss) 55%, transparent);
            background: color-mix(in srgb, var(--moss) 14%, transparent); }
        [class*="st-key-asp_"] button:hover {
            border-color: color-mix(in srgb, var(--amber) 55%, transparent);
            background: color-mix(in srgb, var(--amber) 14%, transparent); }
        [class*="st-key-rej_"] button:hover {
            border-color: color-mix(in srgb, var(--clay) 55%, transparent);
            background: color-mix(in srgb, var(--clay) 14%, transparent); }

        /* ---------- sekcje panelu sterowania ----------
           Numeracja kroków niesie informację: to jest realna sekwencja,
           w której kolejność ma znaczenie, a nie ozdobnik. */
        .sec-label {
            font-family: var(--font-mono);
            font-size: 0.68rem;
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
        .step-num {
            font-family: var(--font-mono);
            font-size: 0.75rem;
            font-weight: 500;
            letter-spacing: 0.1em;
            color: var(--accent-dim);
            font-variant-numeric: tabular-nums;
        }
        .step-title {
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: 1.08rem;
            font-weight: 600;
            letter-spacing: -0.01em;
            color: var(--text);
        }
        .step-desc {
            grid-column: 2;
            font-size: 0.84rem;
            line-height: 1.5;
            color: var(--muted);
            margin: 0.3rem 0 0 0;
            max-width: 72ch;
        }
        .step-meta {
            grid-column: 2;
            font-size: 0.74rem;
            color: var(--faint);
            margin-top: 0.35rem;
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
        .next-up-lbl {
            font-family: var(--font-mono);
            font-size: 0.63rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--faint);
            white-space: nowrap;
        }
        .next-up-txt { font-size: 0.9rem; color: var(--text); line-height: 1.45; }

        /* Komunikat stanu w panelu. Własny, bo st.warning/st.info rysują się
           w kolorach Streamlita - żółtym i niebieskim - które nie należą do
           palety aplikacji. Tu wystarczą dwa warianty z --moss i --clay. */
        .panel-note {
            font-size: 0.86rem;
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
        .panel-note.is-stale { border-left-color: var(--clay); }
        .panel-note em { color: var(--muted); font-style: normal; }

        /* ---------- pasek filtrów ---------- */
        [class*="st-key-toolbar_"] {
            background: var(--raised);
            border: 1px solid var(--line);
            border-radius: 6px;
            padding: 0.7rem 0.9rem 0.45rem 0.9rem;
            margin-bottom: 1.1rem;
        }
        [class*="st-key-toolbar_"] label {
            font-size: 0.72rem !important;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--faint) !important;
        }

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

        /* ---------- kanban ---------- */
        .kb-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            padding-bottom: 0.4rem;
            margin-bottom: 0.7rem;
            border-bottom: 1px solid var(--line);
        }
        .kb-name {
            font-size: 0.76rem;
            letter-spacing: 0.09em;
            text-transform: uppercase;
            color: var(--muted);
        }
        .kb-count {
            font-family: var(--font-mono);
            font-size: 0.8rem;
            color: var(--faint);
        }
        [class*="st-key-kb_"] {
            background: var(--raised);
            border: 1px solid var(--line);
            border-left: 2px solid var(--line);
            border-radius: 5px;
            padding: 0.6rem 0.7rem;
            margin-bottom: 0.45rem;
        }
        .kb-title { font-size: 0.87rem; font-weight: 600; line-height: 1.3; }
        .kb-org { font-size: 0.75rem; color: var(--muted); margin-top: 0.15rem; }

        /* ---------- paginacja ---------- */
        .pg-count {
            font-family: var(--font-mono);
            font-size: 0.78rem;
            color: var(--muted);
            letter-spacing: 0.02em;
        }

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
            .jc-top { flex-direction: column; gap: 0.5rem; }
            .jc-score { text-align: left; }
            .rail-item { border-right: none; padding-left: 0; }
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


# Człony nazw spółek nic nie wnoszą do inicjałów - "DAKS sp. z o.o." ma być
# "D", a nie "DS".
_COMPANY_NOISE = {
    "sp", "spolka", "spółka", "z", "o", "oo", "o.o", "o.o.", "zoo", "sa", "s.a",
    "s.a.", "ltd", "llc", "inc", "gmbh", "ograniczona", "odpowiedzialnoscia",
    "odpowiedzialnością", "spzoo", "akcyjna", "and", "the",
    # Kropki są separatorem, więc "S.A." i "S.C." rozpadają się na pojedyncze
    # litery i bez tego trafiałyby do inicjałów ("FLYTRONIC S.A." -> "FS").
    "s", "c",
}


def company_mark(name: str):
    """
    Inicjały i stały odcień wyprowadzony z nazwy firmy - odpowiednik logotypu
    z portali pracy. To właśnie różnokolorowe logotypy sprawiają, że lista
    ofert nie czyta się jak jednolity blok: każdy wiersz dostaje własny punkt
    zaczepienia dla oka. Odcień jest deterministyczny, więc ta sama firma
    zawsze wygląda tak samo.
    """
    clean = re.sub(r'["„”«»\']', '', str(name or "")).strip()
    words = [w for w in re.split(r"[\s/,.\-–—()]+", clean) if w]
    meaningful = [w for w in words if w.lower().strip(".") not in _COMPANY_NOISE]
    initials = "".join(w[0] for w in (meaningful or words)[:2]).upper()[:2] or "?"
    hue = int(hashlib.md5(clean.lower().encode()).hexdigest()[:6], 16) % 360
    return initials, hue


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


def freshness_label(scraped_at):
    """
    Znacznik świeżości W BAZIE - liczony od momentu pobrania, czyli "nowa"
    znaczy "doszła przy ostatnim skanowaniu".

    To celowo NIE to samo co wiek ogłoszenia u pracodawcy: oferta wystawiona pół
    roku temu, a znaleziona przez nas dzisiaj, jest tu "nowa". Od drugiej strony
    jest `ghost_signals` z utils/offer_age.py, które pokazuje na karcie osobny
    znacznik, gdy ogłoszenie wisi zbyt długo.
    """
    if not isinstance(scraped_at, str) or not scraped_at:
        return None
    try:
        days = (datetime.now() - datetime.fromisoformat(scraped_at)).days
    except ValueError:
        return None
    if days <= 1:
        return "nowa"
    if days <= 3:
        return f"{days} dni"
    return None


def _plural_pl(n: int, one: str, few: str, many: str) -> str:
    """
    Polska odmiana przez liczbę: 1 umiejętność, 2-4 umiejętności,
    5+ umiejętności, ale 12-14 znów jak 5+.
    """
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


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
WORK_MODE_FILTER = ["Wszystkie", "100% Zdalnie", "Hybrydowo", "Stacjonarnie"]


# Barwy stanów pochodzą z ui_theme, żeby nie rozjechały się z ikonami
# decyzji, które używają tych samych tokenów w CSS.
DECISION_STYLE = {
    "apply":        (ui_theme.STATES["moss"], "wysłane"),
    "save":         (ui_theme.STATES["slate"], "zapisane"),
    "aspirational": (ui_theme.STATES["amber"], "aspiruję"),
    "reject":       (ui_theme.STATES["clay"], "odrzucone"),
    "rated":        (ui_theme.STATES["grey"], "ocenione"),
}


def render_view_header(eyebrow: str, title: str, subtitle: str = ""):
    """Nagłówek widoku: nadkreślnik mówi gdzie jesteś, tytuł co widzisz."""
    sub_html = f'<div class="vh-sub">{_esc(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="vh">'
        f'<div class="vh-eyebrow">{_esc(eyebrow)}</div>'
        f'<div class="vh-title">{_esc(title)}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True
    )


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

def sync_pagination(source, target):
    """Callback to synchronize pagination between top and bottom controls"""
    st.session_state[target] = st.session_state[source]

def init_session_state():
    if 'cv_text' not in st.session_state: st.session_state.cv_text = None
    if 'analyzed_matches' not in st.session_state: st.session_state.analyzed_matches = []
    if 'raw_jobs' not in st.session_state: st.session_state.raw_jobs = []
    
    if 'user_decisions' not in st.session_state:
        st.session_state.user_decisions = load_user_decisions()
        
    if 'data_loaded' not in st.session_state: st.session_state.data_loaded = False
    if 'current_cv_path' not in st.session_state: st.session_state.current_cv_path = "cv.pdf"
    if 'active_view' not in st.session_state: st.session_state.active_view = "Dopasowane przez AI"
    
    # Indeks ofert po linku - budowany raz przy wczytaniu danych
    if 'job_lookup' not in st.session_state: st.session_state.job_lookup = {}

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
    
    # 3. Indeks po linku
    lookup = {}
    for m in st.session_state.analyzed_matches:
        lookup[m.job.link] = m.job
    for j in st.session_state.raw_jobs:
        if j.link not in lookup:
            lookup[j.link] = j
    st.session_state.job_lookup = lookup
    
    st.session_state.data_loaded = True

def find_job_obj(link):
    """Wyszukanie oferty w czasie stałym, po gotowym indeksie."""
    return st.session_state.job_lookup.get(link)


# =============================================================================
# LISTWA STANU
# =============================================================================

def render_status_rail():
    """
    Kontekst pracy w jednej linii. Świadomie nie są to kafle z gradientem -
    te liczby są tłem dla decyzji, a nie treścią ekranu.
    """
    n_analyzed = len(st.session_state.analyzed_matches)
    n_raw = len(st.session_state.raw_jobs)
    n_active = sum(1 for l in st.session_state.user_decisions
                   if get_decision(l)[0] in ('apply', 'save'))
    profile_exists = Path("preference_profile.json").exists()

    profile_cls = "is-on" if profile_exists else "is-off"
    profile_val = "aktywny" if profile_exists else "brak"

    # Spacja jako separator tysiecy - polska konwencja, a przecinek w f-stringu
    # zderzalby sie z reszta tekstu.
    def n(v):
        return f"{v:,}".replace(",", " ")

    st.markdown(
        f'<div class="rail">'
        f'<div class="rail-item"><span class="rail-val">{n(n_raw)}</span>'
        f'<span class="rail-lbl">w bazie</span></div>'
        f'<div class="rail-item"><span class="rail-val">{n(n_analyzed)}</span>'
        f'<span class="rail-lbl">ocenionych przez AI</span></div>'
        f'<div class="rail-item"><span class="rail-val">{n(n_active)}</span>'
        f'<span class="rail-lbl">zapisanych i wysłanych</span></div>'
        f'<div class="rail-item"><span class="rail-val {profile_cls}">{profile_val}</span>'
        f'<span class="rail-lbl">profil preferencji</span></div>'
        f'</div>',
        unsafe_allow_html=True
    )


# =============================================================================
# KARTA OFERTY
# =============================================================================

@st.fragment
def render_job_card(job, key_prefix, status=None, rating=None, match=None, show_restore=False):
    """
    Karta zbudowana pod jedno zadanie: ocenić ofertę w dwie sekundy.

    Karta jest FRAGMENTEM. Bez tego każde drgnięcie suwaka oceny przeliczało
    cały skrypt od góry - a więc wszystkie 25 kart na stronie, listwę statusu
    i filtry - co wyglądało jak odświeżanie się całej listy przy samym
    dotknięciu suwaka. Fragment ogranicza przeliczenie do jednej karty.

    Dlatego przyciski decyzji wołają `st.rerun(scope="app")`: one faktycznie
    zmieniają zawartość listy i liczniki, więc muszą przeładować stronę.
    Przeładowanie w zakresie fragmentu zostaje tylko tam, gdzie zmiana dotyczy
    wyłącznie tej jednej karty (potwierdzenie usunięcia).

    Układ wzięty z JustJoin.it i NoFluffJobs, gdzie tę samą listę przegląda
    się setkami dziennie:
      znacznik firmy | tytuł + drobna meta | procent dopasowania po prawej,
      pod spodem chipy, werdykt AI i dwie linijki opisu.

    Decyzje zapisują też aktualną pozycję suwaka, więc jedno kliknięcie
    załatwia ocenę i decyzję naraz.
    """
    card_key = f"jc_{key_prefix}_{hashlib.md5(job.link.encode()).hexdigest()[:10]}"

    with st.container(border=False, key=card_key):
        # --- procent dopasowania: pasek przy krawędzi + kafel po prawej ---
        if match:
            pct = max(0, min(100, int(match.match_percentage)))
            color, tint, edge = score_style(pct)
            rail_html = (f'<div class="jc-rail">'
                         f'<div class="jc-rail-fill" style="height:{pct}%;background:{color}">'
                         f'</div></div>')
            score_html = (f'<div class="jc-score" '
                          f'style="background:{tint};border-color:{edge}">'
                          f'<div class="jc-score-val" style="color:{color}">{pct}'
                          f'<span class="jc-score-pct">%</span></div></div>')
        else:
            rail_html = '<div class="jc-rail is-empty"></div>'
            score_html = ''

        # --- znacznik firmy w roli logotypu ---
        initials, hue = company_mark(job.company)
        avatar_html = (
            f'<div class="jc-avatar" style="'
            f'background:hsl({hue} 32% 15%);'
            f'border-color:hsl({hue} 30% 30%);'
            f'color:hsl({hue} 45% 66%)">{_esc(initials)}</div>'
        )

        fresh = freshness_label(getattr(job, "scraped_at", None))
        new_html = f'<span class="jc-new">{fresh}</span>' if fresh else ''

        # Sygnał martwego ogłoszenia. Świeżość powyżej mówi, kiedy MY ją
        # znaleźliśmy; to mówi, jak długo wisi u pracodawcy - a to dwie różne
        # rzeczy. Gdy nie ma z czego liczyć, nie pokazujemy nic.
        stale = ghost_signals(job)
        stale_text = ghost_label(job)
        if stale_text:
            new_html += (
                f'<span class="jc-stale{" is-ghost" if stale["level"] == "ghost" else ""}" '
                f'title="{_esc("; ".join(stale["reasons"]))}">'
                f'{_esc(stale_text)}</span>'
            )

        # --- meta: firma, miejsce, tryb, portal ---
        work_mode = detect_work_mode(job.location or "", job.description or "", job.title or "")
        meta_bits = [f'<span class="jc-org">{_esc(job.company)}</span>']
        for value in (job.location or "Warszawa", work_mode["label"]):
            if value:
                meta_bits.append(f'<span>{_esc(value)}</span>')
        if job.source:
            meta_bits.append(f'<span class="jc-src">{src_dot(job.source)}</span>')
        # Ta sama oferta bywa na kilku portalach - deduplikacja zostawia jeden
        # rekord, ale zapisuje pozostałe linki w `also_on`. Pokazujemy je, bo
        # bywa, że drugi portal ma pełniejszy opis albo działający formularz.
        alt_links = []
        for entry in (getattr(job, "also_on", None) or [])[:3]:
            if not isinstance(entry, dict) or not entry.get("link"):
                continue
            label = re.sub(r"\s*\(.*?\)", "", str(entry.get("source") or "portal")).strip()
            alt_links.append(
                f'<a class="jc-alt" href="{_esc(str(entry["link"]))}" '
                f'target="_blank" rel="noopener">{_esc(label or "portal")}</a>'
            )
        if alt_links:
            meta_bits.append(
                f'<span class="jc-alt-wrap">także na: {", ".join(alt_links)}</span>'
            )

        meta_html = '<span class="jc-sep">·</span>'.join(meta_bits)

        if status in DECISION_STYLE:
            dot_color, state_label = DECISION_STYLE[status]
            if status == 'rated' and rating:
                state_label = f"ocena {rating}/10"
            meta_html += (f'<span class="jc-sep">·</span>'
                          f'<span class="jc-state" style="color:{dot_color}">'
                          f'<span class="jc-dot" style="background:{dot_color}"></span>'
                          f'{_esc(state_label)}</span>')

            decision = st.session_state.user_decisions.get(job.link)
            applied_at = decision.get('applied_at') if isinstance(decision, dict) else None
            if applied_at and status == 'apply':
                meta_html += (f'<span class="jc-sep">·</span>'
                              f'<span>{_esc(str(applied_at)[:10])}</span>')

        # --- chipy: branża, poziom, konkretne braki ---
        # missing_skills leżało wcześniej odłogiem jako sam licznik ("2 braki"),
        # a to najbardziej decyzyjna informacja na całej karcie.
        chips = []
        if match:
            if getattr(match, "industry", None):
                chips.append(f'<span class="jc-chip">{_esc(match.industry)}</span>')
            if match.is_entry_level:
                chips.append('<span class="jc-chip is-good">junior / staż</span>')
            if match.learnable_in_month:
                chips.append('<span class="jc-chip is-good">nauka ≤ 1 mc</span>')
            for skill in (getattr(match, "missing_skills", None) or [])[:3]:
                chips.append(f'<span class="jc-chip is-gap">brak: {_esc(skill)}</span>')
        chips_html = f'<div class="jc-chips">{"".join(chips)}</div>' if chips else ''

        reason_html = ''
        if match and getattr(match, "reason", None):
            reason_html = f'<div class="jc-reason">{_esc(match.reason)}</div>'

        paragraphs = format_description(job.description, drop_prefix=job.title)
        snippet_html = ''
        if paragraphs:
            snippet_html = f'<div class="jc-desc">{_esc(" ".join(paragraphs)[:400])}</div>'

        # Cała głowa karty jednym blokiem - każde osobne st.markdown dokłada
        # własny wrapper z marginesem i rozjeżdża odstępy.
        st.markdown(
            f'{rail_html}'
            f'<div class="jc-top">'
            f'{avatar_html}'
            f'<div class="jc-id">'
            f'<div class="jc-titleline"><span class="jc-title">{_esc(job.title)}</span>'
            f'{new_html}</div>'
            f'<div class="jc-meta">{meta_html}</div>'
            f'</div>'
            f'{score_html}'
            f'</div>'
            f'{chips_html}{reason_html}{snippet_html}',
            unsafe_allow_html=True
        )

        if paragraphs:
            body = "".join(f'<p class="jc-full-p">{_esc(p)}</p>' for p in paragraphs)
            with st.expander("Cały opis", type="compact"):
                st.markdown(f'<div class="jc-full">{body}</div>', unsafe_allow_html=True)

        # --- akcje ---
        # Decyzje bez podpisów: kształt i barwa ikony niosą znaczenie, a opis
        # zostaje w dymku. Cztery ikony obok siebie czyta się jednym rzutem
        # oka, czterech etykiet - nie.
        with st.container(key=f"ja_{card_key}"):
            # Ocena po lewej, wszystkie działania zbite w jedną grupę po
            # prawej - tak jak na portalach, gdzie ikony akcji trzymają się
            # jednej krawędzi i zawsze wiadomo, gdzie ich szukać.
            # Ocena zajmuje tyle, ile potrzebuje suwak; reszta idzie na
            # przyciski, żeby przy węższym oknie rząd nie łamał się na dwie
            # linie i "Usuń" nie lądowało samo pod spodem.
            col_rate, col_act = st.columns([3.4, 8.6], vertical_alignment="center")

            with col_rate:
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.markdown('<div class="jc-rate-lbl">Twoja ocena</div>',
                                unsafe_allow_html=True)
                    rating_val = st.slider(
                        "Ocena", 1, 10, rating if rating else 5,
                        key=get_key(f"slider_{key_prefix}", job.link),
                        label_visibility="collapsed"
                    )
                    if st.button("", icon=":material/check:",
                                 key=get_key(f"conf_{key_prefix}", job.link),
                                 help="Zapisz samą ocenę - oferta zostaje na liście"):
                        update_decision(job.link, "rated", rating_val)
                        st.rerun(scope="app")

            with col_act:
                with st.container(horizontal=True, vertical_alignment="center",
                                  horizontal_alignment="right"):
                    if st.button("Zapisz", icon=":material/bookmark:",
                                 key=get_key(f"save_{key_prefix}", job.link),
                                 help="Trafi do „Zapisane i wysłane”"):
                        update_decision(job.link, "save", rating_val)
                        st.rerun(scope="app")

                    if st.button("Wysłane", icon=":material/send:",
                                 key=get_key(f"app_{key_prefix}", job.link),
                                 help="Aplikacja poszła - zapisuje dzisiejszą datę"):
                        update_decision(job.link, "apply", rating_val)
                        st.rerun(scope="app")

                    if st.button("Aspiruję", icon=":material/trending_up:",
                                 key=get_key(f"asp_{key_prefix}", job.link),
                                 help="Za wysoko na teraz, ale w tę stronę celujesz"):
                        update_decision(job.link, "aspirational", rating_val)
                        st.rerun(scope="app")

                    if st.button("Odrzuć", icon=":material/close:",
                                 key=get_key(f"rej_{key_prefix}", job.link),
                                 help="Do kosza - profil uczy się, czego nie chcesz"):
                        update_decision(job.link, "reject", min(rating_val, 3))
                        st.rerun(scope="app")

                    st.markdown('<div class="jc-actions-sep"></div>',
                                unsafe_allow_html=True)

                    st.link_button("Otwórz", job.link, icon=":material/open_in_new:",
                                   help="Otwórz ofertę na portalu")

                    if show_restore:
                        if st.button("Przywróć", icon=":material/undo:",
                                     key=get_key("rest", job.link),
                                     help="Wraca do bazy jako nieoceniona"):
                            del st.session_state.user_decisions[job.link]
                            save_user_decisions(st.session_state.user_decisions)
                            st.rerun(scope="app")

                    # Kasowanie stoi w rzędzie jak reszta, ale jest jedynym
                    # nieodwracalnym działaniem na karcie - dlatego wymaga
                    # potwierdzenia zamiast chowania się w menu.
                    confirm_key = f"confirm_del_{card_key}"
                    if st.session_state.get(confirm_key):
                        if st.button("Na pewno?", icon=":material/delete_forever:",
                                     key=get_key(f"delyes_{key_prefix}", job.link),
                                     help="Kliknij, żeby usunąć bezpowrotnie"):
                            st.session_state.pop(confirm_key, None)
                            _delete_job_permanent(job.link)
                            st.toast("Oferta usunięta z bazy.")
                            time.sleep(0.4)
                            st.rerun(scope="app")
                    else:
                        if st.button("Usuń", icon=":material/delete:",
                                     key=get_key(f"del_{key_prefix}", job.link),
                                     help="Usuwa z bazy, z wyników AI i z Twoich decyzji"):
                            st.session_state[confirm_key] = True
                            st.rerun()


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

    if job_link in st.session_state.job_lookup:
        del st.session_state.job_lookup[job_link]

    if job_link in st.session_state.user_decisions:
        del st.session_state.user_decisions[job_link]
        save_user_decisions(st.session_state.user_decisions)


def render_paginated_list(items, page_key_prefix, render_item_fn, header_text):
    """
    Wspólne stronicowanie dla wszystkich widoków.
    """
    if not items:
        return
    
    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    
    top_key = f"{page_key_prefix}_page_top"
    btm_key = f"{page_key_prefix}_page_btm"
    if st.session_state.get(top_key, 1) > total_pages: st.session_state[top_key] = total_pages
    if st.session_state.get(btm_key, 1) > total_pages: st.session_state[btm_key] = total_pages
    
    pc1, pc2 = st.columns([4, 1], vertical_alignment="center")
    with pc1:
        st.markdown(f'<div class="pg-count">{html.escape(header_text)}</div>',
                    unsafe_allow_html=True)
    with pc2:
        page_num = st.number_input("Strona", min_value=1, max_value=total_pages, value=1, key=top_key, on_change=sync_pagination, args=(top_key, btm_key), label_visibility="collapsed")
    
    start = (page_num - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    
    for item in items[start:end]:
        render_item_fn(item)
    
    if total_pages > 1:
        st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)
        bc1, bc2 = st.columns([5, 1])
        with bc2:
            st.number_input("Strona", min_value=1, max_value=total_pages, value=page_num, key=btm_key, on_change=sync_pagination, args=(btm_key, top_key), label_visibility="collapsed")


# =============================================================================
# TABLICA KANBAN - ŚLEDZENIE APLIKACJI
# =============================================================================

def render_kanban_view():
    render_view_header(
        "proces", "Tablica rekrutacyjna",
        "Oferty, w których coś się dzieje. Przesuń ofertę do kolejnego etapu, "
        "gdy dostaniesz odpowiedź."
    )

    decisions = st.session_state.user_decisions

    # Klucz etapu MUSI być tym samym słowem, które zapisuje karta oferty
    # (update_decision(..., "save")). Wcześniej tablica szukała "saved", więc
    # kolumna "Zapisane" była zawsze pusta, choć zapisane oferty istniały.
    stage_names = {
        "save":      "Zapisane",
        "apply":     "Wysłane",
        "interview": "Rozmowa",
        "offer":     "Oferta",
        "archive":   "Archiwum",
    }
    # Stare zapisy używały innych nazw etapu
    LEGACY = {"saved": "save", "reject": "archive", "rejected": "archive"}

    stages = {key: {"label": label, "items": []} for key, label in stage_names.items()}

    def board_stage(status, stage):
        """
        Na tablicy lądują tylko oferty, w których faktycznie coś się dzieje.

        Odrzucenie przy przeglądaniu listy NIE jest etapem rekrutacji - takich
        ofert były setki i zalewały kolumnę "Archiwum" (120 pozycji przy czterech
        pozostałych kolumnach pustych), przez co tablica nie pokazywała niczego
        użytecznego. Odrzucone mają własny widok. Do Archiwum trafia wyłącznie
        to, co sam tam przesuniesz - czyli sprawy zakończone PO aplikowaniu.
        """
        stage = LEGACY.get(stage, stage)
        if stage in stages:
            return stage
        return status if status in ("save", "apply") else None

    skipped_missing = 0
    for link, ddata in decisions.items():
        if isinstance(ddata, dict):
            status, stage = ddata.get("status"), ddata.get("stage")
        else:
            status, stage = ddata, None

        target = board_stage(status, stage)
        if target is None:
            continue

        job = find_job_obj(link)
        if not job:
            skipped_missing += 1
            continue
        stages[target]["items"].append((link, job, ddata))

    total_on_board = sum(len(s["items"]) for s in stages.values())
    if total_on_board == 0:
        st.markdown(
            '<div class="panel-note">Tablica jest pusta. Trafiają tu oferty, '
            'które oznaczysz na liście jako <strong>Zapisz</strong> albo '
            '<strong>Wysłane</strong> — a potem przesuwasz je między etapami, '
            'gdy dostaniesz odpowiedź. Oferty odrzucone przy przeglądaniu tu nie '
            'wchodzą, są w widoku „Odrzucone”.</div>',
            unsafe_allow_html=True
        )
    if skipped_missing:
        st.caption(f"{skipped_missing} decyzji dotyczy ofert, których nie ma już "
                   f"w bazie (zostały usunięte jako przeterminowane).")

    columns_map = list(zip(stage_names.keys(), st.columns(len(stage_names))))

    for stage_key, col in columns_map:
        info = stages[stage_key]
        with col:
            st.markdown(
                f'<div class="kb-head"><span class="kb-name">{html.escape(info["label"])}</span>'
                f'<span class="kb-count">{len(info["items"])}</span></div>',
                unsafe_allow_html=True
            )
            if not info['items']:
                st.markdown(
                    '<div style="font-size:0.78rem;color:var(--faint);padding:0.2rem 0 0.8rem 0;">'
                    'pusto</div>', unsafe_allow_html=True
                )
            for link, job, ddata in info['items']:
                with st.container(border=False, key=f"kb_{stage_key}_{get_key('c', link)}"):
                    st.markdown(
                        f'<div class="kb-title">{html.escape(str(job.title))}</div>'
                        f'<div class="kb-org">{html.escape(str(job.company))}'
                        f' · {html.escape(str(job.location or ""))}</div>',
                        unsafe_allow_html=True
                    )
                    
                    current_stage = stage_key
                    new_stage = st.selectbox(
                        "Etap", options=list(stage_names.keys()),
                        index=list(stage_names.keys()).index(current_stage),
                        format_func=lambda x: stage_names[x],
                        key=get_key(f"kanban_sel_{stage_key}", link),
                        label_visibility="collapsed",
                    )

                    if new_stage != current_stage:
                        rating = ddata.get("rating") if isinstance(ddata, dict) else 5
                        status = ddata.get("status") if isinstance(ddata, dict) else ddata
                        update_decision(link, status or "save", rating, stage=new_stage)
                        st.rerun()
                    
                    st.link_button("Otwórz", job.link, icon=":material/open_in_new:",
                                   type="tertiary")


# =============================================================================
# WIDOKI
# =============================================================================

def render_analyzed_view():
    render_view_header(
        "oferty", "Dopasowane przez AI",
        "Ranking według dopasowania do Twojego CV i profilu preferencji. "
        "Każda ocena, którą tu wystawisz, wraca do profilu i poprawia kolejne."
    )
    matches = st.session_state.analyzed_matches
    
    if not matches:
        st.info("Brak przeanalizowanych ofert. Przejdź do 'Panel Sterowania' i uruchom analizę AI.")
        return

    with st.container(key="toolbar_an"):
        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
        with col1: search_query = st.text_input("Stanowisko lub firma", "", key="search_an",
                                                placeholder="Szukaj…").lower()
        with col2: min_match = st.slider("Minimalne dopasowanie", 0, 100, 0, format="%d%%")
        with col3: sort_order = st.selectbox("Sortowanie", ["Najwyższe dopasowanie", "Najniższe dopasowanie", "Najnowsze"], key="sort_an")
        with col4: mode_filter = st.selectbox("Tryb pracy", WORK_MODE_FILTER, key="mode_an")
        with st.container(horizontal=True):
            entry_only = st.checkbox("Tylko junior i staż", value=False)
            # Wykrywanie martwych ofert bez możliwości ich ukrycia było samą
            # informacją; ten filtr dopiero czyni z niego narzędzie.
            hide_stale = st.checkbox(
                "Ukryj prawdopodobnie nieaktualne", value=False, key="hide_stale_an",
                help="Chowa oferty z sygnałem, że ogłoszenie jest martwe: minął "
                     "termin, ogłoszenie ma ponad 75 dni albo wisi od tygodni."
            )
    
    sort_mapping = {
        "Najwyższe dopasowanie": "Highest Match",
        "Najniższe dopasowanie": "Lowest Match",
        "Najnowsze": "Newest"
    }
    mapped_sort = sort_mapping.get(sort_order, "Highest Match")
    
    filtered = []
    for m in matches:
        if search_query and search_query not in m.job.title.lower() and search_query not in m.job.company.lower(): continue
        status, _ = get_decision(m.job.link)
        if status not in ['reject', 'save', 'apply', 'rated', 'aspirational']:
            filtered.append(m)
    
    if min_match > 0:
        filtered = [m for m in filtered if m.match_percentage >= min_match]
    if entry_only:
        filtered = [m for m in filtered if m.is_entry_level]

    if mode_filter != "Wszystkie":
        filtered = [m for m in filtered
                    if detect_work_mode(m.job.location or "", m.job.description or "",
                                        m.job.title or "")["label"] == mode_filter]
    if hide_stale:
        filtered = [m for m in filtered if ghost_signals(m.job)["level"] != "ghost"]

    if mapped_sort == "Highest Match":
        filtered.sort(key=lambda x: x.match_percentage, reverse=True)
    elif mapped_sort == "Lowest Match":
        filtered.sort(key=lambda x: x.match_percentage)
    elif mapped_sort == "Newest":
        filtered.sort(key=lambda x: x.job.scraped_at if getattr(x.job, 'scraped_at', None) else '', reverse=True)

    def _render_analyzed_item(m):
        status, rating = get_decision(m.job.link)
        render_job_card(m.job, "an", status=status, rating=rating, match=m)
    
    render_paginated_list(
        filtered, "anal",
        _render_analyzed_item,
        f"{fmt_n(len(filtered))} z {fmt_n(len(matches))} ofert"
    )


def render_raw_view():
    render_view_header(
        "oferty", "Cała baza",
        "Wszystko, co pobrały skrapery i czego jeszcze nie tknąłeś ani Ty, ani AI."
    )
    jobs = st.session_state.raw_jobs
    
    if not jobs:
        st.info("Brak nieocenionych ofert w bazie danych.")
        return

    with st.container(key="toolbar_raw"):
        sc1, sc2, sc3 = st.columns([2, 1, 1])
        with sc1: search_query = st.text_input("Stanowisko lub firma", "", key="search_raw",
                                               placeholder="Szukaj…").lower()
        with sc2: sort_order = st.selectbox("Sortowanie", ["Najnowsze", "Najstarsze"], key="sort_raw")
        with sc3: mode_filter = st.selectbox("Tryb pracy", WORK_MODE_FILTER, key="mode_raw")
    
    filtered = []
    for j in jobs:
        if search_query and search_query not in j.title.lower() and search_query not in j.company.lower(): continue
        status, rating = get_decision(j.link)
        if not status and not rating:
            filtered.append(j)

    if mode_filter != "Wszystkie":
        filtered = [j for j in filtered
                    if detect_work_mode(j.location or "", j.description or "",
                                        j.title or "")["label"] == mode_filter]

    mapped_sort = "Newest" if sort_order == "Najnowsze" else "Oldest"
    if mapped_sort == "Newest":
        filtered.sort(key=lambda x: x.scraped_at if getattr(x, 'scraped_at', None) else '', reverse=True)
    elif mapped_sort == "Oldest":
        filtered.sort(key=lambda x: x.scraped_at if getattr(x, 'scraped_at', None) else '')
    
    def _render_raw_item(j):
        status, rating = get_decision(j.link)
        render_job_card(j, "raw", status=status, rating=rating)
    
    render_paginated_list(filtered, "raw", _render_raw_item, f"{fmt_n(len(filtered))} nietkniętych ofert")


def _collect_decision_jobs(status_filter, search_query=""):
    """Helper to collect jobs by decision status."""
    decisions = st.session_state.user_decisions
    results = []
    
    for link in decisions.keys():
        job = find_job_obj(link)
        if not job:
            continue
        if search_query and search_query not in job.title.lower() and search_query not in job.company.lower():
            continue
        s, r = get_decision(link)
        if status_filter(s, r):
            results.append((link, job, r, s))
    
    return results


def _sort_decision_jobs(items, sort_order):
    """Helper to sort decision-based job lists."""
    if sort_order in ("Recently Added", "Ostatnio dodane"):
        items.reverse()
    elif sort_order in ("Highest Rated", "Najwyżej ocenione"):
        items.sort(key=lambda x: x[2] if x[2] else 0, reverse=True)
    elif sort_order in ("Lowest Rated", "Najniżej ocenione"):
        items.sort(key=lambda x: x[2] if x[2] else 0)
    elif sort_order in ("Newest (Scraped)", "Najnowsze (Skanowanie)"):
        items.sort(key=lambda x: x[1].scraped_at if x[1] and getattr(x[1], 'scraped_at', None) else '', reverse=True)
    return items


def render_rated_view():
    render_view_header("decyzje", "Ocenione",
                       "Oferty, którym wystawiłeś ocenę. To z nich powstaje profil preferencji.")
    
    with st.container(key="toolbar_rt"):
        sc1, sc2 = st.columns([2, 1])
        with sc1: search_query = st.text_input("Stanowisko lub firma", "", key="search_rt",
                                               placeholder="Szukaj…").lower()
        with sc2: sort_order = st.selectbox("Sortowanie", ["Ostatnio dodane", "Najwyżej ocenione", "Najniżej ocenione", "Najnowsze (Skanowanie)"], key="sort_rt")
    
    items = _collect_decision_jobs(
        lambda s, r: s in ['rated', 'save', 'reject', 'aspirational'] and r is not None,
        search_query
    )
    items = _sort_decision_jobs(items, sort_order)
    
    if not items:
        st.info("Brak ręcznie ocenionych ofert.")
        return
    
    def _render_rated_item(item):
        link, job, rating, status = item
        render_job_card(job, "rt", status=status, rating=rating)
    
    render_paginated_list(items, "rated", _render_rated_item, f"{fmt_n(len(items))} ocenionych ofert")


def render_saved_view():
    render_view_header("decyzje", "Zapisane i wysłane",
                       "Krótka lista: to, do czego wracasz, i to, gdzie już poszła aplikacja.")
    
    with st.container(key="toolbar_sv"):
        sc1, sc2 = st.columns([2, 1])
        with sc1: search_query = st.text_input("Stanowisko lub firma", "", key="search_sv",
                                               placeholder="Szukaj…").lower()
        with sc2: sort_order = st.selectbox("Sortowanie", ["Ostatnio dodane", "Najwyżej ocenione", "Najniżej ocenione", "Najnowsze (Skanowanie)"], key="sort_sv")
    
    items = _collect_decision_jobs(lambda s, r: s in ['save', 'apply'], search_query)
    items = _sort_decision_jobs(items, sort_order)
    
    if not items:
        st.info("Brak zapisanych lub aplikowanych ofert.")
        return
        
    try:
        import pandas as pd
        import io
        
        export_data = []
        for link, job, rating, status in items:
            applied_at = st.session_state.user_decisions.get(link, {}).get('applied_at', '')
            export_data.append({
                "Stanowisko": job.title,
                "Firma": job.company,
                "Lokalizacja": job.location,
                "Portal": job.source,
                "Link": job.link,
                "Status": "Aplikowano" if status == "apply" else "Zapisano",
                "Data Aplikacji": applied_at,
                "Ocena Użytkownika": rating if rating else ""
            })
        df = pd.DataFrame(export_data)
        csv_bytes = df.to_csv(index=False).encode('utf-8')
        
        excel_bytes = None
        try:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Oferty')
            excel_bytes = buffer.getvalue()
        except Exception as e:
            logger.warning(f"Excel error: {e}")
            
        ec1, ec2 = st.columns(2)
        with ec1:
            st.download_button("Pobierz CSV", data=csv_bytes, icon=":material/download:",
                               file_name="zapisane_oferty.csv", mime="text/csv",
                               width="stretch")
        with ec2:
            if excel_bytes:
                st.download_button("Pobierz Excel", data=excel_bytes, icon=":material/download:",
                                   file_name="zapisane_oferty.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   width="stretch")
    except Exception as e:
        st.error(f"Błąd generowania eksportu: {e}")
    
    def _render_saved_item(item):
        link, job, rating, status = item
        render_job_card(job, "sv", status=status, rating=rating)
    
    render_paginated_list(items, "saved", _render_saved_item, f"{len(items)} zapisanych i wysłanych")


def render_aspirational_view():
    render_view_header("decyzje", "Aspiracyjne",
                       "Za wysoko na teraz. Trzymasz je, żeby wiedzieć, czego się douczyć.")
    
    with st.container(key="toolbar_as"):
        sc1, sc2 = st.columns([2, 1])
        with sc1: search_query = st.text_input("Stanowisko lub firma", "", key="search_as",
                                               placeholder="Szukaj…").lower()
        with sc2: sort_order = st.selectbox("Sortowanie", ["Ostatnio dodane", "Najwyżej ocenione", "Najniżej ocenione", "Najnowsze (Skanowanie)"], key="sort_as")
    
    items = _collect_decision_jobs(lambda s, r: s == 'aspirational', search_query)
    items = _sort_decision_jobs(items, sort_order)
    
    if not items:
        st.info("Brak aspirujących ofert w Twojej bazie.")
        return
    
    def _render_asp_item(item):
        link, job, rating, status = item
        render_job_card(job, "as", status=status, rating=rating)
    
    render_paginated_list(items, "asp", _render_asp_item, f"{fmt_n(len(items))} aspiracyjnych ofert")


def render_rejected_view():
    render_view_header("decyzje", "Odrzucone",
                       "Kosz. Oferty stąd nadal uczą profil tego, czego nie chcesz.")
    
    with st.container(key="toolbar_rj"):
        sc1, sc2 = st.columns([2, 1])
        with sc1: search_query = st.text_input("Stanowisko lub firma", "", key="search_rj",
                                               placeholder="Szukaj…").lower()
        with sc2: sort_order = st.selectbox("Sortowanie", ["Ostatnio dodane", "Najniżej ocenione", "Najwyżej ocenione", "Najnowsze (Skanowanie)"], key="sort_rj")
    
    items = _collect_decision_jobs(lambda s, r: s == 'reject', search_query)
    items = _sort_decision_jobs(items, sort_order)
    
    if not items:
        st.info("Kosz jest pusty.")
        return
    
    # Najbardziej niszcząca akcja w aplikacji: kasuje oferty z bazy, z wyników AI
    # ORAZ Twoje decyzje o odrzuceniu - a te są materiałem, z którego budowany
    # jest profil preferencji. Wcześniej stała tu jako goły przycisk, więc jedno
    # przypadkowe kliknięcie kasowało wszystko bez pytania.
    with st.expander(f"Usuń trwale wszystkie odrzucone ({len(items)})",
                     expanded=False, type="compact"):
        st.markdown(
            f'<div class="panel-note is-stale">Usunie <strong>{len(items)}</strong> ofert '
            f'z bazy, z wyników AI i z Twoich decyzji — bezpowrotnie. '
            f'<em>Razem z nimi znika informacja, że je odrzuciłeś, a to właśnie ona '
            f'uczy profil preferencji, czego nie chcesz. Jeśli chodzi Ci tylko '
            f'o czystszą listę, nie musisz nic kasować — odrzucone i tak nie '
            f'pokazują się w „Dopasowane przez AI”.</em></div>',
            unsafe_allow_html=True
        )
        confirm_purge = st.text_input(
            "Wpisz USUŃ, żeby odblokować", key="confirm_purge_rejected",
            placeholder="USUŃ"
        )
        if st.button("Usuń trwale", icon=":material/delete_forever:",
                     key="purge_rejected_btn",
                     disabled=confirm_purge.strip().upper() not in ("USUŃ", "USUN")):
            deleted = 0
            for link, job, rating, status in items:
                _delete_job_permanent(link)
                deleted += 1
            if deleted > 0:
                st.toast(f"Usunięto {deleted} odrzuconych ofert z bazy.")
                time.sleep(0.5)
                st.session_state.data_loaded = False
                st.rerun()
    
    def _render_rejected_item(item):
        link, job, rating, status = item
        render_job_card(job, "rj", status=status, rating=rating, show_restore=True)
    
    render_paginated_list(items, "rej", _render_rejected_item, f"{fmt_n(len(items))} odrzuconych ofert")


# =============================================================================
# STEROWANIE PIPELINE'EM
# =============================================================================

def _read_links(path: Path, nested: bool = False) -> set:
    """Zbiór linków z pliku danych. Błąd odczytu = pusty zbiór, nie wyjątek w UI."""
    if not path.exists():
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Nie udało się odczytać {path.name}: {e}")
        return set()

    links = set()
    for item in data:
        job = item.get("job", {}) if nested else item
        link = job.get("link") if isinstance(job, dict) else None
        if link:
            links.add(link)
    return links


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


def render_pipeline_control():
    """Panel sterowania: stan danych, co zrobić dalej, trzy działania."""
    render_view_header(
        "pipeline", "Panel sterowania",
        "Tu uruchamiasz to, co napełnia aplikację danymi. "
        "Każde działanie mówi, co robi i ile trwa."
    )

    base = Path(__file__).parent
    db_path = base / "jobs_database.json"
    analyzed_path = base / "analyzed_jobs_waterfall.json"
    profile_path = base / "preference_profile.json"
    decisions_path = base / "user_decisions.json"

    # Liczymy na ZBIORACH linków, nie na długościach list. Poprzednia wersja
    # robiła `baza - oceny_AI - decyzje`, a decyzje dotyczą ofert, które już mają
    # ocenę AI - ta sama oferta była odejmowana dwa razy i "czeka na ocenę"
    # potrafiło pokazać 0 przy tysiącach nieprzeanalizowanych ofert.
    db_links = _read_links(db_path)
    analyzed_links = _read_links(analyzed_path, nested=True)
    decided_links = set()
    if decisions_path.exists():
        try:
            with open(decisions_path, "r", encoding="utf-8") as f:
                decided_links = set(json.load(f).keys())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Nie udało się odczytać decyzji: {e}")

    db_count = len(db_links)
    analyzed_count = len(analyzed_links)
    decisions_count = len(decided_links)
    pending = len(db_links - analyzed_links)

    profile = {}
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Nie udało się odczytać profilu: {e}")
    profile_exists = bool(profile)
    profile_built_from = profile.get("_metadata", {}).get("total_decisions_analyzed", 0) or 0

    def _n(v):
        return f"{v:,}".replace(",", " ")

    st.markdown('<div class="sec-label">Stan danych</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="rail" style="margin-bottom:1.2rem;">'
        f'<div class="rail-item"><span class="rail-val">{_n(db_count)}</span>'
        f'<span class="rail-lbl">ofert w bazie</span></div>'
        f'<div class="rail-item"><span class="rail-val">{_n(analyzed_count)}</span>'
        f'<span class="rail-lbl">z oceną AI</span></div>'
        f'<div class="rail-item"><span class="rail-val">{_n(pending)}</span>'
        f'<span class="rail-lbl">czeka na ocenę</span></div>'
        f'<div class="rail-item"><span class="rail-val">{_n(decisions_count)}</span>'
        f'<span class="rail-lbl">Twoich decyzji</span></div>'
        f'<div class="rail-item"><span class="rail-val '
        f'{"is-on" if profile_exists else "is-off"}">'
        f'{"gotowy" if profile_exists else "brak"}</span>'
        f'<span class="rail-lbl">profil preferencji</span></div>'
        f'</div>',
        unsafe_allow_html=True
    )

    # --- Co teraz? Jedno zdanie dla kogoś, kto wchodzi tu pierwszy raz. ---
    new_decisions = max(0, decisions_count - profile_built_from)
    if db_count == 0:
        next_up = ("Zacznij od kroku 01 — baza jest pusta, więc nie ma czego oceniać. "
                   "Pobranie ofert niczego nie kasuje.")
    elif pending > 0:
        next_up = (f"Przejdź do kroku 03 — {_n(pending)} ofert czeka na ocenę AI. "
                   f"Bez tego nie pojawią się w „Dopasowane przez AI”.")
    elif not profile_exists and decisions_count >= 10:
        next_up = (f"Przejdź do kroku 02 — masz {decisions_count} ocen, "
                   f"z których da się zbudować profil i trafniej oceniać kolejne oferty.")
    elif new_decisions >= 25:
        next_up = (f"Warto odświeżyć krok 02 — od zbudowania profilu doszło "
                   f"{new_decisions} nowych decyzji.")
    else:
        next_up = ("Wszystko policzone. Wróć do „Dopasowane przez AI” i oceniaj oferty — "
                   "każda Twoja ocena poprawia kolejne.")

    st.markdown(
        f'<div class="next-up"><span class="next-up-lbl">co teraz</span>'
        f'<span class="next-up-txt">{html.escape(next_up)}</span></div>',
        unsafe_allow_html=True
    )

    # --- KROK 01 ---
    st.markdown(
        '<div class="step">'
        '<span class="step-num">01</span>'
        '<span class="step-title">Pobierz oferty z portali</span>'
        '<div class="step-desc">Odwiedza wszystkie portale i dopisuje do bazy oferty, '
        'których jeszcze nie masz. Istniejących ofert ani Twoich ocen nie rusza.</div>'
        '<div class="step-meta">Pracuj.pl · OLX · aplikuj.pl · GoWork.pl · praca.pl · '
        'NoFluffJobs · JustJoin.it · RocketJobs · SOLID.Jobs · LinkedIn · Indeed '
        '&nbsp;·&nbsp; trwa od kilku minut do ok. 45 min, zależnie od liczby nowych ofert</div>'
        '</div>',
        unsafe_allow_html=True
    )
    if st.button("Pobierz oferty", icon=":material/download:", width="stretch",
                 key="run_scrapers_btn"):
        if _run_step(["main_scraper.py"], "Pobieram oferty z portali…",
                     "Pobieranie zakończone"):
            _run_step(["deduplicate_db.py"], "Scalam duplikaty…", "Duplikaty scalone", tail=6)
            _run_step(["clean_db.py"], "Skracam opisy…", "Opisy skrócone", tail=4)
            st.session_state.data_loaded = False
            st.rerun()

    st.markdown("---")

    # --- KROK 02 ---
    st.markdown(
        '<div class="step">'
        '<span class="step-num">02</span>'
        '<span class="step-title">Przebuduj profil preferencji</span>'
        '<div class="step-desc">Czyta Twoje oceny i wyciąga z nich wzorzec: jakie role '
        'i branże Ci pasują, a co odrzucasz. Profil trafia do polecenia dla AI, '
        'więc kolejne oferty są oceniane trafniej.</div>'
        '<div class="step-meta">Uruchom po każdej większej porcji ocen '
        '&nbsp;·&nbsp; trwa poniżej minuty</div>'
        '</div>',
        unsafe_allow_html=True
    )

    if profile_exists:
        gen_at = profile.get("_metadata", {}).get("generated_at", "")
        when = gen_at[:16].replace("T", ", ") if gen_at else "nieznana data"
        note = f"Obecny profil: {when}, zbudowany z {profile_built_from} Twoich decyzji."
        if new_decisions >= 25:
            st.markdown(
                f'<div class="panel-note is-stale">{html.escape(note)} '
                f'<em>Od tego czasu doszło {new_decisions} nowych ocen — '
                f'przebudowanie profilu poprawi trafność kolejnych ocen AI.</em></div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div class="panel-note is-ok">{html.escape(note)}</div>',
                unsafe_allow_html=True
            )

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
            f'<div class="panel-note">Masz {decisions_count} ocen. Profil zbuduje się '
            f'sensownie od jakichś dziesięciu — oceniaj dalej oferty na liście.</div>',
            unsafe_allow_html=True
        )

    if st.button("Przebuduj profil", icon=":material/autorenew:", width="stretch",
                 key="gen_profile_btn"):
        if _run_step(["generate_preference_profile.py"], "Buduję profil z Twoich ocen…",
                     "Profil przebudowany", tail=8):
            st.rerun()

    st.markdown("---")

    # --- KROK 03 ---
    st.markdown(
        '<div class="step">'
        '<span class="step-num">03</span>'
        '<span class="step-title">Oceń oferty przez AI</span>'
        '<div class="step-desc">Wysyła do Gemini oferty, które nie mają jeszcze oceny, '
        'i dla każdej liczy procent dopasowania oraz uzasadnienie. Dopiero po tym '
        'kroku oferta pojawia się w „Dopasowane przez AI”.</div>'
        f'<div class="step-meta">Do policzenia teraz: {_n(pending)} ofert '
        f'&nbsp;·&nbsp; około {max(1, round(pending / 180))} min '
        f'&nbsp;·&nbsp; zużywa limit Gemini</div>'
        '</div>',
        unsafe_allow_html=True
    )

    if st.button("Oceń oferty", icon=":material/play_arrow:", width="stretch",
                 key="run_analysis_btn", disabled=pending == 0):
        if _run_step(["waterfall_analysis.py"], "Oceniam oferty przez AI…",
                     "Ocenianie zakończone"):
            st.session_state.data_loaded = False
            st.rerun()
    if pending == 0 and db_count:
        st.caption("Wszystkie oferty w bazie mają już ocenę AI.")

    # Przeliczenie od zera jest nieodwracalne i kosztuje cały limit Gemini,
    # więc siedzi osobno, a nie jako pole wyboru obok zwykłego przycisku,
    # gdzie łatwo je kliknąć przez pomyłkę.
    with st.expander("Policz wszystkie oceny AI od nowa", expanded=False, type="compact"):
        st.markdown(
            f"Kasuje **{_n(analyzed_count)}** dotychczasowych ocen AI i liczy je "
            f"od zera. Twoje własne oceny i decyzje zostają nietknięte, ale "
            f"przeliczenie zużyje limit Gemini na wszystkie oferty w bazie "
            f"(około {max(1, round(db_count / 180))} min). Ma sens po zmianie "
            f"polecenia dla AI albo modelu."
        )
        confirm = st.text_input(
            "Wpisz PRZELICZ, żeby odblokować", key="confirm_recalc",
            placeholder="PRZELICZ"
        )
        if st.button("Skasuj oceny AI i policz od nowa", icon=":material/warning:",
                     key="recalc_all_btn", disabled=confirm.strip().upper() != "PRZELICZ"):
            save_json_atomic(str(analyzed_path), [], backup=True)
            st.toast("Skasowano dotychczasowe oceny AI (kopia w backups/).")
            if _run_step(["waterfall_analysis.py"], "Liczę wszystko od nowa…",
                         "Przeliczone"):
                st.session_state.data_loaded = False
                st.rerun()

    st.markdown("---")

    st.markdown('<div class="sec-label">Klucze API</div>', unsafe_allow_html=True)
    
    def read_current_env_keys():
        keys = {
            "GEMINI_API_KEY_PRIMARY": "", "GEMINI_API_KEY_1": "", "GEMINI_API_KEY_2": "",
            "GEMINI_API_KEY_3": "", "GEMINI_API_KEY_4": "", "ADZUNA_APP_ID": "",
            "ADZUNA_APP_KEY": "", "JOOBLE_API_KEY": "", "CAREERJET_API_KEY": ""
        }
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        parts = line.split("=", 1)
                        k = parts[0].strip()
                        if k in keys: keys[k] = parts[1].strip()
            except OSError as e:
                logger.warning(f"Nie udało się odczytać .env: {e}")
        return keys

    with st.expander("Edytuj klucze", expanded=False, type="compact"):
        env_keys = read_current_env_keys()
        
        st.markdown('<div class="sec-label">Gemini - klucze 1-4 służą do rotacji '
                    'przy limitach</div>', unsafe_allow_html=True)
        new_gemini_primary = st.text_input("Główny Klucz Gemini API", value=env_keys.get("GEMINI_API_KEY_PRIMARY", ""), type="password")
        new_gemini_1 = st.text_input("Gemini API Key 1 (Zapasy)", value=env_keys.get("GEMINI_API_KEY_1", ""), type="password")
        new_gemini_2 = st.text_input("Gemini API Key 2 (Zapasy)", value=env_keys.get("GEMINI_API_KEY_2", ""), type="password")
        new_gemini_3 = st.text_input("Gemini API Key 3 (Zapasy)", value=env_keys.get("GEMINI_API_KEY_3", ""), type="password")
        new_gemini_4 = st.text_input("Gemini API Key 4 (Zapasy)", value=env_keys.get("GEMINI_API_KEY_4", ""), type="password")
        
        st.markdown('<div class="sec-label">Portale pracy - opcjonalne, '
                    'skrapery włączają się same po ustawieniu klucza</div>',
                    unsafe_allow_html=True)
        st.markdown("[🔗 Rejestracja Adzuna API](https://developer.adzuna.com/)")
        c_adz1, c_adz2 = st.columns(2)
        with c_adz1: new_adzuna_id = st.text_input("Adzuna App ID", value=env_keys.get("ADZUNA_APP_ID", ""))
        with c_adz2: new_adzuna_key = st.text_input("Adzuna App Key", value=env_keys.get("ADZUNA_APP_KEY", ""), type="password")
            
        st.markdown("[🔗 Rejestracja Jooble API](https://jooble.org/api/about)")
        new_jooble = st.text_input("Jooble API Key", value=env_keys.get("JOOBLE_API_KEY", ""), type="password")
        
        st.markdown("[🔗 Rejestracja Careerjet API](https://www.careerjet.com/partners/api/)")
        new_careerjet = st.text_input("Careerjet API Key", value=env_keys.get("CAREERJET_API_KEY", ""), type="password")
        
        if st.button("Zapisz klucze", icon=":material/save:", key="save_env_keys_btn"):
            updates = {
                "GEMINI_API_KEY_PRIMARY": new_gemini_primary, "GEMINI_API_KEY_1": new_gemini_1,
                "GEMINI_API_KEY_2": new_gemini_2, "GEMINI_API_KEY_3": new_gemini_3,
                "GEMINI_API_KEY_4": new_gemini_4, "ADZUNA_APP_ID": new_adzuna_id,
                "ADZUNA_APP_KEY": new_adzuna_key, "JOOBLE_API_KEY": new_jooble,
                "CAREERJET_API_KEY": new_careerjet
            }
            env_path = Path(__file__).parent / ".env"
            lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
            updated = set()
            new_lines = []
            for line in lines:
                if "=" in line and not line.strip().startswith("#"):
                    parts = line.split("=", 1)
                    k = parts[0].strip()
                    if k in updates:
                        new_lines.append(f"{k}={updates[k]}")
                        updated.add(k)
                        continue
                new_lines.append(line)
            for k, v in updates.items():
                if k not in updated: new_lines.append(f"{k}={v}")
            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            st.success("Zapisano klucze w pliku `.env`!")
            st.toast("Zapisano konfigurację!")
            time.sleep(0.8)
            st.rerun()


# =============================================================================
# RĘCZNE DODANIE OFERTY
# =============================================================================

def render_add_manual_view():
    render_view_header(
        "oferty", "Dodaj z linku",
        "Wklej adres oferty z dowolnego portalu. Dane pobiorą się same, "
        "a Ty je poprawisz przed zapisaniem."
    )

    if 'manual_job_data' not in st.session_state: st.session_state.manual_job_data = None
    if 'manual_job_url' not in st.session_state: st.session_state.manual_job_url = ""

    url_input = st.text_input("URL oferty pracy:", value=st.session_state.manual_job_url, placeholder="https://...")
    
    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1: fetch_clicked = st.button("Pobierz dane", icon=":material/download:",
                                             type="primary", width="stretch")
    with col_btn2:
        if st.session_state.manual_job_data:
            if st.button("Wyczyść formularz"):
                st.session_state.manual_job_data = None
                st.session_state.manual_job_url = ""
                st.rerun()

    if fetch_clicked and url_input:
        with st.spinner("Pobieranie metadanych strony..."):
            from utils.link_fetcher import extract_job_info_from_url
            data = extract_job_info_from_url(url_input)
            data['link'] = url_input
            st.session_state.manual_job_data = data
            st.session_state.manual_job_url = url_input
            st.rerun()
             
    if st.session_state.manual_job_data:
        st.divider()
        st.markdown('<div class="sec-label">Sprawdź dane przed zapisem</div>',
                    unsafe_allow_html=True)
        
        with st.form("manual_job_form", border=True):
            data = st.session_state.manual_job_data
            
            c1, c2 = st.columns(2)
            with c1: title = st.text_input("Tytuł stanowiska *", value=data.get('title', ''))
            with c2: company = st.text_input("Firma *", value=data.get('company', ''))
                
            c3, c4 = st.columns(2)
            with c3: location = st.text_input("Lokalizacja", value=data.get('location', ''))
            with c4: source = st.text_input("Źródło", value=data.get('source', ''))
                 
            description = st.text_area("Opis (lub tagi/podsumowanie)", value=data.get('description', ''), height=100)
            
            submit = st.form_submit_button("Podgląd Karty Oferty", type="primary")
            
            if submit:
                 if not title or not company:
                     st.error("Tytuł i Firma są wymagane!")
                 else:
                     st.session_state.manual_job_data.update({
                         'title': title, 'company': company,
                         'location': location, 'source': source,
                         'description': description
                     })
                     st.rerun()

        if st.session_state.manual_job_data.get('title'):
             st.markdown('<div class="sec-label">Podgląd karty</div>',
                         unsafe_allow_html=True)
             data = st.session_state.manual_job_data
             
             temp_job = Job(
                 title=data['title'], company=data['company'],
                 link=data['link'], description=data['description'],
                 source=data['source'], location=data['location']
             )
             
             is_new = temp_job.link not in st.session_state.job_lookup
             if not is_new:
                 ex_job = st.session_state.job_lookup[temp_job.link]
                 ex_job.title = temp_job.title
                 ex_job.company = temp_job.company
                 ex_job.location = temp_job.location
                 ex_job.source = temp_job.source
                 ex_job.description = temp_job.description
             else:
                 st.session_state.raw_jobs.append(temp_job)
                 st.session_state.job_lookup[temp_job.link] = temp_job
                 
             try:
                 db = JobDatabase(str(JOBS_DATABASE_PATH))
                 existing_jobs = db.load_jobs()
                 updated_jobs = [j for j in existing_jobs if j.link != temp_job.link]
                 updated_jobs.append(temp_job)
                 db.save_jobs(updated_jobs)
             except Exception as e:
                 st.error(f"Błąd dodawania do bazi DB: {e}")
             
             status, rating = get_decision(temp_job.link)
             render_job_card(temp_job, "manual_prev", status=status, rating=rating)
             
             if status:
                  st.success(f"Oferta zapisana i oceniona jako '{status}'!")
                  if st.button("Gotowe - Dodaj kolejną ofertę"):
                       st.session_state.manual_job_data = None
                       st.session_state.manual_job_url = ""
                       st.rerun()

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

        [data-testid="stMainBlockContainer"] {
            padding-top: 1.5rem;
            padding-bottom: 2.5rem;
            max-width: 1680px;
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
            padding-bottom: 0.7rem;
            border-bottom: 1px solid var(--line-soft);
        }
        /* Marka: szeryf, małe litery, bez rozstrzelenia. Wcześniej szła
           tym samym mono-wersalikiem co zakładki, etykiety i log - czyli
           niczym się nie różniła od podpisu kolumny. */
        .wt-brand {
            font-family: var(--font-head);
            font-size: 1.3rem;
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
            font-size: 1.34rem;
            font-weight: 600;
            letter-spacing: -0.02em;
            line-height: 1;
            font-variant-numeric: tabular-nums;
            color: var(--text-bright);
        }
        .wt-item span {
            font-family: var(--font-body);
            font-size: 0.67rem;
            font-weight: 500;
            letter-spacing: 0.01em;
            color: var(--faint);
        }

        /* ---------- zakładki ---------- */
        [class*="st-key-wstabs"] { margin: 0.5rem 0 0.15rem; }
        [class*="st-key-wstabs"] [data-baseweb="button-group"] { gap: 0.1rem; }
        [class*="st-key-wstabs"] button {
            border: 0 !important;
            background: transparent !important;
            border-radius: 0 !important;
            border-bottom: 2px solid transparent !important;
            padding: 0.34rem 0.7rem !important;
            font-family: var(--font-body) !important;
            font-size: 0.84rem !important;
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
            font-size: 0.73rem;
            color: var(--faint);
            margin: 0.55rem 0 0;
        }

        /* ---------- panele ---------- */
        [class*="st-key-wscols"] { margin-top: 1.1rem; }
        [class*="st-key-wsleft"],
        [class*="st-key-wsright"] {
            background: var(--raised);
            border: 1px solid var(--line-soft);
            border-radius: 0.8rem;
            padding: 0.95rem 1.1rem 1.15rem;
            min-height: 62vh;
        }
        /* Jedna skala odstępów w obu panelach - reszta rytmu siedzi
           w marginesach bloków HTML, nie w domyślnych przerwach Streamlita. */
        [class*="st-key-wsleft"] > [data-testid="stVerticalBlock"],
        [class*="st-key-wsright"] > [data-testid="stVerticalBlock"] {
            gap: 0.7rem;
        }

        /* Nagłówek panelu ma stałą wysokość po obu stronach, żeby pierwsza
           włosowa kreska w lewym i prawym panelu leżała na tej samej linii -
           także wtedy, gdy po jednej stronie siedzą przyciski, a po drugiej
           sam napis. */
        .wp-head,
        [class*="st-key-wshead"] {
            min-height: 2.6rem;
            border-bottom: 1px solid var(--line-soft);
        }
        .wp-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
            padding-bottom: 0.55rem;
        }
        [class*="st-key-wshead"] {
            align-items: center !important;
            padding-bottom: 0.45rem;
        }
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
            font-size: 1.03rem;
            font-weight: 600;
            letter-spacing: -0.01em;
            color: var(--text);
            line-height: 1.35;
        }
        .wp-label::first-letter { text-transform: uppercase; }

        .wp-count {
            font-family: var(--font-mono);
            font-size: 0.72rem;
            color: var(--faint);
            font-variant-numeric: tabular-nums;
        }
        .wp-empty {
            padding: 1.4rem 0;
            font-size: 0.8rem;
            color: var(--faint);
        }
        .wp-foot {
            padding-top: 0.8rem;
            font-family: var(--font-mono);
            font-size: 0.68rem;
            letter-spacing: 0.02em;
            color: var(--faint);
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .wp-hint {
            margin-top: 1.3rem;
            padding-top: 0.85rem;
            border-top: 1px solid var(--line-soft);
            font-size: 0.73rem;
            line-height: 1.55;
            color: var(--faint);
        }
        .wp-note {
            font-size: 0.78rem;
            line-height: 1.55;
            color: var(--muted);
            padding: 0.7rem 0.9rem;
            border-left: 2px solid var(--line);
            background: rgba(255, 255, 255, 0.018);
            border-radius: 0 0.4rem 0.4rem 0;
        }
        .wp-note.is-ok { border-left-color: var(--accent); }
        .wp-note.is-warn { border-left-color: var(--amber); }
        .wp-note em { color: var(--text); font-style: normal; }

        [class*="st-key-wslisthead"] [data-testid="stTextInput"] input {
            font-size: 0.79rem;
        }
        [class*="st-key-wslisthead"] > [data-testid="stVerticalBlock"] { gap: 0.55rem; }

        /* ---------- wiersz oferty ---------- */
        [class*="st-key-wsrow_"],
        [class*="st-key-wsdec_"] {
            position: relative;
            border-bottom: 1px solid var(--line-soft);
            transition: background-color .16s ease;
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
        [class*="st-key-btn_wsrow_"] .stButton > button,
        [class*="st-key-btn_wsdec_"] .stButton,
        [class*="st-key-btn_wsdec_"] .stButton > button {
            width: 100% !important;
            height: 100% !important;
            min-height: 0 !important;
            padding: 0 !important;
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
            cursor: pointer;
        }
        [class*="st-key-btn_wsrow_"] .stButton > button,
        [class*="st-key-btn_wsdec_"] .stButton > button { opacity: 0; }

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
            font-size: 0.65rem;
            color: var(--faint);
        }
        .wr-main { grid-column: 2; min-width: 0; }
        .wr-title {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.91rem;
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
            font-size: 0.69rem;
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
            font-size: 1rem;
            font-weight: 600;
            letter-spacing: -0.01em;
        }
        .wr-score small { font-size: 0.58rem; margin-left: 0.06rem; opacity: 0.7; }
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
            font-size: 0.64rem;
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
            font-size: 0.75rem;
            min-width: 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }
        .wl-time {
            font-family: var(--font-mono);
            font-size: 0.65rem;
            color: var(--faint);
            white-space: nowrap;
        }
        /* Rodzaj operacji to etykieta, nie dana - stąd krój interfejsu
           i mocno ścięte rozstrzelenie. Ma być najcichszą warstwą wiersza. */
        .wl-op {
            font-family: var(--font-body);
            font-size: 0.63rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: var(--faint);
            white-space: nowrap;
        }
        .wl-op.is-bad { color: var(--clay); }
        .wl-what {
            font-family: var(--font-mono);
            font-size: 0.71rem;
            color: var(--text);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .wl-detail {
            color: var(--faint);
            font-size: 0.7rem;
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
            font-size: 0.6rem;
            font-weight: 600;
            letter-spacing: 0.02em;
            white-space: nowrap;
        }
        .wl-title {
            color: var(--text);
            font-size: 0.77rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
        }
        .wl-org {
            color: var(--faint);
            font-size: 0.7rem;
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
            font-size: 0.6rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .wd-title {
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: 1.45rem;
            line-height: 1.18;
            letter-spacing: -0.018em;
            color: var(--text-bright);
            text-wrap: balance;
        }
        .wd-meta {
            margin-top: 0.4rem;
            font-size: 0.74rem;
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
            font-size: 2.15rem;
            font-weight: 600;
            line-height: 1;
            letter-spacing: -0.03em;
        }
        .wd-score small { font-size: 0.8rem; margin-left: 0.08rem; opacity: 0.65; }
        .wd-gauge-lbl {
            font-family: var(--font-body);
            font-size: 0.63rem;
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
            font-size: 0.79rem;
            line-height: 1.55;
            color: var(--text);
        }

        /* Opis: własne pole przewijania z wygaszeniem u dołu, żeby ucięcie
           tekstu wyglądało na zamierzone, a nie na przypadkowe. */
        .wd-descwrap { position: relative; margin-top: 1.05rem; }
        .wd-descwrap::after {
            content: "";
            position: absolute;
            left: 0; right: 0; bottom: 0;
            height: 2.6rem;
            pointer-events: none;
            background: linear-gradient(to bottom,
                        rgba(0, 0, 0, 0), var(--raised) 88%);
        }
        .wd-desc {
            max-height: 15.5rem;
            overflow-y: auto;
            padding: 0 1rem 1.8rem 0;
            font-size: 0.8rem;
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
            font-size: 0.77rem;
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
            font-size: 0.76rem !important;
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
            font-size: 0.95rem !important;
            opacity: 0.75;
        }

        /* Odrzucenie i usunięcie mają barwę konsekwencji, nie akcentu. */
        [class*="st-key-ws_rej_"] button:hover,
        [class*="st-key-ws_del_"] button:hover,
        [class*="st-key-ws_delyes_"] button,
        [class*="st-key-wstool_recalc"] button:hover {
            color: var(--clay) !important;
            border-color: color-mix(in srgb, var(--clay) 45%, transparent) !important;
            background: color-mix(in srgb, var(--clay) 10%, transparent) !important;
        }
        [class*="st-key-ws_conf_"] button {
            color: var(--accent) !important;
            border-color: var(--accent-edge) !important;
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
            font-size: 0.63rem;
            font-weight: 600;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--faint);
            white-space: nowrap;
        }
        [class*="st-key-wsactions"] [data-testid="stSliderTickBarMin"],
        [class*="st-key-wsactions"] [data-testid="stSliderTickBarMax"] { display: none; }
        [class*="st-key-wsactions"] [data-testid="stSlider"] { padding-top: 0; }
        [class*="st-key-wsactions"] [data-testid="stThumbValue"] {
            font-family: var(--font-mono);
            font-size: 0.66rem;
            color: var(--accent);
        }
        [class*="st-key-wsactions"] > [data-testid="stVerticalBlock"] { gap: 0.5rem; }

        /* ---------- widoki narzędziowe w tym samym fasonie ---------- */
        .wx-step {
            padding: 0.95rem 0 0.55rem;
            border-top: 1px solid var(--line-soft);
        }
        .wx-step.is-first { border-top: 0; padding-top: 0.15rem; }
        .wx-num {
            font-family: var(--font-mono);
            font-size: 0.64rem;
            letter-spacing: 0.1em;
            color: var(--accent);
            font-variant-numeric: tabular-nums;
        }
        .wx-title {
            margin-top: 0.2rem;
            font-family: var(--font-head);
            font-stretch: var(--head-stretch);
            font-size: 1.08rem;
            font-weight: 600;
            letter-spacing: -0.01em;
            color: var(--text-bright);
        }
        .wx-desc {
            margin-top: 0.35rem;
            font-size: 0.77rem;
            line-height: 1.55;
            color: var(--muted);
        }
        .wx-meta {
            margin-top: 0.45rem;
            font-family: var(--font-mono);
            font-size: 0.63rem;
            line-height: 1.55;
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
        .wx-stat-lbl { font-size: 0.75rem; color: var(--muted); }
        .wx-stat-val {
            font-family: var(--font-mono);
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-bright);
        }
        .wx-stat-val.is-on { color: var(--accent); }
        .wx-stat-val.is-off { color: var(--clay); }
        .wx-file {
            font-family: var(--font-mono);
            font-size: 0.72rem;
            color: var(--text);
            word-break: break-all;
        }
        .wx-path {
            margin-top: 0.15rem;
            font-family: var(--font-mono);
            font-size: 0.62rem;
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
        .wr-row.is-fresh,
        .wl-line.is-fresh,
        .wl-dec.is-fresh,
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
            .wd-score { font-size: 1.75rem; }
            .wd-title { font-size: 1.18rem; }
        }
        </style>
    """, unsafe_allow_html=True)

# =============================================================================
# PULPIT: NAWIGACJA U GORY + DWA PANELE
# =============================================================================

WS_TABS = ["Dopasowane", "Wszystkie", "Ocenione", "Zapisane", "Aspiracyjne", "Odrzucone"]
WS_TOOLS = ["Tablica", "Dodaj z linku", "Panel sterowania"]
WS_ALL = WS_TABS + WS_TOOLS

WS_TAB_HINT = {
    "Dopasowane": "ocenione przez AI, jeszcze nietknięte przez Ciebie",
    "Wszystkie":  "cała baza prosto ze skraperów",
    "Ocenione":   "wystawiłeś ocenę i zostawiłeś na później",
    "Zapisane":   "zapisane oraz te, gdzie aplikacja już poszła",
    "Aspiracyjne": "za wysoko na teraz, ale w tę stronę celujesz",
    "Odrzucone":  "odrzucone - profil uczy się, czego nie chcesz",
    "Tablica":    "oferty, w których coś się dzieje - po lewej etap i decyzje",
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
    matches_by_link = {m.job.link: m for m in st.session_state.analyzed_matches}
    out = []

    def add(job, match):
        status, rating = get_decision(job.link)
        out.append((job, match, status, rating))

    if tab == "Dopasowane":
        for m in st.session_state.analyzed_matches:
            status, _ = get_decision(m.job.link)
            if status in ('reject', 'save', 'apply', 'rated', 'aspirational'):
                continue
            add(m.job, m)
        out.sort(key=lambda t: t[1].match_percentage if t[1] else -1, reverse=True)

    elif tab == "Wszystkie":
        for j in st.session_state.raw_jobs:
            add(j, matches_by_link.get(j.link))
        out.sort(key=lambda t: (t[1].match_percentage if t[1] else -1,
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

        source = getattr(job, "source", "") or "—"
        location = getattr(job, "location", "") or "Warszawa"

        meta = (f'<span class="wr-org">{_esc(job.company)}</span>'
                f'<span class="wr-sep">·</span>'
                f'<span class="wr-src">{src_dot(source)}</span>'
                f'<span class="wr-sep">·</span>'
                f'<span>{_esc(location)}</span>')

        cls = "wr-row"
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


def ws_render_list_panel(tab):
    """Prawy panel w całości: pasek narzędzi, wiersze, stronicowanie."""
    search = st.session_state.get("ws_search", "") or ""
    items = ws_collect_cached(tab, search)

    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE)) if items else 1
    page_key = f"ws_page_{tab}"
    if st.session_state.get(page_key, 1) > total_pages:
        st.session_state[page_key] = total_pages

    with st.container(key="wslisthead"):
        ws_panel_head("oferty", fmt_n(len(items)))
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        with c1:
            st.text_input("Szukaj", key="ws_search", placeholder="Stanowisko lub firma…",
                          label_visibility="collapsed")
        with c2:
            page = st.number_input("Strona", min_value=1, max_value=total_pages,
                                   value=min(st.session_state.get(page_key, 1), total_pages),
                                   key=page_key, label_visibility="collapsed")

    if not items:
        st.markdown('<div class="wp-empty">Nic tu nie ma. '
                    'Zmień zakładkę albo wyczyść szukanie.</div>',
                    unsafe_allow_html=True)
        return

    sig = f"{tab}|{search}|{page}|{len(items)}"
    fresh = st.session_state.get("_ws_sig") != sig
    st.session_state["_ws_sig"] = sig

    start = (page - 1) * PAGE_SIZE
    page_items = list(enumerate(items))[start:start + PAGE_SIZE]
    ws_render_rows(page_items, fresh, st.session_state.get("ws_selected"))

    if total_pages > 1:
        st.markdown(f'<div class="wp-foot">strona {page} z {total_pages}</div>',
                    unsafe_allow_html=True)


# --- lewy panel: log -------------------------------------------------------

def ws_activity_rows(limit=9):
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
        if shown >= 9:
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

        raw = st.session_state.user_decisions.get(link)
        stamp = raw.get("decided_at") or raw.get("applied_at") if isinstance(raw, dict) else None
        right = _esc(stamp) if stamp else _esc(getattr(job, "company", "") or "")

        row_key = f"wsdec_{hashlib.md5(link.encode()).hexdigest()[:12]}"
        with st.container(key=row_key):
            st.markdown(
                f'<div class="wl-dec is-fresh" style="--i:{shown}">'
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

    match = next((m for m in st.session_state.analyzed_matches if m.job.link == link), None)
    status, rating = get_decision(link)

    # Nagłówek: podpis po lewej, dwa drobne przyciski po prawej, wszystko na
    # jednej linii środka. Kontener ma własną włosową kreskę, tej samej
    # wysokości co nagłówek prawego panelu.
    with st.container(key="wshead", horizontal=True, vertical_alignment="center",
                      gap="small"):
        st.markdown('<div class="wp-label">szczegóły oferty</div>',
                    unsafe_allow_html=True, width="stretch")
        st.link_button("Otwórz", job.link, icon=":material/open_in_new:",
                       help="Otwiera ofertę na portalu, w nowej karcie")
        if st.button("Zamknij", icon=":material/close:", key="ws_close",
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
        match = next((m for m in st.session_state.analyzed_matches
                      if m.job.link == job.link), None)
        rating = ddata.get("rating") if isinstance(ddata, dict) else None
        stages[target].append((job, match, status, rating))

    return stages


def ws_tool_board(left, right):
    stages = ws_board_stages()
    total = sum(len(v) for v in stages.values())
    selected = st.session_state.get("ws_selected")

    with right:
        with st.container(key="wsright"):
            ws_panel_head("tablica", fmt_n(total))
            if total == 0:
                st.markdown(
                    '<div class="wp-note">Tablica jest pusta. Trafiają tu oferty '
                    'oznaczone jako <em>Zapisz</em> albo <em>Wysłane</em> - a potem '
                    'przesuwasz je między etapami, gdy dostaniesz odpowiedź. '
                    'Odrzucone przy przeglądaniu tu nie wchodzą, mają własną '
                    'zakładkę.</div>', unsafe_allow_html=True)
            else:
                sig = f"tablica|{total}"
                fresh = st.session_state.get("_ws_sig") != sig
                st.session_state["_ws_sig"] = sig
                i = 0
                for key, label in WS_STAGE_NAMES.items():
                    items = stages[key]
                    if not items:
                        continue
                    st.markdown(f'<div class="wr-stage"><span>{_esc(label)}</span>'
                                f'<span>{len(items)}</span></div>',
                                unsafe_allow_html=True)
                    ws_render_rows(list(enumerate(items, start=i)), fresh, selected,
                                   rank=False)
                    i += len(items)

    with left:
        with st.container(key="wsleft"):
            on_board = any(job.link == selected
                           for items in stages.values() for job, *_ in items)
            if selected and on_board:
                ws_render_detail(selected, stage_ctl=True)
            else:
                ws_panel_head("etapy")
                for key, label in WS_STAGE_NAMES.items():
                    n = len(stages[key])
                    st.markdown(
                        f'<div class="wx-stat"><span class="wx-stat-lbl">{_esc(label)}'
                        f'</span><span class="wx-stat-val'
                        f'{" is-off" if n == 0 else ""}">{n}</span></div>',
                        unsafe_allow_html=True)
                st.markdown('<div class="wp-hint">Kliknij ofertę z tablicy, żeby '
                            'zobaczyć szczegóły i przestawić jej etap.</div>',
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
    db_path = base / "jobs_database.json"
    analyzed_path = base / "analyzed_jobs_waterfall.json"
    profile_path = base / "preference_profile.json"
    decisions_path = base / "user_decisions.json"

    # Liczymy na ZBIORACH linków, nie na długościach list. Poprzednia wersja
    # robiła `baza - oceny_AI - decyzje`, a decyzje dotyczą ofert, które już
    # mają ocenę AI - ta sama oferta była odejmowana dwa razy i "czeka na ocenę"
    # potrafiło pokazać 0 przy tysiącach nieprzeanalizowanych ofert.
    db_links = _read_links(db_path)
    analyzed_links = _read_links(analyzed_path, nested=True)
    decided_links = set()
    if decisions_path.exists():
        try:
            with open(decisions_path, "r", encoding="utf-8") as f:
                decided_links = set(json.load(f).keys())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Nie udało się odczytać decyzji: {e}")

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
            rows = ws_activity_rows(limit=8)
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
    """Pasek u góry: marka, listwa liczb, zakładki."""
    n_raw = len(st.session_state.raw_jobs)
    n_analyzed = len(st.session_state.analyzed_matches)
    n_active = sum(1 for l in st.session_state.user_decisions
                   if get_decision(l)[0] in ('apply', 'save'))

    # Marka i liczby jednym blokiem: kolumny Streamlita mierzyly wlasna
    # wysokosc inaczej niz tresc i wlosowa kreska przechodzila przez cyfry.
    st.markdown(
        f'<div class="wt-bar">'
        f'<div class="wt-brand">job<span>finder</span></div>'
        f'<div class="wt-rail">'
        f'<div class="wt-item"><b>{fmt_n(n_raw)}</b><span>w bazie</span></div>'
        f'<div class="wt-item"><b>{fmt_n(n_analyzed)}</b><span>ocen AI</span></div>'
        f'<div class="wt-item"><b>{fmt_n(n_active)}</b><span>zapisanych</span></div>'
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

        if active == "Tablica":
            ws_tool_board(left, right)
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
                        ws_render_detail(selected)
                    else:
                        ws_render_activity()


VIEWS = {
    "Dopasowane przez AI":  render_analyzed_view,
    "Cała baza":            render_raw_view,
    "Dodaj z linku":        render_add_manual_view,
    "Tablica rekrutacyjna": render_kanban_view,
    "Zapisane i wysłane":   render_saved_view,
    "Ocenione":             render_rated_view,
    "Aspiracyjne":          render_aspirational_view,
    "Odrzucone":            render_rejected_view,
    "Panel sterowania":     render_pipeline_control,
}
VIEW_ORDER = list(VIEWS)

# Stare nazwy widokow - zeby sesja zapisana przed przebudowa nie wyladowala
# na widoku domyslnym bez slowa wyjasnienia.
VIEW_ALIASES = {
    "🚀 Analiza AI (Dopasowanie)": "Dopasowane przez AI",
    "📂 Surowa Baza Ofert":        "Cała baza",
    "➕ Dodaj Ofertę z Linku":     "Dodaj z linku",
    "📋 Tablica Rekrutacyjna (Kanban)": "Tablica rekrutacyjna",
    "💾 Zapisane i Aplikowane":    "Zapisane i wysłane",
    "⭐ Ocenione Oferty":          "Ocenione",
    "🌟 Oferty Aspirujące":        "Aspiracyjne",
    "🗑 Odrzucone Oferty":         "Odrzucone",
    "🎛 Panel Sterowania":         "Panel sterowania",
}


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
