import { useEffect, useRef } from 'react';

export function useDismiss(open: boolean, close: () => void, isInside: (target: Node) => boolean) {
  const latest = useRef({ close, isInside });
  latest.current = { close, isInside };

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!latest.current.isInside(event.target as Node)) latest.current.close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') latest.current.close();
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
}
