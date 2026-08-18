# Job Finder — LLM-scored job offer analysis

Collects job postings from a dozen-plus Polish job boards, scores how well each one
matches my CV using a language model, and ranks them. Built because manually reading
through fifteen thousand listings is not a plan.

**Current database: 15,701 offers.**

## Why it actually works (and not just looks like it does)

The easy version of this project prints confident percentages nobody can verify.
So the ranking is measured against several hundred ratings I entered by hand.

| metric | result | meaning |
|---|---|---|
| precision@5 | **76.7%** | 3.3× better than random picking |
| precision@10 | 59.2% | 2.5× better than random |
| Spearman correlation | **+0.649** | model's score genuinely tracks my judgement |

Baseline (accuracy of a random pick) is 23.3%. Measured by `eval_ranking.py`.

## Architecture

```
main_scraper.py               collects offers from all sources in parallel
   └── scrapers/              one module per board (API where available, HTML where not)
migrate_normalize_links.py    canonicalises URLs — MUST run before deduplication
deduplicate_db.py             drops duplicates (the same offer sits on 4 boards)
clean_db.py                   trims descriptions to avoid burning tokens
waterfall_analysis.py         match scoring: model cascade + API key rotation
eval_ranking.py               checks whether the ranking works at all
streamlit_app.py              UI for browsing and rating offers
```

Run everything: `python run_final_pipeline.py`

### Decisions that cost the most time

- **Link normalisation before deduplication.** The same offer under two URLs
  (`?utm_source=…`) passed as two distinct listings. The pipeline's step order
  is deliberate, not incidental.
- **A model cascade instead of one model.** A cheaper model handles the bulk;
  a stronger one steps in where the cheap one declines. Plus key rotation and
  resume-after-failure — scoring 2,400 offers takes tens of minutes and cannot
  restart from zero on every network hiccup.
- **Limits derived from measurement, not intuition.** Batch size and pagination
  depth come from `benchmark_models.py` and real runs.
- **Scrapers are built around each board's defences.** Every module documents what
  was verified empirically — for example why Indeed is not paginated, and why its
  descriptions are collected by clicking cards rather than opening subpages.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # add your own Gemini key
python run_final_pipeline.py
streamlit run streamlit_app.py
```

API keys are read only from `.env`, which never enters the repository. Personal data
(my CV, my rating history) stays out too — see `.gitignore`.

## Stack

Python · Playwright · Gemini API · Streamlit · BeautifulSoup

## What is mine in this project

The problem, the architecture of the pipeline, the choice of sources, the order of
the stages and the method for evaluating ranking quality are my own design. The last
one was the hardest call to make: it would have been far easier to print confident
percentages and never check whether they mean anything.

---
---

# Job Finder — analiza ofert pracy modelem językowym

Zbiera ogłoszenia o pracę z kilkunastu polskich portali, ocenia dopasowanie każdego
z nich do mojego CV modelem językowym i układa je w ranking. Powstało, bo przeglądanie
piętnastu tysięcy ofert ręcznie nie ma sensu.

**Baza na dziś: 15 701 ofert.**

## Dlaczego to działa (a nie tylko wygląda, że działa)

Najłatwiej zbudować system, który wypisuje procenty i nie da się sprawdzić, czy mają
sens. Dlatego ranking jest mierzony względem kilkuset ocen, które wystawiłem ręcznie.

| miara | wynik | co znaczy |
|---|---|---|
| precision@5 | **76,7 %** | 3,3× lepiej niż wybór losowy |
| precision@10 | 59,2 % | 2,5× lepiej niż losowo |
| korelacja Spearmana | **+0,649** | ocena modelu realnie idzie w parze z moją |

Baseline (trafność losowego wyboru) to 23,3 %. Liczy to `eval_ranking.py`.

## Jak to jest zbudowane

```
main_scraper.py               zbiera oferty ze wszystkich źródeł równolegle
   └── scrapers/              jeden moduł na portal (API tam, gdzie jest; HTML gdzie nie ma)
migrate_normalize_links.py    ujednolica adresy — MUSI iść przed deduplikacją
deduplicate_db.py             usuwa duplikaty (ta sama oferta bywa na 4 portalach)
clean_db.py                   skraca opisy, żeby nie przepalać tokenów
waterfall_analysis.py         ocena dopasowania: kaskada modeli + rotacja kluczy
eval_ranking.py               sprawdza, czy ranking w ogóle działa
streamlit_app.py              interfejs do przeglądania i oceniania ofert
```

Całość jednym poleceniem: `python run_final_pipeline.py`

### Decyzje, które kosztowały najwięcej czasu

- **Normalizacja linków przed deduplikacją.** Ta sama oferta pod dwoma adresami
  (`?utm_source=…`) przechodziła jako dwie różne. Kolejność etapów w pipelinie
  nie jest przypadkowa.
- **Kaskada modeli zamiast jednego.** Tańszy model bierze większość ofert, mocniejszy
  wchodzi tam, gdzie tańszy odmawia. Do tego rotacja kluczy i wznawianie po
  przerwaniu — analiza 2 400 ofert trwa kilkadziesiąt minut i nie może zaczynać
  od zera po każdym błędzie sieci.
- **Limity dobrane pomiarem, nie na oko.** Wielkość paczki i głębokość paginacji
  wynikają z `benchmark_models.py` i realnych przebiegów.
- **Scrapery są zbudowane wokół zabezpieczeń portali.** Każdy moduł dokumentuje to,
  co zostało sprawdzone empirycznie — np. dlaczego Indeed nie jest paginowany
  i dlaczego opisy zbierane są klikaniem kart, a nie wchodzeniem na podstrony.

## Uruchomienie

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # wpisz własny klucz Gemini
python run_final_pipeline.py
streamlit run streamlit_app.py
```

Klucze API czytane są wyłącznie z `.env`, który nigdy nie trafia do repozytorium.
Dane osobowe (moje CV, historia moich ocen) też są poza repo — patrz `.gitignore`.

## Stos

Python · Playwright · Gemini API · Streamlit · BeautifulSoup

## Co w tym projekcie jest moje

Problem, architektura pipeline'u, dobór źródeł, kolejność etapów i sposób oceny
jakości rankingu to moje decyzje. Ta ostatnia była najtrudniejsza: dużo łatwiej
byłoby wypisywać pewne siebie procenty i nigdy nie sprawdzić, czy cokolwiek znaczą.
