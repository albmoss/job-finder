import { useCallback, useRef, useState } from 'react';
import { Info } from 'lucide-react';
import type { ToastOptions } from '../app_context';

interface ToastItem extends ToastOptions {
  id: number;
  message: string;
  leaving: boolean;
}

const VISIBLE_MS = 5000;
const VISIBLE_WITH_ACTION_MS = 8000;
const LEAVE_MS = 400;
const MAX_TOASTS = 3;

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), LEAVE_MS);
  }, []);

  const toast = useCallback(
    (message: string, opts: ToastOptions = {}) => {
      const id = nextId.current++;
      setToasts((list) => [...list.slice(-(MAX_TOASTS - 1)), { id, message, leaving: false, ...opts }]);
      window.setTimeout(() => dismiss(id), opts.action ? VISIBLE_WITH_ACTION_MS : VISIBLE_MS);
    },
    [dismiss],
  );

  return { toasts, toast, dismiss };
}

export function Toasts({ toasts, dismiss }: { toasts: ToastItem[]; dismiss: (id: number) => void }) {
  return (
    <>
      {toasts.map(({ id, message, action, icon: Icon = Info, leaving }) => (
        <div key={id} className={`toast panel${leaving ? ' is-leaving' : ''}`} role="status">
          <Icon />
          <span className="toast-text">{message}</span>
          {action && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                action.run();
                dismiss(id);
              }}
            >
              {action.label}
            </button>
          )}
        </div>
      ))}
    </>
  );
}
