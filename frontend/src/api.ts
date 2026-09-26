import type {
  ActivityResponse,
  ApplicationsResponse,
  BootstrapData,
  CVInfo,
  EnvField,
  EnvKeysResponse,
  GapFilter,
  OfferDetail,
  OffersResponse,
  PipelinePrerequisites,
  PipelineState,
  SkillGapsResponse,
  Stats,
} from './types';

const API_BASE = '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let errorMsg = `HTTP ${res.status} ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson && errJson.error) {
        errorMsg = errJson.error;
      }
    } catch {
      // ignore
    }
    throw new Error(errorMsg);
  }

  return res.json() as Promise<T>;
}

export const api = {
  getBootstrap: () => request<BootstrapData>('/bootstrap'),
  getStats: () => request<Stats>('/stats'),

  /** `gap`: oferty z tym brakiem (panel braków) zamiast zakładki; próg jak w panelu. */
  getOffers: (
    tab: string,
    search: string = '',
    page: number = 1,
    pageSize: number = 25,
    gap?: GapFilter | null,
  ) => {
    const params = new URLSearchParams({
      tab: String(tab || 'Dopasowane'),
      search: String(search || ''),
      page: String(page || 1),
      page_size: String(pageSize || 25),
    });
    if (gap) {
      params.set('gap', gap.skill);
      params.set('gap_threshold', String(gap.threshold));
    }
    return request<OffersResponse>(`/offers?${params.toString()}`);
  },

  getOfferDetail: (link: string) => {
    const params = new URLSearchParams({ link });
    return request<OfferDetail>(`/offers/detail?${params.toString()}`);
  },

  updateDecision: (link: string, status: string, rating: number | null, stage?: string) =>
    request<{ ok: boolean; stats: Stats; offer: OfferDetail }>('/offers/decision', {
      method: 'POST',
      body: JSON.stringify({ link, status, rating, stage }),
    }),

  restoreDecision: (link: string) =>
    request<{ ok: boolean; changed: boolean; stats: Stats; offer: OfferDetail }>('/offers/restore', {
      method: 'POST',
      body: JSON.stringify({ link }),
    }),

  deleteOfferPermanent: (link: string) =>
    request<{ ok: boolean; stats: Stats }>('/offers/delete', {
      method: 'POST',
      body: JSON.stringify({ link }),
    }),

  /** Pusty `label` usuwa następny krok. */
  saveNextStep: (link: string, label: string, due: string | null) =>
    request<{ ok: boolean; offer: OfferDetail }>('/offers/next-step', {
      method: 'POST',
      body: JSON.stringify({ link, label, due }),
    }),

  getActivity: () => request<ActivityResponse>('/activity'),

  getApplications: () => request<ApplicationsResponse>('/applications'),

  getSkillGaps: (threshold: number = 50) =>
    request<SkillGapsResponse>(`/gaps?threshold=${threshold}`),

  fetchLinkData: (url: string) =>
    request<{ ok: boolean; data: Record<string, string> }>('/tools/fetch-link', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  saveManualJob: (data: {
    title: string;
    company: string;
    link: string;
    description: string;
    source: string;
    location: string;
  }) =>
    request<{ ok: boolean; message: string; stats: Stats; offer: OfferDetail }>('/tools/save-manual-job', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getPipelineState: () => request<PipelineState>('/pipeline/state'),

  getPipelinePrerequisites: () => request<PipelinePrerequisites>('/pipeline/prerequisites'),

  startPipeline: (mode: 'full' | 'skip_scraping' = 'full') =>
    request<{ ok: boolean; message: string; state: PipelineState }>('/pipeline/start', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),

  stopPipeline: () =>
    request<{ ok: boolean; message: string; state: PipelineState }>('/pipeline/stop', {
      method: 'POST',
    }),

  forceStopPipeline: () =>
    request<{ ok: boolean; message: string; state: PipelineState }>('/pipeline/force-stop', {
      method: 'POST',
    }),

  resumePipeline: () =>
    request<{ ok: boolean; message: string; state: PipelineState }>('/pipeline/resume', {
      method: 'POST',
    }),

  reloadDatabase: () =>
    request<{ ok: boolean; stats: Stats }>('/pipeline/reload-data', {
      method: 'POST',
    }),

  runStandaloneStep: (step: 'scrapers' | 'profile' | 'analysis' | 'rescore_all') =>
    request<{ ok: boolean; message: string; state: PipelineState }>('/pipeline/run-step', {
      method: 'POST',
      body: JSON.stringify({ step }),
    }),

  getCV: () => request<CVInfo>('/cv'),

  uploadCVFile: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return request<{ ok: boolean; message: string; cv_info: CVInfo }>('/cv/upload', {
      method: 'POST',
      body: formData,
    });
  },

  pasteCVText: (text: string) =>
    request<{ ok: boolean; message: string; cv_info: CVInfo }>('/cv/paste', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  openLocalCV: () =>
    request<{ ok: boolean; message: string }>('/cv/open-local', {
      method: 'POST',
    }),

  getEnvKeys: () => request<EnvKeysResponse>('/env-keys'),

  saveEnvKeys: (keys: Record<string, string>) =>
    request<{ ok: boolean; message: string; fields: EnvField[]; api_info: EnvKeysResponse['api_info'] }>('/env-keys', {
      method: 'POST',
      body: JSON.stringify({ keys }),
    }),

  /** `models`/`base_url`: pominięte = bez zmian, pusty tekst = wartość domyślna dostawcy. */
  saveLlmSettings: (settings: { provider: string; key?: string; models?: string; base_url?: string }) =>
    request<{ ok: boolean; message: string; api_info: EnvKeysResponse['api_info']; fields: EnvField[] }>('/env-keys/llm', {
      method: 'POST',
      body: JSON.stringify(settings),
    }),
};
