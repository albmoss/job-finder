# Job Finder

*Every job board in Poland, read overnight and sorted against one CV.*

![Job Finder - offer list and activity log](docs/app.png)

## What this is

A dozen Polish job boards go in, one ranked list comes out. Every offer gets a match score
against my CV, and I decide on it in one click.

```
    job boards              the pipeline                  the interface
    Pracuj, OLX,            run_final_pipeline.py         streamlit run
    justjoin, praca.pl,           |                             |
    aplikuj, gowork, ...          v                             v
          |                deduplicate -> clean ->        offers sorted by match,
          v                score with two models          decided in one click:
    thousands of            (the cheap one first,          saved / applied /
    offers in the DB        the strong one for            aspirational / rejected
                            what it declines)                    |
                                  ^                              |
                                  |                              v
                        preference_profile.json   <------   my ratings
                        built from contrasting pairs
                        of what I rated and how
```

The loop at the bottom is the point: every rating I enter rewrites the prompt that produces
the next ranking.

## How it works

```
main_scraper.py               collects offers from all sources in parallel
   +-- scrapers/              one module per board - the API where there is one, HTML where not
migrate_normalize_links.py    canonicalises URLs; runs before deduplication, because the same
                              offer under ?utm_source=... otherwise counts as two
deduplicate_db.py             removes duplicates - one offer often sits on four boards at once
clean_db.py                   trims descriptions so scoring does not burn tokens on boilerplate
waterfall_analysis.py         scoring: a cheap model takes the bulk, a stronger one picks up
                              what it declines; API keys rotate, an interrupted run resumes
eval_ranking.py               compares the ranking against ratings entered by hand
streamlit_app.py              the interface - browsing, filtering and rating offers
```

`python run_final_pipeline.py` runs the whole thing end to end.

Four scripts sit outside the pipeline and are run by hand:

```
benchmark_models.py           measures how closely each model agrees with my own ratings
compare_before_after.py       the only way to tell whether a prompt change helped - the
                              pipeline skips offers I have rated, so their stored scores
                              never refresh on their own
skill_gaps.py                 what the well-matched offers keep asking for and my CV has not
enrich_olx_descriptions.py    fetches the real text for OLX offers left with a placeholder
```

The interface is a single screen: offers on the right, details and decision buttons on the
left. Rating an offer moves it between tabs - saved, applied, aspirational, rejected.

## Running it

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # your own Gemini API key
python run_final_pipeline.py
streamlit run streamlit_app.py

python eval_ranking.py                                    # is the ranking any good?
python skill_gaps.py                                      # what to learn next
PYTHONIOENCODING=utf-8 python tests/integration_test.py   # 74 tests, no API calls
```

Exit code 0 does not mean the run succeeded - the log has to say `PIPELINE COMPLETE`.

## Notes

- **Two models, not one** - the cheap one scores the bulk, the stronger one picks up what it
  declines. Which model sits where is measured by `benchmark_models.py`, not guessed.
- **The profile is built from contrasts** - near-identical offers I rated differently. Those
  teach the model to tell them apart; a pile of good examples does not.
- **Scores know when they are stale** - each one stores the description hash and profile
  version behind it, and is re-scored when either changes.
- **Offers the board has taken down** sink to the bottom of the list with a badge, rather
  than being deleted - the signal is `last_seen`, and it is a heuristic, not a certainty.
- **The database is JSON files**, with atomic writes and backup rotation in `utils/safe_io.py`.
  Readable and 27 MB; Postgres is the obvious next step.
- **Keys and personal data stay out of the repo** - keys come from `.env`, my CV and rating
  history are gitignored.

## Stack

Python - Playwright - Gemini API - Streamlit - BeautifulSoup

---
---

# Job Finder

*Wszystkie polskie portale pracy, przeczytane w nocy i ułożone pod jedno CV.*

![Job Finder - lista ofert i log ostatnich zdarzeń](docs/app.png)

## Czym to jest

Na wejściu kilkanaście polskich portali, na wyjściu jedna lista. Każda oferta dostaje procent
dopasowania do mojego CV, a decyzję podejmuję jednym kliknięciem.

```
    portale pracy           pipeline                      interfejs
    Pracuj, OLX,            run_final_pipeline.py         streamlit run
    justjoin, praca.pl,           |                             |
    aplikuj, gowork, ...          v                             v
          |                deduplikacja -> czyszczenie -> oferty ułożone wg
          v                ocena dwoma modelami           dopasowania, decyzja
    tysiące ofert          (najpierw tańszy,              jednym kliknięciem:
    w bazie                 mocniejszy tam, gdzie         zapisane / wysłane /
                            tańszy odmówił)               aspiracyjne / odrzucone
                                  ^                              |
                                  |                              v
                        preference_profile.json   <------   moje oceny
                        zbudowany z par kontrastowych:
                        co oceniłem i jak
```

Pętla na dole jest tu sednem: każda moja ocena przebudowuje prompt, z którego powstanie
następny ranking.

## Jak to działa

```
main_scraper.py               zbiera oferty ze wszystkich źródeł równolegle
   +-- scrapers/              jeden moduł na portal - API tam, gdzie jest, HTML tam, gdzie nie
migrate_normalize_links.py    ujednolica adresy; idzie przed deduplikacją, bo bez tego ta sama
                              oferta pod ?utm_source=... liczyłaby się jako dwie różne
deduplicate_db.py             usuwa duplikaty - jedna oferta bywa naraz na czterech portalach
clean_db.py                   skraca opisy, żeby ocena nie przepalała tokenów na wypełniaczu
waterfall_analysis.py         ocena: tańszy model bierze większość, mocniejszy wchodzi tam,
                              gdzie tańszy odmówił; klucze rotują, przerwany bieg się wznawia
eval_ranking.py               porównuje ranking z ocenami wystawionymi ręcznie
streamlit_app.py              interfejs - przeglądanie, filtrowanie i ocenianie ofert
```

`python run_final_pipeline.py` przechodzi całość od początku do końca.

Cztery skrypty stoją poza pipelinem i uruchamia się je ręcznie:

```
benchmark_models.py           mierzy, jak bardzo każdy model zgadza się z moimi ocenami
compare_before_after.py       jedyny sposób, żeby stwierdzić, czy zmiana promptu pomogła -
                              pipeline pomija oferty, które oceniłem, więc ich zapisane
                              wyniki nigdy nie odświeżają się same
skill_gaps.py                 czego najczęściej brakuje w ofertach, które i tak dobrze pasują
enrich_olx_descriptions.py    dociąga prawdziwą treść ofert OLX, które zostały z zaślepką
```

Interfejs to jeden ekran: po prawej oferty, po lewej szczegóły i przyciski decyzji. Ocena
oferty przenosi ją między zakładkami - zapisane, wysłane, aspiracyjne, odrzucone.

## Uruchomienie

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # własny klucz Gemini
python run_final_pipeline.py
streamlit run streamlit_app.py

python eval_ranking.py                                    # czy ranking działa?
python skill_gaps.py                                      # czego się nauczyć
PYTHONIOENCODING=utf-8 python tests/integration_test.py   # 74 testy, bez API
```

Przebieg zakończony kodem 0 nie znaczy, że się udał - w logu musi być `PIPELINE COMPLETE`.

## Uwagi

- **Dwa modele, nie jeden** - tańszy ocenia większość, mocniejszy wchodzi tam, gdzie tańszy
  odmówił. Który model gdzie siedzi, mierzy `benchmark_models.py`, nie zgadywanie.
- **Profil zbudowany na kontrastach** - bliźniaczo podobne oferty, którym dałem różne noty.
  To one uczą model rozróżniać; sterta samych dobrych przykładów tego nie zrobi.
- **Oceny wiedzą, kiedy są nieaktualne** - każda niesie hash opisu i wersję profilu, z których
  powstała, i jest wystawiana od nowa, gdy któreś się zmieni.
- **Oferty zdjęte z portalu** schodzą na dół listy z plakietką, zamiast znikać - sygnałem jest
  `last_seen`, a to heurystyka, nie pewnik.
- **Bazą są pliki JSON**, z zapisem atomowym i rotacją kopii w `utils/safe_io.py`. Czytelne
  i ma 27 MB; Postgres to oczywisty następny krok.
- **Klucze i dane osobowe zostają poza repozytorium** - klucze idą z `.env`, moje CV i historia
  ocen są w `.gitignore`.

## Stack

Python - Playwright - Gemini API - Streamlit - BeautifulSoup
