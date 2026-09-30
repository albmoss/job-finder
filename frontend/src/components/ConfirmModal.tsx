import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  onConfirm(): void;
  onCancel(): void;
}

/**
 * Okno potwierdzenia nieodwracalnej akcji. Renderowane do `body`, bo panel z `backdrop-filter`
 * byłby układem odniesienia dla `position: fixed`. Fokus startuje na „Anuluj”, Esc anuluje.
 */
export const ConfirmModal: React.FC<ConfirmModalProps> = ({
  isOpen,
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
}) => {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onCancel]);

  // Wyjście tą samą drogą co wejście: po `isOpen=false` okno zostaje na 180 ms animacji.
  const [present, setPresent] = useState(isOpen);
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    if (isOpen) {
      setPresent(true);
      setClosing(false);
      return;
    }
    setClosing(true);
    const timer = window.setTimeout(() => {
      setPresent(false);
      setClosing(false);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [isOpen]);

  // Fokus na „Anuluj” dopiero, gdy okno jest w DOM; po zamknięciu wraca tam, skąd przyszedł.
  useEffect(() => {
    if (!isOpen || !present) return;
    const returnTo = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => returnTo?.focus();
  }, [isOpen, present]);

  if (!present) return null;

  return createPortal(
    <div
      className={`modal-backdrop${closing ? ' is-closing' : ''}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
    >
      <div className={`modal confirm-modal${closing ? ' is-closing' : ''}`}>
        <div className="modal-head">
          <h2 id="confirm-title">{title}</h2>
          <button type="button" className="iconbtn press" onClick={onCancel} aria-label="Anuluj (Esc)">
            <X />
          </button>
        </div>
        <div className="confirm-body">{children}</div>
        <div className="confirm-foot">
          <button ref={cancelRef} type="button" className="btn btn-secondary press" onClick={onCancel}>
            Anuluj
          </button>
          <button type="button" className="btn btn-primary press" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
