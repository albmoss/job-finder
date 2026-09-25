import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { GapFilter, OfferListItem } from '../types';
import { VIEW_ICON, isOfferTab } from '../views';
import { SWAP_CLASS, type SwapPhase } from '../swap';
import { Search, ChevronLeft, ChevronRight, ChevronUp, Sparkles, Layers, Star, Bookmark, MountainSnow, CircleX, Rocket, X } from 'lucide-react';
import { shouldIgnoreShortcut } from '../keys';
import { formatStamp } from '../pipeline';
import { NEW_ONES, OFFERS, RESULTS, plural } from '../plural';
import '../styles/offers.css';

export interface NormalizedMatchScore {
  isAnalyzed: boolean;
  score: number | null;
  ratio: number;
  displayText: string;
}

export function normalizeMatchScore(raw: number | null | undefined): NormalizedMatchScore {
  if (raw === null || raw === undefined || typeof raw !== 'number' || !Number.isFinite(raw)) {
    return { isAnalyzed: false, score: null, ratio: 0, displayText: '—' };
  }
  const clamped = Math.max(0, Math.min(100, raw));
  const rounded = Math.round(clamped);
  return { isAnalyzed: true, score: rounded, ratio: clamped / 100, displayText: `${rounded}` };
}

export const SEARCH_INPUT_ID = 'offer-search';

// Kolejność list z backendu (ws_collect); pokazana przy liczniku, żeby nie trzeba jej zgadywać.
const TAB_ORDER: Record<string, string> = {
  Wszystkie: 'od najlepszego dopasowania',
  Ocenione: 'od najwyższej oceny',
  Zapisane: 'od najnowszych',
  Aspiracyjne: 'od najnowszych',
  Odrzucone: 'od najnowszych',
};

/** "2026-09-23 17:02" → "dziś" / "wczoraj" / "23.09" (inny rok: "23.09.25"). */
function formatDecisionDay(raw: string | null): string | null {
  const m = raw ? /^(\d{4})-(\d{2})-(\d{2})/.exec(raw) : null;
  if (!m) return null;
  const [, y, mo, d] = m;
  const day = new Date(Number(y), Number(mo) - 1, Number(d));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - day.getTime()) / 86_400_000);
  if (diff === 0) return 'dziś';
  if (diff === 1) return 'wczoraj';
  return day.getFullYear() === today.getFullYear() ? `${d}.${mo}` : `${d}.${mo}.${y.slice(2)}`;
}

interface RowMetric {
  text: string;
  unit?: string;
  /** Wypełnienie paska 0–100. */
  level: number;
  tone: 'hi' | 'normal' | 'muted';
  label: string;
}

// Liczba po prawej zależy od kategorii: w Dopasowanych i Wszystkich % dopasowania AI,
// w zakładkach decyzji ocena użytkownika (0–10).
function rowMetric(item: OfferListItem, decisionTab: boolean): RowMetric {
  if (decisionTab) {
    const r = item.rating;
    if (typeof r !== 'number') return { text: '—', level: 0, tone: 'muted', label: 'bez Twojej oceny' };
    return { text: `${r}`, unit: '/10', level: r * 10, tone: r >= 8 ? 'hi' : 'normal', label: `Twoja ocena ${r}/10` };
  }
  const norm = normalizeMatchScore(item.match_percentage);
  return {
    text: norm.displayText,
    level: norm.score ?? 0,
    tone: !norm.isAnalyzed ? 'muted' : norm.score! >= 88 ? 'hi' : 'normal',
    label: norm.isAnalyzed ? `dopasowanie ${norm.score}%` : 'bez oceny AI',
  };
}

interface OfferListPanelProps {
  items: OfferListItem[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  search: string;
  onSearchChange: (query: string) => void;
  /** Zmiana strony: mysz = rozmycie wierszy (App.changePage). */
  onPageChange: (newPage: number) => void;
  /** Wartość sortowania na początku każdej strony (`OffersResponse.page_marks`). */
  pageMarks: (number | null)[] | null;
  /** Nowe od startu ostatniego pobierania na całej liście i sam start (`OffersResponse`). */
  freshCount: number;
  freshSince: string | null;
  /** Lista zawężona do ofert z brakiem z panelu braków; `onClearGap` wraca do całej zakładki. */
  gapFilter: GapFilter | null;
  onClearGap: () => void;
  /** Faza podmiany wierszy przy zmianie strony (useBlurSwap w App). */
  listSwap: SwapPhase;
  selectedLink: string | null;
  onSelectOffer: (link: string) => void;
  activeTab: string;
  rawCount: number;
  analyzedCount: number;
  onOpenLaunchModal: () => void;
  loading: boolean;
  /** Oferta, której wiersz ma wyjechać z listy, gdy zniknie z `items` (decyzja myszą). */
  exitingLink?: string | null;
}

// Wyjazd wiersza i domknięcie luki. Wyjście: ease-out, 220 ms, w lewo (skąd przyszła lista);
// domknięcie to ruch na ekranie tuż po wyjściu — ease-out 260 ms, sam transform (FLIP).
const EXIT_MS = 220;
const CLOSE_MS = 260;
const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';

interface ExitGhost {
  item: OfferListItem;
  index: number;
}

// Skok o wiele stron: klawisze, które przesuwają suwak, nie mogą przesuwać też zaznaczenia (J/K, strzałki w App).
const SLIDER_KEYS: Record<string, true> = {
  ArrowLeft: true, ArrowRight: true, ArrowUp: true, ArrowDown: true, Home: true, End: true, PageUp: true, PageDown: true,
};

export const OfferListPanel: React.FC<OfferListPanelProps> = ({
  items,
  total,
  page,
  pageSize,
  totalPages,
  search,
  onSearchChange,
  onPageChange,
  pageMarks,
  freshCount,
  freshSince,
  gapFilter,
  onClearGap,
  listSwap,
  selectedLink,
  onSelectOffer,
  activeTab,
  rawCount,
  analyzedCount,
  onOpenLaunchModal,
  loading,
  exitingLink = null,
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Wiersz, który zniknął z listy po decyzji, zostaje chwilę jako „duch” na swoim miejscu
  // (ten sam klucz = ten sam węzeł DOM, więc klasa wyjścia uruchamia przejście).
  const [prevItems, setPrevItems] = useState(items);
  const [ghost, setGhost] = useState<ExitGhost | null>(null);
  if (items !== prevItems) {
    setPrevItems(items);
    const idx = exitingLink ? prevItems.findIndex((i) => i.link === exitingLink) : -1;
    const stillListed = idx >= 0 && items.some((i) => i.link === exitingLink);
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setGhost(idx >= 0 && !stillListed && !reduce ? { item: prevItems[idx], index: idx } : null);
  }
  const displayItems = ghost
    ? [...items.slice(0, ghost.index), ghost.item, ...items.slice(ghost.index)]
    : items;

  // Po wyjściu: zapamiętaj pozycje (First), usuń ducha, w layout effect zmierz nowe (Last)
  // i przesuń wiersze od starych pozycji do nowych samym transformem.
  const flipFromRef = useRef<Map<string, number> | null>(null);
  useEffect(() => {
    if (!ghost) return;
    const timer = window.setTimeout(() => {
      const tops = new Map<string, number>();
      scrollRef.current?.querySelectorAll<HTMLElement>('.ol-row[data-link]').forEach((row) => {
        tops.set(row.dataset.link!, row.offsetTop);
      });
      flipFromRef.current = tops;
      setGhost(null);
    }, EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [ghost]);
  useLayoutEffect(() => {
    const from = flipFromRef.current;
    if (ghost || !from) return;
    flipFromRef.current = null;
    scrollRef.current?.querySelectorAll<HTMLElement>('.ol-row[data-link]').forEach((row) => {
      const before = from.get(row.dataset.link!);
      const delta = before === undefined ? 0 : before - row.offsetTop;
      if (delta === 0) return;
      row.animate(
        [{ transform: `translateY(${delta}px)` }, { transform: 'translateY(0)' }],
        { duration: CLOSE_MS, easing: EASE_OUT },
      );
    });
  }, [ghost]);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [searchOpen, setSearchOpen] = useState(Boolean(search));
  const [jumpOpen, setJumpOpen] = useState(false);
  const [jumpDraft, setJumpDraft] = useState(page);
  useEffect(() => setJumpDraft(page), [page]);
  const panelRef = useRef<HTMLElement>(null);
  const searchToolsRef = useRef<HTMLDivElement>(null);
  const searchSlotRef = useRef<HTMLSpanElement>(null);
  const jumpRef = useRef<HTMLDivElement>(null);
  const jumpToggleRef = useRef<HTMLButtonElement>(null);

  // Lupa po otwarciu przejeżdża z nagłówka na miejsce ikony w polu szukania. Przesunięcie
  // liczone z układu (.ol-tools stoi w miejscu, przesuwa się sam przycisk) przy każdej zmianie
  // rozmiaru panelu; CSS przejściem `translate` zajmuje się ruchem.
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const measure = () => {
      const tools = searchToolsRef.current?.getBoundingClientRect();
      const slot = searchSlotRef.current?.getBoundingClientRect();
      const btn = searchToolsRef.current?.firstElementChild as HTMLElement | null;
      if (!tools || !slot || !btn) return;
      btn.style.setProperty('--glyph-x', `${slot.left + slot.width / 2 - (tools.left + tools.width / 2)}px`);
      btn.style.setProperty('--glyph-y', `${slot.top + slot.height / 2 - (tools.top + tools.height / 2)}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);

  // Pas skoku to wyskakujące okno: zamyka je klik gdziekolwiek poza nim i poza przełącznikiem.
  useEffect(() => {
    if (!jumpOpen) return;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (jumpRef.current?.contains(target) || jumpToggleRef.current?.contains(target)) return;
      setJumpOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [jumpOpen]);

  // Gdy search z zewnątrz dostaje wartość, otwórz pole
  useEffect(() => {
    if (search) setSearchOpen(true);
  }, [search]);

  // Skrót / z App otwiera pole wyszukiwania i daje fokus
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (shouldIgnoreShortcut(event)) return;
      if (event.key === '/') {
        event.preventDefault();
        setSearchOpen(true);
        setTimeout(() => {
          searchInputRef.current?.focus();
          searchInputRef.current?.select();
        }, 30);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // Wybór klawiaturą (J/K) może wskazać wiersz poza widokiem — dociągnij go
  useEffect(() => {
    if (!selectedLink || !scrollRef.current) return;
    const row = scrollRef.current.querySelector<HTMLElement>(`[data-link="${CSS.escape(selectedLink)}"]`);
    row?.scrollIntoView({ block: 'nearest' });
  }, [selectedLink]);

  // Nowa strona zaczyna się od góry listy
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [page, activeTab]);

  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  const CatIcon = VIEW_ICON[activeTab] || Sparkles;
  // Zakładki z decyzjami użytkownika: liczy się jego ocena i dzień decyzji, nie % AI.
  const decisionTab = isOfferTab(activeTab) && activeTab !== 'Dopasowane' && activeTab !== 'Wszystkie';

  const countText =
    activeTab === 'Dopasowane'
      ? `${total.toLocaleString('pl-PL')} czeka na decyzję`
      : `${total.toLocaleString('pl-PL')} ${plural(total, OFFERS)}${TAB_ORDER[activeTab] && total > 1 ? ` · ${TAB_ORDER[activeTab]}` : ''}`;

  // Skok o wiele stron: suwak po stronach, etykieta to wartość sortowania na początku strony
  // („od 45%”). Większość ofert ma niskie dopasowanie, więc zakres 90→30% zajmuje ułamek suwaka —
  // pod suwakiem są progi (80%, 70%…), każdy prowadzi do pierwszej strony z takim wynikiem.
  const canJump = totalPages > 2;
  const rated = activeTab === 'Ocenione';
  const markText = (p: number): string | null => {
    if (!pageMarks) return null;
    const v = pageMarks[p - 1];
    if (v === null || v === undefined) return '—';
    return rated ? `${v}/10` : `${v}%`;
  };
  const jumpTicks: { page: number; label: string }[] = [];
  if (canJump && pageMarks) {
    for (const threshold of rated ? [9, 7, 5, 3, 1] : [90, 80, 70, 60, 50, 40, 30, 20, 10]) {
      const p = pageMarks.findIndex((v) => v !== null && v <= threshold) + 1;
      if (p > 0 && p !== jumpTicks[jumpTicks.length - 1]?.page) {
        jumpTicks.push({ page: p, label: rated ? `${threshold}/10` : `${threshold}%` });
      }
    }
  } else if (canJump) {
    for (const f of [0, 0.25, 0.5, 0.75, 1]) {
      const p = Math.round(1 + (totalPages - 1) * f);
      if (p !== jumpTicks[jumpTicks.length - 1]?.page) jumpTicks.push({ page: p, label: `${p}` });
    }
  }
  // Podświetlony próg: ostatni, do którego suwak już doszedł.
  const reachedTicks = jumpTicks.filter((t) => t.page <= jumpDraft);
  const activeTickPage = reachedTicks[reachedTicks.length - 1]?.page;
  const jumpFill = canJump ? `${((jumpDraft - 1) / (totalPages - 1)) * 100}%` : '0%';
  const commitJump = (target: number) => {
    if (target !== page) onPageChange(target);
  };

  const renderEmpty = () => {
    if (loading) {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Sparkles /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Ładowanie ofert…</span>
          </div>
        </div>
      );
    }
    if (search) {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Search /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Nic nie pasuje do „{search}”</span>
            <span className="ol-empty-hint">Spróbuj innego zapytania lub wyczyść filtr.</span>
          </div>
          <button type="button" className="btn btn-secondary press" onClick={() => onSearchChange('')}>
            Wyczyść szukanie
          </button>
        </div>
      );
    }
    if (activeTab === 'Dopasowane' && rawCount === 0) {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Rocket /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Baza ofert jest pusta</span>
            <span className="ol-empty-hint">Uruchom pipeline — pobierze oferty z portali i oceni je względem Twojego CV.</span>
          </div>
          <button type="button" className="btn btn-primary press" onClick={onOpenLaunchModal}>
            <Rocket size={16} /> Uruchom pipeline
          </button>
        </div>
      );
    }
    if (activeTab === 'Dopasowane' && analyzedCount === 0) {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Sparkles /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">{rawCount.toLocaleString('pl-PL')} ofert czeka na ocenę AI</span>
            <span className="ol-empty-hint">Dopasowane pojawią się tu po ocenieniu ofert przez model.</span>
          </div>
          <button type="button" className="btn btn-primary press" onClick={onOpenLaunchModal}>
            <Sparkles size={16} /> Uruchom ocenianie
          </button>
        </div>
      );
    }
    if (activeTab === 'Dopasowane') {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Sparkles /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Wszystko przejrzane</span>
            <span className="ol-empty-hint">Każda oceniona oferta ma już Twoją decyzję. Nowe przyjdą z kolejnym przebiegiem.</span>
          </div>
        </div>
      );
    }
    if (activeTab === 'Zapisane') {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Bookmark /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Nic jeszcze nie zapisałeś</span>
            <span className="ol-empty-hint">W doku oferty wciśnij Z albo ikonę zakładki.</span>
          </div>
        </div>
      );
    }
    if (activeTab === 'Aspiracyjne') {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><MountainSnow /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Brak ofert aspiracyjnych</span>
            <span className="ol-empty-hint">Oferty ponad obecny poziom oznaczysz klawiszem A w doku oferty.</span>
          </div>
        </div>
      );
    }
    if (activeTab === 'Odrzucone') {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><CircleX /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Brak odrzuconych ofert</span>
            <span className="ol-empty-hint">Odrzucone oferty trafiają tutaj — klawisz X w doku oferty.</span>
          </div>
        </div>
      );
    }
    if (activeTab === 'Ocenione') {
      return (
        <div className="ol-empty">
          <div className="ol-empty-well"><Star /></div>
          <div className="ol-empty-text">
            <span className="ol-empty-title">Brak ocenionych ofert</span>
            <span className="ol-empty-hint">Oceń ofertę suwakiem w doku i zatwierdź ✓.</span>
          </div>
        </div>
      );
    }
    return (
      <div className="ol-empty">
        <div className="ol-empty-well"><Layers /></div>
        <div className="ol-empty-text">
          <span className="ol-empty-title">Pusto w tej zakładce</span>
          <span className="ol-empty-hint">Baza nie zawiera obecnie ofert w tej kategorii.</span>
        </div>
      </div>
    );
  };

  return (
    <section className="panel glass ol-panel" aria-label="Lista ofert" ref={panelRef}>
      <div className="ol-head">
        <div className="ol-head-main">
          <div className="ol-title-row">
            <h1 className="ol-title">{activeTab}</h1>
            <CatIcon className="ol-cat-icon" aria-hidden="true" />
          </div>
          <p className="ol-count">{countText}</p>
        </div>
        <div className="ol-tools" ref={searchToolsRef}>
          {/* Otwarta lupa stoi w polu szukania jako jego ikona; klik w nią daje fokus polu. */}
          <button
            type="button"
            className={`iconbtn press ol-search-btn${searchOpen ? ' is-open' : ''}`}
            data-tip={searchOpen ? undefined : 'Szukaj'}
            data-kbd={searchOpen ? undefined : '/'}
            aria-label="Szukaj ofert"
            aria-expanded={searchOpen}
            aria-controls={SEARCH_INPUT_ID}
            onClick={() => {
              setSearchOpen(true);
              setTimeout(() => {
                searchInputRef.current?.focus();
                if (!searchOpen) searchInputRef.current?.select();
              }, 30);
            }}
          >
            <Search size={16} />
          </button>
        </div>
      </div>

      {/* Pole szukania rozwija się spod lupy: pas rośnie w dół, pole odsłania się od prawej,
          a lupa przejeżdża na miejsce ikony pola (.ol-search-slot). */}
      <div className={`ol-reveal ol-search-reveal${searchOpen ? ' is-open' : ''}`} inert={!searchOpen}>
        <div className="ol-reveal-inner">
          {/* Puste pole zamyka się, gdy fokus z niego wyjdzie (klik obok, Tab). Z wpisanym
              zapytaniem zostaje — filtruje listę, w którą się właśnie klika. */}
          <div
            className="ol-search-field"
            onMouseDown={(e) => {
              // Klik w obrzeże pola nie zabiera fokusu z inputu.
              if (e.target === e.currentTarget) e.preventDefault();
            }}
            onBlur={(e) => {
              const next = e.relatedTarget as Node | null;
              if (next && (e.currentTarget.contains(next) || searchToolsRef.current?.contains(next))) return;
              // Przełączenie okna/karty też zdejmuje fokus — wtedy pole zostaje.
              if (!document.hasFocus()) return;
              if (!search) setSearchOpen(false);
            }}
          >
            <span className="ol-search-slot" ref={searchSlotRef} aria-hidden="true" />
            <input
              ref={searchInputRef}
              id={SEARCH_INPUT_ID}
              className="ol-search-input"
              type="text"
              placeholder="Stanowisko lub firma…"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault();
                  if (search) {
                    onSearchChange('');
                  } else {
                    setSearchOpen(false);
                  }
                }
              }}
            />
            {search ? (
              <span className="ol-search-count">
                {total} {plural(total, RESULTS)}
              </span>
            ) : null}
            <button
              type="button"
              className="ol-search-clear"
              data-tip="Zamknij (Esc)"
              data-kbd="Esc"
              onClick={() => {
                if (search) onSearchChange('');
                else setSearchOpen(false);
              }}
            >
              Esc
            </button>
          </div>
        </div>
      </div>

      {gapFilter ? (
        <div className="ol-filter">
          <span className="ol-filter-label">
            Brakuje: <b>{gapFilter.skill}</b>
          </span>
          <button
            type="button"
            className="ol-filter-clear press"
            onClick={onClearGap}
            data-tip="Pokaż całą zakładkę"
            aria-label={`Usuń filtr braku: ${gapFilter.skill}`}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      ) : freshCount > 0 && freshSince ? (
        <div className="ol-fresh">
          <span className="ol-fresh-dot" aria-hidden="true" />
          <span className="ol-fresh-label">
            <b className="tnum">+{freshCount.toLocaleString('pl-PL')}</b> {plural(freshCount, NEW_ONES)} od pobierania{' '}
            {formatStamp(freshSince)}
          </span>
          <span className="ol-fresh-rule" aria-hidden="true" />
        </div>
      ) : null}

      <div className={`ol-rows${SWAP_CLASS[listSwap]}`} ref={scrollRef} aria-busy={loading}>
        {displayItems.length === 0
          ? renderEmpty()
          : displayItems.map((item, index) => {
              const isExiting = ghost?.item.link === item.link;
              // Wyjeżdżający wiersz był zaznaczony — zostaje w tym wyglądzie do końca wyjścia.
              const isSelected = selectedLink === item.link || isExiting;
              const metric = rowMetric(item, decisionTab);
              const decidedDay = decisionTab ? formatDecisionDay(item.decided_at) : null;

              return (
                <button
                  key={item.link}
                  type="button"
                  data-link={item.link}
                  className={`ol-row press ${isSelected ? 'is-selected' : ''} ${isExiting ? 'is-exiting' : ''}`}
                  style={{ '--i': index } as React.CSSProperties}
                  onClick={() => onSelectOffer(item.link)}
                  tabIndex={isExiting ? -1 : undefined}
                  aria-hidden={isExiting || undefined}
                  aria-pressed={isSelected}
                  aria-label={`${item.is_new ? 'Nowa: ' : ''}${item.title}, ${item.company}, ${metric.label}`}
                >
                  {item.is_new && <span className="ol-row-new" aria-hidden="true" />}
                  <span className="ol-row-content">
                    <span className="ol-row-text">
                      <span className="ol-row-title">{item.title}</span>
                      <span className="ol-row-meta">
                        {[item.company, item.location, decidedDay].filter(Boolean).join('  ·  ')}
                        {item.is_gone && <span className="ol-row-gone">· zdjęta</span>}
                      </span>
                    </span>
                    <span className={`ol-row-score is-${metric.tone}`}>
                      {metric.text}
                      {metric.unit && <span className="ol-row-unit">{metric.unit}</span>}
                    </span>
                  </span>
                  <span className="ol-row-bar" aria-hidden="true">
                    <span className="ol-row-level" style={{ width: `${metric.level}%` }} />
                  </span>
                </button>
              );
            })}
      </div>

      <footer className="ol-pager">
        {canJump && (
          <div
            ref={jumpRef}
            className={`ol-jump${jumpOpen ? ' is-open' : ''}`}
            inert={!jumpOpen}
            role="group"
            aria-label="Skok o wiele stron"
            onKeyDown={(e) => {
              if (e.key !== 'Escape') return;
              e.preventDefault();
              e.stopPropagation();
              setJumpOpen(false);
              jumpToggleRef.current?.focus();
            }}
          >
            <div className="ol-jump-head">
              <span className="ol-jump-page">
                Strona <b className="mono tnum">{jumpDraft}</b> z {totalPages.toLocaleString('pl-PL')}
              </span>
              {pageMarks && (
                <span className="ol-jump-mark">
                  od <b className="mono tnum">{markText(jumpDraft)}</b>
                </span>
              )}
            </div>
            <input
              type="range"
              className="range-slider"
              min={1}
              max={totalPages}
              step={1}
              value={jumpDraft}
              style={{ '--fill-pct': jumpFill } as React.CSSProperties}
              aria-label="Przeskocz do strony"
              aria-valuetext={`Strona ${jumpDraft} z ${totalPages}${pageMarks ? `, od ${markText(jumpDraft)}` : ''}`}
              onChange={(e) => setJumpDraft(Number(e.target.value))}
              onPointerUp={(e) => commitJump(Number(e.currentTarget.value))}
              onKeyUp={(e) => {
                if (SLIDER_KEYS[e.key]) commitJump(Number(e.currentTarget.value));
              }}
              onKeyDown={(e) => {
                if (SLIDER_KEYS[e.key]) e.stopPropagation();
              }}
            />
            <div className="range-ticks">
              {jumpTicks.map((t) => (
                <button
                  key={t.page}
                  type="button"
                  className={`range-tick press ${t.page === activeTickPage ? 'is-active' : ''}`}
                  onClick={() => {
                    setJumpDraft(t.page);
                    commitJump(t.page);
                  }}
                  aria-label={pageMarks ? `Skocz do ${t.label} (strona ${t.page})` : `Strona ${t.page}`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {canJump ? (
          <button
            ref={jumpToggleRef}
            type="button"
            className={`ol-pager-range ol-jump-toggle press${jumpOpen ? ' is-on' : ''}`}
            aria-expanded={jumpOpen}
            data-tip={jumpOpen ? 'Zwiń' : 'Przeskocz o wiele stron'}
            onClick={() => setJumpOpen((open) => !open)}
          >
            <span className={SWAP_CLASS[listSwap].trim()}>
              {`${from}–${to} z ${total.toLocaleString('pl-PL')}`}
            </span>
            <ChevronUp aria-hidden="true" />
          </button>
        ) : (
          <span className={`ol-pager-range${SWAP_CLASS[listSwap]}`}>
            {total === 0 ? 'Brak ofert' : `${from}–${to} z ${total.toLocaleString('pl-PL')}`}
          </span>
        )}
        <div className="ol-pager-btns">
          <button
            type="button"
            className="iconbtn press"
            disabled={page <= 1 || loading}
            onClick={() => onPageChange(page - 1)}
            data-tip="Poprzednia strona"
            aria-label="Poprzednia strona"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            className="iconbtn press"
            disabled={page >= totalPages || loading}
            onClick={() => onPageChange(page + 1)}
            data-tip="Następna strona"
            aria-label="Następna strona"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </footer>
    </section>
  );
};
