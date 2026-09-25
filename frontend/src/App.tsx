import React, { useState, useEffect, useLayoutEffect, useCallback, useRef } from 'react';
import type {
  ActivityRow,
  CVInfo,
  EnvField,
  GapFilter,
  OfferDetail,
  OfferListItem,
  PipelinePrerequisites,
  PipelineState,
  RecentDecision,
  Stats,
} from './types';
import { api } from './api';
import { Check, Info } from 'lucide-react';
import { isPipelineBusy, pipelineStatus } from './pipeline';
import { shouldIgnoreShortcut } from './keys';
import { SWAP_OUT_MS, SWAP_SETTLE_MS, useBlurSwap, type SwapPhase } from './swap';
import { ADD_LINK_VIEW, APPLICATIONS_VIEW, GAPS_VIEW, isOfferTab } from './views';
import { TopBar } from './components/TopBar';
import { OfferListPanel, SEARCH_INPUT_ID } from './components/OfferListPanel';
import { OfferDetailPanel } from './components/OfferDetailPanel';
import { OverviewPanel } from './components/OverviewPanel';
import { PipelinePill } from './components/PipelinePill';
import { PipelineSheet } from './components/PipelineSheet';
import { PipelineLaunchModal } from './components/PipelineLaunchModal';
import { HelpModal } from './components/HelpModal';
import { ApplicationsBoard } from './components/ApplicationsBoard';
import { SkillGapsPanel } from './components/SkillGapsPanel';
import { AddFromLinkPanel } from './components/AddFromLinkPanel';
import { TooltipLayer } from './components/ui/TooltipLayer';
import './theme_tokens.css';
import './styles/base.css';

const PAGE_SIZE = 25;
/** Podmiana widoku i prawej karty pod rozmyciem (.workspace.is-* w base.css, czasy w swap.ts). */
const VIEW_SWAP_CLASS = { idle: '', out: ' is-leaving', in: ' is-waiting', settle: ' is-settling' } as const;
const DETAIL_SWAP_CLASS = {
  idle: '',
  out: ' is-detail-leaving',
  in: ' is-detail-waiting',
  settle: ' is-detail-settling',
} as const;
/** Wejścia CSS, które zastępuje rozmycie (base.css, offers.css). */
const ENTRY_ANIMATIONS: Record<string, true> = { 'panel-in': true, 'od-content-in': true };
/** Wejście strony (offers.css): przegląd czeka ukryty na pierwsze oferty, potem paski dopasowania
 *  rosną od lewej, a treść przeglądu odsłania się w tym samym kierunku. */
type IntroPhase = 'wait' | 'play' | 'done';
const INTRO_CLASS: Record<IntroPhase, string> = { wait: ' is-intro-wait', play: ' is-intro', done: '' };
/** Najdłuższe opóźnienie + czas animacji wejścia (offers.css), potem klasa znika. */
const INTRO_MS = 1800;

interface Toast {
  id: number;
  message: string;
  action?: { label: string; run: () => void };
  /** Potwierdzenie wykonanej akcji dostaje ✓, informacja — ⓘ. */
  done?: boolean;
  /** Toast gra wyjście (200 ms) przed usunięciem ze stanu. */
  leaving?: boolean;
}

const DECISION_TOAST: Record<string, string> = {
  save: 'Zapisano',
  apply: 'Wysłane',
  aspirational: 'Aspiracyjne',
  reject: 'Odrzucono',
  rated: 'Oceniono',
};

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

export const App: React.FC = () => {
  const [stats, setStats] = useState<Stats | null>(null);
  const [pipeline, setPipeline] = useState<PipelineState | null>(null);
  const [activeView, setActiveView] = useState<string>('Dopasowane');

  const [offers, setOffers] = useState<OfferListItem[]>([]);
  const [totalOffers, setTotalOffers] = useState(0);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [search, setSearch] = useState('');
  const [loadingOffers, setLoadingOffers] = useState(false);
  const [intro, setIntro] = useState<IntroPhase>('wait');
  // Wartość sortowania na początku każdej strony (% albo ocena) — etykiety skoku o wiele stron.
  const [pageMarks, setPageMarks] = useState<(number | null)[] | null>(null);
  // Nowe od startu ostatniego pobierania (cała lista, nie strona).
  const [fresh, setFresh] = useState<{ count: number; since: string | null }>({ count: 0, since: null });
  // „Pokaż N ofert” z panelu braków: lista Wszystkie zawężona do ofert z tym brakiem.
  const [gapFilter, setGapFilter] = useState<GapFilter | null>(null);
  const gapFilterRef = useRef(gapFilter);
  gapFilterRef.current = gapFilter;

  const [selectedLink, setSelectedLink] = useState<string | null>(null);
  const [selectedOffer, setSelectedOffer] = useState<OfferDetail | null>(null);

  const [activityRows, setActivityRows] = useState<ActivityRow[]>([]);
  const [recentDecisions, setRecentDecisions] = useState<RecentDecision[]>([]);

  const [prerequisites, setPrerequisites] = useState<PipelinePrerequisites | null>(null);
  const [cvInfo, setCvInfo] = useState<CVInfo | null>(null);
  const [envFields, setEnvFields] = useState<EnvField[]>([]);
  const [isLaunchModalOpen, setIsLaunchModalOpen] = useState(false);
  const [isSheetOpen, setIsSheetOpen] = useState(false);
  const [isHelpOpen, setIsHelpOpen] = useState(false);
  const closeHelp = useCallback(() => setIsHelpOpen(false), []);
  // Zmiana po każdej decyzji: tablica aplikacji dociąga świeże etapy.
  const [decisionsVersion, setDecisionsVersion] = useState(0);
  const [exitingLink, setExitingLink] = useState<string | null>(null);

  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastIdRef = useRef(0);

  // Liczniki żądań: odrzucają spóźnione odpowiedzi po szybkim przełączaniu.
  const offersRequestIdRef = useRef(0);
  const offerDetailRequestIdRef = useRef(0);
  const prevPipelineBusyRef = useRef(false);
  // Po przejściu J/K przez granicę strony: którą ofertę zaznaczyć po wczytaniu.
  const pendingSelectRef = useRef<'first' | 'last' | null>(null);

  const activeViewRef = useRef(activeView);
  const searchRef = useRef(search);
  const pageRef = useRef(page);
  const statsRef = useRef(stats);
  const selectedLinkRef = useRef<string | null>(selectedLink);
  activeViewRef.current = activeView;
  searchRef.current = search;
  pageRef.current = page;
  statsRef.current = stats;

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 200);
  }, []);

  const showToast = useCallback((message: string, action?: Toast['action'], done = false) => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev.slice(-2), { id, message, action, done }]);
    setTimeout(() => dismissToast(id), action ? 6000 : 3500);
  }, [dismissToast]);

  // Otwarcie innej oferty myszą: treść prawej karty rozmywa się (SWAP_OUT_MS, równolegle
  // z pobieraniem), nowa oferta wchodzi pod rozmyciem i wyostrza się (SWAP_SETTLE_MS).
  // Odświeżenie tej samej oferty (po decyzji) i J/K podmieniają treść bez animacji.
  const [detailSwap, setDetailSwap] = useState<SwapPhase>('idle');
  const detailSwapRef = useRef<SwapPhase>('idle');
  detailSwapRef.current = detailSwap;
  const detailOutUntilRef = useRef(0);
  const detailTimerRef = useRef(0);
  const shownOfferLinkRef = useRef<string | null>(null);
  shownOfferLinkRef.current = selectedOffer?.link ?? null;

  // Trwa pobieranie oferty — podmiana widoku (navigate) czeka z wyostrzeniem także na nią.
  const [loadingDetail, setLoadingDetail] = useState(false);

  const loadOfferDetail = useCallback(async (link: string, animate = false) => {
    const reqId = ++offerDetailRequestIdRef.current;
    setLoadingDetail(true);
    try {
      const detail = await api.getOfferDetail(link);
      const commit = () => {
        if (reqId !== offerDetailRequestIdRef.current || selectedLinkRef.current !== link) return;
        setSelectedOffer(detail);
        setLoadingDetail(false);
        if (animate) setDetailSwap('in');
      };
      // Podmiana dopiero po pełnym rozmyciu starej treści.
      const wait = animate ? detailOutUntilRef.current - performance.now() : 0;
      if (wait > 0) window.setTimeout(commit, wait);
      else commit();
    } catch (err) {
      if (reqId === offerDetailRequestIdRef.current) {
        console.error('Błąd pobierania szczegółów oferty:', err);
        setSelectedOffer(null);
        setLoadingDetail(false);
        setDetailSwap('idle');
      }
    }
  }, []);

  /** `allowAnimate = false`: oferta otwierana pod innym rozmyciem (podmiana widoku). */
  const handleSelectOffer = useCallback((link: string, allowAnimate = true) => {
    const animate = allowAnimate
      && link !== shownOfferLinkRef.current
      && isOfferTab(activeViewRef.current)
      && document.documentElement.dataset.input !== 'keyboard';
    window.clearTimeout(detailTimerRef.current);
    if (!animate) setDetailSwap('idle');
    else if (detailSwapRef.current !== 'out') {
      detailOutUntilRef.current = performance.now() + SWAP_OUT_MS;
      setDetailSwap('out');
    }
    setSelectedLink(link);
    selectedLinkRef.current = link;
    loadOfferDetail(link, animate);
  }, [loadOfferDetail]);

  const handleCloseDetail = useCallback(() => {
    offerDetailRequestIdRef.current++;
    window.clearTimeout(detailTimerRef.current);
    setDetailSwap('idle');
    setLoadingDetail(false);
    setSelectedLink(null);
    selectedLinkRef.current = null;
    setSelectedOffer(null);
  }, []);

  // Zamknięcie oferty myszą (X, usunięcie): prawa karta rozmywa się, pod rozmyciem wraca
  // przegląd i wyostrza się — jak przy otwarciu innej oferty. Esc zamyka bez animacji.
  const closeDetailAnimated = useCallback(() => {
    if (document.documentElement.dataset.input === 'keyboard') {
      handleCloseDetail();
      return;
    }
    offerDetailRequestIdRef.current++;
    window.clearTimeout(detailTimerRef.current);
    setDetailSwap('out');
    detailTimerRef.current = window.setTimeout(() => {
      handleCloseDetail();
      setDetailSwap('in');
    }, SWAP_OUT_MS);
  }, [handleCloseDetail]);

  // Nowa treść prawej karty pod rozmyciem: bez zwykłego wejścia panelu i treści oferty.
  useLayoutEffect(() => {
    if (detailSwap !== 'in') return;
    const panel = workspaceRef.current?.querySelector(':scope > .panel:last-child');
    panel?.getAnimations({ subtree: true }).forEach((animation) => {
      if (animation instanceof CSSAnimation && ENTRY_ANIMATIONS[animation.animationName]) animation.cancel();
    });
  }, [detailSwap]);

  useEffect(() => {
    if (detailSwap !== 'in') return;
    setDetailSwap('settle');
    detailTimerRef.current = window.setTimeout(() => setDetailSwap('idle'), SWAP_SETTLE_MS);
  }, [detailSwap]);

  useEffect(() => () => window.clearTimeout(detailTimerRef.current), []);

  /** Zwraca wczytane pozycje albo null, gdy odpowiedź przyszła za późno lub z błędem. */
  const loadOffers = useCallback(async (tab: string, query: string, pageNum: number): Promise<OfferListItem[] | null> => {
    if (!isOfferTab(tab)) return null;
    const reqId = ++offersRequestIdRef.current;
    setLoadingOffers(true);
    try {
      const res = await api.getOffers(tab, query, pageNum, PAGE_SIZE, gapFilterRef.current);
      if (reqId !== offersRequestIdRef.current) return null;
      setOffers(res.items);
      setTotalOffers(res.total);
      setPage(res.page);
      setTotalPages(res.total_pages);
      setPageMarks(res.page_marks);
      setFresh({ count: res.fresh_count, since: res.fresh_since });
      const pending = pendingSelectRef.current;
      if (pending && res.items.length) {
        pendingSelectRef.current = null;
        handleSelectOffer(res.items[pending === 'first' ? 0 : res.items.length - 1].link);
      }
      return res.items;
    } catch (err) {
      if (reqId === offersRequestIdRef.current) console.error('Błąd pobierania listy ofert:', err);
      return null;
    } finally {
      if (reqId === offersRequestIdRef.current) {
        setLoadingOffers(false);
        setIntro((phase) => (phase === 'wait' ? 'play' : phase));
      }
    }
  }, [handleSelectOffer]);

  const loadActivity = useCallback(async () => {
    try {
      const res = await api.getActivity();
      setActivityRows(res.activity_rows);
      setRecentDecisions(res.recent_decisions);
    } catch (err) {
      console.error('Błąd pobierania aktywności:', err);
    }
  }, []);

  const loadCV = useCallback(async () => {
    try {
      setCvInfo(await api.getCV());
    } catch (err) {
      console.error('Błąd pobierania informacji o CV:', err);
    }
  }, []);

  const loadEnvKeys = useCallback(async () => {
    try {
      setEnvFields((await api.getEnvKeys()).fields);
    } catch (err) {
      console.error('Błąd pobierania kluczy .env:', err);
    }
  }, []);

  const loadPrerequisites = useCallback(async () => {
    try {
      setPrerequisites(await api.getPipelinePrerequisites());
    } catch (err) {
      console.error('Błąd pobierania wymagań pipeline:', err);
    }
  }, []);

  useEffect(() => {
    api
      .getBootstrap()
      .then((b) => {
        setStats(b.stats);
        setPipeline(b.pipeline);
        prevPipelineBusyRef.current = isPipelineBusy(b.pipeline);
      })
      .catch((err) => console.error('Błąd ładowania danych startowych:', err));
    loadActivity();
    loadCV();
    loadEnvKeys();
    loadPrerequisites();
  }, [loadActivity, loadCV, loadEnvKeys, loadPrerequisites]);

  useEffect(() => {
    loadOffers(activeView, search, page);
  }, [activeView, search, page, gapFilter, loadOffers]);

  useEffect(() => {
    if (intro !== 'play') return;
    const timer = window.setTimeout(() => setIntro('done'), INTRO_MS);
    return () => window.clearTimeout(timer);
  }, [intro]);

  // Zmiana kategorii myszą: treść paneli rozmywa się, pod rozmyciem podmienia się widok, nowa
  // treść czeka rozmyta na dane (oferty, oferta otwierana razem z widokiem, dane narzędzia)
  // i wyostrza się; klasy w base.css. Z klawiatury podmiana jest natychmiastowa.
  const [pendingView, setPendingView] = useState<string | null>(null);
  // Widok narzędzia (tablica, braki) sam pobiera dane i zgłasza koniec przez onReady.
  const [loadingTool, setLoadingTool] = useState(false);
  const onToolReady = useCallback(() => setLoadingTool(false), []);
  const viewSwap = useBlurSwap(loadingOffers || loadingDetail || loadingTool);
  const runViewSwap = viewSwap.run;
  const workspaceRef = useRef<HTMLElement>(null);
  /** `then` biegnie pod rozmyciem zaraz po podmianie widoku (np. otwarcie oferty). */
  const navigate = useCallback((view: string, then?: () => void) => {
    const changed = view !== activeViewRef.current;
    // Nowy widok nie powtarza wejścia strony.
    if (changed) setIntro('done');
    const commit = () => {
      if (view !== activeViewRef.current) {
        handleCloseDetail();
        // Lista pokazuje oferty poprzedniej kategorii, dopóki nie przyjdą nowe — wyostrzenie czeka.
        if (isOfferTab(view)) setLoadingOffers(true);
        setLoadingTool(view === APPLICATIONS_VIEW || view === GAPS_VIEW);
        setGapFilter(null);
      }
      setActiveView(view);
      setPage(1);
      setPendingView(null);
      then?.();
    };
    if (changed) setPendingView(view);
    runViewSwap(commit, !changed);
  }, [handleCloseDetail, runViewSwap]);

  const showGapOffers = useCallback((skill: string, threshold: number) => {
    navigate('Wszystkie', () => {
      setSearch('');
      setGapFilter({ skill, threshold });
    });
  }, [navigate]);

  const clearGapFilter = useCallback(() => {
    setLoadingOffers(true);
    setGapFilter(null);
    setPage(1);
  }, []);

  // Panele zamontowane pod rozmyciem nie grają zwykłego wejścia (opacity + przesunięcie).
  // Anulowana animacja CSS nie wraca, dopóki nie zmieni się jej nazwa.
  useLayoutEffect(() => {
    if (viewSwap.phase !== 'in') return;
    workspaceRef.current?.querySelectorAll(':scope > .panel').forEach((panel) => {
      panel.getAnimations().forEach((animation) => animation.cancel());
    });
  }, [viewSwap.phase, activeView]);

  // Zmiana strony listy myszą: to samo rozmycie, tylko na wierszach listy. J/K przez granicę
  // strony ustawia stronę bezpośrednio (bez animacji).
  const listSwap = useBlurSwap(loadingOffers);
  const runListSwap = listSwap.run;
  const changePage = useCallback((next: number) => {
    if (next === pageRef.current) return;
    runListSwap(() => {
      setLoadingOffers(true);
      setPage(next);
    });
  }, [runListSwap]);

  const openLaunchModal = useCallback(() => {
    loadPrerequisites();
    loadCV();
    loadEnvKeys();
    setIsLaunchModalOpen(true);
  }, [loadPrerequisites, loadCV, loadEnvKeys]);

  // Jedna pętla odpytywania: szybka w trakcie przebiegu, wolna w spoczynku.
  const busy = isPipelineBusy(pipeline);
  useEffect(() => {
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const pState = await api.getPipelineState();
        if (cancelled) return;
        setPipeline(pState);

        const wasBusy = prevPipelineBusyRef.current;
        const nowBusy = isPipelineBusy(pState);
        prevPipelineBusyRef.current = nowBusy;

        if (wasBusy && !nowBusy) {
          const finalStatus = pipelineStatus(pState);
          if (finalStatus === 'completed') {
            showToast('Pipeline ukończony.', { label: 'Przejrzyj dopasowane', run: () => navigate('Dopasowane') }, true);
          } else if (finalStatus === 'failed') {
            showToast('Pipeline zatrzymał się z błędem.', { label: 'Pokaż', run: () => setIsSheetOpen(true) });
          }
        }

        if (busy || nowBusy || wasBusy) {
          const newStats = await api.getStats();
          if (cancelled) return;
          const prev = statsRef.current;
          const countsChanged = !prev || newStats.raw_count !== prev.raw_count || newStats.analyzed_count !== prev.analyzed_count;
          setStats(newStats);
          loadActivity();
          if (countsChanged || (wasBusy && !nowBusy)) {
            loadOffers(activeViewRef.current, searchRef.current, pageRef.current);
          }
        }
      } catch {
        // przejściowy błąd odpytywania — kolejny tick spróbuje ponownie
      }
    }, busy ? 1200 : 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [busy, loadActivity, loadOffers, navigate, showToast]);

  const handleRestoreDecision = useCallback(async (link: string, reselect = false) => {
    setExitingLink(null);
    try {
      const res = await api.restoreDecision(link);
      if (!res.ok) return;
      setStats(res.stats);
      if (selectedLinkRef.current === link) setSelectedOffer(res.offer);
      await loadOffers(activeViewRef.current, searchRef.current, pageRef.current);
      if (reselect) handleSelectOffer(link);
      loadActivity();
      setDecisionsVersion((v) => v + 1);
      showToast('Cofnięto — oferta wróciła do nieocenionych.');
    } catch (err) {
      showToast(`Błąd przywracania: ${errorText(err)}`);
    }
  }, [handleSelectOffer, loadActivity, loadOffers, showToast]);

  const handleUpdateDecision = useCallback(async (
    link: string,
    statusVal: string,
    ratingVal: number | null,
    stageVal?: string,
    opts?: { animateExit?: boolean },
  ) => {
    const indexBefore = offers.findIndex((o) => o.link === link);
    // Lista animuje wyjazd tylko tej oferty i tylko przy decyzji myszą (patrz OfferListPanel).
    setExitingLink(opts?.animateExit ? link : null);
    const hadStatus = selectedLinkRef.current === link ? Boolean(selectedOffer?.status) : true;
    try {
      const res = await api.updateDecision(link, statusVal, ratingVal, stageVal);
      if (!res.ok) return;
      setStats(res.stats);
      if (selectedLinkRef.current === link) setSelectedOffer(res.offer);
      const items = await loadOffers(activeViewRef.current, searchRef.current, pageRef.current);
      loadActivity();
      setDecisionsVersion((v) => v + 1);

      // Oferta zniknęła z bieżącej listy (np. z Dopasowanych) — przejdź do tej, która zajęła jej miejsce.
      if (items && indexBefore >= 0 && selectedLinkRef.current === link && !items.some((o) => o.link === link)) {
        if (items.length) handleSelectOffer(items[Math.min(indexBefore, items.length - 1)].link);
        else handleCloseDetail();
      }

      if (stageVal) {
        showToast('Zmieniono etap rekrutacji.', undefined, true);
      } else {
        const label = `${DECISION_TOAST[statusVal] ?? 'Zapisano'}: ${res.offer.title}`;
        showToast(label, hadStatus ? undefined : { label: 'Cofnij', run: () => handleRestoreDecision(link, true) }, true);
      }
    } catch (err) {
      showToast(`Błąd zapisu decyzji: ${errorText(err)}`);
    }
  }, [offers, selectedOffer, loadOffers, loadActivity, handleSelectOffer, handleCloseDetail, handleRestoreDecision, showToast]);

  const handleDeleteOffer = useCallback(async (link: string) => {
    try {
      const res = await api.deleteOfferPermanent(link);
      if (!res.ok) return;
      setStats(res.stats);
      closeDetailAnimated();
      loadOffers(activeViewRef.current, searchRef.current, pageRef.current);
      loadActivity();
      setDecisionsVersion((v) => v + 1);
      showToast('Usunięto ofertę z bazy.', undefined, true);
    } catch (err) {
      showToast(`Błąd usuwania oferty: ${errorText(err)}`);
    }
  }, [closeDetailAnimated, loadOffers, loadActivity, showToast]);

  const handleSaveNextStep = useCallback(async (link: string, label: string, due: string | null) => {
    try {
      const res = await api.saveNextStep(link, label, due);
      if (!res.ok) return false;
      if (selectedLinkRef.current === link) setSelectedOffer(res.offer);
      setDecisionsVersion((v) => v + 1);
      showToast(label ? 'Zapisano następny krok.' : 'Usunięto następny krok.', undefined, true);
      return true;
    } catch (err) {
      showToast(`Błąd zapisu kroku: ${errorText(err)}`);
      return false;
    }
  }, [showToast]);

  const runPipelineAction = async (
    call: () => Promise<{ message: string; state: PipelineState }>,
    errorLabel: string,
    openSheet: boolean,
  ) => {
    try {
      const res = await call();
      setPipeline(res.state);
      prevPipelineBusyRef.current = isPipelineBusy(res.state);
      showToast(res.message);
      if (openSheet) setIsSheetOpen(true);
    } catch (err) {
      showToast(`${errorLabel}: ${errorText(err)}`);
    }
  };

  const handleReloadDatabase = async () => {
    try {
      const res = await api.reloadDatabase();
      setStats(res.stats);
      loadOffers(activeView, search, page);
      loadActivity();
      loadPrerequisites();
      showToast('Wczytano dane z dysku od nowa.');
    } catch (err) {
      showToast(`Błąd odświeżania: ${errorText(err)}`);
    }
  };

  // Ostatnie wejście (klawiatura / wskaźnik) jako atrybut na <html>: przejścia treści grają
  // tylko po kliknięciu — przy J/K i innych skrótach zmiana ma być natychmiastowa.
  useEffect(() => {
    const root = document.documentElement;
    const onKey = () => { root.dataset.input = 'keyboard'; };
    const onPointer = () => { root.dataset.input = 'pointer'; };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPointer, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onPointer, true);
    };
  }, []);

  // P otwiera i zwija arkusz pipeline'u (Esc zwija go w samym arkuszu).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'p' && event.key !== 'P') return;
      if (isSheetOpen ? event.ctrlKey || event.metaKey || event.altKey : shouldIgnoreShortcut(event)) return;
      event.preventDefault();
      setIsSheetOpen((open) => !open);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isSheetOpen]);

  // ? otwiera pomoc ze skrótami (zamyka ją to samo ? albo Esc w HelpModal).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== '?' || shouldIgnoreShortcut(event)) return;
      event.preventDefault();
      setIsHelpOpen(true);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // Nawigacja klawiaturą po liście: J/K (i strzałki), / szuka, Esc zamyka szczegóły.
  useEffect(() => {
    if (!isOfferTab(activeView)) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === '/' && !shouldIgnoreShortcut(event)) {
        event.preventDefault();
        document.getElementById(SEARCH_INPUT_ID)?.focus();
        return;
      }
      if (shouldIgnoreShortcut(event)) return;
      const key = event.key;
      if (key === 'Escape' && selectedLink) {
        handleCloseDetail();
        return;
      }
      const next = key === 'j' || key === 'J' || key === 'ArrowDown';
      const prev = key === 'k' || key === 'K' || key === 'ArrowUp';
      if (!next && !prev) return;
      if (!offers.length) return;
      event.preventDefault();
      const idx = offers.findIndex((o) => o.link === selectedLink);
      if (idx < 0) {
        handleSelectOffer(offers[next ? 0 : offers.length - 1].link);
      } else if (next && idx < offers.length - 1) {
        handleSelectOffer(offers[idx + 1].link);
      } else if (prev && idx > 0) {
        handleSelectOffer(offers[idx - 1].link);
      } else if (next && page < totalPages) {
        pendingSelectRef.current = 'first';
        setPage(page + 1);
      } else if (prev && page > 1) {
        pendingSelectRef.current = 'last';
        setPage(page - 1);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [activeView, offers, selectedLink, page, totalPages, handleSelectOffer, handleCloseDetail]);

  const offerTab = isOfferTab(activeView);
  const stopPipeline = () => runPipelineAction(api.stopPipeline, 'Błąd zatrzymania', false);
  const resumePipeline = () => runPipelineAction(api.resumePipeline, 'Błąd wznowienia', true);

  return (
    <div className="app">
      <TopBar
        activeView={pendingView ?? activeView}
        onSelectView={navigate}
        badges={{ [APPLICATIONS_VIEW]: (stats?.applications_stale ?? 0) > 0 }}
        pill={
          <PipelinePill
            pipeline={pipeline}
            activityRows={activityRows}
            onOpenSheet={() => setIsSheetOpen(true)}
            onOpenLaunchModal={openLaunchModal}
            onStop={stopPipeline}
            onResume={resumePipeline}
            onShowMatched={() => navigate('Dopasowane')}
          />
        }
        onOpenHelp={() => setIsHelpOpen(true)}
      />

      {/* Klucz = widok: zmiana kategorii montuje panele od nowa; wejście i rozmycie w base.css. */}
      <main
        ref={workspaceRef}
        className={`workspace${VIEW_SWAP_CLASS[viewSwap.phase]}${DETAIL_SWAP_CLASS[detailSwap]}${INTRO_CLASS[intro]}`}
        key={activeView}
      >
        {offerTab && (
          <>
            <OfferListPanel
              items={offers}
              total={totalOffers}
              page={page}
              pageSize={PAGE_SIZE}
              totalPages={totalPages}
              search={search}
              onSearchChange={(q) => {
                setSearch(q);
                setPage(1);
              }}
              onPageChange={changePage}
              pageMarks={pageMarks}
              freshCount={fresh.count}
              freshSince={fresh.since}
              gapFilter={gapFilter}
              onClearGap={clearGapFilter}
              listSwap={listSwap.phase}
              selectedLink={selectedLink}
              onSelectOffer={handleSelectOffer}
              activeTab={activeView}
              rawCount={stats?.raw_count ?? 0}
              analyzedCount={stats?.analyzed_count ?? 0}
              onOpenLaunchModal={openLaunchModal}
              loading={loadingOffers}
              exitingLink={exitingLink}
            />
            {selectedOffer ? (
              <OfferDetailPanel
                offer={selectedOffer}
                onClose={closeDetailAnimated}
                onUpdateDecision={handleUpdateDecision}
                onRestoreDecision={handleRestoreDecision}
                onDeleteOffer={handleDeleteOffer}
                onSaveNextStep={handleSaveNextStep}
                pipelineRunning={!!pipeline?.running}
              />
            ) : (
              <OverviewPanel
                activeTab={activeView}
                gapSkill={gapFilter?.skill ?? null}
                total={totalOffers}
                stats={stats}
                activityRows={activityRows}
                recentDecisions={recentDecisions}
                onSelectOffer={handleSelectOffer}
                onStartReview={offers.length ? () => handleSelectOffer(offers[0].link) : null}
              />
            )}
          </>
        )}

        {activeView === APPLICATIONS_VIEW && (
          <ApplicationsBoard
            refreshKey={decisionsVersion}
            onUpdateDecision={handleUpdateDecision}
            onOpenOffer={(link) => navigate('Zapisane', () => handleSelectOffer(link, false))}
            onReady={onToolReady}
          />
        )}

        {activeView === GAPS_VIEW && <SkillGapsPanel onReady={onToolReady} onShowOffers={showGapOffers} />}

        {activeView === ADD_LINK_VIEW && (
          <AddFromLinkPanel
            offer={selectedOffer}
            onOfferSaved={(offer) => {
              setSelectedLink(offer.link);
              selectedLinkRef.current = offer.link;
              setSelectedOffer(offer);
              loadActivity();
            }}
            onCloseOffer={closeDetailAnimated}
            showToast={showToast}
            onUpdateDecision={handleUpdateDecision}
            onRestoreDecision={handleRestoreDecision}
            onDeleteOffer={handleDeleteOffer}
            onSaveNextStep={handleSaveNextStep}
            pipelineRunning={!!pipeline?.running}
          />
        )}
      </main>

      <PipelineSheet
        open={isSheetOpen}
        onClose={() => setIsSheetOpen(false)}
        pipeline={pipeline}
        activityRows={activityRows}
        stats={stats}
        onStop={stopPipeline}
        onForceStop={() => runPipelineAction(api.forceStopPipeline, 'Błąd wymuszenia zatrzymania', false)}
        onResume={resumePipeline}
        onRunStep={(step) => runPipelineAction(() => api.runStandaloneStep(step), 'Błąd uruchomienia kroku', false)}
        onReloadDatabase={handleReloadDatabase}
        onOpenLaunchModal={openLaunchModal}
      />

      <PipelineLaunchModal
        isOpen={isLaunchModalOpen}
        onClose={() => setIsLaunchModalOpen(false)}
        prerequisites={prerequisites}
        cvInfo={cvInfo}
        envFields={envFields}
        onStartPipeline={(mode) => runPipelineAction(() => api.startPipeline(mode), 'Błąd uruchomienia', true)}
        onRefreshPrerequisites={loadPrerequisites}
        onRefreshCV={loadCV}
        onRefreshEnvKeys={loadEnvKeys}
        showToast={showToast}
      />

      <HelpModal isOpen={isHelpOpen} onClose={closeHelp} />

      {toasts.length > 0 && (
        <div className="toasts" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`toast${t.leaving ? ' is-leaving' : ''}`} role="status">
              {t.done ? <Check aria-hidden="true" /> : <Info aria-hidden="true" />}
              <span className="msg">{t.message}</span>
              {t.action && (
                <>
                  <span className="divider" aria-hidden="true" />
                  <button type="button" className="action press" onClick={() => { dismissToast(t.id); t.action!.run(); }}>
                    {t.action.label}
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      <TooltipLayer />
    </div>
  );
};
