import type { OfferFields } from '../../types';

const SENIORITY_LABELS: Record<string, string> = {
  intern: 'Staż',
  junior: 'Junior',
  mid: 'Mid',
  senior: 'Senior',
  lead: 'Lead',
  manager: 'Manager',
};

const CONTRACT_LABELS: Record<string, string> = {
  uop: 'UoP',
  b2b: 'B2B',
  zlecenie: 'Zlecenie',
  dzielo: 'O dzieło',
  staz: 'Staż',
  other: 'Inna',
};

export function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** „Warszawa / zdalnie”, „Zdalnie” bez miasta, samo miasto bez trybu pracy. */
export function locationLine(location: string, workMode: string): string {
  const place = location.trim();
  if (!place) return capitalize(workMode);
  if (!workMode || place.toLowerCase().includes(workMode)) return place;
  return `${place} / ${workMode}`;
}

function labelList(values: string[] | null | undefined, labels: Record<string, string>): string {
  const seen = new Set<string>();
  for (const value of values ?? []) {
    const label = labels[value] ?? capitalize(value);
    if (label) seen.add(label);
  }
  return [...seen].join(' / ');
}

export const seniorityText = (fields: OfferFields) => labelList(fields.seniority, SENIORITY_LABELS);

export const contractText = (fields: OfferFields) => labelList(fields.contract_types, CONTRACT_LABELS);

/** Wymagane, potem mile widziane, bez powtórzeń. */
export function offerSkills(fields: OfferFields): string[] {
  const seen = new Map<string, string>();
  for (const skill of [...(fields.skills_required ?? []), ...(fields.skills_nice ?? [])]) {
    const key = skill.trim().toLowerCase();
    if (key && !seen.has(key)) seen.set(key, skill.trim());
  }
  return [...seen.values()];
}
