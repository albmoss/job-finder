export type SearchStatus = 'queued' | 'reading' | 'profile' | 'ranking' | 'triage' | 'scoring' | 'done' | 'error';

export interface PublicPick {
  link: string;
  title: string;
  company: string;
  location: string;
  source: string;
  salary: string | null;
  work_mode: string;
  logo_url: string | null;
  percent: number;
}

export interface SearchProfile {
  city: string | null;
  seniority: string | null;
  roles: string[];
}

export interface SearchState {
  status: SearchStatus;
  queue: number;
  progress: { good?: number };
  profile: SearchProfile | null;
  result: { picks: PublicPick[]; good: number; teasers: number[] } | null;
  error: string | null;
}

export interface LegalInfo {
  operator: string;
  email: string;
  hosting: string;
  cv_reader: string;
  search_ttl_hours: number;
  free_window_days: number;
}

export class PublicApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new PublicApiError('Brak połączenia z serwerem. Sprawdź internet i spróbuj ponownie.', 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new PublicApiError(body?.error ?? `Błąd serwera (${response.status}).`, response.status, body?.code ?? null);
  }
  return body as T;
}

export const publicApi = {
  legal: () => call<LegalInfo>('/api/public/legal'),
  start: (file: File) =>
    call<{ id: string }>('/api/public/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    }),
  state: (id: string) => call<SearchState>(`/api/public/search/${encodeURIComponent(id)}`),
  waitlist: (email: string) =>
    call<{ ok: boolean }>('/api/public/waitlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    }),
};
