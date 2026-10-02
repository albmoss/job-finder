import { useEffect, useState } from 'react';
import { ArrowLeft, Check, RotateCcw, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { navigate, paths } from '../router';
import { Modal } from '../shell/Modal';
import type { TailorInstructions } from '../types';
import type { ScreenProps } from './types';
import '../styles/instructions.css';

export function InstructionsScreen(_props: ScreenProps) {
  const { toast } = useApp();
  const [saved, setSaved] = useState<TailorInstructions | null>(null);
  const [draft, setDraft] = useState('');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [leaveTarget, setLeaveTarget] = useState<string | null>(null);

  const dirty = saved !== null && draft !== saved.text;

  useEffect(() => {
    let alive = true;
    api
      .getTailorInstructions()
      .then((res) => {
        if (!alive) return;
        setSaved(res);
        setDraft(res.text);
      })
      .catch((err: unknown) => {
        if (alive) setLoadError(errorMessage(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  useEffect(() => {
    if (!dirty) return;
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const anchor = (e.target as Element | null)?.closest?.('a[href^="#/"]');
      if (!anchor) return;
      e.preventDefault();
      setLeaveTarget(anchor.getAttribute('href')!.slice(1));
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [dirty]);

  const apply = async (action: () => Promise<TailorInstructions>, success: string, failure: string) => {
    setBusy(true);
    try {
      const res = await action();
      setSaved(res);
      setDraft(res.text);
      toast(success);
    } catch (err) {
      toast(`${failure}: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const save = () => apply(() => api.saveTailorInstructions(draft), 'Zapisano instrukcje agenta.', 'Nie udało się zapisać instrukcji');

  const reset = () => {
    setConfirmReset(false);
    apply(api.resetTailorInstructions, 'Przywrócono domyślną instrukcję.', 'Nie udało się przywrócić instrukcji');
  };

  const status = loadError
    ? `Nie udało się wczytać instrukcji: ${loadError}`
    : !saved
      ? 'Wczytywanie…'
      : dirty
        ? 'Niezapisane zmiany'
        : saved.is_default
          ? 'Wczytano instrukcję agenta dopasowania CV.'
          : 'Własna wersja instrukcji.';

  return (
    <div className="in-screen">
      <aside className="in-intro">
        <button
          type="button"
          className="btn in-back"
          onClick={() => (dirty ? setLeaveTarget(paths.cv) : navigate(paths.cv))}
        >
          <ArrowLeft />
          Wróć do Mojego CV
        </button>
        <h1 className="in-title">Instrukcje agenta</h1>
        <p className="in-lead">
          Stałe zasady tworzenia wersji CV pod oferty. Zapisujesz je raz, a agent używa ich przy kolejnych
          dopasowaniach.
        </p>
        <p className="in-text">Informacje o konkretnym doświadczeniu dopisz przy ofercie w polu „Dodatkowe fakty”.</p>
        <div className="in-spacer" />
        <ShieldCheck className="in-shield" />
        <p className="in-text">Zmiana instrukcji nie wyłącza sprawdzania liczb przed eksportem CV.</p>
      </aside>

      <section className="panel in-editor-panel">
        <div className="in-head">
          <h2 className="in-panel-title">Instrukcja systemowa</h2>
          <span className="label">{saved?.file_name ?? ''}</span>
        </div>
        <p className={`in-status${dirty || loadError ? ' is-alert' : ''}`}>{status}</p>
        <textarea
          className="in-editor scroll"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!saved || busy}
          spellCheck={false}
          aria-label="Instrukcja systemowa"
        />
        <div className="in-actions">
          {confirmReset ? (
            <div className="in-confirm">
              <span className="in-confirm-text">Przywrócić domyślną instrukcję? Twoje zmiany zostaną usunięte.</span>
              <button type="button" className="btn" onClick={() => setConfirmReset(false)}>
                Anuluj
              </button>
              <button type="button" className="btn" onClick={reset}>
                <RotateCcw />
                Przywróć
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn"
              disabled={!saved || busy || (saved.is_default && !dirty)}
              onClick={() => setConfirmReset(true)}
            >
              <RotateCcw />
              Przywróć domyślne
            </button>
          )}
          <button type="button" className="btn btn-primary" disabled={!dirty || busy} onClick={save}>
            <Check />
            Zapisz instrukcje
          </button>
        </div>
      </section>

      {leaveTarget !== null && (
        <Modal title="Niezapisane zmiany" onClose={() => setLeaveTarget(null)}>
          <p className="muted">Zmiany w instrukcji agenta nie zostały zapisane. Wyjść bez zapisywania?</p>
          <div className="modal-actions">
            <button type="button" className="btn" onClick={() => setLeaveTarget(null)}>
              Zostań
            </button>
            <button type="button" className="btn btn-primary" onClick={() => navigate(leaveTarget)}>
              Wyjdź bez zapisywania
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
