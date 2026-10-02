const MONTHS_GENITIVE = [
  'stycznia',
  'lutego',
  'marca',
  'kwietnia',
  'maja',
  'czerwca',
  'lipca',
  'sierpnia',
  'września',
  'października',
  'listopada',
  'grudnia',
];

export const countFormat = new Intl.NumberFormat('pl-PL');

/** "YYYY-MM-DD HH:MM", "YYYY-MM-DD" albo ISO → Date (czas lokalny); null przy złym tekście. */
export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(/^\d{4}-\d{2}-\d{2} \d/.test(value) ? value.replace(' ', 'T') : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** „30 września 2026”; bez roku, gdy `withYear` = false. */
export function formatDayLong(value: string | Date | null | undefined, withYear = true): string {
  const date = value instanceof Date ? value : parseDate(value);
  if (!date) return '';
  const day = `${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]}`;
  return withYear ? `${day} ${date.getFullYear()}` : day;
}

/** „dzisiaj”, „wczoraj” albo „28 września” (rok tylko, gdy inny niż bieżący). */
export function formatRelativeDay(value: string | null | undefined, now = new Date()): string {
  const date = parseDate(value);
  if (!date) return '';
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / 86_400_000);
  if (days === 0) return 'dzisiaj';
  if (days === 1) return 'wczoraj';
  return formatDayLong(date, date.getFullYear() !== now.getFullYear());
}
