import { ChevronRight, Pause } from 'lucide-react';
import { useApp } from '../app_context';
import { countFormat } from '../format';
import { COMPARED, DOWNLOADED, OFFERS, plural } from '../plural';
import { paths } from '../router';
import {
  STAGE_TITLE,
  currentStageId,
  elapsedSeconds,
  formatClock,
  scoringProgress,
  useNow,
} from '../run_progress';
import type { PipelineState, RunSummary } from '../types';
import { Meter } from './Meter';

function describeRun(pipeline: PipelineState, summary: RunSummary | null) {
  const stageId = currentStageId(pipeline);
  const title = STAGE_TITLE[stageId] ?? 'Szukanie ofert';
  const scoring = stageId === 'phase3' ? scoringProgress(pipeline) : null;
  const progress = scoring?.fraction ?? null;
  if (pipeline.status === 'stopping') return { title, detail: 'Zatrzymywanie…', progress };
  if (scoring && scoring.total != null) {
    return {
      title,
      detail: `${countFormat.format(scoring.scored)} z ${countFormat.format(scoring.total)} ${plural(scoring.total, COMPARED)}`,
      progress,
    };
  }
  const downloaded = summary?.downloaded;
  if (downloaded != null) {
    return {
      title,
      detail: `${countFormat.format(downloaded)} ${plural(downloaded, OFFERS)} ${plural(downloaded, DOWNLOADED)}`,
      progress,
    };
  }
  return { title, detail: pipeline.current_stage_idx > 0 ? 'Profil odczytany' : 'Czytamy Twoje CV', progress };
}

export function RunPill() {
  const { pipeline, runSummary, stopSearch } = useApp();
  const now = useNow(true);
  if (!pipeline) return null;
  const { title, detail, progress } = describeRun(pipeline, runSummary);
  const stopping = pipeline.status === 'stopping';
  const elapsed = elapsedSeconds(pipeline, now);

  return (
    <div className="tb-run glass" role="status" aria-label="Wyszukiwanie ofert">
      <a className="tb-run-link" href={`#${paths.progress}`} title="Szczegóły wyszukiwania">
        <span className="spinner tb-run-spinner" aria-hidden="true" />
        <span className="tb-run-text">
          <span className="tb-run-row">
            <span className="tb-run-title">{title}</span>
            {elapsed != null && <span className="tb-run-clock">{formatClock(elapsed)}</span>}
          </span>
          <span className="tb-run-detail">{detail}</span>
          <Meter value={progress} className="tb-run-meter" />
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
