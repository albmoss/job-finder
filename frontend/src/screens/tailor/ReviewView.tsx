import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Check,
  CircleCheck,
  Download,
  FileText,
  Pencil,
  RotateCw,
  Save,
  ShieldCheck,
  TriangleAlert,
  Undo2,
  X,
} from 'lucide-react';
import { api, errorMessage } from '../../api';
import { useApp } from '../../app_context';
import { plural } from '../../plural';
import { navigate, paths } from '../../router';
import type { CvChange, CvNumber, CvVersionDetail } from '../../types';
import { CHANGES, NUMBERS_ACC, TailorHeader, downloadFile } from './common';

const PAGE_WIDTH = 794;
const PAGE_HEIGHT = 1123;
const MAX_PAPER_WIDTH = 500;

interface ReviewProps {
  version: CvVersionDetail;
  offerPath: string;
  onUpdate: (version: CvVersionDetail) => void;
  /** Podgląd gotowej wersji: bez edycji, z pobieraniem PDF. */
  readOnly?: boolean;
  onClosePreview?: () => void;
}

export function ReviewView({ version, offerPath, onUpdate, readOnly = false, onClosePreview }: ReviewProps) {
  const { toast, bumpData } = useApp();
  const [which, setWhich] = useState<'tailored' | 'base'>('tailored');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const pending = version.pending_numbers;

  const run = async (action: () => Promise<CvVersionDetail>, failure: string) => {
    if (busy) return false;
    setBusy(true);
    try {
      onUpdate(await action());
      setRevision((r) => r + 1);
      bumpData();
      return true;
    } catch (err) {
      toast(`${failure}: ${errorMessage(err)}`);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const changeAction = (idx: number, action: 'revert' | 'apply' | 'edit', text?: string) =>
    run(() => api.updateCvChange(version.id, idx, action, text), 'Nie udało się zmienić CV');
  const numberAction = (idx: number, action: 'confirm' | 'edit' | 'remove', text?: string) =>
    run(() => api.updateCvNumber(version.id, idx, action, text), 'Nie udało się zapisać liczby');

  const finalize = async () => {
    setFinalizing(true);
    try {
      const done = await api.finalizeCvVersion(version.id);
      downloadFile(api.cvVersionPdfUrl(version.id));
      onUpdate(done);
      bumpData();
    } catch (err) {
      toast(`Nie udało się zapisać PDF: ${errorMessage(err)}`);
    } finally {
      setFinalizing(false);
    }
  };

  const saveDraft = () => {
    toast('Szkic zapisany', { icon: Save });
    navigate(offerPath);
  };

  const pendingNumbers = version.numbers.filter((n) => n.state === 'pending');
  const resolvedNumbers = version.numbers.filter((n) => n.state !== 'pending');
  const hasNotes = version.missing.length > 0 || version.questions.length > 0;

  return (
    <div className="tl-screen">
      <TailorHeader
        backLabel={readOnly ? 'Wróć do oferty' : 'Wróć do ustawień'}
        onBack={() => navigate(readOnly ? offerPath : paths.tailorNew(version.link))}
        title={readOnly ? 'Gotowa wersja CV' : 'Sprawdź swoją wersję'}
        subtitle={
          <>
            {version.company} <span className="tl-sep">/</span> {version.title} <span className="tl-sep">/</span>{' '}
            {readOnly ? `Wersja ${version.version}` : `Szkic ${version.version}`}
          </>
        }
      />
      <div className="tl-review">
        <section className="panel tl-doc">
          <Paper url={`${api.cvVersionHtmlUrl(version.id, which, which === 'tailored' && !readOnly)}&r=${revision}`}>
            <div className="segmented" role="radiogroup" aria-label="Wersja podglądu">
              <button type="button" className="btn" role="radio" aria-checked={which === 'tailored'} onClick={() => setWhich('tailored')}>
                <FileText />
                Dopasowane CV
              </button>
              <button type="button" className="btn" role="radio" aria-checked={which === 'base'} onClick={() => setWhich('base')}>
                Bazowe CV
              </button>
            </div>
          </Paper>
        </section>

        <section className="panel tl-changes">
          <div className="tl-changes-head">
            <h2 className="tl-changes-title">Zmiany w CV</h2>
            <span className="tl-small">
              {version.changes.length} {plural(version.changes.length, CHANGES)}
            </span>
          </div>
          <div className="tl-list scroll">
            {version.changes.length === 0 && (
              <p className="tl-copy">Agent nie zaproponował zmian w treści. Dopasowana wersja jest taka jak bazowe CV.</p>
            )}
            {version.changes.map((change) => (
              <ChangeCard key={change.idx} change={change} readOnly={readOnly} busy={busy} onAction={changeAction} />
            ))}
            {pendingNumbers.map((n, i) => (
              <NumberBox
                key={n.idx}
                number={n}
                title={
                  pendingNumbers.length === 1
                    ? '1 liczba do potwierdzenia'
                    : `Liczba do potwierdzenia ${i + 1} z ${pendingNumbers.length}`
                }
                readOnly={readOnly}
                busy={busy}
                onAction={numberAction}
              />
            ))}
            {resolvedNumbers.map((n) => (
              <p key={n.idx} className="tl-resolved">
                <CircleCheck />
                <span>{resolvedLine(n)}</span>
              </p>
            ))}
            {hasNotes && (
              <div className="tl-notes">
                <h3 className="tl-notes-title">Braki i pytania</h3>
                {version.missing.length > 0 && (
                  <p className="tl-notes-missing">
                    Brak potwierdzenia w CV: {version.missing.join(', ')}. Nie dodaliśmy tego do CV.
                  </p>
                )}
                {version.questions.map((q, i) => (
                  <p key={i} className="tl-notes-question" title={q.why || undefined}>
                    {q.question}
                  </p>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      <footer className="glass tl-bar">
        <ShieldCheck />
        <p className="tl-bar-text">
          {pending > 0 ? `Przed pobraniem sprawdź ${pending} ${plural(pending, NUMBERS_ACC)}.` : 'Wszystkie liczby sprawdzone.'}
        </p>
        <div className="tl-spacer" />
        {readOnly ? (
          <>
            <button type="button" className="btn" onClick={onClosePreview}>
              <X />
              Zamknij podgląd
            </button>
            <a className="btn btn-primary" href={api.cvVersionPdfUrl(version.id)} download={version.file_name ?? true}>
              <Download />
              Pobierz PDF
            </a>
          </>
        ) : (
          <>
            <button type="button" className="btn" onClick={saveDraft}>
              <Save />
              Zapisz szkic
            </button>
            <button
              type="button"
              className={pending > 0 ? 'btn' : 'btn btn-primary'}
              disabled={pending > 0 || busy || finalizing}
              onClick={finalize}
            >
              <Download className={finalizing ? 'spin' : undefined} />
              Zapisz i pobierz PDF
            </button>
          </>
        )}
      </footer>
    </div>
  );
}

function resolvedLine(n: CvNumber): string {
  if (n.state === 'removed') return `Usunięta liczba: ${n.number}`;
  if (n.state === 'edited') return `Poprawiona liczba: „${n.text}”`;
  return `Potwierdzona liczba: „${n.text || n.number}”`;
}

function Paper({ url, children }: { url: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const scale = Math.min(width, MAX_PAPER_WIDTH) / PAGE_WIDTH;

  return (
    <>
      <div className="tl-doc-bar">
        {children}
        <span className="tl-zoom">{width ? `${Math.round(scale * 100)}%` : ''}</span>
      </div>
      <div ref={ref} className="tl-paper-area scroll">
        {width > 0 && (
          <div className="tl-paper" style={{ width: PAGE_WIDTH * scale, height: PAGE_HEIGHT * scale }}>
            <iframe
              src={url}
              title="Podgląd CV"
              style={{ width: PAGE_WIDTH, height: PAGE_HEIGHT, transform: `scale(${scale})` }}
            />
          </div>
        )}
      </div>
    </>
  );
}

interface ChangeProps {
  change: CvChange;
  readOnly: boolean;
  busy: boolean;
  onAction: (idx: number, action: 'revert' | 'apply' | 'edit', text?: string) => Promise<boolean>;
}

function ChangeCard({ change, readOnly, busy, onAction }: ChangeProps) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(change.after);
  const reverted = change.state === 'reverted';

  const save = async () => {
    if (await onAction(change.idx, 'edit', text)) setEditing(false);
  };

  return (
    <article className={`tl-change${reverted ? ' is-reverted' : ''}`}>
      <h3 className="tl-change-section">{change.section}</h3>
      {(change.before || reverted) && (
        <>
          <p className="tl-change-label">Było</p>
          <p className="tl-change-before">{change.before || '—'}</p>
        </>
      )}
      {!reverted && (
        <>
          <p className="tl-change-label is-after">{change.state === 'edited' ? 'Twoja wersja' : 'Propozycja'}</p>
          {editing ? (
            <textarea
              className="field tl-edit"
              value={text}
              autoFocus
              aria-label="Nowy tekst"
              onChange={(e) => setText(e.target.value)}
            />
          ) : (
            <p className="tl-change-after">{change.after || <span className="faint">Usunięte z CV</span>}</p>
          )}
        </>
      )}
      {reverted && <p className="tl-change-label">Zmiana cofnięta. W CV zostaje tekst z bazowego CV.</p>}
      {!readOnly && (
        <div className="tl-row">
          {editing ? (
            <>
              <button type="button" className="btn" disabled={busy || !text.trim()} onClick={save}>
                <Check />
                Zapisz
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setEditing(false);
                  setText(change.after);
                }}
              >
                <X />
                Anuluj
              </button>
            </>
          ) : reverted ? (
            <button type="button" className="btn" disabled={busy} onClick={() => onAction(change.idx, 'apply')}>
              <RotateCw />
              Przywróć zmianę
            </button>
          ) : (
            <>
              {change.after && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => {
                    setText(change.after);
                    setEditing(true);
                  }}
                >
                  <Pencil />
                  Edytuj
                </button>
              )}
              <button type="button" className="btn" disabled={busy} onClick={() => onAction(change.idx, 'revert')}>
                <Undo2 />
                Cofnij zmianę
              </button>
            </>
          )}
        </div>
      )}
    </article>
  );
}

interface NumberProps {
  number: CvNumber;
  title: string;
  readOnly: boolean;
  busy: boolean;
  onAction: (idx: number, action: 'confirm' | 'edit' | 'remove', text?: string) => Promise<boolean>;
}

function NumberBox({ number, title, readOnly, busy, onAction }: NumberProps) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(number.text);

  const save = async () => {
    if (await onAction(number.idx, 'edit', text)) setEditing(false);
  };

  return (
    <article className="tl-number">
      <h3 className="tl-number-title">
        <TriangleAlert />
        {title}
      </h3>
      {editing ? (
        <textarea
          className="field tl-edit"
          value={text}
          autoFocus
          aria-label="Poprawiony tekst"
          onChange={(e) => setText(e.target.value)}
        />
      ) : (
        <p className="tl-number-text">„{number.text || number.number}”</p>
      )}
      <p className="tl-number-hint">
        {number.question || 'Agent oszacował tę liczbę. Potwierdź ją tylko, jeśli jest prawdziwa, albo usuń.'}
      </p>
      {!readOnly && (
        <div className="tl-row">
          {editing ? (
            <>
              <button type="button" className="btn" disabled={busy || !text.trim()} onClick={save}>
                <Check />
                Zapisz
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setEditing(false);
                  setText(number.text);
                }}
              >
                <X />
                Anuluj
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn" disabled={busy} onClick={() => onAction(number.idx, 'confirm')}>
                <Check />
                Potwierdź
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  setText(number.text);
                  setEditing(true);
                }}
              >
                <Pencil />
                Popraw
              </button>
              <button type="button" className="btn" disabled={busy} onClick={() => onAction(number.idx, 'remove')}>
                <X />
                Usuń liczbę
              </button>
            </>
          )}
        </div>
      )}
    </article>
  );
}
