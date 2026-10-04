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
  state: 'running' | 'done' | 'skipped' | 'failed';
  found: number | null;
  added: number | null;
  /** Postęp pobierania opisów ofert (strony szczegółów), z meldunków scrapera. */
  details_done?: number | null;
  details_total?: number | null;
}

/** Przesiew w kodzie (matching/prefilter.py) i wstępna ocena (matching/triage.py): powód → liczba odrzuconych ofert. */
export type PrefilterReasons = Partial<
  Record<'miasto' | 'poziom' | 'lata' | 'jezyk' | 'kierunek', number>
>;

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

export interface RunSummaryOffer {
  link: string;
  title: string;
  company: string;
  location: string;
  work_mode: string;
  match_percentage: number | null;
}

export interface RunSummary {
  /** Start przebiegu (ISO). */
  started_at: string | null;
  /** Oferty pobrane w tym przebiegu; null, zanim wiadomo. */
  downloaded: number | null;
  checked: number;
  to_check: number | null;
  matched_count: number;
  /** Trzy najlepsze dopasowania z tego przebiegu. */
  recent: RunSummaryOffer[];
}

export type OfferTab = 'Dopasowane' | 'Ukryte' | 'Zapisane';
export type OfferSort = 'match' | 'newest';
export type Stage = 'apply' | 'interview' | 'offer' | 'archive';
export type DecisionStatus = 'save' | 'apply' | 'reject';
export type WorkMode = 'zdalnie' | 'hybrydowo' | 'stacjonarnie' | '';

export interface CvVersionSummary {
  id: string;
  link: string;
  company: string;
  title: string;
  /** 1, 2, … dla jednej oferty. */
  version: number;
  status: 'running' | 'failed' | 'draft' | 'ready';
  /** Tylko w trakcie: 'base_cv' | 'tailoring'. */
  phase: string | null;
  language: 'pl' | 'en';
  /** "YYYY-MM-DD HH:MM" */
  created_at: string;
  updated_at: string;
  /** Np. "cv_forma_studio_v1.pdf" (tylko gotowe). */
  file_name: string | null;
  /** Liczby do sprawdzenia, wciąż 'pending'. */
  pending_numbers: number;
  /** Komunikat dla użytkownika (tylko nieudane). */
  error: string | null;
}

export type CvChangeState = 'applied' | 'reverted' | 'edited';
export type CvNumberState = 'pending' | 'confirmed' | 'edited' | 'removed';

export interface CvChange {
  idx: number;
  path: string;
  section: string;
  before: string;
  after: string;
  reason: string;
  state: CvChangeState;
}

export interface CvNumber {
  idx: number;
  path: string;
  number: string;
  question: string;
  text: string;
  state: CvNumberState;
}

export interface CvQuestion {
  about: string;
  question: string;
  why: string;
}

export interface CvVersionDetail extends CvVersionSummary {
  facts: string;
  lock_contact: boolean;
  keep_order: boolean;
  changes: CvChange[];
  numbers: CvNumber[];
  questions: CvQuestion[];
  /** Wymagania oferty bez pokrycia w CV. */
  missing: string[];
  match: { must_met: number; must_total: number; ratio: number; recommendation: string } | null;
}

export interface CvVersionCreate {
  link: string;
  language: 'pl' | 'en';
  facts: string;
  lock_contact: boolean;
  keep_order: boolean;
  consent: true;
}

export interface TailorInstructions {
  text: string;
  is_default: boolean;
  file_name: string;
}

export interface OfferListItem {
  link: string;
  title: string;
  company: string;
  location: string;
  work_mode: WorkMode;
  source: string;
  logo_url: string | null;
  is_gone: boolean;
  /** Pierwszy raz zobaczona od startu ostatniego pobierania (`fresh_since`). */
  is_new: boolean;
  match_percentage: number | null;
  /** Np. "6 000–9 000 zł"; null bez widełek. */
  salary_text: string | null;
  status: DecisionStatus | null;
  /** Data ostatniej decyzji ("YYYY-MM-DD HH:MM"). */
  decided_at: string | null;
  note: string;
  /** Najnowsza wersja CV dla tej oferty. */
  cv: CvVersionSummary | null;
}

export interface OffersResponse {
  items: OfferListItem[];
  total: number;
  page: number;
  total_pages: number;
  page_size: number;
  /** Ile ofert całej listy (nie strony) jest nowych od `fresh_since`. */
  fresh_count: number;
  /** Start ostatniego pobierania (ISO, czas lokalny); null, gdy nigdy nie zapisany. */
  fresh_since: string | null;
  /** Liczba ukrytych ofert (etykieta filtra „Ukryte”). */
  hidden_count: number;
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
  logo_url: string | null;
  is_gone: boolean;
  work_mode: WorkMode;
  match_percentage: number | null;
  /** Powód odrzucenia przez przesiew; oferta oceniona, ale bez procentu. */
  filtered: string | null;
  fields: OfferFields;
  description_blocks: DescriptionBlock[];
  raw_description: string;
  /** ISO; UI pokazuje „dodana dzisiaj / wczoraj / 28 września”. */
  scraped_at: string | null;
  salary_text: string | null;
  status: DecisionStatus | null;
  stage: Stage | null;
  decided_at: string | null;
  applied_at: string | null;
  note: string;
  next_step: NextStep | null;
  /** Od najnowszej. */
  cv_versions: CvVersionSummary[];
}

export interface DecisionResponse {
  ok: boolean;
  offer: OfferDetail;
}

export interface ManualJob {
  title: string;
  company: string;
  link: string;
  description: string;
  source: string;
  location: string;
  status?: 'save' | 'apply';
}

export interface CVProfile {
  city: string | null;
  remote: boolean;
  seniority: string | null;
  years: number | null;
  roles: string[];
  skills: string[];
  languages: { name: string; level: string | null }[];
  /** Policzony z bieżącego CV; false = pipeline przeliczy go na starcie. */
  current: boolean;
}

export interface CVInfo {
  ready: boolean;
  text: string;
  chars: number;
  filename: string;
  /** "/api/cv/file", gdy jest cv.pdf. */
  pdf_url: string | null;
  /** Liczba stron cv.pdf; null bez PDF albo przy błędzie odczytu. */
  pdf_pages: number | null;
  /** Profil z CV (candidate_profile.json); null przed pierwszym przebiegiem. */
  profile: CVProfile | null;
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
  stage: Stage;
  decided_at: string | null;
  applied_at: string | null;
  next_step: NextStep | null;
  note: string;
  /** Wersja CV oznaczona jako wysłana; null = bazowe CV. */
  cv: CvVersionSummary | null;
}

export interface ApplicationsResponse {
  items: ApplicationItem[];
  counts: Record<Stage, number>;
  total: number;
}
