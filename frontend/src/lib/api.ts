import type {
  AdversarialLabReport,
  AdversarialLabRequest,
  AnalyticsSummary,
  AuthSession,
  BenchmarkSummary,
  ChatMessageRecord,
  DashboardSnapshot,
  DoraSreDashboard,
  FeedbackLearningSnapshot,
  FeedbackRecord,
  Incident,
  IncidentRun,
  JobRecord,
  MemoryEntry,
  ModelRoute,
  PromptProfile,
  RemediationReceipt,
  RunComparison,
  ServiceGraphContext,
  ServiceGraphEdge,
  ServiceGraphNode,
  SyntheticGenerationRequest,
  TicketRecord,
  OptimizerRecommendation,
} from "@/lib/types";

function resolveApiBase(): string {
  const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  if (configuredBase) {
    if (configuredBase.startsWith("/") && typeof window !== "undefined") {
      return `${window.location.origin}${configuredBase}`;
    }
    return configuredBase;
  }

  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "https" : "http";
    return `${protocol}://${window.location.hostname}:8000`;
  }

  return "http://127.0.0.1:8000";
}

function buildHeaders(init?: RequestInit): HeadersInit {
  const token = process.env.NEXT_PUBLIC_API_TOKEN;
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init?.headers ?? {}),
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${resolveApiBase()}${path}`, {
    cache: "no-store",
    ...init,
    headers: buildHeaders(init),
  });

  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function loadSnapshot(): Promise<DashboardSnapshot> {
  const [incidents, runs, tickets, messages, benchmarks, profiles, modelRoutes, memory, feedback, remediations, jobs, analytics, doraSre, auth, optimizerRecommendation, feedbackSummary, serviceGraph] = await Promise.all([
    requestJson<Incident[]>("/incidents"),
    requestJson<IncidentRun[]>("/runs"),
    requestJson<TicketRecord[]>("/tickets"),
    requestJson<ChatMessageRecord[]>("/messages"),
    requestJson<BenchmarkSummary[]>("/benchmarks/history"),
    requestJson<PromptProfile[]>("/profiles"),
    requestJson<ModelRoute[]>("/model-routes"),
    requestJson<MemoryEntry[]>("/memory"),
    requestJson<FeedbackRecord[]>("/feedback"),
    requestJson<RemediationReceipt[]>("/remediations"),
    requestJson<JobRecord[]>("/jobs"),
    requestJson<AnalyticsSummary>("/analytics/summary"),
    requestJson<DoraSreDashboard>("/analytics/dora-sre"),
    requestJson<AuthSession>("/auth/me"),
    requestJson<OptimizerRecommendation>("/optimizer/recommendation"),
    requestJson<FeedbackLearningSnapshot>("/feedback/summary"),
    requestJson<{ nodes: ServiceGraphNode[]; edges: ServiceGraphEdge[] } | ServiceGraphContext>("/service-graph"),
  ]);

  return {
    incidents,
    runs,
    tickets,
    messages,
    benchmarks,
    profiles,
    modelRoutes,
    memory,
    feedback,
    remediations,
    jobs,
    analytics,
    doraSre,
    auth,
    optimizerRecommendation,
    feedbackSummary,
    serviceGraph,
  };
}

export function getApiBase(): string {
  return resolveApiBase();
}

export async function createSampleIncident(autoProcess = false): Promise<Incident> {
  const query = autoProcess ? "?auto_process=true" : "";
  return requestJson<Incident>(`/incidents/sample${query}`, {
    method: "POST",
  });
}

export async function processIncident(
  incidentId: string,
  promptProfile?: string,
  modelRouteId?: string,
  options?: { background?: boolean },
): Promise<IncidentRun | JobRecord> {
  const params = new URLSearchParams();
  if (promptProfile) {
    params.set("prompt_profile", promptProfile);
  }
  if (modelRouteId) {
    params.set("model_route_id", modelRouteId);
  }
  if (options?.background) {
    params.set("background", "true");
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return requestJson<IncidentRun | JobRecord>(`/incidents/${incidentId}/process${query}`, {
    method: "POST",
  });
}

export async function approveIncident(incidentId: string, comment: string): Promise<IncidentRun> {
  return requestJson<IncidentRun>(`/incidents/${incidentId}/approval`, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      approver: "operator-console",
      comment: comment || "Approved from the command deck.",
    }),
  });
}

export async function runSampleBenchmarks(background = false): Promise<BenchmarkSummary[] | JobRecord[]> {
  const query = background ? "?background=true" : "";
  return requestJson<BenchmarkSummary[] | JobRecord[]>(`/benchmarks/sample/run${query}`, {
    method: "POST",
  });
}

export async function createSyntheticIncidents(
  payload: SyntheticGenerationRequest,
  persist = true,
): Promise<Incident[]> {
  const query = persist ? "?persist=true" : "?persist=false";
  return requestJson<Incident[]>(`/synthetic/incidents${query}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function runAdversarialLab(payload: AdversarialLabRequest): Promise<AdversarialLabReport> {
  return requestJson<AdversarialLabReport>("/lab/adversarial/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function compareRuns(leftRunId: string, rightRunId: string): Promise<RunComparison> {
  const params = new URLSearchParams({ left_run_id: leftRunId, right_run_id: rightRunId });
  return requestJson<RunComparison>(`/runs/compare?${params.toString()}`);
}

export function getPostmortemUrl(runId: string): string {
  return `${resolveApiBase()}/runs/${runId}/postmortem.md`;
}
