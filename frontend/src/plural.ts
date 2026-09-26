/** Formy liczebnika: 1 oferta · 2–4 (bez 12–14) oferty · reszta ofert (także 21, 25, 111). */
export type PluralForms = readonly [one: string, few: string, many: string];

export const OFFERS: PluralForms = ['oferta', 'oferty', 'ofert'];
export const RESULTS: PluralForms = ['wynik', 'wyniki', 'wyników'];
export const CHARS: PluralForms = ['znak', 'znaki', 'znaków'];
export const NEW_ONES: PluralForms = ['nowa', 'nowe', 'nowych'];
export const KEYS: PluralForms = ['klucz', 'klucze', 'kluczy'];
export const BACKUPS: PluralForms = ['zapasowy', 'zapasowe', 'zapasowych'];

export function plural(n: number, [one, few, many]: PluralForms): string {
  const abs = Math.abs(n);
  const rem10 = abs % 10;
  const rem100 = abs % 100;
  if (abs === 1) return one;
  if (rem10 >= 2 && rem10 <= 4 && (rem100 < 10 || rem100 >= 20)) return few;
  return many;
}
