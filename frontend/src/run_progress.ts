import { useEffect, useState } from 'react';
import { countFormat } from './format';
import { NEW_ONES, OFFERS, SKILLS, plural } from './plural';
import type { PipelineEvent, PipelineState, ScoringTelemetry, SourceTelemetry } from './types';

export type GroupState = 'pending' | 'running' | 'done' | 'failed' | 'stopped';
export type Phase = 'profile' | 'scraping' | 'matching' | 'done' | 'failed' | 'stopped' | 'idle';

export const GROUPS = [
  { num: '01', label: 'Twój profil', ids: ['phase0'] },
  { num: '02', label: 'Szukanie ofert', ids: ['phase1', 'phase1_5', 'phase2', 'phase2_5'] },
  { num: '03', label: 'Porównywanie z CV', ids: ['phase3'] },
] as const;

export const RUNNING_PHASES: Phase[] = ['profile', 'scraping', 'matching'];

export const STAGE_TITLE: Record<string, string> = {
  phase0: 'Odczytywanie CV',
  phase1: 'Pobieranie ofert',
  phase1_5: 'Porządkowanie ofert',
  phase2: 'Porządkowanie ofert',
  phase2_5: 'Porządkowanie ofert',
  phase3: 'Porównywanie z CV',
};

export function isBusy(pipeline: PipelineState | null): boolean {
  return Boolean(pipeline && (pipeline.running || pipeline.status === 'stopping'));
}

export function currentStageId(pipeline: PipelineState): string {
  return pipeline.stages[pipeline.current_stage_idx]?.id ?? '';
}

export function groupState(pipeline: PipelineState, ids: readonly string[]): GroupState {
  const busy = isBusy(pipeline);
  const current = currentStageId(pipeline);
  const stages = pipeline.stages.filter((s) => ids.includes(s.id));
  if (stages.some((s) => s.status === 'failed')) return 'failed';
  if (stages.length > 0 && stages.every((s) => s.status === 'done' || s.status === 'skipped')) return 'done';
  if (busy && (stages.some((s) => s.status === 'running') || ids.includes(current))) return 'running';
  if (!busy && pipeline.status === 'stopped' && stages.some((s) => s.status !== 'pending')) return 'stopped';
  return 'pending';
}

export function runPhase(pipeline: PipelineState, states: GroupState[]): Phase {
  if (isBusy(pipeline)) {
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

export function groupSpan(pipeline: PipelineState, ids: readonly string[]): { start: number | null; end: number | null } {
  const stages = pipeline.stages.filter((s) => ids.includes(s.id));
  const starts = stages.map((s) => s.started_at).filter((v): v is number => v != null);
  const ends = stages.map((s) => s.finished_at).filter((v): v is number => v != null);
  const open = stages.some((s) => s.status === 'running');
  return {
    start: starts.length ? Math.min(...starts) : null,
    end: !open && ends.length ? Math.max(...ends) : null,
  };
}

export function elapsedSeconds(pipeline: PipelineState, now: number): number | null {
  if (pipeline.started_at == null) return null;
  const end = isBusy(pipeline) ? now / 1000 : (pipeline.finished_at ?? now / 1000);
  return Math.max(0, end - pipeline.started_at);
}

export function foundTotal(pipeline: PipelineState): number | null {
  const counted = (pipeline.telemetry?.sources ?? []).filter((s) => s.found != null);
  return counted.length ? counted.reduce((sum, s) => sum + (s.found ?? 0), 0) : null;
}

function errorText(error: string | null | undefined): string {
  if (!error) return 'Nie udało się pobrać ofert';
  if (/\b(403|429)\b|forbidden|captcha|blocked|access denied/i.test(error)) return 'Portal zablokował pobieranie';
  if (/timed?\s?out|timeout/i.test(error)) return 'Portal nie odpowiedział na czas';
  if (/name resolution|connection|getaddrinfo|ssl/i.test(error)) return 'Brak połączenia z portalem';
  return 'Scraper zgłosił błąd';
}

export function sourceProblem(source: SourceTelemetry): string | null {
  if (source.state === 'failed') return errorText(source.error);
  const detail = source.health?.detail?.trim();
  if (!source.health || !detail) return null;
  if (source.health.verdict === 'weak') return `Mniej ofert niż zwykle: ${detail}`;
  return detail.charAt(0).toUpperCase() + detail.slice(1);
}

export function sourcesWithProblems(pipeline: PipelineState): { name: string; text: string; failed: boolean }[] {
  return (pipeline.telemetry?.sources ?? [])
    .map((source) => ({ name: source.name, text: sourceProblem(source), failed: source.state === 'failed' }))
    .filter((item): item is { name: string; text: string; failed: boolean } => item.text != null);
}

export interface ScoringProgress {
  scored: number;
  total: number | null;
  fraction: number | null;
  rate: number | null;
  etaSeconds: number | null;
  scoring: ScoringTelemetry;
}

export function scoringProgress(pipeline: PipelineState): ScoringProgress | null {
  const scoring = pipeline.telemetry?.scoring;
  if (!scoring) return null;
  const total = scoring.to_score;
  const fraction = total ? Math.min(1, scoring.scored / total) : total === 0 ? 1 : null;
  const remaining = total != null ? Math.max(0, total - scoring.scored) : null;
  const etaSeconds = remaining && scoring.rate ? remaining / scoring.rate : null;
  return { scored: scoring.scored, total, fraction, rate: scoring.rate, etaSeconds, scoring };
}

const pad = (n: number) => String(n).padStart(2, '0');

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} godz. ${m % 60} min` : `${h} godz.`;
}

export function formatEta(seconds: number): string {
  if (seconds < 60) return `ok. ${Math.max(1, Math.round(seconds))} s`;
  return `ok. ${Math.round(seconds / 60)} min`;
}

export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export interface ActivityItem {
  key: string;
  time: string | null;
  text: string;
  warn: boolean;
}

const PHASE_TEXT: Record<string, string> = {
  phase0: 'Usuwamy oferty, których portale nie pokazują od 14 dni',
  phase0_5: 'Odczytujemy profil z CV',
  phase1: 'Zaczynamy pobieranie ofert z portali',
  phase1_5: 'Ujednolicamy linki ofert',
  phase2: 'Usuwamy duplikaty',
  phase2_5: 'Skracamy opisy ofert',
  phase3: 'Zaczynamy porównywanie ofert z CV',
};

const RESULT_TEXT: Record<string, { text: string; warn?: boolean }> = {
  complete: { text: 'Wyszukiwanie zakończone' },
  stopped: { text: 'Wyszukiwanie zatrzymane' },
  incomplete: { text: 'Wyszukiwanie przerwane', warn: true },
};

const n = (value: number) => countFormat.format(value);

function describeEvent(ev: PipelineEvent): { text: string; warn?: boolean } | null {
  const name = ev.name ?? '';
  switch (ev.event) {
    case 'stage': {
      const text = ev.state === 'running' ? PHASE_TEXT[ev.id ?? ''] : undefined;
      return text ? { text } : null;
    }
    case 'pipeline':
      return RESULT_TEXT[ev.result ?? ''] ?? null;
    case 'source':
      if (ev.state === 'running') return { text: `${name}: pobieramy listę ofert` };
      if (ev.state === 'done') {
        const found = ev.found ?? 0;
        return { text: `${name}: ${n(found)} ${plural(found, OFFERS)} na liście` };
      }
      if (ev.state === 'failed') return { text: `${name}: nie udało się pobrać ofert`, warn: true };
      if (ev.state === 'stopped' && ev.found != null) return { text: `${name}: zatrzymane, zapisujemy pobrane` };
      return null;
    case 'source_saved': {
      const added = ev.added ?? 0;
      return { text: `${name}: ${n(added)} ${plural(added, NEW_ONES)} ${plural(added, OFFERS)} w bazie` };
    }
    case 'throttled':
      return { text: `${name} ogranicza ruch, zwalniamy` };
    case 'profile': {
      const skills = ev.skills ?? 0;
      return { text: `Profil z CV: ${ev.seniority ?? '?'}, ${ev.city ?? '?'}, ${n(skills)} ${plural(skills, SKILLS)}` };
    }
    case 'prefilter': {
      const toScore = ev.to_score ?? 0;
      return { text: `Po przesiewie do oceny: ${n(toScore)} ${plural(toScore, OFFERS)}` };
    }
    case 'matching_done': {
      const scored = ev.scored_now ?? 0;
      return { text: `Ocenione: ${n(scored)} ${plural(scored, OFFERS)}` };
    }
    default:
      return null;
  }
}

const clock = (at: number) => {
  const d = new Date(at * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

export function activityFeed(events: PipelineEvent[], limit: number): ActivityItem[] {
  const items: ActivityItem[] = [];
  const seen = new Map<string, number>();
  for (const ev of events) {
    const described = describeEvent(ev);
    if (!described) continue;
    const previous = items[items.length - 1];
    if (previous && previous.text === described.text) continue;
    const time = clock(ev.at);
    const base = `${time}|${described.text}`;
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    items.push({ key: `${base}|${count}`, time, text: described.text, warn: Boolean(described.warn) });
  }
  return items.slice(-limit).reverse();
}
