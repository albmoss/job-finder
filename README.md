# Job Finder — analiza ofert pracy modelem językowym

Zbiera ogłoszenia o pracę z kilkunastu polskich portali, ocenia dopasowanie każdego
z nich do mojego CV modelem językowym i układa je w ranking. Powstało, bo przeglądanie
kilkunastu tysięcy ofert ręcznie nie ma sensu.

**Baza na dziś: 15 701 ofert.**

---

## Dlaczego to działa (a nie tylko wygląda, że działa)

Najłatwiej zbudować system, który wypisuje procenty i nie da się sprawdzić, czy mają
sens. Dlatego oceniam sam ranking: porównuję jego kolejność z setkami ocen, które
wystawiłem ręcznie w interfejsie.

| miara | wynik | co znaczy |
|---|---|---|
| precision@5 | **76,7 %** | 3,3× lepiej niż wybór losowy |
| precision@10 | 59,2 % | 2,5× lepiej niż losowo |
| korelacja Spearmana | **+0,649** | ocena modelu realnie idzie w parze z moją |

Baseline (trafność losowego wyboru) to 23,3 %. Skrypt liczący to: `eval_ranking.py`.

---

## Jak to jest zbudowane

```
main_scraper.py        zbiera oferty ze wszystkich źródeł równolegle
   └── scrapers/       jeden moduł na portal (API tam, gdzie jest; HTML gdzie nie ma)
migrate_normalize_links.py   ujednolica adresy — MUSI iść przed deduplikacją
deduplicate_db.py      usuwa duplikaty (ta sama oferta bywa na 4 portalach)
clean_db.py            skraca opisy, żeby nie przepalać tokenów
waterfall_analysis.py  ocena dopasowania: kaskada modeli + rotacja kluczy
eval_ranking.py        sprawdza, czy ranking w ogóle działa
streamlit_app.py       interfejs do przeglądania i oceniania ofert
```

Całość jednym poleceniem: `python run_final_pipeline.py`

### Decyzje, które kosztowały najwięcej czasu

- **Normalizacja linków przed deduplikacją.** Ta sama oferta pod dwoma adresami
  (`?utm_source=…`) przechodziła jako dwie różne. Kolejność etapów w pipelinie
  nie jest przypadkowa.
- **Kaskada modeli zamiast jednego.** Tańszy model bierze większość ofert, droższy
  wchodzi tam, gdzie tańszy odmawia. Do tego rotacja kluczy i wznawianie po
  przerwaniu — analiza 2 400 ofert trwa kilkadziesiąt minut i nie może zaczynać
  od zera po każdym błędzie sieci.
- **Limity dobrane pomiarem, nie na oko.** Wielkość paczki i liczba stron
  paginacji wynikają z `benchmark_models.py` i realnych przebiegów, nie z intuicji.
- **Scrapery są zbudowane wokół ograniczeń portali.** Każdy ma w nagłówku opis
  tego, co zostało sprawdzone empirycznie — np. dlaczego Indeed nie jest paginowany
  i dlaczego opisy zbierane są klikaniem kart, a nie wchodzeniem na podstrony.

---

## Uruchomienie

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # wpisz własny klucz Gemini
python run_final_pipeline.py
streamlit run streamlit_app.py
```

Klucze API czytane są wyłącznie z `.env`, który nigdy nie trafia do repozytorium.
Dane osobowe (CV, historia moich ocen) też są poza repo — patrz `.gitignore`.

---

## Stos

Python · Playwright · Gemini API · Streamlit · BeautifulSoup

## Uwaga o powstaniu projektu

Projekt budowałem z pomocą narzędzi AI (Claude Code). Ode mnie pochodzą: problem,
architektura pipeline'u, dobór źródeł, kolejność etapów, sposób oceny jakości
rankingu i wszystkie decyzje wymienione wyżej. Piszę o tym wprost, bo umiejętność
prowadzenia takiego projektu z AI uważam za część warsztatu, a nie coś do ukrycia.
