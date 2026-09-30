import React from 'react';
import type { OfferDetail } from '../types';
import { Stream, seedFromLink, type StreamProps } from './ui/Stream';
import '../styles/offers.css';

interface MatchCardProps {
  link: string;
  matchPercentage: number | null;
  /** Powód odrzucenia przez przesiew; wtedy oferta jest oceniona, tylko bez procentu. */
  filtered?: OfferDetail['filtered'];
  /** Szkic z „Dodaj z linku” — oferty jeszcze nie ma w bazie. */
  preview?: boolean;
  /** Bez samoczynnych fal (pipeline pracuje — ruch jest tylko w jego pasku). */
  still?: boolean;
}

// Reguły z matching/prefilter.py i matching/run.py (MIN_DESCRIPTION) opisane dla człowieka.
const FILTER_REASONS: Record<NonNullable<OfferDetail['filtered']>, string> = {
  miasto: 'Oferta jest w innym mieście niż to z CV i nie daje pracy zdalnej.',
  poziom: 'Oferta szuka kogoś o ponad jeden poziom wyżej, niż wynika z CV.',
  lata: 'Oferta wymaga o ponad dwa lata więcej doświadczenia, niż podaje CV.',
  jezyk: 'Oferta wymaga języka, którego nie ma w CV.',
  brak_opisu: 'Ogłoszenie ma za krótki opis i nie wymienia umiejętności, więc nie było czego ocenić.',
};

/** Ruch odcisku: wolno i cicho, ale bez rytmu — pojedyncze fale co 3,5–11 s. */
export const MATCH_STREAM_MOTION: Pick<StreamProps, 'loop' | 'depth' | 'width' | 'edge'> = {
  loop: { period: 5, running: 0.59, gap: [3.5, 11] },
  depth: 0.25,
  width: 22,
  edge: 1,
};

export const MatchCard: React.FC<MatchCardProps> = ({ link, matchPercentage, filtered = null, preview = false, still = false }) => {
  const isPending = matchPercentage === null || matchPercentage === undefined;

  return (
    <div className="od-match" aria-label="Ocena dopasowania oferty do CV">
      <div className="od-match-score">
        <span className={`od-match-val ${isPending ? 'is-pending' : ''}`}>
          {isPending ? (
            '—'
          ) : (
            <>
              {matchPercentage}
              <small className="od-match-pct">%</small>
            </>
          )}
        </span>
        <span className="od-match-caption">dopasowania do CV</span>
      </div>

      {isPending && filtered ? (
        <div className="od-match-pending">
          <span className="od-match-pending-title">Odrzucona przez przesiew</span>
          <span className="od-match-pending-text">
            {FILTER_REASONS[filtered] ?? 'Oferta nie spełnia twardych wymagań z CV.'} Jev takich ofert nie ocenia.
          </span>
        </div>
      ) : isPending ? (
        <div className="od-match-pending">
          <span className="od-match-pending-title">Czeka na ocenę</span>
          <span className="od-match-pending-text">
            {preview && 'Po zapisie oferta trafia do bazy. '}
            Najbliższy przebieg pipeline’u sprawdzi ją przesiewem i oceni dopasowanie do CV. Do tego
            czasu możesz ją zapisać albo odrzucić.
          </span>
        </div>
      ) : (
        // Kształt jest odciskiem oferty (ziarno z linku), kolor sięga dokładnie do wyniku:
        // 90% to prawie cały strumień w kolorze, 50% — połowa, reszta zostaje szara.
        <Stream
          seed={seedFromLink(link)}
          progress={Math.max(0, Math.min(100, matchPercentage)) / 100}
          {...MATCH_STREAM_MOTION}
          loop={still ? undefined : MATCH_STREAM_MOTION.loop}
          className="od-match-stream"
        />
      )}
    </div>
  );
};
