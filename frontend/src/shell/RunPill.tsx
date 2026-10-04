import { ChevronRight, Pause } from 'lucide-react';
import { useApp } from '../app_context';
import { countFormat } from '../format';
import { COMPARED, DOWNLOADED, OFFERS, plural } from '../plural';
import { paths } from '../router';
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

function describeRun(pipeline: PipelineState, summary: RunSummary | null) {
  const stageId = pipeline.stages[pipeline.current_stage_idx]?.id ?? '';
  const title = STAGE_TITLE[stageId] ?? 'Szukanie ofert';
  if (pipeline.status === 'stopping') return { title, detail: 'Zatrzymywanie…' };
  if (summary && summary.to_check != null && (stageId === 'phase3' || stageId === 'phase4')) {
    const n = summary.to_check;
    return {
      title,
      detail: `${countFormat.format(summary.checked)} z ${countFormat.format(n)} ${plural(n, COMPARED)}`,
    };
  }
  const downloaded = summary?.downloaded;
  if (downloaded != null) {
    return {
      title,
      detail: `${countFormat.format(downloaded)} ${plural(downloaded, OFFERS)} ${plural(downloaded, DOWNLOADED)}`,
    };
  }
  return { title, detail: pipeline.current_stage_idx > 0 ? 'Profil odczytany' : 'Czytamy Twoje CV' };
}

export function RunPill() {
  const { pipeline, runSummary, stopSearch } = useApp();
  if (!pipeline) return null;
  const { title, detail } = describeRun(pipeline, runSummary);
  const stopping = pipeline.status === 'stopping';

  return (
    <div className="tb-run glass" role="status" aria-label="Wyszukiwanie ofert">
      <a className="tb-run-link" href={`#${paths.progress}`} title="Szczegóły wyszukiwania">
        <span className="tb-run-text">
          <span className="tb-run-title">{title}</span>
          <span className="tb-run-detail">{detail}</span>
        </span>
        <ChevronRight />
      </a>
      <button
        type="button"
        className="btn-quiet tb-run-stop"
        disabled={stopping}
        onClick={stopSearch}
        aria-label="Zatrzymaj wyszukiwanie"
        title="Zatrzymaj"
      >
        <Pause />
      </button>
    </div>
  );
}
