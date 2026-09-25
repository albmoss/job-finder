import React from 'react';
import { Play, ArrowRight, Loader2 } from 'lucide-react';
import type { ActivityRow, PipelineState } from '../types';
import { Stream } from './ui/Stream';
import {
  PILL_STREAM_LOOP,
  calculateOverallPipelineProgress,
  formatDuration,
  formatStamp,
  getFreshOffersCount,
  pipelineStatus,
  scoringEtaSeconds,
  summarizeActivity,
  useNowSeconds,
} from '../pipeline';
import '../styles/pipeline.css';

export interface PipelinePillProps {
  pipeline: PipelineState | null;
  activityRows: ActivityRow[];
  onOpenSheet(): void;
  onOpenLaunchModal(): void;
  onStop(): void;
  onResume(): void;
  onShowMatched(): void;
}

export const PipelinePill: React.FC<PipelinePillProps> = ({
  pipeline,
  activityRows,
  onOpenSheet,
  onOpenLaunchModal,
  onStop,
  onResume,
  onShowMatched,
}) => {
  const status = pipelineStatus(pipeline);
  const isRunning = status === 'running';
  const isStopping = status === 'stopping';
  const isFailed = status === 'failed';
  const isStopped = status === 'stopped';
  const isCompleted = status === 'completed';
  const isIdle = status === 'idle';

  const nowSec = useNowSeconds(isRunning || isStopping);
  const { progress, totalPct } = calculateOverallPipelineProgress(pipeline);

  // Pulse przy każdej kolejnej paczce punktacji AI
  const batchPulse = pipeline?.telemetry?.scoring?.processed ?? pipeline?.telemetry?.scoring?.batch ?? 0;

  // Obliczenia napisów dla pigułki
  let stageTitle = 'Gotowy';
  let stageMeta = '';

  const summary = summarizeActivity(activityRows);
  const freshCount = getFreshOffersCount(pipeline, activityRows);

  if (isIdle) {
    stageTitle = 'Gotowy';
    const lastTime = summary.scoring?.time_str ?? summary.scrape?.time_str;
    if (lastTime) {
      const stamp = formatStamp(lastTime);
      stageMeta = freshCount > 0 ? `ostatnio ${stamp} · +${freshCount}` : `ostatnio ${stamp}`;
    } else {
      stageMeta = 'brak przebiegów';
    }
  } else if (isStopping) {
    const b = pipeline?.telemetry?.scoring?.batch;
    stageTitle = b ? `Kończę paczkę ${b}…` : 'Zatrzymywanie…';
    stageMeta = 'nic nie przepadnie';
  } else if (isFailed || isStopped) {
    const curStage = pipeline?.current_stage_title;
    if (isStopped) {
      stageTitle = 'Wstrzymano';
      stageMeta = 'zatrzymano po paczce';
    } else {
      stageTitle = curStage ? `${curStage} przerwane` : 'Błąd etapu';
      stageMeta = pipeline?.error_message || 'pipeline zatrzymany';
    }
  } else if (isCompleted) {
    stageTitle = 'Gotowe';
    const duration =
      pipeline?.started_at && pipeline?.finished_at ? pipeline.finished_at - pipeline.started_at : null;
    stageMeta = freshCount > 0 ? `+${freshCount} nowych · ${formatDuration(duration)}` : `ukończono · ${formatDuration(duration)}`;
  } else if (isRunning) {
    stageTitle = pipeline?.current_stage_title || 'W toku';
    const scoring = pipeline?.telemetry?.scoring;
    const sources = pipeline?.telemetry?.sources;

    if (scoring && scoring.total && scoring.total > 0) {
      const eta = scoringEtaSeconds(scoring, nowSec);
      stageMeta = `${scoring.processed} / ${scoring.total}${eta !== null ? ` · ~${formatDuration(eta)}` : ''}`;
    } else if (sources && sources.length > 0) {
      const doneSources = sources.filter((s) => s.state === 'done' || s.state === 'skipped').length;
      stageMeta = `${doneSources} / ${sources.length} źródeł`;
    } else {
      stageMeta = pipeline?.progress_label || 'przetwarzanie…';
    }
  }

  // Pierścień SVG: obwód r=19 wynosi ok. 119.4
  const circumference = 119.4;
  const strokeDashoffset = isIdle ? circumference : circumference * (1 - totalPct / 100);

  // Akcja przycisku pierścieniowego
  const handleRingClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isIdle) {
      onOpenLaunchModal();
    } else if (isRunning) {
      onStop();
    } else if (isStopping) {
      // noop
    } else if (isFailed || isStopped) {
      if (pipeline?.can_resume) {
        onResume();
      } else {
        onOpenLaunchModal();
      }
    } else if (isCompleted) {
      onShowMatched();
    }
  };

  const ringButtonAriaLabel = isIdle
    ? 'Uruchom pipeline'
    : isRunning
    ? 'Zatrzymaj po paczce'
    : isStopping
    ? 'Zatrzymywanie…'
    : isCompleted
    ? 'Pokaż dopasowane oferty'
    : pipeline?.can_resume
    ? 'Wznów pipeline'
    : 'Uruchom pipeline';

  const ringButtonTip = isIdle
    ? 'Uruchom pipeline'
    : isRunning
    ? 'Zatrzymaj po paczce'
    : isStopping
    ? 'Kończę paczkę…'
    : isCompleted
    ? 'Pokaż nowe oferty'
    : pipeline?.can_resume
    ? 'Wznów pipeline'
    : 'Uruchom pipeline';

  return (
    <div
      className={`pp-pill glass ${isIdle ? 'is-idle' : ''} ${isFailed ? 'is-error' : ''}`}
      onClick={onOpenSheet}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpenSheet();
        }
      }}
      aria-label="Pipeline — otwórz arkusz"
      data-tip="Szczegóły pipeline’u"
      data-kbd="P"
    >
      <div className="pp-stage">
        <b>
          {stageTitle}
          {isRunning && (
            <i className="pp-dots" aria-hidden="true"><i>.</i><i>.</i><i>.</i></i>
          )}
        </b>
        <span>{stageMeta}</span>
      </div>

      <Stream
        progress={isIdle ? 0 : progress}
        pulse={batchPulse}
        loop={isRunning ? PILL_STREAM_LOOP : undefined}
        depth={0.8}
        width={17.68}
        cross={2.4 / 0.54}
        edge={1}
        className="pp-stream"
      />

      <button
        type="button"
        className="pp-ringbtn press"
        onClick={handleRingClick}
        aria-label={ringButtonAriaLabel}
        data-tip={ringButtonTip}
        disabled={isStopping}
      >
        <svg className="pp-ring" viewBox="0 0 40 40" aria-hidden="true">
          <circle cx="20" cy="20" r="19" className="pp-ring-bg" />
          <circle
            cx="20"
            cy="20"
            r="19"
            className="pp-ring-arc"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
          />
        </svg>
        <span className="pp-face">
          {isIdle && <Play className="pp-icon-play" size={14} fill="currentColor" />}
          {isRunning && <span className="pp-stopglyph" />}
          {isStopping && <Loader2 className="pp-spin" size={14} />}
          {(isFailed || isStopped) && (
            <Play className="pp-icon-play" size={14} fill="currentColor" />
          )}
          {isCompleted && <ArrowRight size={14} />}
        </span>
      </button>
    </div>
  );
};
