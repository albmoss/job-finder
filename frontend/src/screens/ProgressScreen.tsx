import type { CSSProperties } from 'react';
import { ArrowLeft, Check, FileUser, LayoutList, Minus, Sparkles, Square, TriangleAlert } from 'lucide-react';
import { useApp } from '../app_context';
import { countFormat } from '../format';
import { COMPARED, DOWNLOADED, OFFERS, plural } from '../plural';
import { navigate, paths } from '../router';
import {
  GROUPS,
  RUNNING_PHASES,
  activityFeed,
  currentStageId,
  elapsedSeconds,
  formatClock,
  formatDuration,
  formatEta,
  groupSpan,
  groupState,
  isBusy,
  runPhase,
  scoringProgress,
  sourceProblem,
  useNow,
  type GroupState,
  type Phase,
  type ScoringProgress,
} from '../run_progress';
import { Meter } from '../shell/Meter';
import type { PipelineState, PrefilterReasons, RunSummary, SourceTelemetry } from '../types';
import { locationLine } from './offer/offer_format';
import type { ScreenProps } from './types';
import '../styles/progress.css';

const TITLE: Record<Phase, string> = {
  profile: 'Czytamy Twoje CV',
  scraping: 'Szukamy ofert',
  matching: 'Porównujemy oferty z CV',
  done: 'Wyszukiwanie zakończone',
  failed: 'Wyszukiwanie przerwane',
  stopped: 'Wyszukiwanie zatrzymane',
  idle: 'Brak wyszukiwania w toku',
};

const CLEANUP_STAGES = ['phase1_5', 'phase2', 'phase2_5'];

const REASON_LABEL: Record<keyof PrefilterReasons, string> = {
  kierunek: 'inna dziedzina',
  poziom: 'poziom stanowiska',
  jezyk: 'język',
  miasto: 'inne miasto',
  lata: 'wymagane lata',
};

const n = (value: number) => countFormat.format(value);
const rateFormat = new Intl.NumberFormat('pl-PL', { maximumFractionDigits: 1 });

function subtitle(phase: Phase, pipeline: PipelineState, summary: RunSummary | null): string {
  switch (phase) {
    case 'profile':
      return 'Pierwszy krok, zanim ruszy pobieranie ofert.';
    case 'scraping':
      return CLEANUP_STAGES.includes(currentStageId(pipeline))
        ? 'Portale skończone. Porządkujemy pobrane oferty: linki, duplikaty i opisy.'
        : 'Przeglądamy portale z ofertami. To najdłuższy etap wyszukiwania.';
    case 'matching':
      return 'Przesiew odrzuca oferty spoza Twojego profilu, resztę porównujemy z CV.';
    case 'failed':
      return pipeline.error_message ?? 'Przebieg zakończył się błędem.';
    default: {
      if (!summary) return '';
      const compared = `${n(summary.checked)} ${plural(summary.checked, OFFERS)} ${plural(summary.checked, COMPARED)} z Twoim CV.`;
      return summary.downloaded != null
        ? `${n(summary.downloaded)} ${plural(summary.downloaded, OFFERS)} ${plural(summary.downloaded, DOWNLOADED)}, ${compared}`
        : compared;
    }
  }
}

function stepStatus(index: number, state: GroupState, pipeline: PipelineState, summary: RunSummary | null): string {
  if (state === 'failed') return 'Błąd';
  if (state === 'stopped') return 'Zatrzymane';
  if (state === 'pending') return 'Czeka';
  if (state === 'running') {
    if (index === 1) return CLEANUP_STAGES.includes(currentStageId(pipeline)) ? 'Porządkowanie ofert' : 'Pobieranie z portali';
    if (index === 2) {
      const scoring = scoringProgress(pipeline);
      return scoring?.total != null ? `${n(scoring.scored)} z ${n(scoring.total)}` : 'Przesiew ofert';
    }
    return 'W toku';
  }
  if (index === 0) return 'Profil odczytany';
  if (index === 1 && summary?.downloaded != null) {
    return `${n(summary.downloaded)} ${plural(summary.downloaded, OFFERS)} ${plural(summary.downloaded, DOWNLOADED)}`;
  }
  if (index === 2 && summary) return `${n(summary.checked)} ${plural(summary.checked, OFFERS)} ${plural(summary.checked, COMPARED)}`;
  return 'Gotowe';
}

function StepMark({ state, num }: { state: GroupState; num: string }) {
  if (state === 'running') return <span className="spinner pg-step-spinner" aria-hidden="true" />;
  if (state === 'done') return <Check className="pg-step-check" />;
  if (state === 'failed') return <TriangleAlert className="pg-step-alert" />;
  return <span className="pg-step-num">{num}</span>;
}

export function ProgressScreen(_props: ScreenProps) {
  const { pipeline, runSummary: summary, stopSearch } = useApp();
  const now = useNow(isBusy(pipeline));
  if (!pipeline) return <div className="pg" />;

  const states = GROUPS.map((g) => groupState(pipeline, g.ids));
  const phase = runPhase(pipeline, states);
  const running = RUNNING_PHASES.includes(phase);
  const stopping = pipeline.status === 'stopping';
  const sub = subtitle(phase, pipeline, summary);
  const elapsed = elapsedSeconds(pipeline, now);
  const scoring = scoringProgress(pipeline);
  const sources = pipeline.telemetry?.sources ?? [];
  const view: 'profile' | 'scraping' | 'matching' = running
    ? (phase as 'profile' | 'scraping' | 'matching')
    : scoring
      ? 'matching'
      : sources.length
        ? 'scraping'
        : 'profile';
  const feed = activityFeed(pipeline.events, 12);
  const recent = summary?.recent.slice(0, 3) ?? [];

  return (
    <div className="pg">
      <header className="pg-head">
        <div className="pg-head-copy">
          <p className="pg-eyebrow">
            {running && <span className="live-dot" aria-hidden="true" />}
            WYSZUKIWANIE OFERT
          </p>
          <h1 className="pg-title">{TITLE[phase]}</h1>
          {sub && <p className={`pg-sub${phase === 'failed' ? ' warn' : ''}`}>{sub}</p>}
        </div>
        <dl className="pg-stats glass">
          <div className="pg-stat">
            <dt>Czas</dt>
            <dd>{elapsed != null ? formatClock(elapsed) : '–'}</dd>
          </div>
          <div className="pg-stat">
            <dt>Nowe oferty</dt>
            <dd>{summary?.downloaded != null ? n(summary.downloaded) : '–'}</dd>
          </div>
          <div className="pg-stat">
            <dt>Dopasowane</dt>
            <dd>{summary ? n(summary.matched_count) : '–'}</dd>
          </div>
        </dl>
      </header>

      <ol className="pg-steps">
        {GROUPS.map((g, i) => {
          const state = states[i];
          const span = groupSpan(pipeline, g.ids);
          const time =
            state === 'running' && span.start != null
              ? formatClock(now / 1000 - span.start)
              : state === 'done' && span.start != null && span.end != null
                ? formatDuration(span.end - span.start)
                : null;
          return (
            <li key={g.num} className={`pg-step is-${state}`}>
              <span className="pg-step-mark">
                <StepMark state={state} num={g.num} />
              </span>
              <div className="pg-step-copy">
                <p className="pg-step-label">{g.label}</p>
                <p className="pg-step-status">{stepStatus(i, state, pipeline, summary)}</p>
              </div>
              {time && <span className="pg-step-time">{time}</span>}
              {state === 'running' && <Meter value={i === 2 ? (scoring?.fraction ?? null) : null} className="pg-step-meter" />}
            </li>
          );
        })}
      </ol>

      <section className="pg-panel panel">
        {view === 'profile' && <ProfileView running={running} />}
        {view === 'scraping' && <SourcesView sources={sources} running={running} />}
        {view === 'matching' && (
          <MatchingView scoring={scoring} running={running} summary={phase === 'done' ? summary : null} />
        )}
        <div className="pg-sep" />
        <aside className="pg-side">
          <section className="pg-feed">
            <h2 className="pg-side-title">Na bieżąco</h2>
            {feed.length > 0 ? (
              <ol className="pg-feed-list">
                {feed.map((item) => (
                  <li key={item.key} className={`pg-feed-item${item.warn ? ' is-warn' : ''}`}>
                    <span className="pg-feed-time">{item.time ?? ''}</span>
                    <span className="pg-feed-text" title={item.text}>
                      {item.text}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="pg-side-empty">Tu pojawią się kolejne kroki wyszukiwania.</p>
            )}
          </section>
          <section className="pg-best">
            <div className="pg-best-head">
              <h2 className="pg-side-title">Najlepsze dopasowania</h2>
              {summary && summary.matched_count > 0 && (
                <span className="pg-best-total">
                  {n(summary.matched_count)} {plural(summary.matched_count, OFFERS)}
                </span>
              )}
            </div>
            {recent.length > 0 ? (
              recent.map((offer, index) => (
                <button
                  key={offer.link}
                  type="button"
                  className="pg-row"
                  style={{ '--i': index } as CSSProperties}
                  onClick={() => navigate(paths.matched(offer.link))}
                >
                  <span className="pg-row-id">
                    <span className="pg-row-title">{offer.title}</span>
                    <span className="pg-row-meta">
                      {[offer.company, locationLine(offer.location, offer.work_mode)].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  {offer.match_percentage != null && (
                    <span className="pg-row-pct">{Math.round(offer.match_percentage)}%</span>
                  )}
                </button>
              ))
            ) : (
              <div className="pg-best-empty">
                <Sparkles />
                <p>Pierwsze dopasowania pojawią się tutaj.</p>
              </div>
            )}
          </section>
        </aside>
      </section>

      <footer className="pg-actions">
        {running ? (
          <>
            <button type="button" className="btn" onClick={() => navigate(paths.matched())}>
              <LayoutList />
              Przeglądaj oferty w tle
            </button>
            <p className="pg-actions-copy">Wyszukiwanie będzie trwać dalej, a postęp zobaczysz w górnym pasku.</p>
            <button type="button" className="btn" disabled={stopping} onClick={stopSearch}>
              <Square />
              {stopping ? 'Zatrzymywanie…' : 'Zatrzymaj'}
            </button>
          </>
        ) : phase === 'failed' ? (
          <>
            <p className="pg-actions-copy" />
            <button type="button" className="btn" onClick={() => navigate(paths.matched())}>
              <ArrowLeft />
              Wróć do ofert
            </button>
          </>
        ) : (
          <>
            <p className="pg-actions-copy" />
            <button type="button" className="btn btn-primary" onClick={() => navigate(paths.matched())}>
              Pokaż oferty
            </button>
          </>
        )}
      </footer>
    </div>
  );
}

function ProfileView({ running }: { running: boolean }) {
  return (
    <div className="pg-live pg-profile">
      <span className={`pg-orb${running ? ' is-live' : ''}`} aria-hidden="true">
        <FileUser />
      </span>
      <p className="pg-profile-title">{running ? 'Odczytujemy profil z CV' : 'Profil z CV'}</p>
      <p className="pg-profile-copy">
        Z CV bierzemy miasto, poziom i umiejętności. Na tej podstawie zapytamy portale o oferty i odsiejemy te, które
        nie pasują.
      </p>
    </div>
  );
}

const SOURCE_ORDER: Record<SourceTelemetry['state'], number> = { running: 0, failed: 1, done: 2, stopped: 3, skipped: 4 };

function sourceStatus(source: SourceTelemetry): string {
  const problem = sourceProblem(source);
  if (problem) return problem;
  if (source.state === 'skipped') return 'Pominięty';
  if (source.state === 'stopped') return 'Zatrzymane';
  if (source.state === 'done') return 'Gotowe';
  if (source.details_total) return `Opisy ofert: ${n(source.details_done ?? 0)} z ${n(source.details_total)}`;
  return 'Zbieramy listę ofert';
}

function SourcesView({ sources, running }: { sources: SourceTelemetry[]; running: boolean }) {
  const sorted = sources
    .map((source, index) => ({ source, index }))
    .sort((a, b) => SOURCE_ORDER[a.source.state] - SOURCE_ORDER[b.source.state] || a.index - b.index);
  const active = sources.filter((s) => s.state === 'running').length;
  const finished = sources.filter((s) => s.state !== 'running').length;
  const failed = sources.filter((s) => s.state === 'failed').length;
  const meta = [
    active ? `w toku ${active}` : null,
    `gotowe ${finished} z ${sources.length}`,
    failed ? `z błędem ${failed}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="pg-live">
      <div className="pg-live-head">
        <h2 className="pg-live-title">Portale</h2>
        {sources.length > 0 && <p className="pg-live-meta">{meta.charAt(0).toUpperCase() + meta.slice(1)}</p>}
      </div>
      {sources.length > 0 ? (
        <div className="pg-sources scroll">
          {sorted.map(({ source }, index) => {
            const fraction =
              source.details_total && source.details_done != null ? source.details_done / source.details_total : null;
            return (
              <div
                key={source.name}
                className={`pg-src is-${source.state}${sourceProblem(source) ? ' has-problem' : ''}`}
                style={{ '--i': Math.min(index, 8) } as CSSProperties}
              >
                <span className="pg-src-mark">
                  {source.state === 'running' && <span className="spinner" aria-hidden="true" />}
                  {source.state === 'done' && (source.health ? <TriangleAlert /> : <Check />)}
                  {source.state === 'failed' && <TriangleAlert />}
                  {(source.state === 'skipped' || source.state === 'stopped') && <Minus />}
                </span>
                <span className="pg-src-id">
                  <span className="pg-src-name">{source.name}</span>
                  <span className="pg-src-status" title={source.error ?? undefined}>
                    {sourceStatus(source)}
                  </span>
                </span>
                <span className="pg-src-nums">
                  {source.found != null && (
                    <span className="pg-src-found">
                      {n(source.found)} {plural(source.found, OFFERS)}
                    </span>
                  )}
                  {source.added != null && (
                    <span className="pg-src-added">{n(source.added)} nowe w bazie</span>
                  )}
                </span>
                {source.state === 'running' && <Meter value={fraction} className="pg-src-meter" />}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="pg-live-empty">
          {running && <span className="spinner" aria-hidden="true" />}
          <p>{running ? 'Uruchamiamy pierwsze portale.' : 'Ten przebieg nie pobierał ofert z portali.'}</p>
        </div>
      )}
    </div>
  );
}

function MatchingView({
  scoring,
  running,
  summary,
}: {
  scoring: ScoringProgress | null;
  running: boolean;
  summary: RunSummary | null;
}) {
  const reasons = Object.entries(scoring?.scoring.prefilter_reasons ?? {})
    .filter((entry): entry is [keyof PrefilterReasons, number] => typeof entry[1] === 'number' && entry[1] > 0)
    .sort((a, b) => b[1] - a[1]);
  const rejected = scoring?.scoring.prefilter_rejected ?? null;
  const errors = scoring?.scoring.errors ?? 0;

  return (
    <div className="pg-live pg-match">
      <div className="pg-live-head">
        <h2 className="pg-live-title">{summary ? 'Do przejrzenia' : 'Ocena ofert'}</h2>
        {errors > 0 && <p className="pg-live-meta warn">Nieudane oceny: {n(errors)}</p>}
      </div>
      {summary ? (
        <>
          <div className="pg-match-count">
            <span className="pg-match-num">{n(summary.matched_count)}</span>
            <span className="pg-match-of">z {n(summary.checked)} sprawdzonych</span>
          </div>
          <p className="pg-match-note">Oferty z procentem dopasowania, bez zapisanych, ukrytych i wysłanych.</p>
          <dl className="pg-facts">
            <div className="pg-fact">
              <dt>Ocenione teraz</dt>
              <dd>{scoring ? n(scoring.scored) : '–'}</dd>
            </div>
            <div className="pg-fact">
              <dt>Tempo</dt>
              <dd>{scoring?.rate != null ? `${rateFormat.format(scoring.rate)} na sekundę` : '–'}</dd>
            </div>
            <div className="pg-fact">
              <dt>Odrzucone w przesiewie</dt>
              <dd>{rejected != null ? n(rejected) : '–'}</dd>
            </div>
          </dl>
        </>
      ) : scoring?.total != null ? (
        <>
          <div className="pg-match-count">
            <span className="pg-match-num">{n(scoring.scored)}</span>
            <span className="pg-match-of">z {n(scoring.total)} do oceny</span>
          </div>
          <Meter value={scoring.fraction} className="pg-match-meter" />
          <dl className="pg-facts">
            <div className="pg-fact">
              <dt>Tempo</dt>
              <dd>{scoring.rate != null ? `${rateFormat.format(scoring.rate)} na sekundę` : '–'}</dd>
            </div>
            {running && (
              <div className="pg-fact">
                <dt>Do końca</dt>
                <dd>{scoring.etaSeconds != null ? formatEta(scoring.etaSeconds) : '–'}</dd>
              </div>
            )}
            <div className="pg-fact">
              <dt>Odrzucone w przesiewie</dt>
              <dd>{rejected != null ? n(rejected) : '–'}</dd>
            </div>
          </dl>
        </>
      ) : (
        <>
          <div className="pg-match-count">
            <span className="pg-match-wait">{running ? 'Przesiew ofert' : 'Ocena się nie zaczęła'}</span>
          </div>
          {running && <Meter value={null} className="pg-match-meter" />}
          <p className="pg-match-note">
            Przesiew sprawdza poziom, miasto, język i dziedzinę oferty. Pozostałe oferty oceni model.
          </p>
        </>
      )}
      {reasons.length > 0 && (
        <div className="pg-reasons">
          <p className="pg-reasons-label">Powody odrzucenia</p>
          <div className="pg-reasons-list">
            {reasons.map(([key, count]) => (
              <span key={key} className="chip">
                {REASON_LABEL[key] ?? key} <span className="pg-reason-count">{n(count)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
