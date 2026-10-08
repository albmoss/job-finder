import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Check, ChevronDown, PenLine, Settings2, Trash2, TriangleAlert, UserPlus } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong, parseDate } from '../format';
import type { Candidate } from '../types';
import { SENIORITY_LABEL } from './LaunchModal';
import { Modal } from './Modal';

const BUSY_HINT = 'Nie można zmieniać kandydata w trakcie wyszukiwania.';

type Dialog = { kind: 'rename' } | { kind: 'delete'; candidate: Candidate };

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : words[0].slice(0, 2);
  return letters.toUpperCase();
}

function cvLine(candidate: Candidate): string {
  if (!candidate.has_cv) return 'Bez CV';
  const day = parseDate(candidate.cv_updated_at);
  if (!day) return 'CV dodane';
  return `CV z ${formatDayLong(day, day.getFullYear() !== new Date().getFullYear())}`;
}

function profileLine(candidate: Candidate): string {
  if (!candidate.has_cv) return 'Bez CV';
  const raw = candidate.seniority?.trim();
  const seniority = raw ? (SENIORITY_LABEL[raw.toLowerCase()] ?? raw.charAt(0).toUpperCase() + raw.slice(1)) : null;
  const parts = [seniority, candidate.city?.trim()].filter(Boolean);
  return parts.length ? parts.join(' · ') : cvLine(candidate);
}

export function CandidateMenu() {
  const { candidates, applyCandidates, pipelineBusy, toast, openSettings } = useApp();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [creating, setCreating] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!anchorRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const active = candidates?.items.find((c) => c.id === candidates.active);
  if (!candidates || !active) return null;
  const others = candidates.items.filter((c) => c.id !== active.id);
  const busyHint = pipelineBusy ? BUSY_HINT : undefined;

  const openDialog = (next: Dialog) => {
    setOpen(false);
    setDialog(next);
  };

  const activate = async (candidate: Candidate) => {
    setOpen(false);
    try {
      await applyCandidates(await api.activateCandidate(candidate.id));
      toast(`Przełączono na ${candidate.name}.`);
    } catch (err) {
      toast(`Nie udało się przełączyć: ${errorMessage(err)}`, { icon: TriangleAlert });
    }
  };

  const create = async () => {
    setOpen(false);
    setCreating(true);
    try {
      await applyCandidates(await api.createCandidate());
      toast('Dodano kandydata. Nazwą stanie się imię i nazwisko z CV.', { icon: UserPlus });
    } catch (err) {
      toast(`Nie udało się dodać kandydata: ${errorMessage(err)}`, { icon: TriangleAlert });
    } finally {
      setCreating(false);
    }
  };

  const rename = async (name: string) => {
    if (name === active.name) return true;
    try {
      await applyCandidates(await api.renameCandidate(active.id, name));
      return true;
    } catch (err) {
      toast(`Nie udało się zmienić nazwy: ${errorMessage(err)}`, { icon: TriangleAlert });
      return false;
    }
  };

  const remove = async (candidate: Candidate) => {
    try {
      await applyCandidates(await api.deleteCandidate(candidate.id));
      toast(`Usunięto kandydata „${candidate.name}”.`);
      return true;
    } catch (err) {
      toast(`Nie udało się usunąć: ${errorMessage(err)}`, { icon: TriangleAlert });
      return false;
    }
  };

  return (
    <>
      <div className="cd-anchor" ref={anchorRef}>
        <button
          type="button"
          className="cd-chip"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`Kandydat: ${active.name}`}
          title={active.name}
          onClick={() => setOpen((o) => !o)}
        >
          <span className="cd-avatar cd-avatar-chip" aria-hidden="true">
            {initials(active.name)}
          </span>
          <span className="cd-chip-text">
            <span className="cd-chip-name">{active.name}</span>
            <span className="cd-chip-meta">{profileLine(active)}</span>
          </span>
          <ChevronDown className="cd-chip-caret" aria-hidden="true" />
        </button>
        {open && (
          <div className="menu panel cd-menu" role="menu" aria-label="Kandydaci">
            <div className="cd-head">
              <span className="cd-avatar cd-avatar-lg" aria-hidden="true">
                {initials(active.name)}
              </span>
              <span className="cd-text">
                <span className="cd-name cd-name-strong">{active.name}</span>
                <span className="cd-meta">{profileLine(active)}</span>
              </span>
            </div>
            {others.length > 0 && <div className="divider cd-divider" />}
            {others.map((candidate) => (
              <div key={candidate.id} className="cd-row" title={busyHint}>
                <button
                  type="button"
                  role="menuitem"
                  className="menu-item cd-switch"
                  disabled={pipelineBusy}
                  onClick={() => activate(candidate)}
                >
                  <span className="cd-avatar" aria-hidden="true">
                    {initials(candidate.name)}
                  </span>
                  <span className="cd-text">
                    <span className="cd-name">{candidate.name}</span>
                    <span className="cd-meta">{cvLine(candidate)}</span>
                  </span>
                </button>
                <button
                  type="button"
                  className="btn-quiet cd-delete"
                  aria-label={`Usuń kandydata ${candidate.name}`}
                  title={pipelineBusy ? undefined : 'Usuń kandydata'}
                  disabled={pipelineBusy}
                  onClick={() => openDialog({ kind: 'delete', candidate })}
                >
                  <Trash2 />
                </button>
              </div>
            ))}
            <div className="divider cd-divider" />
            <div className="cd-action" title={busyHint}>
              <button
                type="button"
                role="menuitem"
                className="menu-item"
                disabled={pipelineBusy || creating}
                onClick={create}
              >
                <UserPlus />
                Nowy kandydat
              </button>
            </div>
            <button type="button" role="menuitem" className="menu-item" onClick={() => openDialog({ kind: 'rename' })}>
              <PenLine />
              Zmień nazwę
            </button>
            <div className="divider cd-divider" />
            <button
              type="button"
              role="menuitem"
              className="menu-item"
              onClick={() => {
                setOpen(false);
                openSettings();
              }}
            >
              <Settings2 />
              Ustawienia
            </button>
          </div>
        )}
      </div>
      {dialog?.kind === 'rename' && (
        <NameDialog
          initial={active.name}
          onSubmit={rename}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteDialog
          candidate={dialog.candidate}
          busyHint={busyHint}
          onSubmit={remove}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}

function DeleteDialog({
  candidate,
  busyHint,
  onSubmit,
  onClose,
}: {
  candidate: Candidate;
  busyHint: string | undefined;
  onSubmit: (candidate: Candidate) => Promise<boolean>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    if (await onSubmit(candidate)) onClose();
    else setBusy(false);
  };

  return (
    <Modal title="Usunąć kandydata?" onClose={onClose} closeButton={false}>
      <p className="muted">
        Usuniesz na stałe kandydata <span className="cd-ink">{candidate.name}</span>: jego CV, oceny ofert, Zapisane,
        Aplikacje i wersje CV. Pobrane oferty zostają.
      </p>
      <div className="modal-actions modal-actions-end">
        <button type="button" className="btn" onClick={onClose}>
          Anuluj
        </button>
        <span title={busyHint}>
          <button type="button" className="btn btn-primary" disabled={Boolean(busyHint) || busy} onClick={confirm}>
            <Trash2 />
            Usuń
          </button>
        </span>
      </div>
    </Modal>
  );
}

function NameDialog({
  initial,
  onSubmit,
  onClose,
}: {
  initial: string;
  onSubmit: (name: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const trimmed = name.trim();
  const blocked = busy || !trimmed || trimmed === initial;

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (blocked) return;
    setBusy(true);
    if (await onSubmit(trimmed)) onClose();
    else setBusy(false);
  };

  return (
    <Modal title="Zmień nazwę" onClose={onClose} closeButton={false}>
      <form className="cd-form" onSubmit={submit}>
        <input
          ref={inputRef}
          className="field"
          value={name}
          placeholder="Nazwa kandydata"
          maxLength={60}
          aria-label="Nazwa kandydata"
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
        <div className="modal-actions modal-actions-end">
          <button type="button" className="btn" onClick={onClose}>
            Anuluj
          </button>
          <button type="submit" className="btn btn-primary" disabled={blocked}>
            <Check />
            Zapisz
          </button>
        </div>
      </form>
    </Modal>
  );
}
