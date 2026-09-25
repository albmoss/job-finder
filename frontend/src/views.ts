import type { LucideIcon } from 'lucide-react';
import { Bookmark, CircleX, Layers, Link2, MountainSnow, Puzzle, Send, Sparkles, Star } from 'lucide-react';

// Klucze widoków muszą zgadzać się z nazwami zakładek backendu (WS_TAB_HINT, /api/offers?tab=).
export const OFFER_TABS = ['Dopasowane', 'Wszystkie', 'Ocenione', 'Zapisane', 'Aspiracyjne', 'Odrzucone'] as const;

export const APPLICATIONS_VIEW = 'Aplikacje';
export const GAPS_VIEW = 'Czego brakuje';
export const ADD_LINK_VIEW = 'Dodaj z linku';
export const TOOL_VIEWS = [APPLICATIONS_VIEW, GAPS_VIEW, ADD_LINK_VIEW] as const;

export function isOfferTab(view: string): boolean {
  return (OFFER_TABS as readonly string[]).includes(view);
}

// Kategorie w nawigacji to same ikony; aktywna rozwija się z nazwą.
export const VIEW_ICON: Record<string, LucideIcon> = {
  Dopasowane: Sparkles,
  Wszystkie: Layers,
  Ocenione: Star,
  Zapisane: Bookmark,
  Aspiracyjne: MountainSnow,
  Odrzucone: CircleX,
  [APPLICATIONS_VIEW]: Send,
  [GAPS_VIEW]: Puzzle,
  [ADD_LINK_VIEW]: Link2,
};
