# Job Finder

*Every job board in Poland, read overnight and sorted against one CV.*

![Job Finder — offer list and activity log](docs/app.png)

## What this is

A dozen Polish job boards go in, one ranked list comes out — and the list gets better the more
of it I rate, because my ratings are what the scoring prompt is built from.

```
    job boards              the pipeline                  the interface
    Pracuj, OLX,            run_final_pipeline.py         streamlit run
    justjoin, praca.pl,           |                             |
    aplikuj, gowork, ...          v                             v
          |                deduplicate → clean →          offers sorted by match,
          v                score with two models          decided in one click:
    17,597 offers          (the cheap one first,          saved / applied /
    in jobs_database       the strong one for             aspirational / rejected
                            what it declines)                    |
                                  ^                              |
                                  |                              v
                        preference_profile.json   ←──────   my ratings
                        built from contrasting pairs
                        of what I rated and how
```

The loop at the bottom is the whole point. Scoring 17,000 offers against a CV is the easy half;
the half that matters is that every rating I enter reshapes the prompt that produces the next
ranking.

## How it works

The pipeline runs in stages, each one a separate script:

```
main_scraper.py               collects offers from all sources in parallel
   └── scrapers/              one module per board — the API where there is one, HTML where there isn't
migrate_normalize_links.py    canonicalises URLs; runs before deduplication, because the same
                              offer under ?utm_source=… otherwise counts as two
deduplicate_db.py             removes duplicates — one offer often sits on four boards at once
clean_db.py                   trims descriptions so scoring doesn't burn tokens on boilerplate
waterfall_analysis.py         scoring: a cheap model takes the bulk, a stronger one picks up
                              what it declines; API keys rotate and an interrupted run resumes
eval_ranking.py               compares the ranking against ratings entered by hand
streamlit_app.py              the interface — browsing, filtering and rating offers
```

`python run_final_pipeline.py` runs the whole thing end to end.

Four more scripts sit outside the pipeline and are run by hand:

```
benchmark_models.py           measures how closely each model agrees with my own ratings
compare_before_after.py       the only way to tell whether a prompt change helped: the
                              pipeline skips offers I have rated, so the stored scores for
                              the test set never refresh
skill_gaps.py                 what the well-matched offers keep asking for and my CV hasn't got
enrich_olx_descriptions.py    fetches the real text for OLX offers left with a placeholder
```

The interface is a single screen: offers on the right, details and decision buttons on the
left. Rating an offer moves it between tabs — saved, applied, aspirational, rejected.

## Running it

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # add your own Gemini key
python run_final_pipeline.py
streamlit run streamlit_app.py

python eval_ranking.py                                    # is the ranking working?
python skill_gaps.py                                      # what to learn next
PYTHONIOENCODING=utf-8 python tests/integration_test.py   # 57 tests, no API calls
```

A run that exits 0 has not necessarily worked: check the log for `PIPELINE COMPLETE`.

## Four decisions worth explaining

**Two models, not one.** A cheap model scores the bulk; the stronger one only picks up what
it declined. Which models fill which slot is not a guess — `benchmark_models.py` measures each
one against my ratings and the winner takes the seat.

**The profile is built from contrasts.** `generate_preference_profile.py` turns my rating
history into a description of what I'm looking for and injects it into the scoring prompt. It
deliberately includes contrasting pairs — near-identical offers I rated differently — because
those are what teach the model to discriminate, which a pile of good examples never does.

**Scores know when they are stale.** Every stored score carries the hash of the description it
was made from and the version of the profile that made it. Change either and the offer is
re-scored on the next run; nothing has to be invalidated by hand.

**The database is JSON files, and that is a decision, not an accident.** Atomic writes with
rotating backups in `utils/safe_io.py`; deduplication, purging and link normalisation written
out as explicit stages instead of inherited from an engine. It is legible and it is on borrowed
time — 27 MB of it — and Postgres is the obvious next move.

API keys are read only from `.env`, which never enters the repository. Personal data — my CV
and my rating history — stays out too; see `.gitignore`.

## Stack

Python · Playwright · Gemini API · Streamlit · BeautifulSoup

---
---

# Job Finder

*Wszystkie polskie portale pracy, przeczytane w nocy i ułożone pod jedno CV.*

![Job Finder — lista ofert i log ostatnich zdarzeń](docs/app.png)

## Czym to jest

Na wejściu kilkanaście polskich portali, na wyjściu jedna lista — i ta lista jest tym lepsza,
im więcej z niej ocenię, bo to z moich ocen powstaje prompt oceniający.

```
    portale pracy           pipeline                      interfejs
    Pracuj, OLX,            run_final_pipeline.py         streamlit run
    justjoin, praca.pl,           |                             |
    aplikuj, gowork, ...          v                             v
          |                deduplikacja → czyszczenie →   oferty ułożone wg
          v                ocena dwoma modelami           dopasowania, decyzja
    17 597 ofert           (najpierw tańszy,              jednym kliknięciem:
    w jobs_database         mocniejszy tam, gdzie         zapisane / wysłane /
                            tańszy odmówił)               aspiracyjne / odrzucone
                                  ^                              |
                                  |                              v
                        preference_profile.json   ←──────   moje oceny
                        zbudowany z par kontrastowych:
                        co oceniłem i jak
```

Pętla na dole jest tu najważniejsza. Ocenienie 17 tysięcy ofert względem CV to łatwiejsza
połowa; ta, która decyduje, polega na tym, że każda wystawiona przeze mnie ocena przebudowuje
prompt, z którego powstanie następny ranking.

## Jak to działa

Pipeline idzie etapami, każdy to osobny skrypt:

```
main_scraper.py               zbiera oferty ze wszystkich źródeł równolegle
   └── scrapers/              jeden moduł na portal — API tam, gdzie jest, HTML tam, gdzie nie ma
migrate_normalize_links.py    ujednolica adresy; idzie przed deduplikacją, bo bez tego ta sama
                              oferta pod ?utm_source=… liczyłaby się jako dwie różne
deduplicate_db.py             usuwa duplikaty — jedna oferta bywa naraz na czterech portalach
clean_db.py                   skraca opisy, żeby ocena nie przepalała tokenów na wypełniaczu
waterfall_analysis.py         ocena: tańszy model bierze większość, mocniejszy wchodzi tam,
                              gdzie tańszy odmówił; klucze API się rotują, przerwany bieg wznawia
eval_ranking.py               porównuje ranking z ocenami wystawionymi ręcznie
streamlit_app.py              interfejs — przeglądanie, filtrowanie i ocenianie ofert
```

`python run_final_pipeline.py` przechodzi całość od początku do końca.

Cztery skrypty stoją poza pipelinem i uruchamia się je ręcznie:

```
benchmark_models.py           mierzy, jak bardzo każdy model zgadza się z moimi ocenami
compare_before_after.py       jedyny sposób, żeby stwierdzić, czy zmiana promptu pomogła:
                              pipeline pomija oferty, które oceniłem, więc zapisane wyniki
                              dla zbioru testowego nigdy się nie odświeżają
skill_gaps.py                 czego najczęściej brakuje w ofertach, które i tak dobrze pasują
enrich_olx_descriptions.py    dociąga prawdziwą treść ofert OLX, które zostały z zaślepką
```

Interfejs to jeden ekran: po prawej oferty, po lewej szczegóły i przyciski decyzji. Ocena
oferty przenosi ją między zakładkami — zapisane, wysłane, aspiracyjne, odrzucone.

## Uruchomienie

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # wpisz własny klucz Gemini
python run_final_pipeline.py
streamlit run streamlit_app.py

python eval_ranking.py                                    # czy ranking działa?
python skill_gaps.py                                      # czego się nauczyć
PYTHONIOENCODING=utf-8 python tests/integration_test.py   # 57 testów, bez API
```

Przebieg zakończony kodem 0 nie znaczy, że się udał: w logu musi być `PIPELINE COMPLETE`.

## Cztery decyzje warte wyjaśnienia

**Dwa modele, nie jeden.** Tańszy ocenia większość, mocniejszy wchodzi tylko tam, gdzie tańszy
odmówił. To, który model siedzi na którym miejscu, nie jest zgadywane — `benchmark_models.py`
mierzy każdy względem moich ocen i wygrany bierze stanowisko.

**Profil zbudowany na kontrastach.** `generate_preference_profile.py` zamienia historię moich
ocen w opis tego, czego szukam, i wstrzykuje go do promptu. Celowo zawiera pary kontrastowe —
bliźniaczo podobne oferty, którym dałem różne noty — bo to one uczą model rozróżniać, czego
sterta samych dobrych przykładów nigdy nie zrobi.

**Oceny wiedzą, kiedy są nieaktualne.** Każdy zapisany wynik niesie hash opisu, z którego
powstał, i wersję profilu, który go wystawił. Zmiana jednego z nich powoduje ponowną ocenę przy
następnym przebiegu; niczego nie trzeba unieważniać ręcznie.

**Bazą są pliki JSON i to jest decyzja, nie zaniedbanie.** Zapis atomowy z rotacją kopii w
`utils/safe_io.py`; deduplikacja, czyszczenie i normalizacja linków wypisane jako jawne etapy
zamiast odziedziczone po silniku. Jest czytelne i ma policzone dni — 27 MB — a Postgres to
oczywisty następny krok.

Klucze API czytane są wyłącznie z `.env`, który nigdy nie trafia do repozytorium. Dane
osobowe — moje CV i historia moich ocen — też są poza repo; patrz `.gitignore`.

## Stack

Python · Playwright · Gemini API · Streamlit · BeautifulSoup
