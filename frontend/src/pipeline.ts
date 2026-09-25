import { useEffect, useState } from 'react';
import type { ActivityRow, PipelineStage, PipelineState, PipelineStatus, ScoringTelemetry, StageStatus } from './types';
import type { StreamLoop } from './components/ui/Stream';

/** Fale strumienia w trakcie przebiegu — widać, że pipeline pracuje także między paczkami
 *  (scraping trwa kwadrans bez jednej paczki). Czas przejścia jak `cross` danego miejsca
 *  (u_period / u_running z jobfinder.pen), ale siła ~⅔ — ciągłe fale przy pełnej sile
 *  wyglądały jak zmiana kształtu strumienia (decyzja użytkownika: subtelniej). */
export const PILL_STREAM_LOOP: StreamLoop = { period: 1.6, running: 0.36, gap: [2, 5] };
export const SHEET_STREAM_LOOP: StreamLoop = { period: 2.6, running: 0.4, gap: [2, 5] };

// Opis etapów widoczny w widoku pipeline'u. Kolejność i identyfikatory
// pochodzą z pipeline_manager.py; tutaj tylko krótkie wyjaśnienie „po co”.
export const STAGE_INFO: Record<string, { short: string; about: string }> = {
  phase0: {
    short: 'Archiwizacja',
    about: 'Przenosi ręczne oceny starych ofert do archiwum, zanim wygasłe ogłoszenia znikną z bazy.',
  },
  phase1: {
    short: 'Pobieranie',
    about: 'Odwiedza portale pracy i dopisuje nowe oferty. Źródła pobrane dziś z powodzeniem są pomijane.',
  },
  phase1_5: {
    short: 'Normalizacja',
    about: 'Ujednolica adresy ofert, zanim ruszy deduplikacja — inaczej ta sama oferta liczyłaby się dwa razy.',
  },
  phase2: {
    short: 'Deduplikacja',
    about: 'Scala tę samą ofertę znalezioną na kilku portalach i łączy jej źródła.',
  },
  phase2_5: {
    short: 'Czyszczenie',
    about: 'Przycina opisy przed płatnym ocenianiem: mniej tokenów, ten sam sens.',
  },
  phase3: {
    short: 'Ocena AI',
    about: 'Kaskada modeli ocenia dopasowanie ofert do CV paczkami. Przy limitach rotuje klucze i schodzi na kolejny model.',
  },
  phase4: {
    short: 'Ewaluacja',
    about: 'Sprawdza bieżący ranking względem Twoich ręcznych ocen.',
  },
};
export const STAGE_SHORT_NAMES = [
  'Archiwizacja',
  'Pobieranie',
  'Normalizacja',
  'Deduplikacja',
  'Czyszczenie',
  'Ocena AI',
  'Ewaluacja',
];


export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  pending: 'czeka',
  running: 'w toku',
  done: 'gotowe',
  skipped: 'pominięty',
  failed: 'błąd',
};

export const PIPELINE_STATUS_LABEL: Record<PipelineStatus, string> = {
  idle: 'Gotowy',
  running: 'W toku',
  stopping: 'Zatrzymywanie',
  stopped: 'Wstrzymany',
  completed: 'Ukończony',
  failed: 'Błąd',
};

export function pipelineStatus(p: PipelineState | null): PipelineStatus {
  if (!p) return 'idle';
  if (p.status === 'stopping') return 'stopping';
  if (p.running || p.status === 'running') return 'running';
  return p.status || 'idle';
}

export function isPipelineBusy(p: PipelineState | null): boolean {
  const s = pipelineStatus(p);
  return s === 'running' || s === 'stopping';
}

/** Udział etapów zamkniętych (gotowe lub pominięte) w całym przebiegu, 0–100. */
export function stageProgress(p: PipelineState | null): number {
  const stages = p?.stages ?? [];
  if (!stages.length) return 0;
  if (p?.status === 'completed') return 100;
  const resolved = stages.filter((s) => s.status === 'done' || s.status === 'skipped').length;
  return Math.round((resolved / stages.length) * 100);
}

/** Etap, na którym przebieg stoi: bieżący, nieudany albo pierwszy niezamknięty. */
export function focusStageIndex(p: PipelineState | null): number {
  const stages = p?.stages ?? [];
  const running = stages.findIndex((s) => s.status === 'running');
  if (running >= 0) return running;
  const failed = stages.findIndex((s) => s.status === 'failed');
  if (failed >= 0) return failed;
  if (p && p.status !== 'idle' && p.status !== 'completed') {
    return Math.min(Math.max(p.current_stage_idx ?? 0, 0), Math.max(stages.length - 1, 0));
  }
  return -1;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '—';
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

export function stageDuration(stage: PipelineStage, nowSec: number): number | null {
  if (!stage.started_at) return null;
  const end = stage.finished_at ?? (stage.status === 'running' ? nowSec : null);
  return end === null ? null : end - stage.started_at;
}

/**
 * Sekundy do końca oceny z realnego tempa bieżącego przebiegu: oceny / (koniec ostatniej
 * paczki − start pierwszej), odliczane od końca ostatniej paczki. Bez skończonej paczki
 * albo gdy paczka trwa dłużej niż średnia — null (pasek pokazuje wtedy same liczby).
 */
export function scoringEtaSeconds(scoring: ScoringTelemetry | null | undefined, nowSec: number): number | null {
  if (!scoring?.total || !scoring.first_batch_at || !scoring.last_done_at || scoring.processed <= 0) return null;
  const elapsed = scoring.last_done_at - scoring.first_batch_at;
  const left = scoring.total - scoring.processed;
  if (elapsed <= 0 || left <= 0) return null;
  const eta = (left * elapsed) / scoring.processed - (nowSec - scoring.last_done_at);
  return eta > 0 ? eta : null;
}

export interface ScoringPacks {
  /** Ofert w paczce (BATCH_SIZE oceny). */
  size: number;
  total: number;
  done: number;
  /** 0–1: szacunek postępu bieżącej paczki ze średniego czasu paczki; null = brak tempa. */
  current: number | null;
}

/**
 * Paczki oceny AI: ile jest w przebiegu, ile gotowych i jak daleko jest bieżąca. Rozmiar
 * paczki podaje telemetria (`batch_size` z „Target Batch Size”); przebieg uruchomiony przez
 * serwer sprzed tego pola go nie ma — wtedy rozmiar wynika z kolejki: „Batch k (…, R remaining)”
 * liczy R przed zdjęciem paczki, więc k − 1 paczek zabrało total − R ofert.
 */
export function scoringPacks(scoring: ScoringTelemetry | null | undefined, nowSec: number): ScoringPacks | null {
  if (!scoring?.total) return null;
  let size = scoring.batch_size ?? 0;
  if (!size && scoring.batch > 1 && scoring.remaining != null) {
    size = Math.round((scoring.total - scoring.remaining) / (scoring.batch - 1));
  }
  if (size <= 0) return null;
  const total = Math.ceil(scoring.total / size);
  // Ostatnia paczka bywa niepełna (5574 = 74 × 75 + 24) — po ocenie wszystkiego liczy się jako gotowa.
  const done = scoring.processed >= scoring.total ? total : Math.floor(scoring.processed / size);
  let current: number | null = null;
  if (done > 0 && scoring.first_batch_at && scoring.last_done_at) {
    const perPack = (scoring.last_done_at - scoring.first_batch_at) / (scoring.processed / size);
    if (perPack > 0) current = Math.min(0.92, Math.max(0, (nowSec - scoring.last_done_at) / perPack));
  }
  return { size, total, done, current };
}

export interface GridFit {
  cols: number;
  cell: number;
  gap: number;
}

/**
 * Największe kwadratowe kafelki, w których `n` sztuk mieści się w polu w×h (px). Przy kilku
 * sztukach rosną najwyżej do `maxCell`; przy remisie wygrywa mniej rzędów.
 */
export function fitGrid(n: number, w: number, h: number, maxCell = 44): GridFit | null {
  if (n <= 0 || w <= 0 || h <= 0) return null;
  let best: GridFit = { cols: Math.max(1, Math.floor((w + 1) / 4)), cell: 3, gap: 1 };
  for (let cols = 1; cols <= n; cols++) {
    const gap = w / cols >= 16 ? 5 : w / cols >= 9 ? 3 : 2;
    const cell = Math.min(maxCell, Math.floor((w - (cols - 1) * gap) / cols));
    const rows = Math.ceil(n / cols);
    if (cell >= 3 && cell >= best.cell && rows * cell + (rows - 1) * gap <= h) best = { cols, cell, gap };
  }
  return best;
}

export function formatClock(epochSec: number | null | undefined): string {
  if (!epochSec) return '—';
  return new Date(epochSec * 1000).toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
}

/** "2026-09-20 19:29:30" → "20.09, 19:29" (albo "dziś, 19:29"). */
export function formatStamp(timeStr: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(timeStr);
  if (!m) return timeStr;
  const [, y, mo, d, h, mi] = m;
  const now = new Date();
  const isToday =
    now.getFullYear() === Number(y) && now.getMonth() + 1 === Number(mo) && now.getDate() === Number(d);
  return `${isToday ? 'dziś' : `${d}.${mo}`}, ${h}:${mi}`;
}

/** Tyka co sekundę tylko wtedy, gdy `active` — w spoczynku nie trzyma timera. */
export function useNowSeconds(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    setNow(Date.now() / 1000);
    if (!active) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export interface LastRunSummary {
  scrape: ActivityRow | null;
  scoring: ActivityRow | null;
  sources: ActivityRow[];
}

/** Ostatnie pobrania per źródło i ostatnia ocena AI z dziennika aktywności. */
export function summarizeActivity(rows: ActivityRow[]): LastRunSummary {
  const sources: ActivityRow[] = [];
  const seen = new Set<string>();
  let scoring: ActivityRow | null = null;
  for (const row of rows) {
    if (row.op === 'pobieranie' && !seen.has(row.what)) {
      seen.add(row.what);
      sources.push(row);
    } else if (row.op.startsWith('ocena') && !scoring) {
      scoring = row;
    }
  }
  return { scrape: sources[0] ?? null, scoring, sources };
}
/** Oblicza ułamkowy postęp (0..1) dla komponentu Stream oraz procent (0..100) dla pigułki i arkusza. */
export function calculateOverallPipelineProgress(p: PipelineState | null): { progress: number; totalPct: number } {
  if (!p) return { progress: 0, totalPct: 0 };
  const status = pipelineStatus(p);
  if (status === 'completed') return { progress: 1, totalPct: 100 };
  if (status === 'idle') return { progress: 0, totalPct: 0 };

  const stages = p.stages ?? [];
  const totalStages = Math.max(stages.length, 7);
  const runningIdx = stages.findIndex((s) => s.status === 'running');
  const activeIdx = runningIdx >= 0 ? runningIdx : Math.min(Math.max(p.current_stage_idx ?? 0, 0), totalStages - 1);

  let withinStage = 0.5;
  // Etap pobierania (phase1 / indeks 1)
  if (activeIdx === 1 && p.telemetry?.sources?.length) {
    const total = p.telemetry.sources.length;
    const done = p.telemetry.sources.filter((s) => s.state === 'done' || s.state === 'skipped').length;
    withinStage = total > 0 ? done / total : 0.5;
  }
  // Etap oceny AI (phase3 / indeks 5)
  else if (activeIdx === 5 && p.telemetry?.scoring?.total) {
    const total = p.telemetry.scoring.total;
    const proc = p.telemetry.scoring.processed;
    withinStage = total > 0 ? Math.min(1, proc / total) : 0.5;
  } else {
    const resolved = stages.filter((s) => s.status === 'done' || s.status === 'skipped').length;
    withinStage = resolved > activeIdx ? 1 : 0.3;
  }

  const progress = Math.min(0.99, Math.max(0.01, (activeIdx + withinStage) / totalStages));
  const totalPct = Math.round(progress * 100);
  return { progress, totalPct };
}

/** Liczba świeżych ofert dodanych w ostatnim przebiegu. */
export function getFreshOffersCount(p: PipelineState | null, activityRows: ActivityRow[]): number {
  if (p?.telemetry?.sources?.length) {
    const added = p.telemetry.sources.reduce((acc, s) => acc + (s.added ?? 0), 0);
    if (added > 0) return added;
  }
  for (const row of activityRows) {
    if (row.op === 'scoring' || row.op === 'scrape') {
      const m = /\+(\d+)/.exec(row.detail);
      if (m) return parseInt(m[1], 10);
    }
  }
  return 0;
}

export type LogTone = 'phase' | 'err' | 'warn' | 'ok' | 'plain';

export interface ParsedLogLine {
  time: string | null;
  text: string;
  tone: LogTone;
}

// Format loggerów projektu: "2026-09-23 10:02:11,123 - nazwa - LEVEL - treść".
const LOG_PREFIX = /^(?:\d{4}-\d{2}-\d{2}[ T])?(\d{2}:\d{2}:\d{2})(?:[,.]\d+)?(?:\s*-\s*[\w.]+)??\s*-\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*-\s*/;

export function parseLogLine(raw: string): ParsedLogLine | null {
  const trimmed = raw.trim();
  if (!trimmed || /^[=\-─]{6,}$/.test(trimmed)) return null;
  const m = LOG_PREFIX.exec(trimmed);
  const time = m ? m[1] : null;
  const level = m ? m[2] : null;
  const text = m ? trimmed.slice(m[0].length) : trimmed;
  if (!text.trim() || /^[=\-─]{6,}$/.test(text.trim())) return null;
  let tone: LogTone = 'plain';
  if (level === 'ERROR' || level === 'CRITICAL') tone = 'err';
  else if (/PHASE \d|PIPELINE (COMPLETE|STOPPED)|^PIPELINE:/.test(text)) tone = 'phase';
  else if (/\b(ERROR|CRITICAL|Traceback)\b|✗|Failed|FAILED|PIPELINE INCOMPLETE/.test(text)) tone = 'err';
  else if (level === 'WARNING' || /Rate limit|Toxic|Sleeping|Waiting/.test(text)) tone = 'warn';
  else if (/✓|Success!|DONE\./.test(text)) tone = 'ok';
  return { time, text, tone };
}
