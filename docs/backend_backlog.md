# Braki backendu względem designu V2

Elementy z ekranów „V2 dark — …” w `../design/jobfinder.pen`, których interfejs nie pokazywał,
bo backend nie miał danych. Nic z tego nie jest udawane w UI: element jest ukryty albo pokazuje
to, co dane naprawdę mają.

Status: `[ ]` do decyzji · `[x]` zrobione · `[-]` odrzucone (dopisz dlaczego).

## 1. [x] Świeże oferty na liście („+N nowych od pobierania”, kropka przy nowych)

- **Granica:** `last_scrape_run.json` (`{"started_at": ISO}`), zapisywany przez
  `run_final_pipeline.py` na starcie fazy 1 (wznowienie go nie nadpisuje). Nie
  `pipeline_checkpoint.json` — ten znika po udanym przebiegu.
- **Backend:** `/api/offers` — `is_new` na pozycji (`scraped_at >= started_at`), `fresh_count`
  (cała lista, nie strona) i `fresh_since`.
- **Frontend:** `OfferListPanel` — linia `.ol-fresh` nad listą (bez filtra braku) i kropka
  `.ol-row-new` w lewym marginesie wiersza.

## 2. [x] „W skrócie” — słowa kluczowe oferty (`highlights`)

- **Backend:** pole `highlights` (3–5 haseł) w `JobEval` i prompcie `waterfall_analysis.py`,
  w `JobMatch`, zwracane z `get_offer_detail`. Oferty z opisem ~50 znaków dostają pustą listę.
- **Frontend:** neutralne chipy przed lukami; sekcja widoczna, gdy jest cokolwiek z dwóch.
- Stare oceny pola nie mają (UI bez zmian); uzupełnia je dopiero `--rescore-all` (płatne).
- **Pomiar (2 przebiegi vs 2 przebiegi HEAD, ten sam model, 51 ocenionych ofert, ~$0.04):**
  Spearman 0,621 / 0,595 vs 0,663 / 0,603; precision@10 0,52 / 0,20 vs 0,45 / 0,30; MAE
  1,82 / 1,94 vs 1,83 / 1,70. Różnice w granicach rozrzutu powtórek (ρ ~0,06). Wyjście +15%
  (~5,2k → ~6,1k tokenów na 51 ofert).

## 3. [-] Odcisk dopasowania z ocen cząstkowych

- Odrzucone: użytkownikowi nie zależy na płomieniu innym dla każdej oferty, a pole
  `criteria` wydłużało wyjście modelu ×2. Odcisk zostaje z linku (`seedFromLink`).
- **Pomiar przed decyzją (2 × A/B, ten sam model i chwila, 51 ocenionych ofert, ~$0.10):**
  prompt z `highlights` + `criteria` vs HEAD — Spearman 0,663 → 0,627 i 0,603 → 0,551;
  precision@10 0,45 → 0,27 i 0,30 → 0,20; MAE bez zmiany. Rozrzut powtórek tego samego promptu
  jest tego samego rzędu (ρ 0,06), ale oba przebiegi wypadły w tę samą stronę.

## 4. [x] Następny krok na karcie aplikacji

- **Backend:** `next_step` (`{"label", "due": "YYYY-MM-DD HH:MM" | null}`) w obiekcie decyzji;
  `POST /api/offers/next-step` (pusty `label` usuwa; zła data → 400; bez decyzji → 400;
  decyzja w starym formacie zamienia się w obiekt). Przeżywa zmianę etapu i ponowną decyzję.
- **Frontend:** `NextStepEditor` pod `StageSwitch` w karcie oferty (nie dla odrzuconych),
  wiersz `.ab-card-next` na karcie tablicy; po terminie tusz zamiast szarości.

## 5. [x] „Pokaż N ofert” przy braku w „Czego brakuje”

- **Backend:** `gap=` i `gap_threshold=` w `/api/offers` → ten sam indeks co ranking
  (`skill_gaps.collect`, cache per `_rev` i próg). Ranking liczy różne oferty (`offers`),
  nie wystąpienia — liczba na przycisku = długość listy.
- **Frontend:** przycisk `.sg-hero-action` w `SkillGapsPanel` → „Wszystkie” z chipem
  `.ol-filter` „Brakuje: …”; zmiana widoku zdejmuje filtr.

## 6. [x] Liczby etapów w arkuszu pipeline'u

- **Backend:** `pipeline_manager._parse_telemetry` → `telemetry.stages` (usunięte stare,
  linki/scalone, duplikaty, znaki opisów przed/po).
- **Frontend:** kolumny `PipelineSheet`: „−N starych”, „N linków · −M”, „−N duplikatów”,
  „−N% znaków opisu”.

## 7. [x] Szacowany czas do końca oceny w pasku

- **Backend:** `telemetry.scoring.first_batch_at` / `last_done_at`.
- **Frontend:** `scoringEtaSeconds` — tempo z ukończonych paczek; pasek „175 / 412 · ~4:10”
  dopiero po pierwszej ukończonej paczce.

## Tylko frontend (dla porządku, bez backendu)

- [x] Wyjazd oferty z listy po decyzji (w lewo, lista domyka lukę) — zrobione: wiersz zostaje
  chwilę jako „duch” i gaśnie, reszta dojeżdża transformem (FLIP). Tylko przy kliknięciu
  w dok; skróty Z/W/A/X bez animacji.
