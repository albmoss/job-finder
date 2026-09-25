import React from 'react';
import { Stream, seedFromLink, type StreamProps } from './ui/Stream';
import '../styles/offers.css';

interface MatchCardProps {
  link: string;
  matchPercentage: number | null;
  /** Szkic z „Dodaj z linku” — oferty jeszcze nie ma w bazie. */
  preview?: boolean;
  /** Bez samoczynnych fal (pipeline pracuje — ruch jest tylko w jego pasku). */
  still?: boolean;
}

/** Ruch odcisku według węzła „Shape” karty dopasowania w jobfinder.pen (wave_stream.glsl:
 *  u_period 5, u_running 0.59, u_depth 0.25, u_width 22) — wolno i cicho, ale bez rytmu:
 *  pojedyncze fale co 3,5–11 s (decyzja użytkownika). Laboratorium kształtu używa tych samych wartości. */
export const MATCH_STREAM_MOTION: Pick<StreamProps, 'loop' | 'depth' | 'width' | 'edge'> = {
  loop: { period: 5, running: 0.59, gap: [3.5, 11] },
  depth: 0.25,
  width: 22,
  edge: 1,
};

export const MatchCard: React.FC<MatchCardProps> = ({ link, matchPercentage, preview = false, still = false }) => {
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

      {isPending ? (
        <div className="od-match-pending">
          <span className="od-match-pending-title">Czeka na ocenę AI</span>
          <span className="od-match-pending-text">
            {preview && 'Po zapisie oferta trafia do kolejki. '}
            Dopasowanie, fakty i „W skrócie” policzy najbliższy przebieg pipeline’u — do tego czasu
            możesz ją już zapisać albo odrzucić.
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
