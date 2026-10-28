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

export type Decision = 'save' | 'reject';

export interface ProOffer extends PublicPick {
  decision: Decision | null;
}

export interface SearchProfile {
  city: string | null;
  seniority: string | null;
  roles: string[];
}

export interface GuestResult {
  picks: PublicPick[];
  good: number;
  teasers: number[];
}

export interface SearchState {
  kind: 'guest' | 'pro';
  status: SearchStatus;
  queue: number;
  progress: { good?: number; scored?: number; total?: number };
  profile: SearchProfile | null;
  result: GuestResult | { count: number } | null;
  error: string | null;
}

export interface Me {
  email: string;
  plan: 'free' | 'pro';
  pro: boolean;
  admin: boolean;
  cv: (SearchProfile & { at: number }) | null;
  running: string | null;
  searches_left: number | null;
}

export interface LegalInfo {
  operator: string;
  email: string;
  hosting: string;
  cv_reader: string;
  search_ttl_hours: number;
  free_window_days: number;
  session_days: number;
  login_minutes: number;
}

export interface SearchStats {
  total: number;
  ok: number;
  tokens: number;
}

export interface AdminUser {
  id: number;
  email: string;
  plan: 'free' | 'pro';
  admin: boolean;
  created_at: number;
  last_login: number | null;
  has_cv: boolean;
  matches: number;
  pro_searches: number;
}

export interface AdminOverview {
  offers: {
    snapshot: string | null;
    published_at: string | null;
    total: number | null;
    sources: { source: string; offers: number; last_seen: string }[];
  };
  scrapers: {
    name: string;
    scope: string;
    last_run: string | null;
    status: string | null;
    count: number | null;
    median: number | null;
  }[];
  users: AdminUser[];
  searches: { day: Record<'guest' | 'pro', SearchStats>; week: Record<'guest' | 'pro', SearchStats> };
  usd_per_mtok: number;
  limits: { daily: number; used_today: number; pro_scored: number; pro_daily: number };
  connections: { name: string; ok: boolean; detail: string }[];
  waitlist: { email: string; created_at: string }[];
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

function post<T>(path: string, body: unknown = {}): Promise<T> {
  return call<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function upload<T>(path: string, file: File | null): Promise<T> {
  return call<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file ?? new Blob([]),
  });
}

export const publicApi = {
  legal: () => call<LegalInfo>('/api/public/legal'),
  start: (file: File) => upload<{ id: string }>('/api/public/search', file),
  state: (id: string) => call<SearchState>(`/api/public/search/${encodeURIComponent(id)}`),
  me: () => call<{ user: Me | null }>('/api/public/me'),
  requestLogin: (email: string) => post<{ ok: boolean }>('/api/public/auth/request', { email }),
  verifyLogin: (token: string) => post<{ user: Me }>('/api/public/auth/verify', { token }),
  logout: () => post<{ ok: boolean }>('/api/public/auth/logout'),
  deleteAccount: () => post<{ ok: boolean }>('/api/public/me/delete'),
  proSearch: (file: File | null) => upload<{ id: string }>('/api/public/pro/search', file),
  proOffers: () => call<{ offers: ProOffer[]; user: Me }>('/api/public/pro/offers'),
  decide: (link: string, status: Decision | null) => post<{ ok: boolean }>('/api/public/pro/decision', { link, status }),
  admin: () => call<AdminOverview>('/api/public/admin'),
  setPlan: (userId: number, plan: 'free' | 'pro') => post<{ ok: boolean }>('/api/public/admin/plan', { user_id: userId, plan }),
};
