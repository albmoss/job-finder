import React, { useEffect, useRef, useState } from 'react';
import { Bookmark, Send, MountainSnow, X, Check } from 'lucide-react';
import '../styles/offers.css';

interface DecisionDockProps {
  status: string | null;
  /** 0 = oferta bez oceny: żaden poziom nie świeci. */
  currentRating: number;
  initialRating: number;
  onDecide: (status: string) => void;
  onRatingChange: (newRating: number) => void;
  onConfirmRating: () => void;
}

const BAR_HEIGHTS = [14, 16, 18, 20, 22, 24, 26, 28, 30, 32];

export const DecisionDock: React.FC<DecisionDockProps> = ({
  status,
  currentRating,
  initialRating,
  onDecide,
  onRatingChange,
  onConfirmRating,
}) => {
  // Hover tylko podpowiada: poziomy, które by się zmieniły, dostają wygaszony kolor;
  // wartość i zapalone poziomy zostają, dopóki nie klikniesz.
  const [hoverRating, setHoverRating] = useState<number | null>(null);
  const [showSavedNum, setShowSavedNum] = useState(false);
  const segsRef = useRef<HTMLDivElement>(null);
  const isConfirmDisabled = currentRating === initialRating;

  // Poziom z pozycji kursora na całej szerokości i wysokości miernika: granica wypada
  // w połowie odstępu między słupkami, więc nie ma martwych miejsc ani nad krótkimi słupkami.
  const ratingAt = (clientX: number) => {
    const bars = segsRef.current?.children;
    if (!bars) return null;
    for (let i = 0; i < bars.length - 1; i++) {
      const a = bars[i].getBoundingClientRect();
      const b = bars[i + 1].getBoundingClientRect();
      if (clientX < (a.right + b.left) / 2) return i + 1;
    }
    return bars.length;
  };

  const handleConfirm = () => {
    if (isConfirmDisabled) return;
    onConfirmRating();
    setShowSavedNum(true);
  };

  useEffect(() => {
    if (!showSavedNum) return;
    const timer = setTimeout(() => setShowSavedNum(false), 600);
    return () => clearTimeout(timer);
  }, [showSavedNum]);

  const step = (delta: number) => {
    if (!currentRating && delta < 0) return;
    const next = Math.min(10, Math.max(1, currentRating + delta));
    if (next !== currentRating) onRatingChange(next);
  };

  return (
    <div className="od-dock" role="toolbar" aria-label="Decyzja i ocena oferty">
      <button
        type="button"
        className={`od-dock-btn press ${status === 'save' ? 'is-active' : ''}`}
        data-tip="Zapisz"
        data-kbd="Z"
        aria-label="Zapisz (Z)"
        aria-pressed={status === 'save'}
        onClick={() => onDecide('save')}
      >
        <Bookmark aria-hidden="true" />
      </button>

      <button
        type="button"
        className={`od-dock-btn press ${status === 'apply' ? 'is-active' : ''}`}
        data-tip="Wysłane"
        data-kbd="W"
        aria-label="Wysłane (W)"
        aria-pressed={status === 'apply'}
        onClick={() => onDecide('apply')}
      >
        <Send aria-hidden="true" />
      </button>

      <button
        type="button"
        className={`od-dock-btn press ${status === 'aspirational' ? 'is-active' : ''}`}
        data-tip="Aspiruję"
        data-kbd="A"
        aria-label="Aspiruję (A)"
        aria-pressed={status === 'aspirational'}
        onClick={() => onDecide('aspirational')}
      >
        <MountainSnow aria-hidden="true" />
      </button>

      <span className="od-dock-divider" aria-hidden="true" />

      <div className="od-rating">
        <div
          ref={segsRef}
          className="od-segs"
          role="slider"
          tabIndex={0}
          aria-label="Ocena oferty"
          aria-valuemin={1}
          aria-valuemax={10}
          aria-valuenow={currentRating || undefined}
          aria-valuetext={currentRating ? `${currentRating} na 10` : 'brak oceny'}
          onPointerMove={(e) => {
            if (e.pointerType === 'mouse') setHoverRating(ratingAt(e.clientX));
          }}
          onPointerLeave={() => setHoverRating(null)}
          onClick={(e) => {
            const num = ratingAt(e.clientX);
            if (num !== null) onRatingChange(num);
          }}
          onKeyDown={(e) => {
            // Strzałki góra/dół przełączają oferty (App.tsx) — tu tylko lewo/prawo.
            if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
              e.preventDefault();
              e.stopPropagation();
              step(e.key === 'ArrowRight' ? 1 : -1);
            }
          }}
        >
          {BAR_HEIGHTS.map((h, idx) => {
            const num = idx + 1;
            const on = num <= currentRating;
            const inHover = hoverRating !== null && num <= hoverRating;
            const state = hoverRating === null ? (on ? ' is-on' : '') : on && inHover ? ' is-on' : on !== inHover ? ' is-hint' : '';
            return <span key={num} className={`od-seg${state}`} style={{ height: `${h}px` }} />;
          })}
        </div>
        <span className="od-rating-val" aria-hidden="true">{currentRating}</span>
      </div>

      <button
        type="button"
        className="od-confirm press"
        disabled={isConfirmDisabled}
        data-tip="Zapisz ocenę"
        data-kbd="Enter"
        aria-label="Zapisz ocenę (Enter)"
        onClick={handleConfirm}
      >
        <span className="od-confirm-well">
          {showSavedNum ? (
            <span className="od-confirm-num">{currentRating}</span>
          ) : (
            <Check aria-hidden="true" />
          )}
        </span>
      </button>

      <span className="od-dock-divider" aria-hidden="true" />

      <button
        type="button"
        className={`od-dock-btn is-reject press ${status === 'reject' ? 'is-active' : ''}`}
        data-tip="Odrzuć"
        data-kbd="X"
        aria-label="Odrzuć (X)"
        aria-pressed={status === 'reject'}
        onClick={() => onDecide('reject')}
      >
        <X aria-hidden="true" />
      </button>
    </div>
  );
};
