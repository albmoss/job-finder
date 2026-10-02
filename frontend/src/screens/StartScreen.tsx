import { useEffect, useRef, useState, type DragEvent } from 'react';
import { ClipboardPaste, FileCheck, FileUser, Plus, Search, Send, Sparkles, TriangleAlert, Upload } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { CHARS, plural } from '../plural';
import { profileLine } from '../shell/LaunchModal';
import type { CVInfo, PipelinePrerequisites } from '../types';
import type { ScreenProps } from './types';
import '../styles/start.css';

const STEPS = [
  { icon: FileUser, title: 'Dodajesz CV', text: 'Miasto, umiejętności i poziom odczytamy automatycznie.' },
  { icon: Sparkles, title: 'Dostajesz dopasowane oferty', text: 'Wybór kategorii i wyszukiwanie wykonuje aplikacja.' },
  { icon: Send, title: 'Wybierasz i aplikujesz', text: 'Zapisujesz oferty, dopasowujesz CV i śledzisz wysłane aplikacje.' },
];

const ACCEPT = '.pdf,.docx,.txt';
const PREREQ_RETRY_MS = 5000;

function savedLine(cv: CVInfo): string {
  if (cv.profile?.current) return profileLine(cv.profile);
  return `CV zapisane: ${cv.chars} ${plural(cv.chars, CHARS)}.`;
}

export function StartScreen(_props: ScreenProps) {
  const { cv, refreshCv, toast } = useApp();
  const [pasting, setPasting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  const ready = Boolean(cv?.ready);

  const save = async (source: File | string) => {
    setBusy(true);
    try {
      await (typeof source === 'string' ? api.pasteCVText(source) : api.uploadCVFile(source));
      const info = await refreshCv();
      if (!info?.ready) toast('Nie udało się odczytać CV. Spróbuj innego pliku albo wklej treść.', { icon: TriangleAlert });
      else setPasting(false);
    } catch (err) {
      toast(`Nie udało się zapisać CV: ${errorMessage(err)}`, { icon: TriangleAlert });
    } finally {
      setBusy(false);
    }
  };

  const dropProps = ready
    ? {}
    : {
        onDragOver: (e: DragEvent<HTMLElement>) => {
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = 'copy';
          setDragging(true);
        },
        onDragLeave: (e: DragEvent<HTMLElement>) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
        },
        onDrop: (e: DragEvent<HTMLElement>) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file && !busy) save(file);
        },
      };

  return (
    <div className="st0">
      <div className="st0-intro">
        <h1 className="st0-heading">Zacznij od swojego CV.</h1>
        <p className="st0-lead">Odczytamy Twoje doświadczenie i znajdziemy oferty pasujące do Twojego profilu.</p>
        {STEPS.map(({ icon: Icon, title, text }) => (
          <div key={title} className="st0-step">
            <Icon />
            <div className="st0-step-copy">
              <p className="st0-step-title">{title}</p>
              <p className="st0-step-text">{text}</p>
            </div>
          </div>
        ))}
      </div>
      <section className={`st0-card panel${dragging ? ' is-dragging' : ''}`} aria-label="Dodaj swoje CV" {...dropProps}>
        {ready && cv ? (
          <ConsentStep cv={cv} />
        ) : pasting ? (
          <PasteStep busy={busy} onCancel={() => setPasting(false)} onSave={save} />
        ) : (
          <UploadStep busy={busy} onFile={save} onPaste={() => setPasting(true)} />
        )}
        <p className="st0-note">
          Po wgraniu CV i potwierdzeniu kosztu API zaczniemy szukać ofert. Na kolejnym ekranie zobaczysz postęp.
        </p>
      </section>
    </div>
  );
}

function UploadStep({ busy, onFile, onPaste }: { busy: boolean; onFile: (file: File) => void; onPaste: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="st0-body">
      <Upload className="st0-icon" />
      <h2 className="st0-title">Dodaj swoje CV</h2>
      <p className="st0-hint">Przeciągnij PDF lub wybierz plik</p>
      <button type="button" className="btn btn-primary" disabled={busy} onClick={() => inputRef.current?.click()}>
        <Plus />
        Wybierz plik PDF
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) onFile(file);
        }}
      />
      <p className="st0-or">lub</p>
      <button type="button" className="btn" disabled={busy} onClick={onPaste}>
        <ClipboardPaste />
        Wklej treść CV
      </button>
    </div>
  );
}

function PasteStep({
  busy,
  onCancel,
  onSave,
}: {
  busy: boolean;
  onCancel: () => void;
  onSave: (text: string) => void;
}) {
  const [text, setText] = useState('');
  return (
    <div className="st0-body st0-paste">
      <h2 className="st0-title">Wklej treść CV</h2>
      <textarea
        className="field st0-textarea scroll"
        value={text}
        autoFocus
        placeholder="Doświadczenie, umiejętności, wykształcenie…"
        onChange={(e) => setText(e.target.value)}
      />
      <div className="st0-actions">
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>
          Anuluj
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => onSave(text)}>
          <FileCheck />
          Zapisz CV
        </button>
      </div>
    </div>
  );
}

function ConsentStep({ cv }: { cv: CVInfo }) {
  const { startSearch, openSettings } = useApp();
  const [prereq, setPrereq] = useState<PipelinePrerequisites | null>(null);
  const [prereqError, setPrereqError] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [starting, setStarting] = useState(false);

  const prereqReady = Boolean(prereq?.ready_full) && !prereqError;

  useEffect(() => {
    if (prereqReady) return;
    let cancelled = false;
    let timer = 0;
    const load = async () => {
      try {
        const next = await api.getPipelinePrerequisites();
        if (cancelled) return;
        setPrereq(next);
        setPrereqError(null);
        if (next.ready_full) return;
      } catch (err) {
        if (cancelled) return;
        setPrereqError(errorMessage(err));
      }
      timer = window.setTimeout(load, PREREQ_RETRY_MS);
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [cv, prereqReady]);

  const issues = prereqError ? [prereqError] : (prereq?.issues ?? []);

  const start = async () => {
    setStarting(true);
    const ok = await startSearch();
    if (!ok) setStarting(false);
  };

  return (
    <div className="st0-body">
      <FileCheck className="st0-icon" />
      <h2 className="st0-title">Poszukać ofert?</h2>
      <p className="st0-hint">{savedLine(cv)}</p>
      <p className="st0-meta">Dopasowanie: Jev. Model odczytu CV: z ustawień.</p>
      {issues.length > 0 && (
        <div className="st0-issues">
          {issues.map((issue) => (
            <p key={issue} className="label">
              {issue}
            </p>
          ))}
          <button type="button" className="link-btn label" onClick={openSettings}>
            Otwórz ustawienia
          </button>
        </div>
      )}
      <label className="check st0-consent">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        Zgadzam się na przetwarzanie CV przez modele i koszt API.
      </label>
      <button type="button" className="btn btn-primary" disabled={!consent || !prereqReady || starting} onClick={start}>
        <Search />
        Szukaj ofert
      </button>
    </div>
  );
}
