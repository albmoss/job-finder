import { useEffect, useState } from 'react';
import type { ActivityRow, PipelineStage, PipelineState, PipelineStatus, PrefilterReasons, ScoringTelemetry, StageStatus } from './types';
import type { StreamLoop } from './components/ui/Stream';

/** Fale strumienia w trakcie przebiegu — widać, że pipeline pracuje także wtedy, gdy nic
 *  się nie kończy (scraping trwa kwadrans bez meldunku postępu). Czas przejścia jak `cross`
 *  danego miejsca, ale siła ~⅔ — ciągłe fale przy pełnej sile wyglądały jak zmiana kształtu strumienia. */
export const PILL_STREAM_LOOP: StreamLoop = { period: 1.6, running: 0.36, gap: [2, 5] };
export const SHEET_STREAM_LOOP: StreamLoop = { period: 2.6, running: 0.4, gap: [2, 5] };

// Krótkie nazwy 7 etapów toru; kolejność i identyfikatory pochodzą z pipeline_manager.py.
// Etap 00 obejmuje też profil z CV (PHASE 0.5).
export const STAGE_SHORT_NAMES = [
  'Archiwum i CV',
  'Pobieranie',
  'Normalizacja',
  'Deduplikacja',
  'Czyszczenie',
  'Dopasowanie',
  'Ewaluacja',
];

/** Powody odrzucenia przez przesiew (matching/prefilter.py) w kolejności pokazywania. */
export const PREFILTER_LABELS: Record<keyof PrefilterReasons, string> = {
  miasto: 'miasto',
  poziom: 'poziom',
  lata: 'lata',
  jezyk: 'język',
  brak_opisu: 'bez opisu',
};

/** Opis przebiegu w nagłówku arkusza: tryb z pipeline_manager i wznowienie z polecenia. */
export function runLabel(p: PipelineState | null): string {
  if (!p) return 'przebieg';
  if (p.mode === 'standalone') {
    const stage = p.stages?.find((s) => s.status !== 'skipped');
    return stage ? `pojedynczy krok · ${stage.title}` : 'pojedynczy krok';
  }
  const resumed = p.cmd?.includes('--resume') ? 'wznowiony ' : '';
  return p.mode === 'skip_scraping' ? `${resumed}przebieg bez pobierania` : `${resumed}pełny przebieg`;
}


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
 * Sekundy do końca oceny z tempa podanego przez matching/run.py (oceny/s od startu),
 * odliczane od ostatniego meldunku. Bez meldunku — null (pasek pokazuje same liczby).
 */
export function scoringEtaSeconds(scoring: ScoringTelemetry | null | undefined, nowSec: number): number | null {
  if (!scoring?.to_score || !scoring.rate || !scoring.last_done_at) return null;
  const left = scoring.to_score - scoring.scored;
  if (left <= 0) return null;
  const eta = left / scoring.rate - (nowSec - scoring.last_done_at);
  return eta > 0 ? eta : null;
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

/** Ostatnie pobrania per źródło i ostatnie dopasowanie z dziennika aktywności (app_services). */
export function summarizeActivity(rows: ActivityRow[]): LastRunSummary {
  const sources: ActivityRow[] = [];
  const seen = new Set<string>();
  let scoring: ActivityRow | null = null;
  for (const row of rows) {
    if (row.op === 'pobieranie' && !seen.has(row.what)) {
      seen.add(row.what);
      sources.push(row);
    } else if (row.op === 'dopasowanie' && !scoring) {
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
  // Etap dopasowania (phase3 / indeks 5)
  else if (activeIdx === 5) {
    const total = p.telemetry?.scoring?.to_score;
    const proc = p.telemetry?.scoring?.scored ?? 0;
    withinStage = total && total > 0 ? Math.min(1, proc / total) : 0.5;
  } else {
    const resolved = stages.filter((s) => s.status === 'done' || s.status === 'skipped').length;
    withinStage = resolved > activeIdx ? 1 : 0.3;
  }

  const progress = Math.min(0.99, Math.max(0.01, (activeIdx + withinStage) / totalStages));
  const totalPct = Math.round(progress * 100);
  return { progress, totalPct };
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
  else if (/\b(ERROR|CRITICAL|Traceback)\b|✗|Failed|FAILED|PIPELINE INCOMPLETE|Jev error|^Brak /.test(text)) tone = 'err';
  else if (level === 'WARNING' || /Stop requested|Rate limit/.test(text)) tone = 'warn';
  else if (/✓|Success!|DONE\./.test(text)) tone = 'ok';
  return { time, text, tone };
}
