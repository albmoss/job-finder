import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Pause,
  Play,
  ChevronUp,
  Globe,
  Sparkles,
  RotateCcw,
  Database,
  ArrowUpRight,
  Loader2,
  XOctagon,
  ChevronDown,
} from 'lucide-react';
import type { PipelineState } from '../types';
import { Stream } from './ui/Stream';
import { ERRORS, OFFERS, plural } from '../plural';
import {
  PREFILTER_LABELS,
  SHEET_STREAM_LOOP,
  STAGE_SHORT_NAMES,
  calculateOverallPipelineProgress,
  fitGrid,
  formatClock,
  formatDuration,
  parseLogLine,
  pipelineStatus,
  runLabel,
  scoringEtaSeconds,
  stageDuration,
  useNowSeconds,
} from '../pipeline';
import '../styles/pipeline.css';

export interface PipelineSheetProps {
  open: boolean;
  onClose(): void;
  pipeline: PipelineState | null;
  onStop(): void;
  onForceStop(): void;
  onResume(): void;
  onRunStep(step: 'scrapers' | 'matching' | 'rescore_all'): void;
  onReloadDatabase(): void;
  onOpenLaunchModal(): void;
}

export const PipelineSheet: React.FC<PipelineSheetProps> = ({
  open,
  onClose,
  pipeline,
  onStop,
  onForceStop,
  onResume,
  onRunStep,
  onReloadDatabase,
  onOpenLaunchModal,
}) => {
  const [showFullLog, setShowFullLog] = useState(false);
  const [confirmingRescore, setConfirmingRescore] = useState(false);
  const logConsoleRef = useRef<HTMLDivElement>(null);

  const status = pipelineStatus(pipeline);
  const isRunning = status === 'running';
  const isStopping = status === 'stopping';
  const isFailed = status === 'failed';
  const isStopped = status === 'stopped';
  const isCompleted = status === 'completed';
  const isIdle = status === 'idle';

  const nowSec = useNowSeconds(open);

  // Arkusz jest zawsze w DOM, żeby otwarcie i zamknięcie miały przejście. Zamknięty arkusz
  // jest przycięty (clip-path) do prostokąta paska — otwarcie rozwija go z paska w dół,
  // zamknięcie zwija tą samą drogą. Strumień arkusza stoi w stanie zamkniętym (transform)
  // dokładnie na strumieniu paska i przy otwarciu zjeżdża na swoje miejsce; tytuł etapu leży
  // na tytule paska (--stage-x/y), a „zwiń” stoi na miejscu przycisku startu.
  // Arkusz jest co najmniej tak szeroki jak pasek (na szerokim ekranie pasek bywa szerszy
  // niż 810 px) i kończy się na prawej krawędzi paska — na prawo od paska stoi przycisk pomocy,
  // więc stałe `right` rozjeżdżało arkusz z paskiem. Pomiar przy każdym otwarciu i zamknięciu:
  // pasek ma elastyczną szerokość i zmienny tekst etapu, arkusz zmienia wysokość z dziennikiem.
  const sheetRef = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(false);
  useLayoutEffect(() => {
    const sheet = sheetRef.current;
    const pill = document.querySelector('.pp-pill');
    const pillStream = pill?.querySelector('canvas.pp-stream');
    const pillStage = pill?.querySelector('.pp-stage');
    const band = sheet?.querySelector('.pp-band');
    const sheetStage = sheet?.querySelector('.pp-sheet-head .pp-stage');
    if (sheet && pill && pillStream && band && pillStage && sheetStage) {
      // Przy otwieraniu stan zamknięty ma wskoczyć od razu na nowe pomiary — bez tego
      // przejście ruszyłoby z pozycji zmierzonej przy poprzednim zamknięciu.
      if (open) sheet.setAttribute('data-measure', '');
      const set = (name: string, value: number, unit = 'px') => sheet.style.setProperty(name, `${value}${unit}`);
      const p = pill.getBoundingClientRect();
      set('--sheet-w', Math.max(810, p.width));
      set('--sheet-right', document.documentElement.clientWidth - p.right);
      const s = sheet.getBoundingClientRect();
      set('--from-top', p.top - s.top);
      set('--from-right', s.right - p.right);
      set('--from-bottom', s.bottom - p.bottom);
      set('--from-left', p.left - s.left);
      // .pp-band nie ma transformu, więc daje niezaburzone położenie canvasu arkusza.
      const f = pillStream.getBoundingClientRect();
      const t = band.getBoundingClientRect();
      set('--stream-x', f.left - t.left);
      set('--stream-y', f.top - t.top);
      set('--stream-sx', f.width / t.width, '');
      set('--stream-sy', f.height / t.height, '');
      // Tekst etapu arkusza w stanie zamkniętym leży co do piksela na tekście paska. Przesunięcie
      // od położenia bez transformu: bieżący transform (także w połowie przejścia) odejmujemy.
      const q = pillStage.getBoundingClientRect();
      const r = sheetStage.getBoundingClientRect();
      const m = new DOMMatrix(getComputedStyle(sheetStage).transform);
      set('--stage-x', q.left - (r.left - m.m41));
      set('--stage-y', q.top - (r.top - m.m42));
      if (open) {
        void sheet.offsetWidth;
        sheet.removeAttribute('data-measure');
      }
    }
    if (!open) {
      setShown(false);
      return;
    }
    // Klasa otwarcia dopiero w następnej klatce: przejście musi wystartować ze stanu zamkniętego.
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, [open]);
  const { progress, totalPct } = calculateOverallPipelineProgress(pipeline);

  // Zamykanie przez Esc
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  // Autoprzewijanie konsoli logów (wyłącznie scrollTop, bez scrollIntoView)
  useEffect(() => {
    if (showFullLog && logConsoleRef.current) {
      logConsoleRef.current.scrollTop = logConsoleRef.current.scrollHeight;
    }
  }, [showFullLog, pipeline?.logs?.length]);

  // Odczyt logów i parsowanie
  const parsedLogs = useMemo(() => {
    const raw = pipeline?.logs ?? [];
    const res = [];
    for (let i = 0; i < raw.length; i++) {
      const parsed = parseLogLine(raw[i]);
      if (parsed) res.push(parsed);
    }
    return res;
  }, [pipeline?.logs]);

  // Karta „Na żywo” wypełnia się od dołu: najnowszy komunikat na dole, starsze wygaszają się
  // ku górze karty. Ile się zmieści, tyle widać — nadmiar ucina maska u góry.
  const lastLogs = useMemo(() => parsedLogs.slice(-16), [parsedLogs]);

  // Czas trwania przebiegu
  const elapsedStr = useMemo(() => {
    if (!pipeline?.started_at) return '—';
    const end = pipeline.finished_at ?? nowSec;
    return formatDuration(end - pipeline.started_at);
  }, [pipeline?.started_at, pipeline?.finished_at, nowSec]);
  const elapsedWord = isRunning || isStopping ? 'trwa' : 'trwał';

  const startTimeStr = formatClock(pipeline?.started_at);

  // Nagłówek etapu
  let stageHeaderTitle = 'Gotowy';
  if (isRunning) stageHeaderTitle = pipeline?.current_stage_title || 'W toku';
  else if (isStopping) stageHeaderTitle = 'Zatrzymywanie…';
  else if (isCompleted) stageHeaderTitle = 'Gotowe';
  else if (isStopped) stageHeaderTitle = 'Wstrzymano';
  else if (isFailed) stageHeaderTitle = pipeline?.current_stage_title ? `Błąd: ${pipeline.current_stage_title}` : 'Błąd etapu';

  // 7 kolumn danych etapów
  const stages = pipeline?.stages ?? [];
  const currentStageIdx = stages.findIndex((s) => s.status === 'running');
  const scoring = pipeline?.telemetry?.scoring;
  const sources = pipeline?.telemetry?.sources ?? [];
  const stageStats = pipeline?.telemetry?.stages;

  const stageCols = useMemo(() => {
    return STAGE_SHORT_NAMES.map((name, idx) => {
      const stage = stages[idx];
      const isSkipped = stage?.status === 'skipped';
      // Bez biegnącego etapu „bieżący” jest tylko tam, gdzie przebieg stanął (błąd, stop).
      const isCur = currentStageIdx === idx
        || (currentStageIdx === -1 && (isStopping || isFailed || isStopped) && pipeline?.current_stage_idx === idx);
      const isDone = stage?.status === 'done' || (isCompleted && !isSkipped);
      const isWait = !isCur && !isDone;
      const stateWord = isSkipped ? 'pominięte' : isDone ? 'gotowe' : isCur ? 'w toku' : 'czeka';

      const dur = stage ? stageDuration(stage, nowSec) : null;
      const durStr = dur !== null ? formatDuration(dur) : null;

      // Liczby tylko tam, gdzie stan przebiegu je naprawdę ma; reszta pokazuje czas etapu.
      let topVal = durStr ?? '—';
      let subVal = stateWord;
      let hasMetric = true;
      const totalFound = sources.reduce((acc, s) => acc + (s.found ?? 0), 0);
      const diet = stageStats?.phase2_5;
      const evaluation = stageStats?.phase4;
      if (isSkipped) {
        topVal = '—';
        hasMetric = false;
      } else if (idx === 1 && totalFound > 0) {
        topVal = totalFound.toLocaleString('pl-PL');
        subVal = 'ofert';
      } else if (stage?.id === 'phase0' && stageStats?.phase0) {
        const n = stageStats.phase0.removed;
        topVal = n > 0 ? `−${n.toLocaleString('pl-PL')}` : '0';
        subVal = 'starych';
      } else if (stage?.id === 'phase1_5' && stageStats?.phase1_5) {
        topVal = stageStats.phase1_5.links.toLocaleString('pl-PL');
        subVal = stageStats.phase1_5.merged > 0 ? `linków · −${stageStats.phase1_5.merged}` : 'linków';
      } else if (stage?.id === 'phase2' && stageStats?.phase2) {
        const n = stageStats.phase2.removed;
        topVal = n > 0 ? `−${n.toLocaleString('pl-PL')}` : '0';
        subVal = 'duplikatów';
      } else if (stage?.id === 'phase2_5' && diet && diet.chars_before > 0) {
        const pct = Math.round(((diet.chars_before - diet.chars_after) / diet.chars_before) * 100);
        topVal = pct > 0 ? `−${pct}%` : '0%';
        subVal = 'znaków opisu';
      } else if (stage?.id === 'phase3' && scoring?.to_score) {
        topVal = scoring.scored.toLocaleString('pl-PL');
        subVal = `z ${scoring.to_score.toLocaleString('pl-PL')}`;
      } else if (stage?.id === 'phase3' && scoring?.to_score === 0) {
        topVal = '0';
        subVal = 'nowych do oceny';
      } else if (stage?.id === 'phase4' && evaluation?.rho !== undefined) {
        // Spearman: czy wyższy % dopasowania idzie w parze z wyższą ręczną oceną.
        topVal = `${evaluation.rho >= 0 ? '+' : '−'}${Math.abs(evaluation.rho).toFixed(2).replace('.', ',')}`;
        subVal = 'zgodność z ocenami';
      } else if (stage?.id === 'phase4' && evaluation?.insufficient) {
        topVal = '—';
        subVal = 'za mało ocen';
      } else {
        hasMetric = false;
      }

      return {
        name,
        isCur,
        isDone,
        isWait,
        topVal,
        subVal,
        durStr: hasMetric ? durStr ?? stateWord : null,
      };
    });
  }, [stages, currentStageIdx, pipeline?.current_stage_idx, isCompleted, isStopping, isFailed, isStopped, scoring, sources, stageStats, nowSec]);

  // Linia stacji: stacje stoją na środkach 7 kolumn. Jedna linia łączy pierwszą stację
  // z bieżącą (albo z ostatnią gotową, gdy nic nie biegnie); bieżącą wyróżnia sama kropka.
  const stationPct = (i: number) => ((i + 0.5) / STAGE_SHORT_NAMES.length) * 100;
  let lineEndIdx = -1;
  stageCols.forEach((c, i) => {
    if (c.isDone || c.isCur) lineEndIdx = i;
  });
  const doneLineStyle = { left: `${stationPct(0)}%`, width: `${Math.max(0, stationPct(lineEndIdx) - stationPct(0))}%` };

  const [gridEl, setGridEl] = useState<HTMLDivElement | null>(null);
  const [gridBox, setGridBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!gridEl) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setGridBox((b) => (b.w === width && b.h === height ? b : { w: width, h: height }));
    });
    ro.observe(gridEl);
    return () => ro.disconnect();
  }, [gridEl]);

  const tileTone = (i: number, n: number) => ({ '--t': n > 1 ? i / (n - 1) : 0 }) as React.CSSProperties;

  // Przed oceną AI karta paczek pokazuje źródła: kafelek = źródło. Scrapery pracują
  // równolegle, więc w pętli ładuje się każde biegnące źródło — wszystkie w jednym rytmie.
  const sourcesDone = sources.filter((s) => s.state === 'done' || s.state === 'skipped').length;
  const sourcesFound = sources.reduce((acc, s) => acc + (s.found ?? 0), 0);
  const sourceCells = sources.map((s, i) => {
    const live = s.state === 'running' && isRunning;
    let stateClass = '';
    if (s.state === 'done' || s.state === 'skipped') stateClass = 'done';
    else if (live) stateClass = 'cur';
    const tip = live && s.details_total
      ? `${s.name}: opisy ${(s.details_done ?? 0).toLocaleString('pl-PL')} / ${s.details_total.toLocaleString('pl-PL')}`
      : s.found !== null
        ? `${s.name}: ${s.found.toLocaleString('pl-PL')} ${plural(s.found, OFFERS)}`
        : s.name;
    return { name: s.name, stateClass, tip, styleObj: tileTone(i, sources.length) };
  });

  // Pętle ładowania startują z kafelkiem, więc równoległe źródła rozjeżdżały się w fazie
  // (jedno pełne, drugie puste). Wspólny start na osi dokumentu trzyma je w jednym rytmie.
  useEffect(() => {
    sheetRef.current?.getAnimations({ subtree: true }).forEach((a) => {
      if (a instanceof CSSAnimation && a.animationName === 'pp-pack-load' && a.startTime !== 0) a.startTime = 0;
    });
  });
  const grid = fitGrid(sources.length, gridBox.w, gridBox.h);
  const gridStyle = grid
    ? ({ gridTemplateColumns: `repeat(${grid.cols}, ${grid.cell}px)`, gap: grid.gap, '--cell': `${grid.cell}px` } as React.CSSProperties)
    : undefined;
  const runningSources = isRunning ? sources.filter((s) => s.state === 'running') : [];
  const detailed = runningSources.find((s) => s.details_total);
  const scrapingSkipped = stages[1]?.status === 'skipped';
  let sourcesBig = `${sourcesFound.toLocaleString('pl-PL')} ${plural(sourcesFound, OFFERS)}`;
  let sourcesSmall = 'łącznie ze wszystkich źródeł';
  if (detailed) {
    sourcesBig = `${detailed.name}: opisy ${(detailed.details_done ?? 0).toLocaleString('pl-PL')} / ${(detailed.details_total ?? 0).toLocaleString('pl-PL')}`;
  } else if (runningSources.length) {
    sourcesBig = `W toku: ${runningSources.map((s) => s.name).join(', ')}`;
  } else if (!sources.length) {
    sourcesBig = scrapingSkipped ? 'Bez pobierania' : 'Czeka na pobieranie';
    sourcesSmall = scrapingSkipped ? 'ten przebieg ocenia oferty z bazy' : 'kafelek = źródło ofert';
  }
  if (runningSources.length) {
    sourcesSmall = sourcesDone > 0
      ? `${sourcesFound.toLocaleString('pl-PL')} ${plural(sourcesFound, OFFERS)} z gotowych źródeł`
      : 'zbieranie list ofert';
  }

  // Dopasowanie: przesiew w kodzie (powody) + ocena Jev (postęp, tempo, błędy).
  const matchStage = stages[5];
  const toScore = scoring?.to_score ?? null;
  const scoredCount = scoring?.scored ?? 0;
  const scoringPct = toScore ? Math.min(100, Math.round((scoredCount / toScore) * 100)) : null;
  const scoringEta = isRunning ? scoringEtaSeconds(scoring, nowSec) : null;
  // Obok paska oceny: czas do końca w trakcie, potem tempo; liczby ocenionych są niżej.
  const meterAside = scoringEta !== null
    ? `~${formatDuration(scoringEta)}`
    : scoring?.rate ? `${scoring.rate.toLocaleString('pl-PL', { maximumFractionDigits: 1 })}/s` : '';
  const reasons = Object.entries(scoring?.prefilter_reasons ?? {})
    .filter(([, n]) => (n ?? 0) > 0)
    .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0)) as Array<[keyof typeof PREFILTER_LABELS, number]>;
  const jevErrors = scoring?.errors ?? 0;
  const withPercent = stageStats?.phase3?.with_percent;
  let matchBig = 'Czeka na etap dopasowania';
  if (matchStage?.status === 'skipped') matchBig = 'Pominięte w tym przebiegu';
  else if (toScore === 0) matchBig = 'Nic nowego do oceny';
  else if (toScore) matchBig = `${scoredCount.toLocaleString('pl-PL')} z ${toScore.toLocaleString('pl-PL')} ocenionych`;
  else if (matchStage?.status === 'running') matchBig = 'Przesiew ofert…';
  let matchSmall = 'przesiew w kodzie, ocena Jev';
  if (jevErrors > 0) matchSmall = `${jevErrors.toLocaleString('pl-PL')} ${plural(jevErrors, ERRORS)} Jev`;
  else if (withPercent !== undefined) matchSmall = `z procentem w bazie: ${withPercent.toLocaleString('pl-PL')}`;

  return (
    <>
      <div
        className={`pp-sheet-backdrop${shown ? ' is-open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        ref={sheetRef}
        className={`pp-sheet${shown ? ' is-open' : ''}`}
        role="dialog"
        aria-modal={open || undefined}
        aria-label="Pipeline — szczegóły"
        inert={!open}
      >
        {/* Nagłówek arkusza */}
        <div className="pp-sheet-head">
          <div className="pp-stage">
            <b>
              {stageHeaderTitle}
              {isRunning && (
                <i className="pp-dots" aria-hidden="true"><i>.</i><i>.</i><i>.</i></i>
              )}
            </b>
            <span>
              {isIdle
                ? 'spoczynek · oczekiwanie na start'
                : `${runLabel(pipeline)} · start ${startTimeStr} · ${elapsedWord} ${elapsedStr}`}
            </span>
          </div>

          <div className="pp-total">
            <span>{isIdle ? 0 : totalPct}</span>
            <small>%</small>
          </div>

          {/* Akcje nagłówka */}
          {isRunning && (
            <>
              <button
                type="button"
                className="pp-round press"
                onClick={onStop}
                data-tip="Zatrzymaj po bieżącym etapie"
                aria-label="Zatrzymaj po bieżącym etapie"
              >
                <Pause size={16} />
              </button>
              <button
                type="button"
                className="pp-round press"
                onClick={onForceStop}
                data-tip="Wymuś zatrzymanie natychmiast"
                aria-label="Wymuś zatrzymanie"
              >
                <XOctagon size={16} />
              </button>
            </>
          )}

          {isStopping && (
            <button type="button" className="pp-round" disabled aria-label="Kończę bieżący etap…">
              <Loader2 size={16} className="pp-spin" />
            </button>
          )}

          {(isFailed || isStopped) && pipeline?.can_resume && (
            <button
              type="button"
              className="pp-round press"
              onClick={onResume}
              data-tip="Wznów pipeline"
              aria-label="Wznów pipeline"
            >
              <Play size={16} />
            </button>
          )}

          <button
            type="button"
            className="pp-round pp-collapse press"
            onClick={onClose}
            data-tip="Zwiń arkusz"
            data-kbd="Esc"
            aria-label="Zamknij arkusz"
          >
            <ChevronUp size={16} />
          </button>
        </div>

        {/* 7 kolumn etapów */}
        <div className="pp-flow">
          {/* Strumień shaderowy */}
          <div className="pp-band">
            <Stream
              progress={isIdle ? 0 : progress}
              pulse={scoredCount}
              loop={isRunning ? SHEET_STREAM_LOOP : undefined}
              depth={0.552}
              width={26}
              cross={3.94 / 0.6}
              edge={1}
              className="pp-sheet-canvas"
            />
          </div>

          {/* Linia stacji */}
          <div className="pp-stations">
            <div className="line">
              <div className="pp-done-line" style={doneLineStyle} />
              {stageCols.map((c, i) => {
                let dotClass = 'pp-station-dot st';
                if (c.isDone) dotClass += ' done';
                else if (c.isCur) dotClass += ' cur';
                return <div key={i} className={dotClass} />;
              })}
            </div>

            <div className="pp-cols names">
              {stageCols.map((c, i) => (
                <div key={i} className={`c ${c.isCur ? 'cur' : ''} ${c.isWait ? 'wait' : ''}`}>
                  <b>{c.name}</b>
                  <strong>{c.topVal}</strong>
                  <span>{c.subVal}</span>
                  {c.durStr && <time>{c.durStr}</time>}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Trzy karty telemetryczne */}
        <div className="pp-now">
          {/* Karta 1: Źródła */}
          <div className="pp-card">
            <div className="h">
              <b>Źródła</b>
              <span>{`${sourcesDone} / ${sources.length}`}</span>
            </div>

            <div className="pp-packs" ref={setGridEl} style={gridStyle}>
              {sourceCells.map(({ name, stateClass, tip, styleObj }) => (
                <div key={name} className={`pp-pack ${stateClass}`} style={styleObj} data-tip={tip} />
              ))}
            </div>

            <div>
              <div className="big">{sourcesBig}</div>
              <div className="small">{sourcesSmall}</div>
            </div>
          </div>

          {/* Karta 2: Dopasowanie — przesiew w kodzie, potem ocena Jev */}
          <div className="pp-card">
            <div className="h">
              <b>Dopasowanie</b>
              <span>{scoringPct !== null ? `${scoringPct}%` : '—'}</span>
            </div>

            <div className="pp-match">
              {toScore ? (
                <div className="pp-meter">
                  <div className="pp-meter-head">
                    <span>Ocena Jev</span>
                    <span className="tnum">{meterAside}</span>
                  </div>
                  <div className="pp-meter-track">
                    <i style={{ width: `${scoringPct}%` }} />
                  </div>
                </div>
              ) : null}
              {reasons.length > 0 ? (
                <div className="pp-reasons">
                  <div className="pp-meter-head">
                    <span>Przesiew odrzucił</span>
                    <span className="tnum">{(scoring?.prefilter_rejected ?? 0).toLocaleString('pl-PL')}</span>
                  </div>
                  <dl>
                    {reasons.map(([reason, n]) => (
                      <div key={reason}>
                        <dt>{PREFILTER_LABELS[reason] ?? reason}</dt>
                        <dd className="tnum">{n.toLocaleString('pl-PL')}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ) : !toScore ? (
                <div className="hint">
                  Przesiew w kodzie odrzuca pewne niedopasowania (miasto, poziom, lata, język), Jev ocenia resztę względem CV.
                </div>
              ) : null}
            </div>

            <div>
              <div className="big">{matchBig}</div>
              <div className="small">{matchSmall}</div>
            </div>
          </div>
          {/* Karta 3: Na żywo */}
          <div className="pp-card">
            <div className="h">
              <b>Na żywo</b>
              <button
                type="button"
                className="pp-log-toggle"
                onClick={() => setShowFullLog(!showFullLog)}
              >
                {showFullLog ? (
                  <>zwiń dziennik <ChevronDown size={12} /></>
                ) : (
                  <>dziennik <ArrowUpRight size={12} /></>
                )}
              </button>
            </div>

            <div className="pp-ticker">
              {lastLogs.length === 0 ? (
                <div className="small">Brak bieżących komunikatów</div>
              ) : (
                lastLogs.map((l, idx) => (
                  <div key={parsedLogs.length - lastLogs.length + idx} className="pp-tk">
                    <time>{l.time}</time>
                    <span title={l.text}>{l.text}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Rozwinięty dziennik logów */}
        {showFullLog && (
          <div className="pp-log-console-wrap">
            <div className="pp-log-console" ref={logConsoleRef}>
              {parsedLogs.length === 0 ? (
                <div style={{ color: 'var(--ink-3)' }}>Brak logów w buforze.</div>
              ) : (
                parsedLogs.map((l, i) => (
                  <div key={i} className={`pp-log-line is-${l.tone}`}>
                    <span className="time">{l.time || '—'}</span>
                    <span className="msg">{l.text}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* Dolny wiersz: Pojedyncze kroki (etykieta w jednej ramce z przyciskami) i Uruchom */}
        <div className="pp-steps">
          <div className="pp-steps-group" role="group" aria-label="Pojedyncze kroki">
            <span className="lbl">Pojedyncze kroki</span>

            <div className="pp-steps-actions">
              <button
                type="button"
                className="iconbtn"
                onClick={() => onRunStep('scrapers')}
                data-tip="Pobierz oferty ze źródeł (bez oceny)"
                aria-label="Pobierz oferty ze źródeł"
                disabled={isRunning || isStopping}
              >
                <Globe size={16} />
              </button>

              <button
                type="button"
                className="iconbtn"
                onClick={() => onRunStep('matching')}
                data-tip="Oceń nowe oferty (przesiew + Jev)"
                aria-label="Oceń nowe oferty"
                disabled={isRunning || isStopping}
              >
                <Sparkles size={16} />
              </button>

              {confirmingRescore ? (
                <div className="pp-confirm-rescore">
                  <span>Ocenić całą bazę od nowa w Jev?</span>
                  <button
                    type="button"
                    className="btn btn-primary"
                    style={{ height: 28, padding: '0 10px', fontSize: 11 }}
                    onClick={() => {
                      setConfirmingRescore(false);
                      onRunStep('rescore_all');
                    }}
                  >
                    Tak, przelicz
                  </button>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    style={{ height: 28, padding: '0 8px', fontSize: 11 }}
                    onClick={() => setConfirmingRescore(false)}
                  >
                    Anuluj
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="iconbtn"
                  onClick={() => setConfirmingRescore(true)}
                  data-tip="Oceń całą bazę od nowa, także już ocenione oferty (płatne, wymaga potwierdzenia)"
                  aria-label="Oceń całą bazę od nowa"
                  disabled={isRunning || isStopping}
                >
                  <RotateCcw size={16} />
                </button>
              )}

              <button
                type="button"
                className="iconbtn"
                onClick={onReloadDatabase}
                data-tip="Przeładuj bazę ofert z dysku"
                aria-label="Odśwież bazę z dysku"
              >
                <Database size={16} />
              </button>
            </div>
          </div>

          <button
            type="button"
            className="btn btn-secondary pp-steps-run"
            disabled={isRunning || isStopping}
            onClick={() => {
              onClose();
              onOpenLaunchModal();
            }}
          >
            <Play size={14} /> Uruchom pipeline…
          </button>
        </div>
      </aside>
    </>
  );
};
