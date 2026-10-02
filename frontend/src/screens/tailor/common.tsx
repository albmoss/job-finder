import { useEffect, useState, type ReactNode } from 'react';
import { ArrowLeft } from 'lucide-react';
import { api } from '../../api';
import { paths } from '../../router';
import type { PluralForms } from '../../plural';

export const CHANGES: PluralForms = ['zmiana', 'zmiany', 'zmian'];
export const NUMBERS_ACC: PluralForms = ['liczbę', 'liczby', 'liczb'];

export const LANGUAGE_LABEL: Record<'pl' | 'en', string> = { pl: 'Polski', en: 'Angielski' };

/** Ekran, na którym oferta jest widoczna, zależnie od decyzji. */
export function offerPath(status: string | null | undefined, link: string): string {
  if (status === 'save') return paths.saved(link);
  if (status === 'apply') return paths.applications(link);
  return paths.matched(link);
}

/** Ścieżka powrotu do oferty; do czasu wczytania decyzji prowadzi do Dopasowanych. */
export function useOfferPath(link: string | undefined): string {
  const [status, setStatus] = useState<string | null>(null);
  useEffect(() => {
    if (!link) return;
    let alive = true;
    api
      .getOfferDetail(link)
      .then((offer) => alive && setStatus(offer.status))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [link]);
  return link ? offerPath(status, link) : paths.matched();
}

export function downloadFile(url: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

interface HeaderProps {
  backLabel: string;
  onBack: () => void;
  title: string;
  subtitle?: ReactNode;
  steps?: { label: string; current: boolean }[];
}

export function TailorHeader({ backLabel, onBack, title, subtitle, steps }: HeaderProps) {
  return (
    <header className="tl-head">
      <button type="button" className="btn" onClick={onBack}>
        <ArrowLeft />
        {backLabel}
      </button>
      <div className="tl-head-text">
        <h1 className="title-xl">{title}</h1>
        {subtitle && <p className="tl-head-sub">{subtitle}</p>}
      </div>
      {steps && (
        <ol className="tl-steps" aria-label="Etapy">
          {steps.map((step) => (
            <li key={step.label} className={step.current ? 'is-current' : undefined} aria-current={step.current ? 'step' : undefined}>
              {step.label}
            </li>
          ))}
        </ol>
      )}
    </header>
  );
}

export type Step = 'settings' | 'review' | 'ready';

export function stepsFor(current: Step) {
  return [
    { label: 'Ustawienia', current: current === 'settings' },
    { label: 'Sprawdzenie', current: current === 'review' },
    { label: 'Gotowe', current: current === 'ready' },
  ];
}
