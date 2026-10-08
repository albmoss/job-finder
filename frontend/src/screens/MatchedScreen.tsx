import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import {
  BookmarkCheck,
  Check,
  EyeOff,
  FileUser,
  Link,
  RotateCcw,
  Search,
  SlidersHorizontal,
} from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { countFormat } from '../format';
import { NEW_ONES, OFFERS, plural } from '../plural';
import { navigate, paths } from '../router';
import type { OfferDetail, OfferListItem, OfferSort, OffersResponse } from '../types';
import { CompanyLogo } from './offer/CompanyLogo';
import { MatchedDetail } from './matched/MatchedDetail';
import { Pager } from './matched/Pager';
import { locationLine } from './offer/offer_format';
import type { ScreenProps } from './types';
import '../styles/matched.css';

const CARD_HEIGHT = 100;
const CARD_GAP = 10;
const PAGER_SPACE = 50;
const SORT_LABELS: Record<OfferSort, string> = { match: 'Najlepsze dopasowanie', newest: 'Najnowsze' };
const CHECK_BATCH = 10;

async function goneAmong(items: OfferListItem[]): Promise<Set<string>> {
  const links = items.filter((item) => !item.is_gone).map((item) => item.link);
  const batches: Promise<{ gone: string[] }>[] = [];
  for (let i = 0; i < links.length; i += CHECK_BATCH) batches.push(api.checkOffers(links.slice(i, i + CHECK_BATCH)));
  const results = await Promise.all(batches);
  return new Set(results.flatMap((res) => res.gone));
}

export function MatchedScreen({ route }: ScreenProps) {
  const app = useApp();
  const { toast, bumpData, dataVersion, openMarkSent } = app;
  const queryLink = route.query.get('oferta') ?? '';

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<OfferSort>('match');
  const [showHidden, setShowHidden] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number | null>(null);
  const [list, setList] = useState<OffersResponse | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detail, setDetail] = useState<OfferDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const cardsRef = useRef<HTMLDivElement>(null);
  const sortRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<OffersResponse | null>(null);
  listRef.current = list;

  const items = list?.items ?? [];
  const tab = showHidden ? 'Ukryte' : 'Dopasowane';
  const selectedLink = queryLink || items[0]?.link || '';
  const selectedRef = useRef(selectedLink);
  selectedRef.current = selectedLink;

  useLayoutEffect(() => {
    const el = cardsRef.current;
    if (!el) return;
    const measure = () => {
      const fits = Math.floor((el.clientHeight - PAGER_SPACE + CARD_GAP) / (CARD_HEIGHT + CARD_GAP));
      setPageSize((prev) => {
        const next = Math.max(3, fits);
        if (prev !== null && prev !== next) setPage((p) => Math.floor(((p - 1) * prev) / next) + 1);
        return next;
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const next = searchInput.trim();
    if (next === search) return;
    const timer = window.setTimeout(() => {
      setSearch(next);
      setPage(1);
      navigate(paths.matched(), { replace: true });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput, search]);

  useEffect(() => {
    if (pageSize === null) return;
    let cancelled = false;
    api
      .getOffers({ tab, search, page, pageSize, sort })
      .then((res) => {
        if (cancelled) return;
        if (res.items.length === 0 && page > 1 && res.total > 0) {
          setPage(Math.max(1, res.total_pages));
          return;
        }
        setList(res);
        setListError(null);
        goneAmong(res.items).then((gone) => {
          if (cancelled || gone.size === 0) return;
          const mark = <T extends { link: string }>(offer: T): T =>
            gone.has(offer.link) ? { ...offer, is_gone: true } : offer;
          setList((prev) => prev && { ...prev, items: prev.items.map(mark) });
          setDetail((prev) => prev && mark(prev));
        }, () => undefined);
      })
      .catch((err) => {
        if (!cancelled) setListError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tab, search, page, pageSize, sort, dataVersion]);

  useEffect(() => {
    if (!selectedLink) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api
      .getOfferDetail(selectedLink)
      .then((res) => {
        if (cancelled) return;
        if ((res.filtered || res.match_percentage === null) && res.status !== 'reject') {
          setDetail(null);
          const first = listRef.current?.items.find((item) => item.link !== selectedLink)?.link ?? '';
          navigate(paths.matched(first || undefined), { replace: true });
          return;
        }
        setDetail(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetail(null);
          toast(`Nie udało się wczytać oferty: ${errorMessage(err)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedLink, dataVersion, toast]);

  useEffect(() => {
    if (!sortOpen) return;
    const onPointer = (event: PointerEvent) => {
      if (!sortRef.current?.contains(event.target as Node)) setSortOpen(false);
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setSortOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [sortOpen]);

  const select = useCallback((link: string) => navigate(paths.matched(link || undefined), { replace: true }), []);

  const dropFromList = useCallback(
    (link: string) => {
      const current = listRef.current;
      if (!current) return;
      const idx = current.items.findIndex((item) => item.link === link);
      if (idx === -1) return;
      const rest = current.items.filter((item) => item.link !== link);
      const next = { ...current, items: rest, total: Math.max(0, current.total - 1) };
      listRef.current = next;
      setList(next);
      if (selectedRef.current === link) select((rest[idx] ?? rest[idx - 1])?.link ?? '');
    },
    [select],
  );

  const restoreTo = useCallback(
    async (link: string, message: string) => {
      try {
        await api.restoreDecision(link);
        bumpData();
        select(link);
        toast(message);
      } catch (err) {
        toast(`Nie udało się cofnąć: ${errorMessage(err)}`);
      }
    },
    [bumpData, select, toast],
  );

  const decide = async (offer: OfferDetail, status: 'save' | 'reject') => {
    setBusy(true);
    try {
      await api.updateDecision(offer.link, status);
      dropFromList(offer.link);
      bumpData();
      toast(status === 'save' ? 'Zapisano w Zapisanych.' : 'Oferta ukryta.', {
        icon: status === 'save' ? BookmarkCheck : EyeOff,
        action: {
          label: 'Cofnij',
          run: () => void restoreTo(offer.link, status === 'save' ? 'Cofnięto zapis.' : 'Oferta wróciła na listę.'),
        },
      });
    } catch (err) {
      toast(`Nie udało się ${status === 'save' ? 'zapisać oferty' : 'ukryć oferty'}: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const restore = async (offer: OfferDetail, message: string) => {
    setBusy(true);
    try {
      await api.restoreDecision(offer.link);
      if (offer.status === 'reject' && showHidden) dropFromList(offer.link);
      bumpData();
      toast(message, { icon: RotateCcw });
    } catch (err) {
      toast(`Nie udało się przywrócić oferty: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const restoreItem = async (item: OfferListItem) => {
    setBusy(true);
    try {
      await api.restoreDecision(item.link);
      dropFromList(item.link);
      bumpData();
      toast('Oferta wróciła do Dopasowanych.', { icon: RotateCcw });
    } catch (err) {
      toast(`Nie udało się przywrócić oferty: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const markSent = (offer: OfferDetail) => {
    let removed = false;
    openMarkSent(offer, {
      onChange: (next) => {
        setDetail((prev) => (prev && prev.link === next.link ? next : prev));
        if (next.status === 'apply' && !removed) {
          removed = true;
          dropFromList(next.link);
        }
        if (next.status !== 'apply' && removed) {
          removed = false;
          select(next.link);
        }
        bumpData();
      },
    });
  };

  const copyLink = async (link: string) => {
    try {
      await navigator.clipboard.writeText(link);
      toast('Skopiowano link.', { icon: Link });
    } catch (err) {
      toast(`Nie udało się skopiować linku: ${errorMessage(err)}`);
    }
  };

  const toggleHidden = () => {
    setSortOpen(false);
    setShowHidden((v) => !v);
    setPage(1);
    select('');
  };

  const changeSort = (next: OfferSort) => {
    setSortOpen(false);
    setShowHidden(false);
    setSort(next);
    setPage(1);
    select('');
  };

  const turnPage = (next: number) => {
    setPage(Math.min(Math.max(1, next), totalPages));
    select('');
  };

  const total = list?.total ?? 0;
  const totalPages = list?.total_pages ?? 1;
  const fresh = !showHidden ? list?.fresh_count ?? 0 : 0;
  const hiddenCount = list?.hidden_count ?? 0;
  const loaded = list !== null;
  const noOffersAtAll = loaded && total === 0 && !search && !showHidden;
  const usableDetail = detail && detail.link === selectedLink ? detail : null;

  return (
    <div className="mo-screen">
      <section className="mo-list" aria-label="Lista ofert">
        <div className="mo-heading">
          <h1 className="mo-title">Dopasowane</h1>
          {loaded && (
            <span className="mo-count">
              {countFormat.format(total)} {plural(total, OFFERS)}
            </span>
          )}
        </div>

        <label className="field mo-search">
          <Search />
          <input
            type="search"
            value={searchInput}
            placeholder="Stanowisko lub firma"
            aria-label="Szukaj ofert"
            onChange={(event) => setSearchInput(event.target.value)}
          />
        </label>

        <div className="mo-sortbar" ref={sortRef}>
          <span className="label">{showHidden ? `Ukryte (${hiddenCount})` : SORT_LABELS[sort]}</span>
          <button
            type="button"
            className="btn-quiet mo-sort-btn"
            aria-label="Sortowanie i filtry"
            aria-haspopup="menu"
            aria-expanded={sortOpen}
            onClick={() => setSortOpen((open) => !open)}
          >
            <SlidersHorizontal />
          </button>
          {sortOpen && (
            <div className="menu panel mo-sort-menu" role="menu">
              {(Object.keys(SORT_LABELS) as OfferSort[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  className="menu-item"
                  role="menuitemradio"
                  aria-checked={!showHidden && sort === key}
                  onClick={() => changeSort(key)}
                >
                  <Check className={!showHidden && sort === key ? '' : 'mo-invisible'} />
                  {SORT_LABELS[key]}
                </button>
              ))}
              <div className="divider mo-menu-divider" />
              <button
                type="button"
                className="menu-item"
                role="menuitemcheckbox"
                aria-checked={showHidden}
                onClick={toggleHidden}
              >
                {showHidden ? <Check /> : <EyeOff />}
                Ukryte ({hiddenCount})
              </button>
            </div>
          )}
        </div>

        {fresh > 0 && (
          <p className="mo-fresh">
            {fresh} {plural(fresh, NEW_ONES)} od ostatniego wyszukiwania
          </p>
        )}

        <div className="mo-results" ref={cardsRef}>
          <div className="mo-cards">
            {listError && <p className="mo-list-note">Nie udało się wczytać ofert: {listError}</p>}
            {!listError && loaded && items.length === 0 && (
              <p className="mo-list-note">
                {search
                  ? 'Żadna oferta nie pasuje do wyszukiwania.'
                  : showHidden
                    ? 'Nie masz ukrytych ofert.'
                    : 'Brak dopasowanych ofert.'}
              </p>
            )}
            {items.map((item) => (
              <OfferCard
                key={item.link}
                item={item}
                active={item.link === selectedLink}
                hidden={showHidden}
                busy={busy}
                onSelect={() => select(item.link)}
                onRestore={() => void restoreItem(item)}
              />
            ))}
          </div>

          {totalPages > 1 && <Pager page={page} totalPages={totalPages} onPage={turnPage} />}
        </div>
      </section>

      {usableDetail ? (
        <MatchedDetail
          key={usableDetail.link}
          offer={usableDetail}
          busy={busy}
          onSave={() => void decide(usableDetail, 'save')}
          onUnsave={() => void restore(usableDetail, 'Cofnięto zapis.')}
          onHide={() => void decide(usableDetail, 'reject')}
          onRestore={() => void restore(usableDetail, 'Oferta wróciła do Dopasowanych.')}
          onMarkSent={() => markSent(usableDetail)}
          onCopyLink={() => void copyLink(usableDetail.link)}
        />
      ) : (
        <article className="panel mo-detail mo-detail-empty">
          {noOffersAtAll ? (
            <div className="mo-empty">
              <h2 className="title-lg">Nie znaleźliśmy ofert</h2>
              <p className="muted">Sprawdź profil odczytany z CV albo wyszukaj ponownie później.</p>
              <a className="btn" href={`#${paths.cv}`}>
                <FileUser />
                Sprawdź profil
              </a>
            </div>
          ) : (
            loaded && items.length === 0 && <div className="empty">Wybierz inną listę albo zmień wyszukiwanie.</div>
          )}
        </article>
      )}
    </div>
  );
}

interface OfferCardProps {
  item: OfferListItem;
  active: boolean;
  hidden: boolean;
  busy: boolean;
  onSelect: () => void;
  onRestore: () => void;
}

function OfferCard({ item, active, hidden, busy, onSelect, onRestore }: OfferCardProps) {
  const place = locationLine(item.location, item.work_mode);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect();
    }
  };
  return (
    <div
      className={`glass mo-card${active ? ' is-active' : ''}`}
      role="button"
      tabIndex={0}
      aria-pressed={active}
      onClick={onSelect}
      onKeyDown={onKeyDown}
    >
      <CompanyLogo url={item.logo_url} source={item.source} size="sm" />
      <div className="mo-card-body">
        <span className="mo-card-title" title={item.title}>{item.title}</span>
        <span className="mo-card-company">{item.company}</span>
        <div className="mo-card-bottom">
          <span className="mo-card-place">{item.is_gone ? [place, 'zdjęta z portalu'].filter(Boolean).join(' · ') : place}</span>
          {hidden && (
            <button
              type="button"
              className="mo-card-restore"
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onRestore();
              }}
            >
              <RotateCcw />
              Przywróć
            </button>
          )}
        </div>
      </div>
      <div className="mo-card-score">
        {item.match_percentage !== null && <span className="mo-card-percent">{item.match_percentage}%</span>}
      </div>
      {item.match_percentage !== null && (
        <span
          className="match-track"
          aria-hidden="true"
          style={{ '--fill': `${Math.max(0, Math.min(100, item.match_percentage))}%` } as CSSProperties}
        />
      )}
    </div>
  );
}
