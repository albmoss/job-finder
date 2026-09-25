import { useCallback, useEffect, useRef, useState } from 'react';

/** Rozmycie starej treści przed podmianą i wyostrzenie nowej; czasy równe z base.css. */
export const SWAP_OUT_MS = 200;
export const SWAP_SETTLE_MS = 420;
export type SwapPhase = 'idle' | 'out' | 'in' | 'settle';

/** Klasy elementu, którego treść się podmienia (`.swap-*` w base.css). */
export const SWAP_CLASS: Record<SwapPhase, string> = {
  idle: '',
  out: ' swap-out',
  in: ' swap-in',
  settle: ' swap-settle',
};

/**
 * Podmiana treści pod rozmyciem: `run(commit)` rozmywa starą treść (SWAP_OUT_MS), wywołuje
 * `commit`, trzyma rozmycie, dopóki `busy` (np. trwa pobieranie), i wyostrza (SWAP_SETTLE_MS).
 * Po skrócie klawiszowym albo z `instant` podmiana jest natychmiastowa. Jeśli `commit` zaczyna
 * pobieranie, musi od razu ustawić `busy` — inaczej wyostrzenie ruszy przed danymi.
 */
export function useBlurSwap(busy: boolean) {
  const [phase, setPhase] = useState<SwapPhase>('idle');
  const timerRef = useRef(0);

  const run = useCallback((commit: () => void, instant = false) => {
    window.clearTimeout(timerRef.current);
    if (instant || document.documentElement.dataset.input === 'keyboard') {
      setPhase('idle');
      commit();
      return;
    }
    setPhase('out');
    timerRef.current = window.setTimeout(() => {
      commit();
      setPhase('in');
    }, SWAP_OUT_MS);
  }, []);

  useEffect(() => {
    if (phase !== 'in' || busy) return;
    setPhase('settle');
    timerRef.current = window.setTimeout(() => setPhase('idle'), SWAP_SETTLE_MS);
  }, [phase, busy]);

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  return { phase, run };
}
