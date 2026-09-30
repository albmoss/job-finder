export interface Stats {
  raw_count: number;
  /** Oferty z procentem od Jev. */
  scored_count: number;
  /** Oferty odrzucone przez przesiew albo bez opisu — bez procentu. */
  filtered_count: number;
  /** Oferty, których przesiew i Jev jeszcze nie widziały. */
  pending_scoring_count: number;
  /** Oferty w bazie zobaczone pierwszy raz od startu ostatniego pobierania. */
  fresh_count: number;
  decisions_count: number;
  /** Aplikacje (bez archiwum), które stoją na etapie co najmniej 14 dni. */
  applications_stale: number;
}

export type StageStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed';

export interface PipelineStage {
  id: string;
  num: string;
  title: string;
  status: StageStatus;
  pattern?: string;
  started_at?: number | null;
  finished_at?: number | null;
}

export interface SourceTelemetry {
  name: string;
  color: string;
  state: 'running' | 'done' | 'skipped' | 'failed';
  found: number | null;
  added: number | null;
  /** Postęp pobierania opisów ofert (strony szczegółów), z meldunków scrapera. */
  details_done?: number | null;
  details_total?: number | null;
}

/** Przesiew w kodzie (matching/prefilter.py): powód → liczba odrzuconych ofert. */
export type PrefilterReasons = Partial<Record<'miasto' | 'poziom' | 'lata' | 'jezyk' | 'brak_opisu', number>>;

/** Etap dopasowania na żywo, z linii „Prefilter: …” i „Scored X/Y (R/s)” matching/run.py. */
export interface ScoringTelemetry {
  prefilter_rejected: number | null;
  prefilter_reasons: PrefilterReasons | null;
  to_score: number | null;
  scored: number;
  /** Nieudane zapytania do Jev. */
  errors: number;
  /** Oceny na sekundę od startu oceniania. */
  rate: number | null;
  /** Czas ostatniego meldunku postępu (epoch s). */
  last_done_at: number | null;
}
/** Liczby etapów, sparsowane z logu przebiegu. */
export interface StageStats {
  phase0?: { removed: number };
  phase1_5?: { links: number; merged: number };
  phase2?: { removed: number };
  phase2_5?: { chars_before: number; chars_after: number };
  phase3?: {
    prefilter_rejected?: number;
    prefilter_reasons?: PrefilterReasons;
    to_score?: number;
    scored?: number;
    total?: number;
    rate?: number;
    errors?: number;
    /** Oferty z procentem w całej bazie po etapie. */
    with_percent?: number;
  };
  /** Ewaluacja rankingu: Spearman % dopasowania vs ręczne oceny; `insufficient` przy < 10 ocen. */
  phase4?: { rho?: number; common?: number; insufficient?: boolean };
}
export interface PipelineTelemetry {
  sources: SourceTelemetry[];
  scoring: ScoringTelemetry | null;
  stages?: StageStats;
}

export type PipelineStatus = 'idle' | 'running' | 'stopping' | 'stopped' | 'failed' | 'completed';

export interface PipelineState {
  running: boolean;
  pid: number | null;
  mode: string;
  cmd: string[] | null;
  stages: PipelineStage[];
  logs: string[];
  current_stage_idx: number;
  current_stage_title: string;
  success: boolean | null;
  exit_code: number | null;
  error_message: string | null;
  status: PipelineStatus;
  can_resume: boolean;
  progress_percent: number | null;
  progress_label: string;
  started_at?: number | null;
  finished_at?: number | null;
  telemetry?: PipelineTelemetry;
}

export interface BootstrapData {
  stats: Stats;
  pipeline: PipelineState;
}

export interface OfferListItem {
  rank: number;
  link: string;
  title: string;
  company: string;
  location: string;
  source: string;
  source_color: string;
  is_gone: boolean;
  /** Pierwszy raz zobaczona od startu ostatniego pobierania (`fresh_since`). */
  is_new: boolean;
  match_percentage: number | null;
  status: string | null;
  rating: number | null;
  /** Data ostatniej decyzji ("YYYY-MM-DD HH:MM"); null bez decyzji albo w dawnym formacie. */
  decided_at: string | null;
  dot_color: string | null;
  dot_label: string | null;
}

export interface OffersResponse {
  items: OfferListItem[];
  total: number;
  page: number;
  total_pages: number;
  page_size: number;
  /**
   * Wartość, po której lista jest ułożona, na początku każdej strony: % dopasowania
   * (Dopasowane, Wszystkie) albo ocena użytkownika (Ocenione); null w zakładkach od najnowszych.
   */
  page_marks: (number | null)[] | null;
  /** Ile ofert całej listy (nie strony) jest nowych od `fresh_since`. */
  fresh_count: number;
  /** Start ostatniego pobierania (ISO, czas lokalny); null, gdy nigdy nie zapisany. */
  fresh_since: string | null;
}


/** Następny krok aplikacji; `due` "YYYY-MM-DD HH:MM" albo sam dzień. */
export interface NextStep {
  label: string;
  due: string | null;
}

export interface DescriptionBlock {
  type: 'p' | 'lead' | 'list';
  text?: string;
  items?: string[];
}


export interface SalaryInfo {
  min: number | null;
  max: number | null;
  currency: string;
  period: string;
  gross: boolean | null;
  contract?: string | null;
}

export interface LanguageRequirement {
  name: string;
  level?: string | null;
  required?: boolean | null;
}

export interface OfferFields {
  seniority?: string[] | null;
  work_modes?: string[] | null;
  contract_types?: string[] | null;
  schedules?: string[] | null;
  salary?: SalaryInfo | null;
  languages?: LanguageRequirement[] | null;
  years_required?: number | null;
  skills_required?: string[] | null;
  skills_nice?: string[] | null;
  category?: string | null;
}

export interface OfferDetail {
  link: string;
  title: string;
  company: string;
  location: string;
  source: string;
  source_color: string;
  is_gone: boolean;
  work_mode: string;
  match_percentage: number | null;
  /** Powód odrzucenia przez przesiew; oferta oceniona, ale bez procentu. */
  filtered: keyof PrefilterReasons | null;
  fields: OfferFields;
  description_blocks: DescriptionBlock[];
  raw_description: string;
  status: string | null;
  rating: number | null;
  stage: string;
  decided_at: string | null;
  applied_at: string | null;
  dot_color: string | null;
  dot_label: string | null;
  next_step: NextStep | null;
}
export interface ActivityRow {
  timestamp: string;
  time_str: string;
  op: string;
  what: string;
  source_color: string | null;
  detail: string;
  bad: boolean;
}

export interface RecentDecision {
  link: string;
  title: string;
  company: string;
  status: string;
  rating: number | null;
  label: string;
  color: string;
  stamp: string | null;
}

export interface ActivityResponse {
  activity_rows: ActivityRow[];
  recent_decisions: RecentDecision[];
}

export interface CVInfo {
  ready: boolean;
  text: string;
  chars: number;
  words: number;
  filename: string;
  pdf_exists: boolean;
  txt_exists: boolean;
  /** Profil z CV (candidate_profile.json); null przed pierwszym przebiegiem. */
  profile: CandidateProfileSummary | null;
}

export interface CandidateProfileSummary {
  city: string | null;
  seniority: string | null;
  years: number | null;
  skills: number;
  /** Policzony z bieżącego CV; false = pipeline przeliczy go na starcie. */
  current: boolean;
}

export interface EnvField {
  key: string;
  label: string;
  secret: boolean;
  configured: boolean;
}

export interface LlmProvider {
  id: string;
  label: string;
  key_env: string;
  key_hint: string;
  models_env: string;
  default_models: string[];
  base_url_env: string;
  default_base_url: string;
}

/** Dostawca modelu, który czyta CV i buduje z niego profil (LLM_PROVIDER w .env). */
export interface ApiKeysInfo {
  ready: boolean;
  error: string;
  count: number;
  primary_masked: string;
  provider: string;
  models: string[];
  models_custom: boolean;
  base_url: string;
  providers: LlmProvider[];
}

export interface EnvKeysResponse {
  fields: EnvField[];
  api_info: ApiKeysInfo;
}

export interface PipelinePrerequisites {
  ready_full: boolean;
  ready_skip: boolean;
  issues: string[];
  issues_skip: string[];
  cv_ready: boolean;
  api_ready: boolean;
  /** Klucz TYPESAFE_API_KEY (Jev ocenia oferty). */
  jev_ready: boolean;
  /** Profil trzeba policzyć z CV, więc model z LLM_PROVIDER jest wymagany. */
  llm_needed: boolean;
  playwright_ready: boolean;
  playwright_msg: string;
  db_count: number;
}

export interface ApplicationItem {
  link: string;
  title: string;
  company: string;
  location: string;
  match_percentage: number | null;
  status: string;
  rating: number | null;
  stage: string;
  decided_at: string | null;
  applied_at: string | null;
  /** Dni od ostatniej zmiany; null, gdy decyzja nie ma daty. */
  age_days: number | null;
  next_step: NextStep | null;
}

export interface ApplicationsResponse {
  items: ApplicationItem[];
  counts: Record<string, number>;
  total: number;
  stale_count: number;
}
