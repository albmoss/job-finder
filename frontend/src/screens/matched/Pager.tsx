import { useState } from 'react';
import type { KeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface PagerProps {
  page: number;
  totalPages: number;
  onPage: (page: number) => void;
}

type Slot = number | 'gap-left' | 'gap-right';

function pageSlots(page: number, total: number): Slot[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  if (page <= 4) return [1, 2, 3, 4, 5, 'gap-right', total];
  if (page >= total - 3) return [1, 'gap-left', total - 4, total - 3, total - 2, total - 1, total];
  return [1, 'gap-left', page - 1, page, page + 1, 'gap-right', total];
}

export function Pager({ page, totalPages, onPage }: PagerProps) {
  const [jumpAt, setJumpAt] = useState<Slot | null>(null);
  const [draft, setDraft] = useState('');

  const openJump = (slot: Slot) => {
    setDraft('');
    setJumpAt(slot);
  };

  const commit = () => {
    const target = Number.parseInt(draft, 10);
    setJumpAt(null);
    if (Number.isFinite(target)) onPage(Math.min(Math.max(1, target), totalPages));
  };

  const onJumpKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') commit();
    if (event.key === 'Escape') setJumpAt(null);
  };

  return (
    <nav className="mo-pager" aria-label="Strony ofert">
      <button
        type="button"
        className="btn btn-sm btn-icon"
        aria-label="Poprzednia strona"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        <ChevronLeft />
      </button>
      <div className="mo-pages">
        {pageSlots(page, totalPages).map((slot) =>
          typeof slot === 'number' ? (
            <button
              key={slot}
              type="button"
              className={`mo-page${slot === page ? ' is-current' : ''}`}
              aria-current={slot === page ? 'page' : undefined}
              onClick={() => onPage(slot)}
            >
              {slot}
            </button>
          ) : jumpAt === slot ? (
            <input
              key={slot}
              className="mo-page-jump"
              inputMode="numeric"
              autoFocus
              value={draft}
              placeholder="…"
              aria-label={`Przejdź do strony (1–${totalPages})`}
              onChange={(event) => setDraft(event.target.value.replace(/\D/g, ''))}
              onKeyDown={onJumpKey}
              onBlur={() => setJumpAt(null)}
            />
          ) : (
            <button
              key={slot}
              type="button"
              className="mo-page mo-page-gap"
              aria-label="Przejdź do strony"
              onClick={() => openJump(slot)}
            >
              …
            </button>
          ),
        )}
      </div>
      <button
        type="button"
        className="btn btn-sm btn-icon"
        aria-label="Następna strona"
        disabled={page >= totalPages}
        onClick={() => onPage(page + 1)}
      >
        <ChevronRight />
      </button>
    </nav>
  );
}
