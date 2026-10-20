import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import { AppContext, type AppContextValue, type MarkSentOffer, type MarkSentOptions } from './app_context';
import { navigate, paths, useRoute } from './router';
import { SCREENS } from './screens';
import { LaunchModal } from './shell/LaunchModal';
import { RunDoneModal, type RunDoneState } from './shell/RunDoneModal';
import { MarkSentModal, type MarkSentState } from './shell/MarkSentModal';
import { SettingsModal } from './shell/SettingsModal';
import { Toasts, useToasts } from './shell/Toasts';
import { TopBar } from './shell/TopBar';
import { currentStageId, isBusy, scoringProgress } from './run_progress';
import type { CandidatesResponse, CVInfo, DecisionStatus, OfferDetail, PipelineState, RunSummary } from './types';

const POLL_BUSY_MS = 1200;
const POLL_IDLE_MS = 4000;
const SUMMARY_PILL_MS = 3000;

export function App() {
  const route = useRoute();
  const { toasts, toast, dismiss } = useToasts();

  const [pipeline, setPipeline] = useState<PipelineState | null>(null);
  const [runSummary, setRunSummary] = useState<RunSummary | null>(null);
  const [cv, setCv] = useState<CVInfo | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [markSent, setMarkSent] = useState<MarkSentState | null>(null);
  const [runDone, setRunDone] = useState<RunDoneState | null>(null);
  const [candidates, setCandidates] = useState<CandidatesResponse | null>(null);
  const [candidateEpoch, setCandidateEpoch] = useState(0);
  const candidatesRef = useRef<CandidatesResponse | null>(null);

  const pipelineBusy = isBusy(pipeline);
  const routeRef = useRef(route);
  routeRef.current = route;

  const bumpData = useCallback(() => setDataVersion((v) => v + 1), []);

  const pipelineText = useRef('');
  const applyPipeline = useCallback((next: PipelineState) => {
    const text = JSON.stringify(next);
    if (text === pipelineText.current) return;
    pipelineText.current = text;
    setPipeline(next);
  }, []);

  const summaryText = useRef('');
  const applySummary = useCallback((next: RunSummary | null) => {
    const text = next ? JSON.stringify(next) : '';
    if (next && text === summaryText.current) return;
    summaryText.current = text;
    setRunSummary(next);
  }, []);

  const cvRequest = useRef(0);
  const loadCv = useCallback(async () => {
    const request = ++cvRequest.current;
    try {
      const info = await api.getCV();
      if (request !== cvRequest.current) return null;
      setCv(info);
      return info;
    } catch {
      return null;
    }
  }, []);

  const applyCandidates = useCallback(
    async (next: CandidatesResponse) => {
      const previous = candidatesRef.current;
      candidatesRef.current = next;
      setCandidates(next);
      if (!previous || previous.active === next.active) return;
      const info = await loadCv();
      const active = next.items.find((c) => c.id === next.active);
      setCandidateEpoch((n) => n + 1);
      bumpData();
      navigate((info?.ready ?? active?.has_cv) ? paths.matched() : paths.start, { replace: true });
    },
    [loadCv, bumpData],
  );

  const refreshCandidates = useCallback(async () => {
    try {
      await applyCandidates(await api.getCandidates());
    } catch {
      return;
    }
  }, [applyCandidates]);

  const refreshCv = useCallback(async () => {
    const [info] = await Promise.all([loadCv(), refreshCandidates()]);
    return info;
  }, [loadCv, refreshCandidates]);

  const summaryInFlight = useRef(false);
  const refreshRunSummary = useCallback(async () => {
    if (summaryInFlight.current) return;
    summaryInFlight.current = true;
    try {
      applySummary(await api.getRunSummary());
    } catch {
      applySummary(null);
    } finally {
      summaryInFlight.current = false;
    }
  }, [applySummary]);

  useEffect(() => {
    refreshCv();
  }, [refreshCv]);

  const actionSeq = useRef(0);
  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    let inFlight = false;
    const tick = async () => {
      window.clearTimeout(timer);
      if (inFlight) return;
      inFlight = true;
      const seq = actionSeq.current;
      let next: PipelineState | null = null;
      try {
        next = await api.getPipelineState();
      } catch {
        next = null;
      }
      inFlight = false;
      if (cancelled) return;
      const stale = seq !== actionSeq.current;
      if (next && !stale) applyPipeline(next);
      if (document.visibilityState === 'visible') {
        timer = window.setTimeout(tick, stale || isBusy(next) ? POLL_BUSY_MS : POLL_IDLE_MS);
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') tick();
    };
    document.addEventListener('visibilitychange', onVisibility);
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [applyPipeline]);

  const onProgress = route.name === 'postep';
  const pillNeedsSummary =
    pipelineBusy &&
    pipeline?.status !== 'stopping' &&
    !(pipeline && currentStageId(pipeline) === 'phase3' && scoringProgress(pipeline)?.total != null);
  const summaryAt = useRef(0);
  useEffect(() => {
    if (!pipeline || !(onProgress || pillNeedsSummary)) return;
    if (!onProgress && Date.now() - summaryAt.current < SUMMARY_PILL_MS) return;
    summaryAt.current = Date.now();
    refreshRunSummary();
  }, [onProgress, pillNeedsSummary, pipeline, refreshRunSummary]);

  const profileDone = pipeline?.stages.find((s) => s.id === 'phase0')?.status === 'done';
  const profileWasDone = useRef(profileDone);
  useEffect(() => {
    if (pipelineBusy && profileDone && !profileWasDone.current) refreshCandidates();
    profileWasDone.current = profileDone;
  }, [pipelineBusy, profileDone, refreshCandidates]);

  const wasBusy = useRef(false);
  useEffect(() => {
    if (!pipeline) return;
    const busy = isBusy(pipeline);
    if (wasBusy.current && !busy) {
      bumpData();
      refreshCv();
      if (pipeline.status === 'completed' || pipeline.status === 'failed') {
        const finished = pipeline;
        const showDone = (summary: RunSummary | null) => {
          if (routeRef.current.name !== 'postep') setRunDone({ pipeline: finished, summary });
        };
        api.getRunSummary().then(
          (summary) => {
            applySummary(summary);
            showDone(summary);
          },
          () => showDone(null),
        );
      } else if (pipeline.status === 'stopped') {
        toast('Wyszukiwanie zatrzymane.');
      }
    }
    wasBusy.current = busy;
  }, [pipeline, bumpData, refreshCv, toast, applySummary]);

  const cvMissing = cv !== null && !cv.ready;
  useEffect(() => {
    if (cvMissing && route.name !== 'start' && route.name !== 'postep') navigate(paths.start, { replace: true });
  }, [cvMissing, route.name]);

  const startSearch = useCallback(async () => {
    actionSeq.current += 1;
    try {
      const res = await api.startPipeline('full');
      actionSeq.current += 1;
      applyPipeline(res.state);
      applySummary(null);
      setRunDone(null);
      navigate(paths.progress);
      return true;
    } catch (err) {
      toast(`Nie udało się uruchomić wyszukiwania: ${errorMessage(err)}`, { icon: TriangleAlert });
      return false;
    }
  }, [toast, applyPipeline, applySummary]);

  const stopSearch = useCallback(async () => {
    actionSeq.current += 1;
    try {
      const res = await api.stopPipeline();
      actionSeq.current += 1;
      applyPipeline(res.state);
      if (!res.ok && isBusy(res.state)) toast(res.message, { icon: TriangleAlert });
    } catch (err) {
      toast(`Nie udało się zatrzymać: ${errorMessage(err)}`, { icon: TriangleAlert });
    }
  }, [toast, applyPipeline]);

  const openLaunch = useCallback(() => {
    if (pipelineBusy) navigate(paths.progress);
    else setLaunchOpen(true);
  }, [pipelineBusy]);

  const openSettings = useCallback(() => setSettingsOpen(true), []);

  const openMarkSent = useCallback(
    async (offer: MarkSentOffer, opts: MarkSentOptions = {}) => {
      const previousStatus = (['save', 'apply', 'reject'].includes(offer.status ?? '') ? offer.status : null) as
        | DecisionStatus
        | null;
      try {
        let detail: OfferDetail;
        if (previousStatus === 'apply') {
          detail = await api.getOfferDetail(offer.link);
        } else {
          const versions =
            offer.cv_versions ??
            (offer.cv && offer.cv.status !== 'ready' ? (await api.listCvVersions(offer.link)).items : undefined);
          const readyCv = versions
            ? versions.find((v) => v.status === 'ready')
            : offer.cv?.status === 'ready'
              ? offer.cv
              : undefined;
          const res = await api.updateDecision(offer.link, 'apply', 'apply', readyCv?.id);
          detail = res.offer;
          bumpData();
          opts.onChange?.(detail);
        }
        setMarkSent({ offer: detail, previousStatus, onChange: opts.onChange });
      } catch (err) {
        toast(`Nie udało się oznaczyć: ${errorMessage(err)}`, { icon: TriangleAlert });
      }
    },
    [bumpData, toast],
  );

  const context = useMemo<AppContextValue>(
    () => ({
      pipeline,
      runSummary,
      pipelineBusy,
      startSearch,
      stopSearch,
      cv,
      refreshCv,
      candidates,
      applyCandidates,
      dataVersion,
      bumpData,
      toast,
      openLaunch,
      openSettings,
      settingsOpen,
      openMarkSent,
    }),
    [
      pipeline,
      runSummary,
      pipelineBusy,
      startSearch,
      stopSearch,
      cv,
      refreshCv,
      candidates,
      applyCandidates,
      dataVersion,
      bumpData,
      toast,
      openLaunch,
      openSettings,
      settingsOpen,
      openMarkSent,
    ],
  );

  const Screen = SCREENS[route.name];
  const minimal = route.name === 'start' || (route.name === 'postep' && cvMissing && !pipelineBusy);
  const blockedByGate = cvMissing && route.name !== 'start' && route.name !== 'postep';

  return (
    <AppContext.Provider value={context}>
      <div className="app">
        <TopBar minimal={minimal} running={pipelineBusy} />
        <main className="workspace">
          {!blockedByGate && <Screen key={`${candidateEpoch}:${route.path}`} route={route} />}
        </main>
      </div>
      <div className="dock">
        <Toasts toasts={toasts} dismiss={dismiss} />
      </div>
      {launchOpen && <LaunchModal onClose={() => setLaunchOpen(false)} />}
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
      {markSent && <MarkSentModal state={markSent} onClose={() => setMarkSent(null)} />}
      {runDone && <RunDoneModal state={runDone} onClose={() => setRunDone(null)} />}
    </AppContext.Provider>
  );
}
