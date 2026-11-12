export interface Profile {
  id: string
  phone?: string
  name: string
  organization: string
  role: string
  plan: string
  modelConfigured: boolean
}

export interface ModelProvider {
  code: string
  name: string
  default_base_url: string
  allow_custom_url: boolean
  is_custom: boolean
}

export interface ModelType {
  code: string
  name: string
  description: string | null
  is_custom: boolean
}

export interface ModelConfig {
  id: string
    provider_code: string
    model_type_code: string
  name: string
  base_url: string
  model_name: string
  status: 'DRAFT' | 'VERIFYING' | 'ACTIVE' | 'INVALID' | 'DISABLED'
  default_for: string[]
  is_default: boolean
  is_account_api?: boolean
  settings: {
    timeout_seconds?: number
    max_output_tokens?: number
  }
  has_api_key: boolean
  masked_api_key: string | null
  last_verified_at: string | null
  last_error_code: string | null
  created_at: string | null
  updated_at: string | null
}

export interface DefaultModelConfig {
  value: string
  name: string
  model_name: string
  source: 'builtin' | 'personal' | 'account_api'
  config_id: string | null
}

export interface TokenUsageSnapshot {
  period_start: string
  period_end: string
  quota: number | null
  used: number
  remaining: number | null
  unlimited: boolean
  usage_percent: number
  breakdown: {
    platform: number
    api: number
  }
}

export interface HistoryItem {
  id: string
  title: string
  time: string
  agentCode: string
}

export interface PersonalKnowledgeFolder {
  id: string
  parent_id: string | null
  name: string
  description: string
  color: string
  paper_count: number
  created_at: string | null
  updated_at: string | null
}

export interface PersonalKnowledgePaper {
  id: string
  folder_id: string
  source_type: 'LOCAL_UPLOAD' | 'PLATFORM_REFERENCE'
  platform_paper_id: string | null
  title: string
  authors: string[]
  abstract: string
  publish_venue: string
  publish_year: number | null
  source_url: string | null
  original_file_name: string | null
  file_size: number | null
  metadata: Record<string, unknown>
  created_at: string | null
}

export interface PlatformKnowledgePaper {
  id: string
  title: string
  authors: string[]
  abstract: string
  publish_venue: string
  publish_year: number | null
  source_url: string | null
  source: string
  parse_status: number
  citation_count: number
}

export interface ResearchProject {
  id: string
  name: string
  description: string
  research_goal: string
  status: string
  role?: 'OWNER' | 'EDITOR' | 'VIEWER'
  settings: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export interface ProjectDocument {
  id: string
  project_id: string
  title: string
  document_type: string
  content?: string
  content_json?: Record<string, unknown>
  version: number
  status: string
  updated_at?: string
}

export interface ProjectConversation {
  id: string
  project_id: string
  title: string
  status: string
  updated_at?: string
}

export interface ProjectTask {
  id: string
  title: string
  agent_code: string
  status: string
  progress: number
  created_at?: string
}

export interface ProjectArtifact {
  id: string
  project_id: string
  task_id?: string
  artifact_type: string
  name: string
  version: number
  updated_at?: string
}

export interface ProjectWorkspace {
  project: ResearchProject
  documents: ProjectDocument[]
  conversations: ProjectConversation[]
  tasks: ProjectTask[]
  artifacts: ProjectArtifact[]
}

export interface CatalogItem {
  id: string
  code?: string
  name: string
  category?: string
  description: string
  status?: string
  readiness?: 'READY' | 'DEGRADED' | 'UNAVAILABLE'
  readiness_detail?: string
  members?: string[]
  downloads?: number
  tags?: string[]
  route?: string
  capabilities?: string[]
}

export interface AgentTeamMember {
  code: string
  name: string
  category: string
  route?: string
  available: boolean
  requires_input: boolean
}

export interface AgentTeam {
  id: string
  name: string
  description: string
  visibility: 'PRIVATE' | 'PUBLIC'
  version: number
  status: string
  mode: 'sequential'
  members: AgentTeamMember[]
  member_names: string[]
  editable: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface AgentTeamTemplate {
  id: string
  name: string
  description: string
  accent: string
  members: AgentTeamMember[]
}

export interface AgentTeamStage {
  code: string
  name: string
  status: string
  task_id?: string | null
  message?: string
  route?: string
  summary?: Record<string, unknown>
  finished_at?: string | null
}

export interface AgentTeamRun {
  id: string
  agent_team_id?: string
  title: string
  prompt: string
  status: string
  progress: number
  current_step?: string
  error?: string
  output: {
    team?: { id: string; name: string }
    stages?: AgentTeamStage[]
    waiting_stage?: AgentTeamStage
    final_summary?: string
  }
}

export interface SkillFile {
  path: string
  content: string
}

export interface SkillDetail {
  id: string
  name: string
  description: string
  status: string
  category?: string
  tags: string[]
  author?: string
  sourceSite?: string
  sourceUrl?: string
  installUrl?: string
  downloadStatus: 'DOWNLOADED' | 'METADATA_ONLY' | 'ERROR'
  downloadedAt?: string | null
  fileCount: number
  fullContent: string
  files: SkillFile[]
  downloadError?: string
}

export interface LiteraturePaper {
  id?: string
  title: string
  authors: string[]
  abstract: string
  year?: number
  venue?: string
  source: string
  sources?: string[]
  url?: string
  pdf_url?: string
  citation_count?: number
  score?: number
}

export interface LiteratureTaskOutput {
  query_plan?: {
    intent_summary: string
    keywords: string[]
    start_year: number
    end_year: number
    queries: string[]
  }
  source_progress?: Record<string, { label: string; count: number; status: string; errors?: unknown[] }>
  papers?: LiteraturePaper[]
  literature_list?: Array<Record<string, string | number | null>>
  report_markdown?: string
  fishbone_url?: string | null
  warnings?: string[]
  errors?: unknown[]
}

export interface ManuscriptSection {
  id: string
  title: string
  content: string
}

export interface InnovationScoreSet {
  novelty?: number
  feasibility?: number
  impact?: number
  risk?: number
  [key: string]: number | undefined
}

export interface InnovationProposal {
  innovation_id?: string
  rank?: number
  title: string
  summary?: string
  method_type?: string
  research_question?: string
  hypothesis?: string
  problem?: string
  method_route?: string
  expected_contribution?: string
  validation_plan?: string
  scores?: InnovationScoreSet
  overall_score?: number
  evidence?: Array<Record<string, unknown>>
  evidence_refs?: string[]
  downstream_wengao_inputs?: Record<string, unknown>
}

export interface ReviewerCommentItem {
  id: string
  comment: string
  category: string
  severity: string
  intent: string
  evidence_needed: string[]
  reply_angle: string
}

export interface SubmissionRecommendation {
  venue?: {
    abbreviation?: string
    full_name?: string
    ccf_level?: string
    type?: string
    avg_review_weeks?: number
    is_oa?: boolean
    [key: string]: unknown
  }
  tier?: string
  match_score?: {
    overall?: number
    topic_similarity?: number
    methodology_alignment?: number
    [key: string]: number | undefined
  }
  estimated_acceptance_prob?: string
  confidence?: number
  rank_score?: number
  strengths?: string[]
  risks?: string[]
  differentiation?: string
}

export interface ComplianceRisk {
  risk_id: string
  type: string
  module: string
  severity: '极高' | '高' | '中' | '低' | '极低'
  title: string
  location?: { section?: string; paragraph_index?: number; quote?: string }
  evidence?: Array<{ source?: string; content?: string }>
  suggestion?: string
  confidence?: number
}

export interface ComplianceModuleResult {
  score?: number
  summary?: string
  excellent_points?: string[]
  revision_suggestions?: string[]
}

export interface TranslationSegment {
  segment_id?: string
  kind?: string
  page?: number | null
  source_text: string
  translated_text: string
}

export interface TranslationTerm {
  source: string
  target: string
  confidence?: number
  origin?: string
}

export interface TranslationFile {
  kind: string
  label: string
  file_name: string
  size: number
}

export interface TranslationRequest {
  file_name: string
  file_type: string
  source_lang: string
  target_lang: string
  precision: 'reading' | 'submission'
  glossary?: Record<string, string>
  domain?: string
  parallel?: number
  preserve_pdf_layout: boolean
  bilingual: boolean
  translate_figures: boolean
  pdf_layout_mode?: 'batch' | 'pagewise' | 'low_memory'
  pdf_timeout_seconds?: number
  [key: string]: unknown
}

export interface TranslationQuality {
  total_segments?: number
  translated_segments?: number
  untranslated_segment_ids?: string[]
  terminology_violations?: string[]
  protected_token_violations?: string[]
  format_violations?: string[]
  warnings?: string[]
}

export interface TranslationTaskEvent {
  sequence: number
  type: string
  progress: number
  message: string
  elapsed_seconds?: number
}

export interface PatentCandidate {
  id: string
  title: string
  technical_background?: string
  innovation?: string
  difference?: string
  feasibility?: string
}

export interface PatentClaim {
  claim_id?: string
  claim_number?: number
  claim_type?: string
  depends_on?: number[]
  text: string
  feature_ids?: string[]
}

export interface FigureLocalizedText {
  zh?: string
  en?: string
}

export interface AcademicFigureSpec {
  figure_type?: string
  title?: FigureLocalizedText
  x?: string | null
  y?: string | null
  series?: string | null
  error?: string | null
  xlabel?: FigureLocalizedText
  ylabel?: FigureLocalizedText
  width_inches?: number
  height_inches?: number
  dpi?: number
  palette?: string[]
  legend?: boolean
  grid?: boolean
  assumptions?: string[]
  nodes?: Array<Record<string, unknown>>
  edges?: Array<Record<string, unknown>>
}

export interface AcademicFigureQuality {
  passed?: boolean
  revision?: number
  checks?: Array<{ name: string; status: 'passed' | 'warning' | 'failed'; message: string }>
  generated_files?: string[]
}

export interface AcademicFigureDataset {
  source_files?: string[]
  row_count?: number
  columns?: string[]
  numeric_columns?: string[]
  categorical_columns?: string[]
  missing_values?: Record<string, number>
  preview?: Array<Record<string, unknown>>
  sha256?: string | null
}

export interface ArxivDailyCategory {
  code: string
  name_cn: string
}

export interface ArxivDailyPaper {
  arxiv_id: string
  title: string
  title_cn: string
  summary_cn: string
  pdf_url: string
  authors: string
  affiliations: string[]
  abstract_cn: string
  abstract: string
  categories: string[]
  updated: string
  submission_label: string
}

export interface ResearchTaskOutput extends LiteratureTaskOutput {
  reading_source?: {
    type: 'USER_UPLOAD' | 'ARXIV'
    uploadId?: string
    fileName?: string
    arxivId?: string
  }
  paper_reading?: PaperReadingReport
  paper_reading_agent_version?: string
  paper_reading_summary?: {
    agent_version: string
    claim_count: number
    evidence_count: number
    scientific_element_count: number
    reliability_record_count: number
  }
  timing?: {
    speed_profile?: string
    effective_configuration?: Record<string, unknown>
    model_requests?: Record<string, { request_count?: number; success_count?: number; failure_count?: number }>
    stages_seconds?: Record<string, number>
    total_seconds?: number
  }
  manuscript_plan?: {
    topic: string
    language: string
    keywords: string[]
    sections: Array<{ id: string; title: string }>
    checks: string[]
  }
  manuscript_markdown?: string
  manuscript_execution_mode?: 'model' | 'deterministic_fallback'
  manuscript_warnings?: string[]
  sections?: ManuscriptSection[]
  metrics?: Record<string, number | string>
  request_plan?: {
    domain: string
    keywords: string[]
    top_k: number
    mode?: 'full' | 'expand' | 'evaluate'
    time_range?: string | null
    seed_ideas?: string[]
  }
  research_domain?: string
  research_trends?: Array<Record<string, unknown>>
  research_gaps?: Array<Record<string, unknown>>
  innovations?: InnovationProposal[]
  candidate_innovations?: Array<Record<string, unknown>>
  evaluated_innovations?: Array<Record<string, unknown>>
  evidence_map?: Record<string, string[]>
  literature_corpus?: Array<Record<string, unknown>>
  knowledge_graph_summary?: string
  citation_network_summary?: string
  workflow_trace?: Array<Record<string, unknown>>
  metadata?: Record<string, unknown>
  logs?: Record<string, unknown>
  reviewer_request?: Record<string, unknown>
  review_items?: ReviewerCommentItem[]
  reply_strategy?: Record<string, unknown>
  response_letter_markdown?: string
  revision_checklist?: Array<Record<string, string>>
  submission_request?: {
    paper?: Record<string, unknown>
    quality?: Record<string, unknown>
    preferences?: Record<string, unknown>
  }
  recommendations?: SubmissionRecommendation[]
  submission_checklist?: Record<string, string[]>
  submission_strategy?: {
    primary_target?: SubmissionRecommendation
    timeline?: Array<Record<string, string | number | null | undefined>>
    fallback_plan?: string
  }
  comparison_matrix?: Record<string, unknown>
  thinking_trace?: Array<Record<string, unknown>>
  final_report?: string
  compliance_request?: {
    file_name: string
    file_type: string
    task_type: 'paper_precheck' | 'journal_submission'
    target_rule_set: string
  }
  academic_compliance?: Record<string, unknown>
  compliance_summary?: {
    compliance_score?: number
    summary?: string
    excellent_points?: string[]
    revision_suggestions?: Array<string | Record<string, unknown>>
  }
  risk_summary?: {
    overall_level?: string
    risk_count?: number
    severity_counts?: Record<string, number>
    module_counts?: Record<string, number>
  }
  risks?: ComplianceRisk[]
  suggestions?: Array<string | Record<string, unknown>>
  module_check_results?: Record<string, ComplianceModuleResult>
  artifacts?: Record<string, string>
  translation_request?: TranslationRequest
  academic_translation?: Record<string, unknown>
  translation_segments?: TranslationSegment[]
  translation_glossary?: TranslationTerm[]
  translation_quality?: TranslationQuality
  translation_warnings?: string[]
  translation_files?: TranslationFile[]
  patent_run_id?: string
  patent_status?: string
  patent_candidates?: PatentCandidate[]
  selected_patent_point_id?: string
  patent_summary?: Record<string, unknown>
  disclosure_sections?: Record<string, string>
  disclosure_markdown?: string
  claim_plan?: Record<string, unknown>
  claims?: { claims?: PatentClaim[]; claim_count?: number; [key: string]: unknown }
  claims_markdown?: string
  claim_validation?: {
    passed?: boolean
    issue_count?: number
    warning_count?: number
    issues?: Array<Record<string, unknown>>
    warnings?: Array<Record<string, unknown> | string>
    [key: string]: unknown
  }
  release_readiness?: Record<string, unknown>
  patent_warnings?: string[]
  figure_request?: Record<string, unknown>
  figure_spec?: AcademicFigureSpec
  dataset_summary?: AcademicFigureDataset
  figure_captions?: { zh?: string; en?: string }
  figure_quality?: AcademicFigureQuality
  figure_warnings?: string[]
  figure_artifacts?: Record<string, unknown>
  daily_request?: {
    category?: string
    category_name?: string
    search_query?: string
    refresh?: boolean
  }
  daily_categories?: ArxivDailyCategory[]
  daily_papers?: ArxivDailyPaper[]
  daily_summary?: {
    source?: string
    paper_count?: number
    fetched_at?: string
    cached?: boolean
  }
  daily_warnings?: string[]
}

export interface PaperReadingEvidence {
  evidence_id: string
  evidence_type: string
  page_number: number
  section_path: string[]
  object_id: string
  evidence_text: string
}

export interface PaperReadingClaim {
  claim_id: string
  claim_type: string
  claim_source: string
  content: string
  evidence_ids: string[]
}

export interface PaperReadingScientificElement {
  element_id: string
  element_type: 'EQUATION' | 'FIGURE' | 'TABLE'
  label: string
  page: number
  explanation: string
  variables: Array<{ symbol: string; meaning: string }>
  findings: string[]
  table_checks: Array<{
    check_type: string
    metric: string
    scope: string
    direction: string
    baseline_label: string
    baseline_value: number
    target_label: string
    target_value: number
    absolute_difference: number
  }>
  table_cell_facts: Array<{
    metric: string
    scope: string
    row_label: string
    column_header: string
    value: number
  }>
  visual_status: string
}

export interface PaperReadingReport {
  paper: {
    title: string
    authors: string[]
    year?: number | null
    source_type: string
    arxiv_id?: string | null
  }
  request: {
    depth: string
    reading_goal: string
    focus_aspects: string[]
  }
  narrative?: {
    one_sentence_summary?: string
    background_and_motivation?: string[]
    problem_definition?: string[]
    method_data_flow?: string[]
    assumptions?: string[]
    further_reading_questions?: string[]
  } | null
  reading_result: {
    basic_information: { title: string; authors: string[]; year?: number | null }
    research_questions: string[]
    method_structure: string[]
    key_equations_and_figures: string[]
    experiment_findings: string[]
    innovations: string[]
    limitations: string[]
    claims: PaperReadingClaim[]
    evidence: PaperReadingEvidence[]
    warnings: Array<{ warning_code: string; message: string }>
  }
  flow_execution?: {
    mode?: string
    completion_status: string
    stages: Record<string, string>
    degradations: Array<{ stage: string; code: string; category?: string; message: string; action?: string }>
  } | null
  scientific_elements?: { elements: PaperReadingScientificElement[] } | null
  scientific_coverage?: Record<string, unknown> | null
  experiments?: {
    datasets: Array<{ name: string; detail: string }>
    baselines: Array<{ name: string; detail: string }>
    metrics: Array<{ name: string; detail: string }>
    findings: Array<{ finding_type: string; content: string }>
    conclusion_assessments: Array<{ conclusion: string; support_status: string; reason: string }>
    reproducibility: {
      code_availability: string
      data_availability: string
      missing_information: string[]
    }
  } | null
  reproducibility_summary?: Record<string, unknown> | null
  qa_response?: {
    question: string
    answer: string
    answer_status: string
    evidence_ids: string[]
  } | null
  core_reliability?: {
    records: Array<{
      item_id: string
      item_type: string
      status: string
      source: string
      final_content?: string | null
      review_candidate_content?: string | null
      reason: string
    }>
  }
}

export interface ResearchTask {
  id: string
  title: string
  prompt: string
  status: string
  progress: number
  current_step?: string
  output: ResearchTaskOutput
  error?: string
  error_detail?: { kind?: string; message?: string; diagnostic?: string } | null
  created_at?: string
  started_at?: string
  finished_at?: string
}

export interface KnowledgeBase {
  id: string
  name: string
  documents: number
  datasets: number
  tags: string[]
  updatedAt: string
}
