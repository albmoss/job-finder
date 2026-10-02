import { useEffect, useRef, useState, type DragEvent } from 'react';
import { flushSync } from 'react-dom';
import {
  ArrowUpRight,
  CalendarDays,
  Check,
  ChevronRight,
  Ellipsis,
  FileText,
  LoaderCircle,
  Plus,
  Undo2,
} from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong, formatRelativeDay } from '../format';
import { navigate, paths } from '../router';
import type { ApplicationItem, NextStep, Stage } from '../types';
import { AddFromLinkModal } from './add/AddFromLinkModal';
import type { ScreenProps } from './types';
import '../styles/applications.css';

const STAGES: { id: Stage; label: string }[] = [
  { id: 'apply', label: 'Wysłane' },
  { id: 'interview', label: 'Rozmowy' },
  { id: 'offer', label: 'Oferta pracy' },
  { id: 'archive', label: 'Zakończone' },
];

const DRAG_TYPE = 'application/x-jobfinder-link';

function parseDue(due: string | null | undefined): { date: Date; hasTime: boolean } | null {
  if (!due) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(due);
  if (!m) return null;
  const hasTime = m[4] !== undefined;
  const date = new Date(+m[1], +m[2] - 1, +m[3], hasTime ? +m[4] : 0, hasTime ? +m[5] : 0);
  return Number.isNaN(date.getTime()) ? null : { date, hasTime };
}

function dueEnd(step: NextStep | null): number | null {
  const parsed = parseDue(step?.due);
  if (!parsed) return null;
  const end = new Date(parsed.date);
  if (!parsed.hasTime) end.setHours(23, 59, 59, 999);
  return end.getTime();
}

function formatDue(due: string | null | undefined, now = new Date()): string {
  const parsed = parseDue(due);
  if (!parsed) return '';
  const { date, hasTime } = parsed;
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(date) - startOf(now)) / 86_400_000);
  const day =
    days === 0
      ? 'Dzisiaj'
      : days === 1
        ? 'Jutro'
        : days === -1
          ? 'Wczoraj'
          : formatDayLong(date, date.getFullYear() !== now.getFullYear());
  if (!hasTime) return day;
  const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  return `${day}, ${time}`;
}

function dateLine(item: ApplicationItem): string {
  if (item.stage === 'apply') return `Wysłano ${formatRelativeDay(item.applied_at ?? item.decided_at)}`;
  if (item.stage === 'archive') return `Zamknięto ${formatRelativeDay(item.decided_at)}`;
  if (item.stage === 'offer' && item.next_step?.due) {
    const parsed = parseDue(item.next_step.due);
    if (parsed) return `Odpowiedź do ${formatDayLong(parsed.date, parsed.date.getFullYear() !== new Date().getFullYear())}`;
  }
  if (item.next_step?.due) return formatDue(item.next_step.due);
  const day = formatRelativeDay(item.decided_at);
  return day.charAt(0).toUpperCase() + day.slice(1);
}

function toInputValue(due: string | null | undefined): string {
  const parsed = parseDue(due);
  if (!parsed) return '';
  const d = parsed.date;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function ApplicationsScreen({ route }: ScreenProps) {
  const queryLink = route.query.get('oferta') ?? '';
  const { dataVersion, toast, bumpData } = useApp();
  const [items, setItems] = useState<ApplicationItem[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [adding, setAdding] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editingFor, setEditingFor] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [dropStage, setDropStage] = useState<Stage | null>(null);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  useEffect(() => {
    let alive = true;
    api
      .getApplications()
      .then((res) => {
        if (!alive) return;
        setItems(res.items);
        setLoadError('');
      })
      .catch((err) => alive && setLoadError(errorMessage(err)));
    return () => {
      alive = false;
    };
  }, [dataVersion]);

  const queriedLoaded = !!queryLink && !!items?.some((i) => i.link === queryLink);
  useEffect(() => {
    if (!queriedLoaded) return;
    setFocused(queryLink);
    document.querySelector(`[data-link="${CSS.escape(queryLink)}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [queryLink, queriedLoaded]);

  const patchItem = (link: string, patch: Partial<ApplicationItem>) =>
    setItems((prev) => prev?.map((i) => (i.link === link ? { ...i, ...patch } : i)) ?? prev);

  const moveTo = async (link: string, stage: Stage) => {
    const item = itemsRef.current?.find((i) => i.link === link);
    if (!item || item.stage === stage) return;
    const previous = item.stage;
    patchItem(link, { stage });
    try {
      const res = await api.updateDecision(link, 'apply', stage);
      patchItem(link, { stage: res.offer.stage ?? stage, decided_at: res.offer.decided_at });
      bumpData();
    } catch (err) {
      patchItem(link, { stage: previous });
      toast(`Nie udało się zmienić etapu: ${errorMessage(err)}`);
    }
  };

  const moveFromMenu = (link: string, stage: Stage) => {
    const run = () => {
      setMenuFor(null);
      void moveTo(link, stage);
    };
    if (!document.startViewTransition || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      run();
      return;
    }
    flushSync(() => setMoving(link));
    const transition = document.startViewTransition(() => flushSync(run));
    void transition.finished.finally(() => setMoving(null));
  };

  const undoSent = async (item: ApplicationItem) => {
    if (!window.confirm(`Cofnąć wysłanie aplikacji do ${item.company}? Oferta wróci do Dopasowanych.`)) return;
    try {
      await api.restoreDecision(item.link);
      setItems((prev) => prev?.filter((i) => i.link !== item.link) ?? prev);
      bumpData();
      toast('Cofnięto wysłanie.', { icon: Undo2 });
    } catch (err) {
      toast(`Nie udało się cofnąć wysłania: ${errorMessage(err)}`);
    }
  };

  const saveNextStep = async (item: ApplicationItem, label: string, due: string) => {
    try {
      const res = await api.saveNextStep(item.link, label.trim(), label.trim() && due ? due.replace('T', ' ') : null);
      patchItem(item.link, { next_step: res.offer.next_step });
      setEditingFor(null);
      bumpData();
      toast(label.trim() ? 'Zapisano następny krok.' : 'Usunięto następny krok.');
    } catch (err) {
      toast(`Nie udało się zapisać kroku: ${errorMessage(err)}`);
    }
  };

  useEffect(() => {
    if (!menuFor) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as Element).closest?.('.ap-menu-wrap')) setMenuFor(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuFor(null);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuFor]);

  const now = Date.now();
  const upcoming = (items ?? [])
    .map((item) => ({ item, at: dueEnd(item.next_step) }))
    .filter((x): x is { item: ApplicationItem; at: number } => x.at !== null && x.at >= now && x.item.stage !== 'archive')
    .sort((a, b) => a.at - b.at)[0]?.item;

  const openApplication = (link: string) => {
    setFocused(link);
    setMenuFor(link);
    document.querySelector(`[data-link="${CSS.escape(link)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  };

  const onDrop = (e: DragEvent, stage: Stage) => {
    e.preventDefault();
    const link = e.dataTransfer.getData(DRAG_TYPE) || dragging;
    setDropStage(null);
    setDragging(null);
    if (link) moveTo(link, stage);
  };

  return (
    <div className="ap-screen">
      <header className="ap-head">
        <div className="ap-head-text">
          <h1 className="title-xl">Twoje aplikacje</h1>
          <p className="ap-sub">Wysłane CV i dalsze etapy rekrutacji w jednym miejscu.</p>
        </div>
        <button type="button" className="btn" onClick={() => setAdding(true)}>
          <Plus />
          Dodaj aplikację
        </button>
      </header>

      {upcoming?.next_step && (
        <div className="glass ap-next">
          <CalendarDays className="ap-next-icon" />
          <span className="ap-next-when">{formatDue(upcoming.next_step.due)}</span>
          <span className="ap-next-what">
            {/\sz\s/i.test(upcoming.next_step.label)
              ? `${upcoming.next_step.label} · ${upcoming.company}`
              : `${upcoming.next_step.label} z ${upcoming.company}`}
          </span>
          <button type="button" className="btn ap-next-btn" onClick={() => openApplication(upcoming.link)}>
            <ArrowUpRight />
            Otwórz aplikację
          </button>
        </div>
      )}

      {!items ? (
        <div className="panel ap-state">
          <div className="empty">
            {loadError ? <p>Nie udało się wczytać aplikacji: {loadError}</p> : <LoaderCircle className="spin" />}
          </div>
        </div>
      ) : (
        <div className="ap-board">
          {STAGES.map((stage) => {
            const cards = items.filter((i) => i.stage === stage.id);
            return (
              <section
                key={stage.id}
                className={`panel ap-col${dropStage === stage.id ? ' is-drop' : ''}`}
                aria-label={stage.label}
                onDragOver={(e) => {
                  if (!dragging) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = 'move';
                  if (dropStage !== stage.id) setDropStage(stage.id);
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
                    setDropStage((s) => (s === stage.id ? null : s));
                  }
                }}
                onDrop={(e) => onDrop(e, stage.id)}
              >
                <header className="ap-col-head">
                  <h2 className="ap-col-title">{stage.label}</h2>
                  <span className="ap-col-count">{cards.length}</span>
                </header>
                <div className="ap-cards scroll">
                  {cards.map((item) => (
                    <ApplicationCard
                      key={item.link}
                      item={item}
                      focused={focused === item.link}
                      dragging={dragging === item.link}
                      moving={moving === item.link}
                      menuOpen={menuFor === item.link}
                      editing={editingFor === item.link}
                      onToggleMenu={() => setMenuFor((m) => (m === item.link ? null : item.link))}
                      onMove={(s) => moveFromMenu(item.link, s)}
                      onEditStep={() => {
                        setMenuFor(null);
                        setEditingFor(item.link);
                      }}
                      onCancelEdit={() => setEditingFor(null)}
                      onSaveStep={(label, due) => saveNextStep(item, label, due)}
                      onUndo={() => {
                        setMenuFor(null);
                        undoSent(item);
                      }}
                      onDragStart={(e) => {
                        e.dataTransfer.setData(DRAG_TYPE, item.link);
                        e.dataTransfer.effectAllowed = 'move';
                        setMenuFor(null);
                        setDragging(item.link);
                      }}
                      onDragEnd={() => {
                        setDragging(null);
                        setDropStage(null);
                      }}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <p className="ap-foot">Etap zmienisz na karcie aplikacji lub przeciągając ją do innej kolumny.</p>

      {adding && (
        <AddFromLinkModal
          status="apply"
          onClose={() => setAdding(false)}
          onSaved={(offer) => navigate(paths.applications(offer.link), { replace: true })}
        />
      )}
    </div>
  );
}

interface ApplicationCardProps {
  item: ApplicationItem;
  focused: boolean;
  dragging: boolean;
  moving: boolean;
  menuOpen: boolean;
  editing: boolean;
  onToggleMenu: () => void;
  onMove: (stage: Stage) => void;
  onEditStep: () => void;
  onCancelEdit: () => void;
  onSaveStep: (label: string, due: string) => Promise<void>;
  onUndo: () => void;
  onDragStart: (e: DragEvent) => void;
  onDragEnd: () => void;
}

function ApplicationCard(props: ApplicationCardProps) {
  const { item, focused, dragging, moving, menuOpen, editing, onToggleMenu, onMove, onEditStep, onUndo } = props;
  const [stagesOpen, setStagesOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) setStagesOpen(false);
  }, [menuOpen]);

  const className = ['glass', 'ap-card', focused && 'is-focused', dragging && 'is-dragging', menuOpen && 'is-menu']
    .filter(Boolean)
    .join(' ');

  return (
    <article
      className={className}
      data-link={item.link}
      style={moving ? { viewTransitionName: 'ap-moving' } : undefined}
      draggable={!editing}
      onDragStart={props.onDragStart}
      onDragEnd={props.onDragEnd}
    >
      <div className="ap-card-top">
        <span className="ap-card-company">{item.company}</span>
        <div className="ap-menu-wrap">
          <button
            type="button"
            className="btn-quiet ap-menu-btn"
            aria-label="Więcej"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={onToggleMenu}
          >
            <Ellipsis />
          </button>
          {menuOpen && (
            <div className="menu panel ap-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                className="menu-item"
                aria-expanded={stagesOpen}
                onClick={() => setStagesOpen((o) => !o)}
              >
                Zmień etap
                <ChevronRight className={stagesOpen ? 'ap-chev is-open' : 'ap-chev'} />
              </button>
              {stagesOpen &&
                STAGES.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={item.stage === s.id}
                    className="menu-item ap-stage-item"
                    onClick={() => onMove(s.id)}
                  >
                    <Check className={item.stage === s.id ? '' : 'ap-hidden'} />
                    {s.label}
                  </button>
                ))}
              <button type="button" role="menuitem" className="menu-item" onClick={onEditStep}>
                Następny krok
              </button>
              <a role="menuitem" className="menu-item" href={item.link} target="_blank" rel="noreferrer">
                Otwórz ogłoszenie
              </a>
              <button type="button" role="menuitem" className="menu-item" onClick={onUndo}>
                Cofnij wysłanie
              </button>
            </div>
          )}
        </div>
      </div>
      <h3 className="ap-card-title">{item.title}</h3>
      <p className="ap-card-date">{dateLine(item)}</p>
      {editing ? (
        <NextStepEditor item={item} onCancel={props.onCancelEdit} onSave={props.onSaveStep} />
      ) : (
        <p className="ap-card-meta">
          {item.next_step ? (
            <>
              {parseDue(item.next_step.due)?.hasTime ? <CalendarDays /> : <FileText />}
              {item.next_step.label}
            </>
          ) : (
            <>
              <FileText />
              {item.cv ? `CV: wersja ${item.cv.version}` : 'CV: bazowe'}
            </>
          )}
        </p>
      )}
    </article>
  );
}

function NextStepEditor({
  item,
  onCancel,
  onSave,
}: {
  item: ApplicationItem;
  onCancel: () => void;
  onSave: (label: string, due: string) => Promise<void>;
}) {
  const [label, setLabel] = useState(item.next_step?.label ?? '');
  const [due, setDue] = useState(toInputValue(item.next_step?.due));
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    await onSave(label, due);
    setBusy(false);
  };

  return (
    <form
      className="ap-editor"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onCancel();
        }
      }}
    >
      <input
        autoFocus
        className="field"
        value={label}
        placeholder="Np. rozmowa z zespołem"
        aria-label="Następny krok"
        disabled={busy}
        onChange={(e) => setLabel(e.target.value)}
      />
      <input
        type="datetime-local"
        className="field"
        value={due}
        aria-label="Termin"
        disabled={busy}
        onChange={(e) => setDue(e.target.value)}
      />
      <div className="ap-editor-actions">
        <button type="submit" className="btn btn-sm btn-primary" disabled={busy}>
          {busy ? <LoaderCircle className="spin" /> : <Check />}
          Zapisz
        </button>
        <button type="button" className="btn btn-sm" disabled={busy} onClick={onCancel}>
          Anuluj
        </button>
      </div>
    </form>
  );
}
