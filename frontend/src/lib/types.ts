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
};

export type ToolAction = {
  id: string;
  name: string;
  description: string;
  command: string;
  risk_level: string;
  status: string;
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
};

export type EventMessage = {
  timestamp: string;
  stage: string;
  message: string;
  payload?: Record<string, unknown>;
};

export type IncidentRun = {
  id: string;
  incident_id: string;
  started_at: string;
  finished_at?: string | null;
  status: string;
  investigation?: InvestigationResult | null;
  remediation_notes: string[];
  event_log: EventMessage[];
  evaluation?: {
    score: number;
    findings: string[];
    latency_ms: number;
    prompt_profile: string;
  } | null;
};

export type BenchmarkSummary = {
  id: string;
  profile_id: string;
  total_cases: number;
  average_score: number;
  root_cause_match_rate: number;
  created_at: string;
};

export type TicketRecord = {
  id: string;
  incident_id: string;
  summary: string;
  severity: string;
  status: string;
  created_at: string;
};

export type ChatMessageRecord = {
  id: string;
  incident_id: string;
  channel: string;
  message: string;
  created_at: string;
};

export type PromptProfile = {
  id: string;
  label: string;
  description: string;
};

export type DashboardSnapshot = {
  incidents: Incident[];
  runs: IncidentRun[];
  tickets: TicketRecord[];
  messages: ChatMessageRecord[];
  benchmarks: BenchmarkSummary[];
  profiles: PromptProfile[];
};
