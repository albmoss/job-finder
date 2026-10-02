import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Bookmark,
  BookmarkCheck,
  Check,
  Download,
  FileCheck2,
  FilePenLine,
  FileText,
  LoaderCircle,
  Plus,
} from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong, parseDate } from '../format';
import { OFFERS, plural } from '../plural';
import { navigate, paths } from '../router';
import type { CvVersionSummary, OfferListItem } from '../types';
import { AddFromLinkModal } from './add/AddFromLinkModal';
import { locationLine } from './offer/offer_format';
import type { ScreenProps } from './types';
import '../styles/saved.css';

const PAGE_SIZE = 100;

async function loadAllSaved(): Promise<OfferListItem[]> {
  const items: OfferListItem[] = [];
  for (let page = 1; ; page += 1) {
    const res = await api.getOffers({ tab: 'Zapisane', page, pageSize: PAGE_SIZE, sort: 'newest' });
    items.push(...res.items);
    if (page >= res.total_pages || res.items.length === 0) return items;
  }
}

function cvStatusText(cv: CvVersionSummary | null): string {
  if (cv?.status === 'ready') return 'CV gotowe';
  if (cv?.status === 'draft') return 'Szkic CV';
  if (cv?.status === 'running') return 'Tworzymy CV…';
  return 'Bez osobnej wersji CV';
}

function placeLine(item: OfferListItem): string {
  const where = locationLine(item.location, item.work_mode);
  return [item.company, where].filter(Boolean).join('\u2002·\u2002');
}

function savedDay(value: string | null): string {
  const date = parseDate(value);
  return date ? formatDayLong(date, date.getFullYear() !== new Date().getFullYear()) : '';
}

export function SavedScreen({ route }: ScreenProps) {
  const app = useApp();
  const { dataVersion, toast, bumpData, refreshStats, openMarkSent } = app;
  const [items, setItems] = useState<OfferListItem[] | null>(null);
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
      refreshStats();
      toast('Usunięto z zapisanych.', {
        icon: Bookmark,
        action: {
          label: 'Cofnij',
          run: async () => {
            try {
              await api.updateDecision(item.link, 'save');
              if (item.note) await api.saveNote(item.link, item.note);
              bumpData();
              refreshStats();
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

  const markSent = (item: OfferListItem) => {
    openMarkSent(item, {
      onChange: (offer) => {
        if (offer.status === 'apply') removeLocal(offer.link);
      },
    });
  };

  const updateNote = (link: string, note: string) =>
    setItems((prev) => prev?.map((i) => (i.link === link ? { ...i, note } : i)) ?? prev);

  const count = items?.length ?? 0;

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
              {items.map((item) => (
                <li
                  key={item.link}
                  className="sv-row"
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
                  <p className="sv-row-place">{placeLine(item)}</p>
                  <div className="sv-row-meta">
                    <span>{item.salary_text || 'Brak widełek'}</span>
                    <span className={item.cv?.status === 'ready' ? 'sv-cv is-ready' : 'sv-cv'}>
                      {cvStatusText(item.cv)}
                    </span>
                  </div>
                  <p className="sv-row-date">Zapisano {savedDay(item.decided_at)}</p>
                </li>
              ))}
            </ul>
          )}
          <p className="sv-foot">Zapisanie oferty nie wysyła aplikacji.</p>
        </section>

        {items?.length !== 0 && (
          <aside className="panel sv-detail">
            {selected && (
              <SavedDetail key={selected.link} item={selected} onMarkSent={markSent} onNoteSaved={updateNote} />
            )}
          </aside>
        )}
      </div>

      {adding && (
        <AddFromLinkModal status="save" onClose={() => setAdding(false)} onSaved={(offer) => select(offer.link)} />
      )}
    </div>
  );
}

interface SavedDetailProps {
  item: OfferListItem;
  onMarkSent: (item: OfferListItem) => void;
  onNoteSaved: (link: string, note: string) => void;
}

function SavedDetail({ item, onMarkSent, onNoteSaved }: SavedDetailProps) {
  const { toast } = useApp();
  const [note, setNote] = useState(item.note);
  const [savingNote, setSavingNote] = useState(false);
  const cv = item.cv;

  const saveNote = async () => {
    setSavingNote(true);
    try {
      const res = await api.saveNote(item.link, note.trim());
      onNoteSaved(item.link, res.offer.note);
      setNote(res.offer.note);
      toast('Zapisano notatkę.');
    } catch (err) {
      toast(`Nie udało się zapisać notatki: ${errorMessage(err)}`);
    } finally {
      setSavingNote(false);
    }
  };

  return (
    <div className="sv-detail-body scroll">
      <p className="sv-company">{item.company}</p>
      <h2 className="sv-title">{item.title}</h2>

      {cv?.status === 'ready' ? (
        <>
          <div className="sv-cv-card">
            <FileCheck2 />
            <p className="sv-cv-card-title">CV pod ofertę jest gotowe</p>
            <p className="label">{cv.file_name}</p>
          </div>
          <a className="btn" href={api.cvVersionPdfUrl(cv.id)} download={cv.file_name ?? undefined}>
            <Download />
            Pobierz CV
          </a>
        </>
      ) : cv?.status === 'draft' || cv?.status === 'running' ? (
        <>
          <div className="sv-cv-card">
            {cv.status === 'running' ? <LoaderCircle className="spin" /> : <FilePenLine />}
            <p className="sv-cv-card-title">{cv.status === 'running' ? 'Tworzymy CV…' : 'Szkic CV czeka na sprawdzenie'}</p>
            <p className="label">Wersja {cv.version}</p>
          </div>
          <a className="btn" href={`#${paths.cvVersion(cv.id)}`}>
            <FilePenLine />
            Sprawdź wersję
          </a>
        </>
      ) : (
        <>
          <div className="sv-cv-card">
            <FileText />
            <p className="sv-cv-card-title">Bez osobnej wersji CV</p>
            <p className="label">Wyślesz bazowe CV albo dopasujesz je do tej oferty.</p>
          </div>
          <a className="btn" href={`#${paths.tailorNew(item.link)}`}>
            <FilePenLine />
            Dopasuj CV
          </a>
        </>
      )}

      <label className="sv-note-label" htmlFor="sv-note">
        Twoja notatka
      </label>
      <textarea
        id="sv-note"
        className="field sv-note"
        value={note}
        placeholder="Np. wysłać CV po sprawdzeniu portfolio."
        disabled={savingNote}
        onChange={(e) => setNote(e.target.value)}
      />
      <button
        type="button"
        className="btn"
        disabled={savingNote || note.trim() === item.note}
        onClick={saveNote}
      >
        {savingNote ? <LoaderCircle className="spin" /> : <Check />}
        Zapisz notatkę
      </button>

      <div className="sv-spacer" />

      <a className="btn btn-primary" href={item.link} target="_blank" rel="noreferrer">
        <ArrowUpRight />
        Otwórz ogłoszenie
      </a>
      <button type="button" className="btn" onClick={() => onMarkSent(item)}>
        <Check />
        Oznacz jako wysłane
      </button>
      <p className="sv-hint">Aplikujesz na stronie pracodawcy. Po wysłaniu zaznacz to tutaj.</p>
    </div>
  );
}
