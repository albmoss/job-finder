import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Pause,
  Play,
  ChevronUp,
  Globe,
  Brain,
  Sparkles,
  RotateCcw,
  Database,
  ArrowUpRight,
  Loader2,
  XOctagon,
  ChevronDown,
} from 'lucide-react';
import type { ActivityRow, PipelineState, Stats } from '../types';
import { Stream } from './ui/Stream';
import { KEYS, OFFERS, plural } from '../plural';
import {
  SHEET_STREAM_LOOP,
  STAGE_SHORT_NAMES,
  calculateOverallPipelineProgress,
  fitGrid,
  formatClock,
  formatDuration,
  parseLogLine,
  pipelineStatus,
  scoringPacks,
  stageDuration,
  useNowSeconds,
} from '../pipeline';
import '../styles/pipeline.css';

export interface PipelineSheetProps {
  open: boolean;
  onClose(): void;
  pipeline: PipelineState | null;
  activityRows: ActivityRow[];
  stats: Stats | null;
  onStop(): void;
  onForceStop(): void;
  onResume(): void;
  onRunStep(step: 'scrapers' | 'profile' | 'analysis' | 'rescore_all'): void;
  onReloadDatabase(): void;
  onOpenLaunchModal(): void;
}

export const PipelineSheet: React.FC<PipelineSheetProps> = ({
  open,
  onClose,
  pipeline,
  activityRows: _activityRows,
  stats: _stats,
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

  // Karta „Na żywo” jest szersza od dwóch pozostałych — mieści cztery ostatnie komunikaty.
  const lastLogs = useMemo(() => parsedLogs.slice(-4), [parsedLogs]);

  // Czas trwania przebiegu
  const elapsedStr = useMemo(() => {
    if (!pipeline?.started_at) return '—';
    const end = pipeline.finished_at ?? nowSec;
    return formatDuration(end - pipeline.started_at);
  }, [pipeline?.started_at, pipeline?.finished_at, nowSec]);

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
      // Bez biegnącego etapu „bieżący” jest tylko tam, gdzie przebieg stanął (błąd, stop).
      const isCur = currentStageIdx === idx
        || (currentStageIdx === -1 && (isStopping || isFailed || isStopped) && pipeline?.current_stage_idx === idx);
      const isDone = stage?.status === 'done' || stage?.status === 'skipped' || isCompleted;
      const isWait = !isCur && !isDone;
      const stateWord = isDone ? 'gotowe' : isCur ? 'w toku' : 'czeka';

      const dur = stage ? stageDuration(stage, nowSec) : null;
      const durStr = dur !== null ? formatDuration(dur) : null;

      // Liczby tylko tam, gdzie stan przebiegu je naprawdę ma; reszta pokazuje czas etapu.
      let topVal = durStr ?? '—';
      let subVal = stateWord;
      const totalFound = sources.reduce((acc, s) => acc + (s.found ?? 0), 0);
      const diet = stageStats?.phase2_5;
      if (idx === 1 && totalFound > 0) {
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
      } else if (idx === 5 && scoring && (scoring.processed > 0 || scoring.total)) {
        topVal = scoring.processed.toLocaleString('pl-PL');
        subVal = scoring.total ? `z ${scoring.total.toLocaleString('pl-PL')} nowych` : 'ocenionych';
      }

      return {
        name,
        isCur,
        isDone,
        isWait,
        topVal,
        subVal,
        durStr: durStr ?? stateWord,
      };
    });
  }, [stages, currentStageIdx, pipeline?.current_stage_idx, isCompleted, isStopping, isFailed, isStopped, scoring, sources, stageStats, nowSec]);

  // Linia stacji: stacje stoją na środkach 7 kolumn. Biała linia łączy gotowe etapy, kobaltowy
  // odcinek prowadzi od ostatniego gotowego do biegnącego — widać, który odcinek trwa.
  const stationPct = (i: number) => ((i + 0.5) / STAGE_SHORT_NAMES.length) * 100;
  let lastDoneIdx = -1;
  stageCols.forEach((c, i) => {
    if (c.isDone) lastDoneIdx = i;
  });
  const doneLineStyle = { left: `${stationPct(0)}%`, width: `${Math.max(0, stationPct(lastDoneIdx) - stationPct(0))}%` };
  const runLineStyle = isRunning && currentStageIdx >= 1
    ? { left: `${stationPct(currentStageIdx - 1)}%`, width: `${stationPct(currentStageIdx) - stationPct(currentStageIdx - 1)}%` }
    : null;

  // Paczki (Karta 1): tyle kafelków, ile paczek ma przebieg; siatka dopasowuje rozmiar
  // kafelka do wolnego pola karty (fitGrid), więc 3 paczki i 300 paczek wypełniają ją tak samo.
  const packs = scoringPacks(scoring, nowSec);
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

  // Kolor gotowego kafelka idzie po siatce jak strumień: kobalt → fiolet.
  const tileTone = (i: number, n: number) => ({ '--t': n > 1 ? i / (n - 1) : 0 }) as React.CSSProperties;
  const packCells = Array.from({ length: packs?.total ?? 0 }, (_, i) => {
    let stateClass = '';
    let styleObj = tileTone(i, packs?.total ?? 0);
    if (isCompleted || (packs && i < packs.done)) {
      stateClass = 'done';
    } else if (packs && i === packs.done && isRunning) {
      stateClass = packs.current === null ? 'cur is-busy' : 'cur';
      styleObj = { ...styleObj, '--p': packs.current ?? 0 } as React.CSSProperties;
    }
    return { packIdx: i + 1, stateClass, styleObj };
  });

  // Przed oceną AI karta paczek pokazuje źródła: kafelek = źródło, wypełnienie = pobrane
  // opisy (meldunki scraperów). Inaczej przez cały scraping stało siedem pustych kratek.
  const sourcesDone = sources.filter((s) => s.state === 'done' || s.state === 'skipped').length;
  const sourcesFound = sources.reduce((acc, s) => acc + (s.found ?? 0), 0);
  const sourceCells = sources.map((s, i) => {
    const hasDetails = !!s.details_total;
    const live = s.state === 'running' && isRunning;
    let stateClass = '';
    let styleObj = tileTone(i, sources.length);
    if (s.state === 'done' || s.state === 'skipped') stateClass = 'done';
    else if (live) {
      stateClass = hasDetails ? 'cur' : 'cur is-busy';
      const fraction = hasDetails ? Math.min(1, (s.details_done ?? 0) / (s.details_total ?? 1)) : 0;
      styleObj = { ...styleObj, '--p': fraction } as React.CSSProperties;
    }
    const tip = hasDetails && live
      ? `${s.name}: opisy ${(s.details_done ?? 0).toLocaleString('pl-PL')} / ${(s.details_total ?? 0).toLocaleString('pl-PL')}`
      : s.found !== null
        ? `${s.name}: ${s.found.toLocaleString('pl-PL')} ${plural(s.found, OFFERS)}`
        : s.name;
    return { name: s.name, stateClass, tip, styleObj };
  });
  const showSources = !scoring && sources.length > 0;
  const grid = fitGrid(showSources ? sources.length : packCells.length, gridBox.w, gridBox.h);
  const gridStyle = grid
    ? ({ gridTemplateColumns: `repeat(${grid.cols}, ${grid.cell}px)`, gap: grid.gap, '--cell': `${grid.cell}px` } as React.CSSProperties)
    : undefined;
  const runningSources = isRunning ? sources.filter((s) => s.state === 'running') : [];
  const detailed = runningSources.find((s) => s.details_total);
  let sourcesBig = `${sourcesFound.toLocaleString('pl-PL')} ${plural(sourcesFound, OFFERS)}`;
  let sourcesSmall = 'łącznie ze wszystkich źródeł';
  if (detailed) {
    sourcesBig = `${detailed.name}: opisy ${(detailed.details_done ?? 0).toLocaleString('pl-PL')} / ${(detailed.details_total ?? 0).toLocaleString('pl-PL')}`;
  } else if (runningSources.length) {
    sourcesBig = `W toku: ${runningSources.map((s) => s.name).join(', ')}`;
  }
  if (runningSources.length) {
    sourcesSmall = sourcesDone > 0
      ? `${sourcesFound.toLocaleString('pl-PL')} ${plural(sourcesFound, OFFERS)} z gotowych źródeł`
      : 'zbieranie list ofert';
  }

  // Klucze API (Karta 2)
  // Liczba kluczy jest w telemetrii od startu przebiegu; aktywny klucz i model — dopiero z oceny.
  const keyCount = scoring?.key_count ?? pipeline?.telemetry?.key_count ?? 0;
  const activeKeyIdx = scoring?.key ?? 0;
  const cooldowns = scoring?.cooldowns ?? {};
  const activeModelName = scoring?.model ?? '';
  const keysWorking = isRunning && !!scoring;

  const keyItems = Array.from({ length: keyCount }, (_, i) => {
    const coolUntil = cooldowns[String(i)];
    const isCool = coolUntil !== undefined && coolUntil > nowSec;
    const remainingSecs = isCool ? Math.ceil(coolUntil - nowSec) : 0;
    const isOn = !isCool && activeKeyIdx === i && keysWorking;
    const state = isCool ? `limit, wraca za ${remainingSecs} s` : isOn ? 'pracuje' : 'gotowy';
    return { idx: i, isCool, remainingSecs, isOn, state };
  });

  const activeCooldownKey = keyItems.find((k) => k.isCool);

  // Pulse dla Stream
  const batchPulse = scoring?.processed ?? scoring?.batch ?? 0;

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
                : `pełny przebieg · start ${startTimeStr} · trwa ${elapsedStr}`}
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
                data-tip="Wstrzymaj po paczce"
                aria-label="Zatrzymaj po paczce"
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
            <button type="button" className="pp-round" disabled aria-label="Kończę paczkę…">
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
          <div className="pp-cols">
            {stageCols.map((c, i) => (
              <div key={i} className={`c ${c.isCur ? 'cur' : ''} ${c.isWait ? 'wait' : ''}`}>
                <b>{c.topVal}</b>
                <span>{c.subVal}</span>
              </div>
            ))}
          </div>

          {/* Strumień shaderowy */}
          <div className="pp-band">
            <Stream
              progress={isIdle ? 0 : progress}
              pulse={batchPulse}
              loop={isRunning ? SHEET_STREAM_LOOP : undefined}
              depth={0.552}
              width={26}
              cross={3.94 / 0.6}
              edge={1}
              ha={[0.1, 0.6, 0.8, 0.84]}
              hb={[0.72, 0.44, 0.26, 0]}
              className="pp-sheet-canvas"
            />
          </div>

          {/* Linia stacji */}
          <div className="pp-stations">
            <div className="line">
              <div className="pp-done-line" style={doneLineStyle} />
              {runLineStyle && <div className="pp-run-line" style={runLineStyle} />}
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
                  <span>{c.durStr}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Trzy karty telemetryczne */}
        <div className="pp-now">
          {/* Karta 1: Źródła (do oceny AI), potem paczki. Siatka wypełnia wolne pole karty. */}
          {showSources ? (
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
          ) : (
            <div className="pp-card">
              <div className="h">
                <b>{packs ? `Paczki po ${packs.size}` : 'Paczki'}</b>
                <span>{packs ? `${packs.done} / ${packs.total}` : '—'}</span>
              </div>

              <div className="pp-packs" ref={setGridEl} style={gridStyle}>
                {packCells.map(({ packIdx, stateClass, styleObj }) => (
                  <div key={packIdx} className={`pp-pack ${stateClass}`} style={styleObj} />
                ))}
              </div>

              <div className="hint">
                {scoring?.total === 0
                  ? 'nic nowego do oceny'
                  : packs
                    ? 'paczka zapisuje się atomowo — stop po niej nic nie gubi'
                    : 'liczba paczek pojawi się ze startem oceny AI'}
              </div>
            </div>
          )}

          {/* Karta 2: Klucze API — kafelek na klucz, rozciągnięty na całe pole karty */}
          <div className="pp-card">
            <div className="h">
              <b>Klucze API</b>
              <span>{activeModelName}</span>
            </div>

            <div
              className="pp-keys"
              style={{ gridTemplateColumns: `repeat(${keyCount <= 6 ? Math.max(1, keyCount) : Math.ceil(keyCount / 2)}, minmax(0, 1fr))` }}
            >
              {keyItems.map((k) => (
                <div
                  key={k.idx}
                  className={`pp-key${k.isOn ? ' on' : ''}${k.isCool ? ' cool' : ''}`}
                  style={k.isCool ? ({ '--cool': Math.min(1, k.remainingSecs / 70) } as React.CSSProperties) : undefined}
                  data-tip={`Klucz ${k.idx + 1}: ${k.state}`}
                >
                  <b>{k.idx + 1}</b>
                  {k.isCool && <span>{k.remainingSecs}s</span>}
                </div>
              ))}
            </div>

            <div>
              <div className="big">
                {keyCount === 0 || !scoring
                  ? 'Czeka na ocenę AI'
                  : keysWorking
                    ? `Pracuje klucz ${activeKeyIdx + 1} z ${keyCount}`
                    : `Skonfigurowano ${keyCount} ${plural(keyCount, KEYS)}`}
              </div>
              <div className="small">
                {activeCooldownKey
                  ? `klucz ${activeCooldownKey.idx + 1} trafił limit — wraca za ${activeCooldownKey.remainingSecs}s`
                  : keyCount > 0 && !scoring
                    ? `${keyCount} ${plural(keyCount, KEYS)} w rotacji przy limitach`
                    : 'rotacja modeli i kluczy przy limitach'}
              </div>
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
                <div className="small" style={{ marginTop: 8 }}>Brak bieżących komunikatów</div>
              ) : (
                lastLogs.map((l, idx) => {
                  // Najnowszy u dołu w pełnym kolorze, starsze coraz bledsze.
                  const opacity = 1 - (lastLogs.length - 1 - idx) * 0.22;
                  return (
                    <div key={idx} className="pp-tk" style={{ opacity }}>
                      <time>{l.time || '—'}</time>
                      <span title={l.text}>{l.text}</span>
                    </div>
                  );
                })
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
                data-tip="Pobierz oferty ze scraperów"
                aria-label="Pobierz ze scraperów"
                disabled={isRunning || isStopping}
              >
                <Globe size={16} />
              </button>

              <button
                type="button"
                className="iconbtn"
                onClick={() => onRunStep('profile')}
                data-tip="Przelicz profil preferencji z decyzji"
                aria-label="Przelicz profil preferencji"
                disabled={isRunning || isStopping}
              >
                <Brain size={16} />
              </button>

              <button
                type="button"
                className="iconbtn"
                onClick={() => onRunStep('analysis')}
                data-tip="Oceń brakujące oferty (waterfall AI)"
                aria-label="Oceń brakujące oferty"
                disabled={isRunning || isStopping}
              >
                <Sparkles size={16} />
              </button>

              {confirmingRescore ? (
                <div className="pp-confirm-rescore">
                  <span>Przeliczyć wszystko od nowa?</span>
                  <button
                    type="button"
                    className="btn btn-primary"
                    style={{ height: 28, padding: '0 10px', fontSize: 11 }}
                    onClick={() => {
                      setConfirmingRescore(false);
                      onRunStep('rescore_all');
                    }}
                  >
                    Tak, rescore
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
                  data-tip="Przelicz wszystkie oceny od zera (wymaga potwierdzenia)"
                  aria-label="Przelicz wszystkie oceny od nowa"
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
