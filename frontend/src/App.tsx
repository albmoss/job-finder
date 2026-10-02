import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CircleCheck, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import { AppContext, type AppContextValue, type MarkSentOffer, type MarkSentOptions } from './app_context';
import { navigate, paths, useRoute } from './router';
import { SCREENS } from './screens';
import { LaunchModal } from './shell/LaunchModal';
import { MarkSentModal, type MarkSentState } from './shell/MarkSentModal';
import { MiniPipelineCard } from './shell/MiniPipelineCard';
import { SettingsModal } from './shell/SettingsModal';
import { Toasts, useToasts } from './shell/Toasts';
import { TopBar } from './shell/TopBar';
import type { CVInfo, DecisionStatus, OfferDetail, PipelineState, RunSummary } from './types';

const POLL_BUSY_MS = 1200;
const POLL_IDLE_MS = 4000;

function isBusy(state: PipelineState | null): boolean {
  return Boolean(state && (state.running || state.status === 'stopping'));
}

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

  const pipelineBusy = isBusy(pipeline);
  const routeRef = useRef(route);
  routeRef.current = route;

  const bumpData = useCallback(() => setDataVersion((v) => v + 1), []);

  const refreshCv = useCallback(async () => {
    try {
      const info = await api.getCV();
      setCv(info);
      return info;
    } catch {
      return null;
    }
  }, []);

  const refreshRunSummary = useCallback(async () => {
    try {
      setRunSummary(await api.getRunSummary());
    } catch {
      setRunSummary(null);
    }
  }, []);

  useEffect(() => {
    refreshCv();
  }, [refreshCv]);

  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    const tick = async () => {
      let next: PipelineState | null = null;
      try {
        next = await api.getPipelineState();
      } catch {
        next = null;
      }
      if (cancelled) return;
      if (next) setPipeline(next);
      timer = window.setTimeout(tick, isBusy(next) ? POLL_BUSY_MS : POLL_IDLE_MS);
    };
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const watchSummary = pipelineBusy || route.name === 'postep';
  useEffect(() => {
    if (watchSummary && pipeline) refreshRunSummary();
  }, [watchSummary, pipeline, refreshRunSummary]);

  const wasBusy = useRef(false);
  useEffect(() => {
    if (!pipeline) return;
    const busy = isBusy(pipeline);
    if (wasBusy.current && !busy) {
      bumpData();
      refreshCv();
      const onProgress = routeRef.current.name === 'postep';
      if (pipeline.status === 'completed') {
        if (onProgress) {
          navigate(paths.matched());
          toast('Wyszukiwanie zakończone.', { icon: CircleCheck });
        } else {
          toast('Wyszukiwanie zakończone.', {
            icon: CircleCheck,
            action: { label: 'Pokaż oferty', run: () => navigate(paths.matched()) },
          });
        }
      } else if (pipeline.status === 'failed') {
        toast(pipeline.error_message ? `Wyszukiwanie przerwane: ${pipeline.error_message}` : 'Wyszukiwanie przerwane.', {
          icon: TriangleAlert,
          action: onProgress ? undefined : { label: 'Szczegóły', run: () => navigate(paths.progress) },
        });
      } else if (pipeline.status === 'stopped') {
        toast('Wyszukiwanie zatrzymane.');
      }
    }
    wasBusy.current = busy;
  }, [pipeline, bumpData, refreshCv, toast]);

  const cvMissing = cv !== null && !cv.ready;
  useEffect(() => {
    if (cvMissing && route.name !== 'start' && route.name !== 'postep') navigate(paths.start, { replace: true });
  }, [cvMissing, route.name]);

  const startSearch = useCallback(async () => {
    try {
      const res = await api.startPipeline('full');
      setPipeline(res.state);
      setRunSummary(null);
      navigate(paths.progress);
      return true;
    } catch (err) {
      toast(`Nie udało się uruchomić wyszukiwania: ${errorMessage(err)}`, { icon: TriangleAlert });
      return false;
    }
  }, [toast]);

  const stopSearch = useCallback(async () => {
    try {
      setPipeline((await api.stopPipeline()).state);
    } catch (err) {
      toast(`Nie udało się zatrzymać: ${errorMessage(err)}`, { icon: TriangleAlert });
    }
  }, [toast]);

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
        setMarkSent({ offer: detail, previousStatus, cvVersionId: readyCv?.id, onChange: opts.onChange });
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
      dataVersion,
      bumpData,
      toast,
      openLaunch,
      openSettings,
      openMarkSent,
    ],
  );

  const Screen = SCREENS[route.name];
  const minimal = route.name === 'start' || route.name === 'postep';
  const showMiniCard = pipelineBusy && !minimal;
  const blockedByGate = cvMissing && route.name !== 'start' && route.name !== 'postep';

  return (
    <AppContext.Provider value={context}>
      <div className="app">
        <TopBar minimal={minimal} />
        <main className="workspace">{!blockedByGate && <Screen key={route.path} route={route} />}</main>
      </div>
      <div className="dock">
        <Toasts toasts={toasts} dismiss={dismiss} />
        {showMiniCard && <MiniPipelineCard />}
      </div>
      {launchOpen && <LaunchModal onClose={() => setLaunchOpen(false)} />}
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
      {markSent && <MarkSentModal state={markSent} onClose={() => setMarkSent(null)} />}
    </AppContext.Provider>
  );
}
