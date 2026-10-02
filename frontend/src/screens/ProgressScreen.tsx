import { ArrowLeft, Check, Sparkles, Square } from 'lucide-react';
import { useApp } from '../app_context';
import { countFormat } from '../format';
import { COMPARED, DOWNLOADED, OFFERS, REMAINING, plural } from '../plural';
import { navigate, paths } from '../router';
import type { PipelineState, RunSummary } from '../types';
import { locationLine } from './offer/offer_format';
import type { ScreenProps } from './types';
import '../styles/progress.css';

type GroupState = 'pending' | 'running' | 'done' | 'failed' | 'stopped';
type Phase = 'profile' | 'scraping' | 'matching' | 'done' | 'failed' | 'stopped' | 'idle';

const GROUPS = [
  { num: '01', label: 'Twój profil', ids: ['phase0'] },
  { num: '02', label: 'Szukanie ofert', ids: ['phase1', 'phase1_5', 'phase2', 'phase2_5'] },
  { num: '03', label: 'Porównywanie z CV', ids: ['phase3', 'phase4'] },
] as const;

const RUNNING_PHASES: Phase[] = ['profile', 'scraping', 'matching'];

const TITLE: Record<Phase, string> = {
  profile: 'Czytamy Twoje CV',
  scraping: 'Szukamy ofert',
  matching: 'Sprawdzamy dopasowanie ofert',
  done: 'Wyszukiwanie zakończone',
  failed: 'Wyszukiwanie przerwane',
  stopped: 'Wyszukiwanie zatrzymane',
  idle: 'Brak wyszukiwania w toku',
};

function groupState(pipeline: PipelineState, ids: readonly string[]): GroupState {
  const busy = pipeline.running || pipeline.status === 'stopping';
  const current = pipeline.stages[pipeline.current_stage_idx]?.id;
  const stages = pipeline.stages.filter((s) => ids.includes(s.id));
  if (stages.some((s) => s.status === 'failed')) return 'failed';
  if (stages.length > 0 && stages.every((s) => s.status === 'done' || s.status === 'skipped')) return 'done';
  if (busy && (stages.some((s) => s.status === 'running') || (current != null && ids.includes(current)))) return 'running';
  if (!busy && pipeline.status === 'stopped' && stages.some((s) => s.status !== 'pending')) return 'stopped';
  return 'pending';
}

function runPhase(pipeline: PipelineState, states: GroupState[]): Phase {
  if (pipeline.running || pipeline.status === 'stopping') {
    const idx = states.indexOf('running');
    if (idx >= 0) return RUNNING_PHASES[idx];
    const firstOpen = states.findIndex((s) => s !== 'done');
    return RUNNING_PHASES[firstOpen < 0 ? 2 : firstOpen];
  }
  if (pipeline.status === 'completed') return 'done';
  if (pipeline.status === 'failed') return 'failed';
  if (pipeline.status === 'stopped') return 'stopped';
  return 'idle';
}

function subtitle(phase: Phase, pipeline: PipelineState, summary: RunSummary | null): string {
  const downloaded = summary?.downloaded ?? null;
  const n = (value: number) => countFormat.format(value);
  switch (phase) {
    case 'profile':
      return 'Odczytujemy z CV miasto, umiejętności i poziom doświadczenia.';
    case 'scraping':
      return downloaded != null
        ? `Twój profil jest gotowy. Pobraliśmy już ${n(downloaded)} ${plural(downloaded, OFFERS)}; szukamy dalej.`
        : 'Twój profil jest gotowy. Przeglądamy portale z ofertami.';
    case 'matching':
      return downloaded != null
        ? `Twój profil jest gotowy. Pobraliśmy ${n(downloaded)} ${plural(downloaded, OFFERS)}; teraz porównujemy ich wymagania z CV.`
        : 'Twój profil jest gotowy. Teraz porównujemy wymagania ofert z CV.';
    case 'done':
    case 'stopped':
    case 'idle': {
      if (!summary) return '';
      const compared = `${n(summary.checked)} ${plural(summary.checked, OFFERS)} ${plural(summary.checked, COMPARED)} z Twoim CV.`;
      return downloaded != null
        ? `${n(downloaded)} ${plural(downloaded, OFFERS)} ${plural(downloaded, DOWNLOADED)}, ${compared}`
        : compared;
    }
    case 'failed':
      return pipeline.error_message ?? 'Przebieg zakończył się błędem.';
  }
}

function statusLine(index: number, state: GroupState, summary: RunSummary | null): string {
  if (state === 'running') return 'W toku';
  if (state === 'failed') return 'Błąd';
  if (state === 'stopped') return 'Zatrzymane';
  if (state === 'pending') return 'Czeka';
  if (index === 0) return 'Profil odczytany';
  if (index === 1 && summary?.downloaded != null) {
    const n = summary.downloaded;
    return `${countFormat.format(n)} ${plural(n, OFFERS)} ${plural(n, DOWNLOADED)}`;
  }
  if (index === 2 && summary) {
    const n = summary.checked;
    return `${countFormat.format(n)} ${plural(n, OFFERS)} ${plural(n, COMPARED)}`;
  }
  return 'Gotowe';
}

function restCopy(phase: Phase): string {
  if (phase === 'profile' || phase === 'scraping') return 'Porównywanie z CV zacznie się po pobraniu ofert.';
  if (phase === 'matching') return 'Porównujemy wymagania i doświadczenie z Twoim CV.';
  if (phase === 'done') return 'Wszystkie oferty z tego wyszukiwania są sprawdzone.';
  return 'Pozostałe oferty sprawdzimy przy następnym wyszukiwaniu.';
}

export function ProgressScreen(_props: ScreenProps) {
  const { pipeline, runSummary: summary, stopSearch } = useApp();
  if (!pipeline) return <div className="pg" />;

  const states = GROUPS.map((g) => groupState(pipeline, g.ids));
  const phase = runPhase(pipeline, states);
  const running = RUNNING_PHASES.includes(phase);
  const stopping = pipeline.status === 'stopping';
  const sub = subtitle(phase, pipeline, summary);
  const left = summary?.to_check != null ? Math.max(0, summary.to_check - summary.checked) : null;
  const recent = summary?.recent.slice(0, 3) ?? [];

  return (
    <div className="pg">
      <header className="pg-head">
        <p className="pg-eyebrow">WYSZUKIWANIE OFERT</p>
        <h1 className="pg-title">{TITLE[phase]}</h1>
        {sub && <p className={`pg-sub${phase === 'failed' ? ' warn' : ''}`}>{sub}</p>}
      </header>

      <ol className="pg-steps">
        {GROUPS.map((g, i) => (
          <li key={g.num} className={`pg-step is-${states[i]}`}>
            {states[i] === 'done' ? <Check className="pg-step-check" /> : <span className="pg-step-num">{g.num}</span>}
            <div className="pg-step-copy">
              <p className="pg-step-label">{g.label}</p>
              <p className={`pg-step-status${states[i] === 'failed' ? ' warn' : ''}`}>
                {statusLine(i, states[i], summary)}
              </p>
            </div>
          </li>
        ))}
      </ol>

      <section className="pg-panel panel">
        <div className="pg-count">
          <p className="pg-eyebrow pg-count-label">SPRAWDZONE OFERTY</p>
          <div className="pg-count-main">
            <p className="pg-count-num">{countFormat.format(summary?.checked ?? 0)}</p>
            {summary?.to_check != null && (
              <p className="pg-count-of">z {countFormat.format(summary.to_check)} do porównania</p>
            )}
          </div>
          <div className="pg-count-rest">
            {left != null && (phase !== 'done' || left > 0) && (
              <p className="pg-count-left">
                {plural(left, REMAINING)} {countFormat.format(left)} {plural(left, OFFERS)}
              </p>
            )}
            <p className="pg-count-copy">{restCopy(phase)}</p>
          </div>
        </div>
        <div className="pg-sep" />
        <div className="pg-recent">
          <div className="pg-recent-head">
            <h2 className="pg-recent-title">Ostatnio dopasowane</h2>
            {summary && (
              <span className="pg-recent-total">
                {countFormat.format(summary.matched_count)} {plural(summary.matched_count, OFFERS)}
              </span>
            )}
          </div>
          {recent.length > 0 ? (
            recent.map((offer) => (
              <button
                key={offer.link}
                type="button"
                className="pg-row"
                onClick={() => navigate(paths.matched(offer.link))}
              >
                <span className="pg-row-id">
                  <span className="pg-row-title">{offer.title}</span>
                  <span className="pg-row-meta">
                    {[offer.company, locationLine(offer.location, offer.work_mode)]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </span>
                {offer.match_percentage != null && (
                  <span className="pg-row-pct">{Math.round(offer.match_percentage)}%</span>
                )}
              </button>
            ))
          ) : (
            <div className="pg-recent-empty">
              <Sparkles />
              <p>Pierwsze dopasowania pojawią się tutaj.</p>
            </div>
          )}
          <p className="pg-recent-foot">Dopasowanie do CV · wyniki z bieżącego wyszukiwania</p>
        </div>
      </section>

      <footer className="pg-actions">
        <p className="pg-actions-copy">
          {running ? 'Po zakończeniu otworzymy pełną listę dopasowanych ofert.' : ''}
        </p>
        {running ? (
          <button type="button" className="btn" disabled={stopping} onClick={stopSearch}>
            <Square />
            {stopping ? 'Zatrzymywanie…' : 'Zatrzymaj'}
          </button>
        ) : phase === 'failed' ? (
          <button type="button" className="btn" onClick={() => navigate(paths.matched())}>
            <ArrowLeft />
            Wróć do ofert
          </button>
        ) : (
          <button type="button" className="btn btn-primary" onClick={() => navigate(paths.matched())}>
            Pokaż oferty
          </button>
        )}
      </footer>
    </div>
  );
}
