export interface Stats {
  raw_count: number;
  analyzed_count: number;
  pending_scoring_count: number;
  decisions_count: number;
  funnel: Record<string, number>;
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

export interface ScoringTelemetry {
  total: number | null;
  processed: number;
  batch: number;
  /** Ofert w paczce („Target Batch Size” z waterfall_analysis). */
  batch_size?: number | null;
  remaining: number | null;
  model: string | null;
  key?: number | null;
  key_count?: number | null;
  cooldowns?: Record<string, number>;
  /** Start pierwszej paczki i koniec ostatniej udanej (epoch s) — tempo do ETA. */
  first_batch_at?: number | null;
  last_done_at?: number | null;
}

/** Liczby etapów porządkowych, sparsowane z logu przebiegu. */
export interface StageStats {
  phase0?: { removed: number };
  phase1_5?: { links: number; merged: number };
  phase2?: { removed: number };
  phase2_5?: { chars_before: number; chars_after: number };
}

export interface PipelineTelemetry {
  sources: SourceTelemetry[];
  scoring: ScoringTelemetry | null;
  stages?: StageStats;
  /** Liczba kluczy API znana od startu przebiegu. */
  key_count?: number | null;
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
  tabs: string[];
  tools: string[];
  all_views: string[];
  tab_hints: Record<string, string>;
  stages: Record<string, string>;
  source_colors: Record<string, string>;
  source_fallback_color: string;
  state_colors: Record<string, string>;
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
  /** Filtr „oferty z tym brakiem” (panel braków) — echo zapytania; null bez filtra. */
  gap: string | null;
  gap_threshold: number | null;
}

/** Lista zawężona do ofert z brakiem `skill` (ten sam próg co w panelu braków). */
export interface GapFilter {
  skill: string;
  threshold: number;
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
  reason: string | null;
  industry: string | null;
  is_entry_level: boolean;
  learnable_in_month: boolean;
  missing_skills: string[];
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
  /** Hasła streszczające ofertę; puste dla ofert ocenionych przed dodaniem pola. */
  highlights: string[];
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

export interface SkillGapRow {
  skill: string;
  /** Liczba różnych ofert z tym brakiem — tyle pokaże lista po „Pokaż N ofert”. */
  offers: number;
  mean_match: number;
  width_pct: number;
}

export interface SkillGapsResponse {
  threshold: number;
  rows: SkillGapRow[];
  considered: number;
  skipped: number;
  total_gaps: number;
}

export interface CVInfo {
  ready: boolean;
  text: string;
  chars: number;
  words: number;
  filename: string;
  pdf_exists: boolean;
  txt_exists: boolean;
}

export interface EnvField {
  key: string;
  label: string;
  secret: boolean;
  configured: boolean;
}

export interface ApiKeysInfo {
  ready: boolean;
  count: number;
  primary_masked: string;
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
  playwright_ready: boolean;
  playwright_msg: string;
  db_count: number;
  model?: string;
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
