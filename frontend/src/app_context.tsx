import { createContext, useContext } from 'react';
import type { LucideIcon } from 'lucide-react';
import type { CVInfo, CvVersionSummary, OfferDetail, PipelineState, RunSummary, Stats } from './types';

export interface ToastOptions {
  /** Np. { label: 'Cofnij', run: undo }. */
  action?: { label: string; run: () => void };
  icon?: LucideIcon;
}

/** Wystarcza `OfferListItem`, `OfferDetail` albo `ApplicationItem`-podobny obiekt. */
export interface MarkSentOffer {
  link: string;
  company: string;
  title: string;
  status: string | null;
  cv?: CvVersionSummary | null;
  cv_versions?: CvVersionSummary[];
}

export interface MarkSentOptions {
  /** Po każdym zapisie: oznaczeniu, zmianie etapu, cofnięciu. */
  onChange?: (offer: OfferDetail) => void;
}

export interface AppContextValue {
  pipeline: PipelineState | null;
  /** Bieżący przebieg: pobrane, sprawdzone, dopasowane; null przed pierwszym odczytem. */
  runSummary: RunSummary | null;
  /** running albo stopping. */
  pipelineBusy: boolean;
  refreshPipeline: () => Promise<void>;
  /** POST /api/pipeline/start {mode:'full'} i przejście na #/postep; false przy błędzie (toast). */
  startSearch: () => Promise<boolean>;
  stopSearch: () => Promise<void>;

  stats: Stats | null;
  refreshStats: () => Promise<void>;

  cv: CVInfo | null;
  refreshCv: () => Promise<CVInfo | null>;

  /** Rośnie po każdej zmianie danych ofert (decyzje, koniec przebiegu); dodaj do zależności pobierania. */
  dataVersion: number;
  bumpData: () => void;

  toast: (message: string, opts?: ToastOptions) => void;
  openLaunch: () => void;
  openSettings: () => void;
  /** Zapisuje status 'apply' (z gotową wersją CV) i otwiera okno „Aplikacja oznaczona jako wysłana”. */
  openMarkSent: (offer: MarkSentOffer, opts?: MarkSentOptions) => void;
}

export const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp poza AppContext');
  return value;
}
