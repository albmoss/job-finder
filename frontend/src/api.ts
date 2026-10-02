import type {
  ApplicationsResponse,
  BootstrapData,
  CVInfo,
  CvVersionCreate,
  CvVersionDetail,
  CvVersionSummary,
  DecisionResponse,
  DecisionStatus,
  EnvField,
  EnvKeysResponse,
  ManualJob,
  OfferDetail,
  OfferSort,
  OffersResponse,
  OfferTab,
  PipelinePrerequisites,
  PipelineState,
  RunSummary,
  Stage,
  Stats,
  TailorInstructions,
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

/** Tekst błędu z `request` (pole `error` z API) albo z wyjątku. */
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

const cvVersionPath = (id: string) => `/cv-versions/${encodeURIComponent(id)}`;

type PipelineAction = { ok: boolean; message: string; state: PipelineState };
type LlmSaveResponse = { ok: boolean; message: string; api_info: EnvKeysResponse['api_info']; fields: EnvField[] };

export const api = {
  getBootstrap: () => request<BootstrapData>('/bootstrap'),
  getStats: () => request<Stats>('/stats'),

  getOffers: (params: { tab: OfferTab; search?: string; page?: number; pageSize?: number; sort?: OfferSort }) => {
    const query = new URLSearchParams({
      tab: params.tab,
      search: params.search ?? '',
      page: String(params.page ?? 1),
      page_size: String(params.pageSize ?? 25),
      sort: params.sort ?? 'match',
    });
    return request<OffersResponse>(`/offers?${query.toString()}`);
  },

  getOfferDetail: (link: string) => request<OfferDetail>(`/offers/detail?${new URLSearchParams({ link })}`),

  /** `cvVersionId` tylko przy `apply`: wersja CV wysłana z aplikacją. */
  updateDecision: (link: string, status: DecisionStatus, stage?: Stage, cvVersionId?: string) =>
    post<DecisionResponse>('/offers/decision', { link, status, stage, cv_version_id: cvVersionId }),

  /** Zdejmuje decyzję: odkrycie, cofnięcie zapisu, „Cofnij oznaczenie”. */
  restoreDecision: (link: string) =>
    post<{ ok: boolean; changed: boolean; stats: Stats; offer: OfferDetail }>('/offers/restore', { link }),

  /** 400, gdy oferta nie ma decyzji. */
  saveNote: (link: string, note: string) => post<{ ok: boolean; offer: OfferDetail }>('/offers/note', { link, note }),

  /** Pusty `label` usuwa następny krok. */
  saveNextStep: (link: string, label: string, due: string | null) =>
    post<{ ok: boolean; offer: OfferDetail }>('/offers/next-step', { link, label, due }),

  getApplications: () => request<ApplicationsResponse>('/applications'),

  fetchLinkData: (url: string) => post<{ ok: boolean; data: Record<string, string> }>('/tools/fetch-link', { url }),

  saveManualJob: (job: ManualJob) =>
    post<{ ok: boolean; message: string; stats: Stats; offer: OfferDetail }>('/tools/save-manual-job', job),

  getPipelineState: () => request<PipelineState>('/pipeline/state'),
  getRunSummary: () => request<RunSummary>('/pipeline/run-summary'),
  getPipelinePrerequisites: () => request<PipelinePrerequisites>('/pipeline/prerequisites'),
  startPipeline: (mode: 'full' | 'skip_scraping' = 'full') => post<PipelineAction>('/pipeline/start', { mode }),
  stopPipeline: () => post<PipelineAction>('/pipeline/stop'),
  forceStopPipeline: () => post<PipelineAction>('/pipeline/force-stop'),
  resumePipeline: () => post<PipelineAction>('/pipeline/resume'),
  reloadDatabase: () => post<{ ok: boolean; stats: Stats }>('/pipeline/reload-data'),

  getCV: () => request<CVInfo>('/cv'),
  cvFileUrl: `${API_BASE}/cv/file`,

  uploadCVFile: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return request<{ ok: boolean; message: string; cv_info: CVInfo }>('/cv/upload', {
      method: 'POST',
      body: formData,
    });
  },

  pasteCVText: (text: string) =>
    post<{ ok: boolean; message: string; cv_info: CVInfo }>('/cv/paste', { text }),

  getEnvKeys: () => request<EnvKeysResponse>('/env-keys'),

  saveEnvKeys: (keys: Record<string, string>) =>
    post<{ ok: boolean; message: string; fields: EnvField[]; api_info: EnvKeysResponse['api_info'] }>('/env-keys', {
      keys,
    }),

  /** `models`/`base_url`: pominięte = bez zmian, pusty tekst = wartość domyślna dostawcy. */
  saveLlmSettings: (settings: { provider: string; key?: string; models?: string; base_url?: string }) =>
    post<LlmSaveResponse>('/env-keys/llm', settings),

  /** Bez `link` wszystkie wersje, od najnowszej. */
  listCvVersions: (link?: string) =>
    request<{ items: CvVersionSummary[] }>(`/cv-versions${link ? `?${new URLSearchParams({ link })}` : ''}`),
  createCvVersion: (body: CvVersionCreate) => post<{ ok: boolean; version: CvVersionSummary }>('/cv-versions', body),
  getCvVersion: (id: string) => request<CvVersionDetail>(cvVersionPath(id)),
  cancelCvVersion: (id: string) => post<{ ok: boolean; version: CvVersionSummary }>(`${cvVersionPath(id)}/cancel`),
  retryCvVersion: (id: string) => post<{ ok: boolean; version: CvVersionSummary }>(`${cvVersionPath(id)}/retry`),
  updateCvChange: (id: string, idx: number, action: 'revert' | 'apply' | 'edit', text?: string) =>
    post<CvVersionDetail>(`${cvVersionPath(id)}/change`, { idx, action, text }),
  updateCvNumber: (id: string, idx: number, action: 'confirm' | 'edit' | 'remove', text?: string) =>
    post<CvVersionDetail>(`${cvVersionPath(id)}/number`, { idx, action, text }),
  /** 400, dopóki są niesprawdzone liczby. */
  finalizeCvVersion: (id: string) => post<CvVersionDetail>(`${cvVersionPath(id)}/finalize`),
  cvVersionPdfUrl: (id: string) => `${API_BASE}${cvVersionPath(id)}/pdf`,
  cvVersionHtmlUrl: (id: string, which: 'tailored' | 'base', marks = true) =>
    `${API_BASE}${cvVersionPath(id)}/html?${new URLSearchParams({ which, marks: marks ? '1' : '0' })}`,

  getTailorInstructions: () => request<TailorInstructions>('/cv-tailor/instructions'),
  saveTailorInstructions: (text: string) => post<TailorInstructions>('/cv-tailor/instructions', { text }),
  resetTailorInstructions: () => post<TailorInstructions>('/cv-tailor/instructions/reset'),
};
