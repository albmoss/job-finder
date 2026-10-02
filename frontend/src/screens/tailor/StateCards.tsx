import { useState } from 'react';
import { ArrowLeft, CircleAlert, Download, FileCheck, FileText, RotateCw, Sparkles, X } from 'lucide-react';
import { api, errorMessage } from '../../api';
import { useApp } from '../../app_context';
import { navigate, paths } from '../../router';
import type { CvVersionDetail } from '../../types';
import { LANGUAGE_LABEL } from './common';

const PHASE_COPY: Record<string, [string, string]> = {
  base_cv: ['Odczytujemy bazowe CV…', 'Przygotowujemy strukturę CV. Robimy to tylko raz dla każdego bazowego CV.'],
  tailoring: ['Dopasowujemy treść do wymagań oferty…', 'Analiza oferty zakończona. Trwa przygotowanie zmian.'],
};

interface CardProps {
  version: CvVersionDetail;
  onUpdate: (version: CvVersionDetail) => void;
}

export function RunningCard({ version, onUpdate }: CardProps) {
  const { toast, bumpData } = useApp();
  const [busy, setBusy] = useState(false);
  const [stage, work] = PHASE_COPY[version.phase ?? ''] ?? PHASE_COPY.base_cv;

  const cancel = async () => {
    setBusy(true);
    try {
      await api.cancelCvVersion(version.id);
      onUpdate(await api.getCvVersion(version.id));
      bumpData();
    } catch (err) {
      toast(`Nie udało się przerwać: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel tl-card" aria-live="polite">
      <Sparkles className="tl-card-icon" />
      <h2 className="title-lg">Tworzymy CV dla {version.company}</h2>
      <p className="tl-card-line">{stage}</p>
      <p className="tl-card-line">{work}</p>
      <p className="tl-card-line">Bazowe CV pozostaje bez zmian.</p>
      <div className="tl-card-actions">
        <button type="button" className="btn" disabled={busy} onClick={cancel}>
          <X />
          Przerwij
        </button>
      </div>
    </section>
  );
}

export function FailedCard({ version, onUpdate }: CardProps) {
  const { bumpData } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.retryCvVersion(version.id);
      onUpdate(await api.getCvVersion(version.id));
      bumpData();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel tl-card">
      <CircleAlert className="tl-card-icon warn" />
      <h2 className="title-lg">Nie udało się utworzyć wersji</h2>
      {version.error && <p className="tl-card-line tl-card-error">{version.error}</p>}
      <p className="tl-card-line">Dodatkowe fakty i ustawienia są zachowane. Twoje bazowe CV jest bez zmian.</p>
      <div className="tl-card-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={retry}>
          <RotateCw className={busy ? 'spin' : undefined} />
          Spróbuj ponownie
        </button>
        <button type="button" className="btn" onClick={() => navigate(paths.tailorNew(version.link))}>
          <ArrowLeft />
          Wróć do ustawień
        </button>
      </div>
      {error && (
        <p className="tl-error" role="alert">
          <CircleAlert />
          <span>{error}</span>
        </p>
      )}
    </section>
  );
}

interface ReadyProps {
  version: CvVersionDetail;
  offerPath: string;
  previewOpen: boolean;
  onTogglePreview: () => void;
}

export function ReadyCard({ version, offerPath, previewOpen, onTogglePreview }: ReadyProps) {
  return (
    <section className="panel tl-card">
      <FileCheck className="tl-card-icon" />
      <h2 className="title-lg">CV dla {version.company} jest gotowe</h2>
      <p className="tl-card-line">
        Wersja {version.version} / {LANGUAGE_LABEL[version.language]} / Sprawdzone liczby
      </p>
      <div className="tl-card-actions">
        <a className="btn btn-primary" href={api.cvVersionPdfUrl(version.id)} download={version.file_name ?? true}>
          <Download />
          Pobierz PDF
        </a>
        <button type="button" className="btn" onClick={() => navigate(offerPath)}>
          <ArrowLeft />
          Wróć do oferty
        </button>
      </div>
      {version.file_name && <p className="tl-card-note">{version.file_name}</p>}
      <p className="tl-card-note">Pobranie CV nie oznacza wysłania aplikacji.</p>
      <button type="button" className="btn-quiet tl-card-quiet" aria-expanded={previewOpen} onClick={onTogglePreview}>
        <FileText />
        {previewOpen ? 'Ukryj podgląd' : 'Podgląd'}
      </button>
    </section>
  );
}
