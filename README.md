<div align="center">

# Job Finder

**Every job board in Poland, read overnight and ranked against one CV.**

![Python](https://img.shields.io/badge/Python-5A70FF?style=flat-square&logo=python&logoColor=white)
![Gemini API](https://img.shields.io/badge/Gemini_API-8B5CFF?style=flat-square&logo=googlegemini&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-5A70FF?style=flat-square)
![Starlette](https://img.shields.io/badge/Starlette-5A70FF?style=flat-square)
![React 19](https://img.shields.io/badge/React_19-5A70FF?style=flat-square&logo=react&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5A70FF?style=flat-square&logo=typescript&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-5A70FF?style=flat-square&logo=vite&logoColor=white)

[How it works](#how-it-works) · [Pipeline](#pipeline) · [Quick start](#quick-start) · [Design notes](#design-notes)

<br>

<img src="docs/app.png" alt="Offer list and offer detail with match score and decision dock" width="920">

</div>

<br>

## How it works

Ten job boards go in, one ranked list comes out. Every offer gets a match score against my
CV, and I decide on it in one click. Those decisions feed back into the prompt that scores
the next batch.

```mermaid
flowchart LR
    boards["10 job<br/>boards"] --> clean["Clean<br/>& dedupe"]
    clean --> score["Gemini<br/>scoring"]
    score --> ui["Ranked<br/>list"]
    ui --> ratings(["My<br/>ratings"])
    ratings -. "contrasting pairs" .-> profile[("Preference<br/>profile")]
    profile -. "rewrites the prompt" .-> score

    classDef step fill:#161a33,stroke:#5A70FF,stroke-width:1.5px,color:#ffffff
    classDef me fill:#241a3d,stroke:#8B5CFF,stroke-width:1.5px,color:#ffffff
    class boards,clean,score,ui step
    class ratings,profile me
```

## Pipeline

One command, seven stages, always in this order. A failed stage stops the run, and
`--resume` picks up where it stopped.

| # | Stage | Why it is there |
|:-:|---|---|
| 0 | `purge_stale_offers` | archives my ratings before old offers are dropped |
| 1 | `main_scraper` | pulls every board in parallel: the API where there is one, HTML where not |
| 1.5 | `migrate_normalize_links` | the same offer under `?utm_source=…` must not count twice |
| 2 | `deduplicate_db` | one offer often sits on four boards at once |
| 2.5 | `clean_db` | trims boilerplate so scoring does not burn tokens on it |
| 3 | `waterfall_analysis` | cheap model first, stronger one for what it declines; API keys rotate |
| 4 | `eval_ranking` | checks the ranking against ratings I entered by hand |

<div align="center">
<img src="docs/pipeline.png" alt="Pipeline sheet: stage counts, scoring batches, API keys and live log" width="640">
<br>
<sub>The pipeline sheet in the UI: per-stage counts, scoring batches, key rotation, live log.</sub>
</div>

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env                      # your Gemini API key(s)

cd frontend && npm install && npm run build && cd ..
PYTHONIOENCODING=utf-8 python server.py   # → http://127.0.0.1:8501
```

Runs start from the UI, which checks the CV and keys and asks for API cost consent first.

<details>
<summary><b>Command line</b></summary>

<br>

```bash
python run_final_pipeline.py                  # full run
python run_final_pipeline.py --skip-scraping  # score offers already in the DB (still paid)
python run_final_pipeline.py --resume         # continue an interrupted or failed run

python eval_ranking.py                        # is the ranking any good?
python skill_gaps.py                          # what the good offers ask for and the CV lacks
python benchmark_models.py                    # which model agrees with my ratings
python compare_before_after.py                # did a prompt change help?

PYTHONIOENCODING=utf-8 python tests/integration_test.py   # no API calls
```

Success is exit code 0 **and** `PIPELINE COMPLETE`. For frontend work, `npm run dev` in
`frontend/` serves on port 5173 and proxies `/api` to `server.py`.

</details>

## Design notes

- **Two models, not one.** The cheap one scores the bulk, the stronger one takes what it
  declines. `benchmark_models.py` decides which sits where.
- **The profile is built from contrasts.** Near-identical offers I rated differently teach the
  model more than a pile of good examples.
- **Scores are never silently redone.** Existing scores stay; `--rescore-changed` and
  `--rescore-all` are explicit, because every rescore costs money.
- **Stopping is safe.** A stop lands between stages or scoring batches of 75; saved batches
  are never scored twice.
- **The database is JSON files** with atomic writes and backup rotation. Postgres is the
  obvious next step.
- **Personal data stays local.** Keys live in `.env`; the CV, ratings and decisions are
  gitignored.
