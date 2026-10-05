import { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import type { CVProfile, PipelinePrerequisites } from '../types';
import { Modal } from './Modal';

const SENIORITY_LABEL: Record<string, string> = {
  intern: 'Stażysta',
  junior: 'Junior',
  mid: 'Mid',
  senior: 'Senior',
  lead: 'Lead',
  manager: 'Manager',
};

/** „CV gotowe. Profil: Frontend Developer · Warszawa, Junior, praca zdalna.” */
export function profileLine(profile: CVProfile | null | undefined): string {
  if (!profile) return 'CV gotowe. Profil zostanie odczytany z CV na starcie.';
  const seniority = profile.seniority ? (SENIORITY_LABEL[profile.seniority.toLowerCase()] ?? profile.seniority) : null;
  const details = [profile.city, seniority, profile.remote ? 'praca zdalna' : null].filter(Boolean).join(', ');
  const summary = [profile.roles[0]?.trim(), details].filter(Boolean).join(' · ');
  return summary ? `CV gotowe. Profil: ${summary}.` : 'CV gotowe.';
}

export function LaunchModal({ onClose }: { onClose: () => void }) {
  const { cv, refreshCv, startSearch, openSettings } = useApp();
  const [prereq, setPrereq] = useState<PipelinePrerequisites | null>(null);
  const [prereqError, setPrereqError] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    refreshCv();
    api
      .getPipelinePrerequisites()
      .then(setPrereq)
      .catch((err: unknown) => setPrereqError(errorMessage(err)));
  }, [refreshCv]);

  const issues = prereqError ? [prereqError] : (prereq?.issues ?? []);
  const ready = Boolean(prereq?.ready_full) && !prereqError;

  const start = async () => {
    setStarting(true);
    const ok = await startSearch();
    setStarting(false);
    if (ok) onClose();
  };

  return (
    <Modal title="Poszukać nowych ofert?" onClose={onClose} closeButton={false}>
      <p className="muted">{cv?.ready ? profileLine(cv.profile) : 'Brak CV. Dodaj je w ustawieniach.'}</p>
      <p className="muted">Dopasowanie: Jev. Model odczytu CV: z ustawień.</p>
      {issues.length > 0 && (
        <div className="lm-issues">
          {issues.map((issue) => (
            <p key={issue} className="label">
              {issue}
            </p>
          ))}
          <button
            type="button"
            className="link-btn label"
            onClick={() => {
              onClose();
              openSettings();
            }}
          >
            Otwórz ustawienia
          </button>
        </div>
      )}
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        Zgadzam się na przetwarzanie CV przez modele i koszt API.
      </label>
      <p className="label">Zgoda jest odznaczona przy każdym uruchomieniu.</p>
      <div className="modal-actions modal-actions-end">
        <button type="button" className="btn" onClick={onClose}>
          Anuluj
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!consent || !ready || starting}
          onClick={start}
        >
          <Search />
          Szukaj ofert
        </button>
      </div>
    </Modal>
  );
}
