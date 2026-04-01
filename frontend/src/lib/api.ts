import type {
  BenchmarkSummary,
  ChatMessageRecord,
  DashboardSnapshot,
  Incident,
  IncidentRun,
  PromptProfile,
  TicketRecord,
} from "@/lib/types";

function resolveApiBase(): string {
  const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  if (configuredBase) {
    return configuredBase;
  }

  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "https" : "http";
    return `${protocol}://${window.location.hostname}:8000`;
  }

  return "http://127.0.0.1:8000";
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${resolveApiBase()}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function loadSnapshot(): Promise<DashboardSnapshot> {
  const [incidents, runs, tickets, messages, benchmarks, profiles] = await Promise.all([
    requestJson<Incident[]>("/incidents"),
    requestJson<IncidentRun[]>("/runs"),
    requestJson<TicketRecord[]>("/tickets"),
    requestJson<ChatMessageRecord[]>("/messages"),
    requestJson<BenchmarkSummary[]>("/benchmarks/history"),
    requestJson<PromptProfile[]>("/profiles"),
  ]);

  return {
    incidents,
    runs,
    tickets,
    messages,
    benchmarks,
    profiles,
  };
}

export function getApiBase(): string {
  return resolveApiBase();
}

export async function createSampleIncident(): Promise<Incident> {
  return requestJson<Incident>("/incidents/sample", {
    method: "POST",
  });
}

export async function processIncident(incidentId: string, promptProfile?: string): Promise<IncidentRun> {
  const query = promptProfile ? `?prompt_profile=${encodeURIComponent(promptProfile)}` : "";
  return requestJson<IncidentRun>(`/incidents/${incidentId}/process${query}`, {
    method: "POST",
  });
}

export async function approveIncident(incidentId: string): Promise<IncidentRun> {
  return requestJson<IncidentRun>(`/incidents/${incidentId}/approval`, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      approver: "operator-console",
      comment: "Approved from the command deck.",
    }),
  });
}

export async function runSampleBenchmarks(): Promise<BenchmarkSummary[]> {
  return requestJson<BenchmarkSummary[]>("/benchmarks/sample/run", {
    method: "POST",
  });
}
