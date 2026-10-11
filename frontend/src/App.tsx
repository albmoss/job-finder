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
import { isBusy } from './run_progress';
import type { CandidatesResponse, CVInfo, DecisionStatus, OfferDetail, PipelineState, RunSummary } from './types';

const POLL_BUSY_MS = 1200;
const POLL_IDLE_MS = 4000;

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

  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    let inFlight = false;
    const tick = async () => {
      window.clearTimeout(timer);
      if (inFlight) return;
      inFlight = true;
      let next: PipelineState | null = null;
      try {
        next = await api.getPipelineState();
      } catch {
        next = null;
      }
      inFlight = false;
      if (cancelled) return;
      if (next) applyPipeline(next);
      if (document.visibilityState === 'visible') {
        timer = window.setTimeout(tick, isBusy(next) ? POLL_BUSY_MS : POLL_IDLE_MS);
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

  const watchSummary = pipelineBusy || route.name === 'postep';
  useEffect(() => {
    if (watchSummary && pipeline) refreshRunSummary();
  }, [watchSummary, pipeline, refreshRunSummary]);

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
        api.getRunSummary().then(
          (summary) => {
            applySummary(summary);
            setRunDone({ pipeline: finished, summary });
          },
          () => setRunDone({ pipeline: finished, summary: null }),
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
    try {
      const res = await api.startPipeline('full');
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
    try {
      applyPipeline((await api.stopPipeline()).state);
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
      const readyCv = offer.cv_versions
        ? offer.cv_versions.find((v) => v.status === 'ready')
        : offer.cv?.status === 'ready'
          ? offer.cv
          : undefined;
      try {
        let detail: OfferDetail;
        if (previousStatus === 'apply') {
          detail = await api.getOfferDetail(offer.link);
        } else {
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
