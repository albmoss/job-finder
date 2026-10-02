import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface ModalProps {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  /** Krzyżyk w rogu; okna z własnym „Anuluj” mogą go pominąć. */
  closeButton?: boolean;
}

/** Okno w `.panel` na `.modal-backdrop`, portal do body; Esc i klik obok zamykają. */
export function Modal({ title, onClose, children, wide, closeButton = true }: ModalProps) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    return () => {
      window.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, []);

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className={`modal panel scroll${wide ? ' modal-wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1}
      >
        <h2 className="title-lg">{title}</h2>
        {closeButton && (
          <button type="button" className="btn-quiet modal-close" onClick={onClose} aria-label="Zamknij">
            <X />
          </button>
        )}
        {children}
      </div>
    </div>,
    document.body,
  );
}
