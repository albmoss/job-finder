import { useCallback, useEffect, useRef, useState } from 'react';
import { Bookmark, BookmarkCheck, LoaderCircle, Plus } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong, parseDate } from '../format';
import { OFFERS, plural } from '../plural';
import { navigate, paths } from '../router';
import type { CvVersionSummary, OfferDetail, OfferListItem } from '../types';
import { AddFromLinkModal } from './add/AddFromLinkModal';
import { MatchedDetail } from './matched/MatchedDetail';
import { locationLine } from './offer/offer_format';
import type { ScreenProps } from './types';
import '../styles/saved.css';

const PAGE_SIZE = 100;
const SEP = '\u2002·\u2002';

async function loadAllSaved(): Promise<OfferListItem[]> {
  const items: OfferListItem[] = [];
  for (let page = 1; ; page += 1) {
    const res = await api.getOffers({ tab: 'Zapisane', page, pageSize: PAGE_SIZE, sort: 'newest' });
    items.push(...res.items);
    if (page >= res.total_pages || res.items.length === 0) return items;
  }
}

function cvStatusText(cv: CvVersionSummary | null): string | null {
  if (cv?.status === 'ready') return 'CV gotowe';
  if (cv?.status === 'draft') return 'Szkic CV';
  if (cv?.status === 'running') return 'Tworzymy CV…';
  return null;
}

function savedText(value: string | null): string {
  const date = parseDate(value);
  return date ? `Zapisano ${formatDayLong(date, date.getFullYear() !== new Date().getFullYear())}` : '';
}

export function SavedScreen({ route }: ScreenProps) {
  const app = useApp();
  const { dataVersion, toast, bumpData, openMarkSent } = app;
  const [items, setItems] = useState<OfferListItem[] | null>(null);
  const [detail, setDetail] = useState<OfferDetail | null>(null);
  const [loadError, setLoadError] = useState('');
  const [adding, setAdding] = useState(false);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  useEffect(() => {
    let alive = true;
    loadAllSaved()
      .then((list) => {
        if (!alive) return;
        setItems(list);
        setLoadError('');
      })
      .catch((err) => alive && setLoadError(errorMessage(err)));
    return () => {
      alive = false;
    };
  }, [dataVersion]);

  const requested = route.query.get('oferta');
  const selected = items?.find((i) => i.link === requested) ?? items?.[0] ?? null;
  const selectedLink = selected?.link ?? null;

  useEffect(() => {
    if (!selectedLink) {
      setDetail(null);
      return;
    }
    let alive = true;
    api
      .getOfferDetail(selectedLink)
      .then((res) => {
        if (!alive) return;
        setDetail(res);
        if (res.is_gone) return;
        api.checkOffers([res.link]).then((check) => {
          if (alive && check.gone.length > 0) setDetail((prev) => (prev?.link === res.link ? { ...prev, is_gone: true } : prev));
        }, () => undefined);
      })
      .catch((err) => {
        if (!alive) return;
        setDetail(null);
        toast(`Nie udało się wczytać oferty: ${errorMessage(err)}`);
      });
    return () => {
      alive = false;
    };
  }, [selectedLink, dataVersion, toast]);

  const select = useCallback((link: string | null) => {
    navigate(paths.saved(link ?? undefined), { replace: true });
  }, []);

  const removeLocal = (link: string) => {
    const prev = itemsRef.current;
    if (!prev) return;
    const idx = prev.findIndex((i) => i.link === link);
    if (idx < 0) return;
    const next = prev.filter((i) => i.link !== link);
    itemsRef.current = next;
    setItems(next);
    const current = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('oferta');
    if (!current || current === link) select(next[Math.min(idx, next.length - 1)]?.link ?? null);
  };

  const unsave = async (item: OfferListItem) => {
    removeLocal(item.link);
    try {
      await api.restoreDecision(item.link);
      bumpData();
      toast('Usunięto z zapisanych.', {
        icon: Bookmark,
        action: {
          label: 'Cofnij',
          run: async () => {
            try {
              await api.updateDecision(item.link, 'save');
              if (item.note) await api.saveNote(item.link, item.note);
              bumpData();
              select(item.link);
            } catch (err) {
              toast(`Nie udało się cofnąć: ${errorMessage(err)}`);
            }
          },
        },
      });
    } catch (err) {
      bumpData();
      toast(`Nie udało się usunąć z zapisanych: ${errorMessage(err)}`);
    }
  };

  const markSent = (offer: OfferDetail) => {
    openMarkSent(offer, {
      onChange: (next) => {
        if (next.status === 'apply') removeLocal(next.link);
      },
    });
  };

  const updateNote = (link: string, note: string) =>
    setItems((prev) => prev?.map((i) => (i.link === link ? { ...i, note } : i)) ?? prev);

  const count = items?.length ?? 0;
  const shown = detail && selected && detail.link === selected.link ? detail : null;

  return (
    <div className="sv-screen">
      <header className="sv-head">
        <div className="sv-head-text">
          <h1 className="title-xl">Zapisane na później</h1>
          <p className="sv-sub">
            {items ? `${count} ${plural(count, OFFERS)}, do których chcesz wrócić.` : '\u00a0'}
          </p>
        </div>
        <button type="button" className="btn" onClick={() => setAdding(true)}>
          <Plus />
          Dodaj z linku
        </button>
      </header>

      <div className="sv-body">
        <section className="panel sv-list" aria-label="Zapisane oferty">
          {loadError && !items ? (
            <div className="empty">
              <p>Nie udało się wczytać zapisanych ofert: {loadError}</p>
            </div>
          ) : !items ? (
            <div className="empty">
              <LoaderCircle className="spin" />
            </div>
          ) : items.length === 0 ? (
            <div className="empty">
              <Bookmark />
              <p className="sv-empty-text">
                Nie masz zapisanych ofert.{' '}
                <a className="link-btn" href={`#${paths.matched()}`}>
                  Przejdź do Dopasowanych
                </a>{' '}
                i zapisz te, do których chcesz wrócić.
              </p>
            </div>
          ) : (
            <ul className="sv-rows scroll">
              {items.map((item) => {
                const cv = cvStatusText(item.cv);
                return (
                  <li
                    key={item.link}
                    className="glass sv-row"
                    aria-current={item.link === selected?.link}
                    tabIndex={0}
                    onClick={() => select(item.link)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        select(item.link);
                      }
                    }}
                  >
                    <div className="sv-row-top">
                      <h2 className="sv-row-title">{item.title}</h2>
                      <button
                        type="button"
                        className="btn-quiet sv-unsave"
                        title="Usuń z zapisanych"
                        aria-label="Usuń z zapisanych"
                        onClick={(e) => {
                          e.stopPropagation();
                          unsave(item);
                        }}
                      >
                        <BookmarkCheck />
                      </button>
                    </div>
                    <p className="sv-row-place">
                      {[item.company, locationLine(item.location, item.work_mode)].filter(Boolean).join(SEP)}
                    </p>
                    <div className="sv-row-meta">
                      <span className="sv-row-pay">
                        {item.salary_text}
                        <span className="sv-row-saved">
                          {item.salary_text && SEP}
                          {savedText(item.decided_at)}
                        </span>
                      </span>
                      {cv && <span className={item.cv?.status === 'ready' ? 'sv-cv is-ready' : 'sv-cv'}>{cv}</span>}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {items?.length !== 0 &&
          (shown && selected ? (
            <MatchedDetail
              key={shown.link}
              offer={shown}
              busy={false}
              onUnsave={() => void unsave(selected)}
              onMarkSent={() => markSent(shown)}
            >
              <NoteField link={shown.link} initial={selected.note} onSaved={updateNote} />
            </MatchedDetail>
          ) : (
            <article className="panel mo-detail" />
          ))}
      </div>

      {adding && (
        <AddFromLinkModal status="save" onClose={() => setAdding(false)} onSaved={(offer) => select(offer.link)} />
      )}
    </div>
  );
}

interface NoteFieldProps {
  link: string;
  initial: string;
  onSaved: (link: string, note: string) => void;
}

function NoteField({ link, initial, onSaved }: NoteFieldProps) {
  const { toast } = useApp();
  const [note, setNote] = useState(initial);
  const stored = useRef(initial);

  const save = async () => {
    const next = note.trim();
    if (next === stored.current) return;
    try {
      const res = await api.saveNote(link, next);
      stored.current = res.offer.note;
      onSaved(link, res.offer.note);
      toast('Zapisano notatkę.');
    } catch (err) {
      toast(`Nie udało się zapisać notatki: ${errorMessage(err)}`);
    }
  };

  return (
    <label className="sv-note">
      <span className="label">Twoja notatka</span>
      <textarea
        className="field"
        value={note}
        placeholder="Np. wysłać CV po sprawdzeniu portfolio."
        onChange={(e) => setNote(e.target.value)}
        onBlur={() => void save()}
      />
    </label>
  );
}
