import { ChevronDown, Pause } from 'lucide-react';
import { useApp } from '../app_context';
import { countFormat } from '../format';
import { DOWNLOADED, OFFERS, REMAINING, plural } from '../plural';
import { navigate, paths } from '../router';
import type { PipelineState, RunSummary } from '../types';

const STAGE_TITLE: Record<string, string> = {
  phase0: 'Odczytywanie CV',
  phase1: 'Pobieranie ofert',
  phase1_5: 'Porządkowanie ofert',
  phase2: 'Porządkowanie ofert',
  phase2_5: 'Porządkowanie ofert',
  phase3: 'Porównywanie z CV',
  phase4: 'Porównywanie z CV',
};

/** Tytuł i trzy linie stanu przebiegu (karta 09 „Porównywanie z CV”). */
function describeRun(pipeline: PipelineState, summary: RunSummary | null) {
  const stageId = pipeline.stages[pipeline.current_stage_idx]?.id ?? '';
  const title = STAGE_TITLE[stageId] ?? 'Szukanie ofert';
  const downloaded = summary?.downloaded;
  const stageLine = [
    pipeline.current_stage_idx > 0 ? 'Profil odczytany' : null,
    downloaded != null
      ? `${countFormat.format(downloaded)} ${plural(downloaded, OFFERS)} ${plural(downloaded, DOWNLOADED)}`
      : null,
  ]
    .filter(Boolean)
    .join(' · ');
  let countLine = '';
  let restLine = '';
  if (summary && summary.to_check != null) {
    countLine = `${countFormat.format(summary.checked)} z ${countFormat.format(summary.to_check)} porównanych z CV`;
    const left = Math.max(0, summary.to_check - summary.checked);
    restLine = `${plural(left, REMAINING)} ${countFormat.format(left)} ${plural(left, OFFERS)}.`;
  }
  return { title, stageLine, countLine, restLine };
}

export function MiniPipelineCard() {
  const { pipeline, runSummary, stopSearch } = useApp();
  if (!pipeline) return null;
  const { title, stageLine, countLine, restLine } = describeRun(pipeline, runSummary);
  const stopping = pipeline.status === 'stopping';

  return (
    <section className="mc panel" aria-label="Wyszukiwanie ofert">
      <h2 className="title-md">{title}</h2>
      {stageLine && <p className="mc-line">{stageLine}</p>}
      {countLine && <p className="mc-count">{countLine}</p>}
      {restLine && <p className="mc-line">{restLine}</p>}
      <div className="mc-actions">
        <button type="button" className="btn btn-sm" disabled={stopping} onClick={stopSearch}>
          <Pause />
          {stopping ? 'Zatrzymywanie…' : 'Zatrzymaj'}
        </button>
        <button type="button" className="btn btn-sm" onClick={() => navigate(paths.progress)}>
          <ChevronDown />
          Szczegóły
        </button>
      </div>
    </section>
  );
}
