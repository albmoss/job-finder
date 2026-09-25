# Job Finder

*Every job board in Poland, read overnight and sorted against one CV.*

![Job Finder - offer list, offer detail with match fingerprint and decision dock](docs/app.png)

## What this is

A dozen Polish job boards go in, one ranked list comes out. Every offer gets a match score
against my CV, and I decide on it in one click.

```
    job boards              the pipeline                  the interface
    Pracuj, OLX,            run_final_pipeline.py         python server.py (React/Vite)
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
purge_stale_offers.py         archives manual ratings before old offers are dropped
main_scraper.py               collects offers from all sources in parallel
   +-- scrapers/              one module per board - the API where there is one, HTML where not
migrate_normalize_links.py    canonicalises URLs; runs before deduplication, because the same
                              offer under ?utm_source=... otherwise counts as two
deduplicate_db.py             merges duplicates - one offer often sits on four boards at once
clean_db.py                   trims descriptions so scoring does not burn tokens on boilerplate
waterfall_analysis.py         scoring: a cheap model takes the bulk, a stronger one picks up
                              what it declines; API keys rotate, an interrupted run resumes
eval_ranking.py               compares the ranking against ratings entered by hand
```

`python run_final_pipeline.py` runs these stages in this order, end to end.

The interface is a local app:

```
server.py                     Starlette/Uvicorn backend: JSON API, pipeline control, serves the UI
pipeline_manager.py           starts, stops and resumes the pipeline process; parses its log
                              into live telemetry (stage counts, batches, key rotation, ETA)
app_services.py               offers, decisions, applications and CV handling behind the API
frontend/                     React 19 + TypeScript + Vite
```

Four scripts sit outside the pipeline and are run by hand:

```
benchmark_models.py           measures how closely each model agrees with my own ratings
compare_before_after.py       the only way to tell whether a prompt change helped - the
                              pipeline skips offers I have rated, so their stored scores
                              never refresh on their own
skill_gaps.py                 what the well-matched offers keep asking for and my CV has not
enrich_olx_descriptions.py    fetches the real text for OLX offers left with a placeholder
```

## The interface

Desktop only, dark theme.

- **Top bar** - logo, categories as icons (matched, all, rated, saved, aspirational,
  rejected, applications, skill gaps, add from link) and the pipeline bar.
- **Offer list** - sorted by match; fresh offers since the last scrape are marked. Decision
  tabs show my own rating instead of the AI score.
- **Offer detail** - a match fingerprint drawn per offer, keyword highlights and gaps against
  the CV, the full description, and a decision dock: save / applied / aspirational / reject
  plus a 1-10 rating. Keyboard: `Z` `W` `A` `X` for decisions, `1`-`0` for the rating.
- **Applications** - a recruitment board (saved → applied → interview → offer, plus
  archive); cards are dragged between stages and carry a next step with a due date.
- **Skill gaps** - what well-matched offers ask for and the CV lacks, with a jump to the
  offers behind each gap.
- **Add from link** - fetches an offer from a URL and saves it into the database.
- **Pipeline bar** - current stage and progress from any view. It unfolds into a sheet with
  per-stage counts, scoring batches, API key rotation, the live log and single steps. Every
  run starts from a launch dialog with prerequisite checks (CV, API keys, scrapers - editable
  in place), a run mode (full or scoring only) and explicit API cost consent.
- **Help** (`?`) - shortcuts and explanations.

## Running it

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env                      # your own Gemini API key(s)

cd frontend && npm install && npm run build && cd ..
PYTHONIOENCODING=utf-8 python server.py   # http://127.0.0.1:8501
```

For frontend work, `npm run dev` in `frontend/` serves on port 5173 and proxies `/api` to
the running `server.py`.

The pipeline can also run without the UI:

```bash
python run_final_pipeline.py                  # full run
python run_final_pipeline.py --skip-scraping  # score offers already in the DB (still paid)
python run_final_pipeline.py --resume         # continue an interrupted or failed run

python eval_ranking.py                        # is the ranking any good?
python skill_gaps.py                          # what to learn next
PYTHONIOENCODING=utf-8 python tests/integration_test.py   # integration checks, no API calls
```

Success means both exit code 0 and `PIPELINE COMPLETE`; a failed stage prints
`PIPELINE INCOMPLETE` and exits non-zero, a stopped run prints `PIPELINE STOPPED`. The
pipeline halts at the first failed stage. `pipeline_checkpoint.json` keeps completed stages
and options, so `--resume` (or Resume in the UI) skips finished phases.

### Stopping a run

- **Cooperative stop** is checked before every stage and at every scoring batch boundary
  (75 offers). A batch already sent to Gemini finishes first, typically 3-15 s.
- **Forced stop** (a second click in the UI) kills the process tree immediately. It is only
  ever an explicit user action, never automatic during a save.
- **Resume** skips batches already written to disk. A batch that was in flight during a
  forced stop is scored again.

## Notes

- **Two models, not one** - the cheap one scores the bulk, the stronger one picks up what it
  declines. Which model sits where is measured by `benchmark_models.py`, not guessed.
- **The profile is built from contrasts** - near-identical offers I rated differently. Those
  teach the model to tell them apart; a pile of good examples does not.
- **Existing scores are kept** - a normal run never re-scores what is already scored;
  `--rescore-changed` re-scores offers whose description changed, `--rescore-all` starts over.
- **Offers the board has taken down** sink to the bottom of the list with a badge, rather
  than being deleted - the signal is `last_seen`, and it is a heuristic, not a certainty.
- **The database is JSON files**, with atomic writes and backup rotation in `utils/safe_io.py`.
  Readable and a few dozen MB; Postgres is the obvious next step.
- **Keys and personal data stay out of the repo** - keys come from `.env`; the CV, ratings
  and decision history are gitignored.

## Stack

Python - Playwright - Gemini API - Starlette / Uvicorn - React / TypeScript (Vite) - BeautifulSoup
