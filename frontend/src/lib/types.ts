export type Incident = {
  id: string;
  title: string;
  service: string;
  environment: string;
  severity: "low" | "medium" | "high" | "critical";
  description: string;
  source: string;
  status: string;
  created_at: string;
  updated_at: string;
  metrics?: Record<string, number>;
  labels?: string[];
  context?: Record<string, unknown>;
  artifacts?: IncidentArtifact[];
};

export type IncidentArtifact = {
  id: string;
  kind: "grafana_screenshot" | "architecture_diagram" | "trace_visualization" | "dashboard_export" | "other";
  title: string;
  description: string;
  content_type: string;
  source_ref?: string | null;
  extracted_text: string;
  annotations: string[];
  related_services: string[];
  metadata?: Record<string, unknown>;
  created_at: string;
};

export type ToolAction = {
  id: string;
  name: string;
  description: string;
  command: string;
  risk_level: string;
  status: string;
  policy_verdict?: string | null;
  policy_reasons?: string[];
  metadata?: Record<string, unknown>;
};

export type InvestigationResult = {
  run_id: string;
  summary: string;
  suspected_root_cause: string;
  confidence: number;
  evidence: string[];
  agent_notes: Record<string, string>;
  recommended_actions: ToolAction[];
  prompt_profile: string;
  used_autogen: boolean;
  model_route_id?: string | null;
  agent_models?: Record<string, string>;
  audience_summaries?: {
    operator: string;
    executive: string;
    engineering: string;
    postmortem: string;
  };
  remediation_review?: {
    risk_summary: string;
    safe_actions: string[];
    risky_actions: string[];
    reviewer_score: number;
  } | null;
  memory_abstractions?: {
    lessons: string[];
    remediation_patterns: string[];
    service_playbook?: string | null;
    embedding_vector?: number[];
  } | null;
  causal_analysis?: {
    likely_root_service?: string | null;
    likely_cause: string;
    confidence: number;
    reasoning: string[];
    impacted_services: string[];
    propagation_path: string[];
    graph_nodes: string[];
    graph_edges: { source: string; target: string }[];
    release_correlation: boolean;
    graph_source?: string;
    knowledge_graph_paths?: ServiceGraphPath[];
  } | null;
  multimodal_analysis?: MultimodalAnalysis | null;
  feedback_learning?: FeedbackLearningSnapshot | null;
  service_graph_context?: ServiceGraphContext | null;
  reflection_notes?: string[];
  revision_count?: number;
  runbook_excerpt?: string | null;
  release_assessment?: string | null;
  transcript: { source: string; content: string }[];
};

export type EventMessage = {
  timestamp: string;
  stage: string;
  message: string;
  trace_id?: string | null;
  job_id?: string | null;
  payload?: Record<string, unknown>;
};

export type PolicyDecision = {
  action_id: string;
  verdict: string;
  approved: boolean;
  requires_human_approval: boolean;
  reasons: string[];
  policy_rule?: string | null;
};

export type IncidentRun = {
  id: string;
  incident_id: string;
  started_at: string;
  finished_at?: string | null;
  status: string;
  trace_id?: string | null;
  job_id?: string | null;
  investigation?: InvestigationResult | null;
  causal_analysis?: InvestigationResult["causal_analysis"] | null;
  multimodal_analysis?: MultimodalAnalysis | null;
  feedback_learning?: FeedbackLearningSnapshot | null;
  service_graph_context?: ServiceGraphContext | null;
  remediation_notes: string[];
  event_log: EventMessage[];
  policy_decisions: PolicyDecision[];
  approval_notes: string[];
  postmortem_summary?: string | null;
  connector_status?: Record<string, unknown>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number;
    duration_ms: number;
  } | null;
  evaluation?: {
    score: number;
    heuristic_score?: number;
    judge_score?: number | null;
    judge_used_llm?: boolean;
    judge_rationale?: string | null;
    sub_scores?: Record<string, number>;
    findings: string[];
    latency_ms: number;
    prompt_profile: string;
    estimated_cost_usd: number;
    action_accuracy?: number;
    failure_categories: string[];
  } | null;
};

export type BenchmarkSummary = {
  id: string;
  profile_id: string;
  model_route_id?: string | null;
  total_cases: number;
  average_score: number;
  root_cause_match_rate: number;
  average_latency_ms: number;
  average_cost_usd: number;
  average_confidence?: number;
  average_judge_score?: number;
  approval_rate?: number;
  executed_action_rate?: number;
  action_accuracy_rate?: number;
  failure_category_counts: Record<string, number>;
  score_delta?: number | null;
  match_rate_delta?: number | null;
  created_at: string;
  details?: Record<string, unknown>[];
};

export type TicketRecord = {
  id: string;
  incident_id: string;
  summary: string;
  severity: string;
  status: string;
  created_at: string;
  external_key?: string | null;
  external_url?: string | null;
};

export type ChatMessageRecord = {
  id: string;
  incident_id: string;
  channel: string;
  message: string;
  created_at: string;
  external_id?: string | null;
};

export type PromptProfile = {
  id: string;
  label: string;
  description: string;
};

export type ModelRoute = {
  id: string;
  label: string;
  description: string;
  planner_model: string;
  investigator_model: string;
  critic_model: string;
  commander_model: string;
};

export type MemoryEntry = {
  id: string;
  incident_id: string;
  title: string;
  service: string;
  environment: string;
  severity: string;
  summary: string;
  root_cause: string;
  action_names: string[];
  postmortem_summary?: string | null;
  symptom_tokens?: string[];
  resolution_quality?: number;
  abstract_lessons?: string[];
  remediation_patterns?: string[];
  service_playbook?: string | null;
  embedding_vector?: number[];
  feedback_score?: number;
  preferred_actions?: string[];
  discouraged_actions?: string[];
  correction_count?: number;
  updated_at: string;
};

export type FeedbackRecord = {
  id: string;
  incident_id: string;
  run_id: string;
  service: string;
  environment: string;
  kind: "approval" | "override" | "correction" | "rating";
  operator: string;
  approved?: boolean | null;
  comment: string;
  corrected_root_cause?: string | null;
  corrected_summary?: string | null;
  accepted_actions: string[];
  rejected_actions: string[];
  reinforcement_score: number;
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type FeedbackLearningSnapshot = {
  generated_at: string;
  service?: string | null;
  guidance: string[];
  preferred_actions: string[];
  discouraged_actions: string[];
  corrected_root_causes: string[];
  escalation_patterns: string[];
  confidence: number;
};

export type MultimodalFinding = {
  artifact_id: string;
  artifact_kind: IncidentArtifact["kind"];
  summary: string;
  visual_signals: string[];
  implicated_services: string[];
  confidence: number;
  evidence: string[];
};

export type MultimodalAnalysis = {
  summary: string;
  findings: MultimodalFinding[];
  visual_signals: string[];
  implicated_services: string[];
  evidence: string[];
  confidence: number;
  generated_with_vision: boolean;
};

export type ServiceGraphPath = {
  nodes: string[];
  relations: string[];
  explanation: string;
  score: number;
};

export type ServiceGraphNode = {
  id: string;
  node_type: string;
  owner?: string | null;
  tier?: string | null;
  criticality?: string | null;
  tags: string[];
  metadata?: Record<string, unknown>;
};

export type ServiceGraphEdge = {
  source: string;
  target: string;
  relation: string;
  weight: number;
  metadata?: Record<string, unknown>;
};

export type ServiceGraphContext = {
  focal_service: string;
  summary: string;
  upstream_services: string[];
  downstream_services: string[];
  critical_dependencies: string[];
  blast_radius: string[];
  candidate_paths: ServiceGraphPath[];
  graph_nodes: ServiceGraphNode[];
  graph_edges: ServiceGraphEdge[];
  confidence: number;
};

export type SyntheticGenerationRequest = {
  count: number;
  difficulty: string;
  include_noise: boolean;
  include_false_positives: boolean;
  include_incomplete_evidence: boolean;
  services: string[];
};

export type AdversarialLabRequest = {
  count: number;
  difficulty: string;
  profile_ids: string[];
  model_route_ids: string[];
  scenario_mix: string[];
  include_noise: boolean;
  include_false_positives: boolean;
  include_incomplete_evidence: boolean;
  services: string[];
};

export type OptimizerRecommendation = {
  generated_at: string;
  recommended_profile_id: string;
  recommended_model_route_id: string;
  rationale: string;
  expected_improvements: string[];
  prompt_adjustments: string[];
  memory_strategy: string[];
  optimizer_actions: string[];
  top_failure_categories: string[];
  confidence: number;
};

export type AdversarialLabReport = {
  generated_at: string;
  total_cases: number;
  scenario_counts: Record<string, number>;
  weakness_counts: Record<string, number>;
  summaries: BenchmarkSummary[];
  recommended_focus_areas: string[];
  optimizer_recommendation?: OptimizerRecommendation | null;
};

export type RemediationReceipt = {
  id: string;
  action_id: string;
  action_name: string;
  incident_id: string;
  status: string;
  executed_at: string;
  message: string;
  service?: string | null;
  environment?: string | null;
  requested_by?: string | null;
  provider?: string | null;
  external_reference?: string | null;
  metadata?: Record<string, unknown>;
};

export type JobRecord = {
  id: string;
  kind: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  trace_id?: string | null;
  subject_id?: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error?: string | null;
};

export type AuthSession = {
  role: string;
  subject: string;
  demo_mode: boolean;
};

export type RunComparison = {
  left_run_id: string;
  right_run_id: string;
  incident_id?: string | null;
  score_delta?: number | null;
  latency_delta_ms?: number | null;
  cost_delta_usd?: number | null;
  root_cause_changed: boolean;
  action_diff: Record<string, string[]>;
  failure_category_diff: Record<string, string[]>;
  summary: string;
};

export type AnalyticsSummary = {
  generated_at: string;
  incident_count: number;
  run_count: number;
  queued_jobs: number;
  top_root_causes: { root_cause: string; count: number }[];
  top_failure_categories: { category: string; count: number }[];
  action_outcomes: Record<string, number>;
  profile_win_rates: { profile: string; average_score: number; runs: number }[];
};

export type DashboardSnapshot = {
  incidents: Incident[];
  runs: IncidentRun[];
  tickets: TicketRecord[];
  messages: ChatMessageRecord[];
  benchmarks: BenchmarkSummary[];
  profiles: PromptProfile[];
  modelRoutes: ModelRoute[];
  memory: MemoryEntry[];
  feedback: FeedbackRecord[];
  remediations: RemediationReceipt[];
  jobs: JobRecord[];
  analytics: AnalyticsSummary | null;
  auth: AuthSession | null;
  optimizerRecommendation?: OptimizerRecommendation | null;
  feedbackSummary?: FeedbackLearningSnapshot | null;
  serviceGraph?: { nodes: ServiceGraphNode[]; edges: ServiceGraphEdge[] } | ServiceGraphContext | null;
};
