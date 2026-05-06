"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import {
  Activity,
  BellRing,
  Bot,
  BriefcaseBusiness,
  FlaskConical,
  Gauge,
  Play,
  Plus,
  Radar,
  RefreshCw,
  ShieldCheck,
  Siren,
  Wrench,
} from "lucide-react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";

import { Panel } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import {
  approveIncident,
  compareRuns,
  createSyntheticIncidents,
  createSampleIncident,
  getApiBase,
  getPostmortemUrl,
  loadSnapshot,
  processIncident,
  runAdversarialLab,
  runSampleBenchmarks,
} from "@/lib/api";
import type {
  AnalyticsSummary,
  AdversarialLabReport,
  BenchmarkSummary,
  ChatMessageRecord,
  DashboardSnapshot,
  EventMessage,
  IncidentRun,
  JobRecord,
  MemoryEntry,
  ModelRoute,
  PromptProfile,
  RemediationReceipt,
  RunComparison,
  TicketRecord,
} from "@/lib/types";

function emptySnapshot(): DashboardSnapshot {
  return {
    incidents: [],
    runs: [],
    tickets: [],
    messages: [],
    benchmarks: [],
    profiles: [],
    modelRoutes: [],
    memory: [],
    feedback: [],
    remediations: [],
    jobs: [],
    analytics: null,
    auth: null,
    optimizerRecommendation: null,
    feedbackSummary: null,
    serviceGraph: null,
  };
}

function severityTone(severity: string) {
  switch (severity) {
    case "critical":
      return "critical";
    case "high":
      return "high";
    case "medium":
      return "medium";
    default:
      return "low";
  }
}

function statusTone(status: string) {
  if (status.includes("fail")) {
    return "critical";
  }
  if (status.includes("approval")) {
    return "medium";
  }
  if (status.includes("resolved") || status.includes("completed")) {
    return "success";
  }
  return "low";
}

function formatTime(value?: string | null) {
  if (!value) {
    return "Pending";
  }
  return new Date(value).toLocaleString();
}

type Notice = {
  tone: "success" | "info";
  message: string;
};

type IncidentConsoleProps = {
  initialIncidentId?: string | null;
  detailMode?: boolean;
};

export function IncidentConsole({ initialIncidentId = null, detailMode = false }: IncidentConsoleProps) {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>(emptySnapshot);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(initialIncidentId);
  const [liveFeed, setLiveFeed] = useState<EventMessage[]>([]);
  const [isRefreshing, setIsRefreshing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<string>("balanced-v1");
  const [selectedModelRoute, setSelectedModelRoute] = useState<string>("balanced-route");
  const [approvalComment, setApprovalComment] = useState<string>("");
  const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [labReport, setLabReport] = useState<AdversarialLabReport | null>(null);

  const deferredIncidentId = useDeferredValue(selectedIncidentId);
  const selectedIncident =
    snapshot.incidents.find((incident) => incident.id === deferredIncidentId) ??
    snapshot.incidents.find((incident) => incident.id === initialIncidentId) ??
    snapshot.incidents[0] ??
    null;
  const selectedIncidentRuns = useMemo(
    () =>
      selectedIncident?.id
        ? snapshot.runs
            .filter((run) => run.incident_id === selectedIncident.id)
            .sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at))
        : [],
    [selectedIncident?.id, snapshot.runs],
  );
  const selectedRun = selectedIncidentRuns[0] ?? null;
  const previousRun = selectedIncidentRuns[1] ?? null;
  const queuedJobs = useMemo(
    () => snapshot.jobs.filter((job) => job.status === "queued" || job.status === "running"),
    [snapshot.jobs],
  );
  const analytics = snapshot.analytics;
  const optimizerRecommendation = snapshot.optimizerRecommendation;
  const isViewer = snapshot.auth?.role === "viewer";

  async function refreshSnapshot(focusIncidentId?: string) {
    setIsRefreshing(true);
    try {
      const nextSnapshot = await loadSnapshot();
      setSnapshot(nextSnapshot);
      setError(null);

      const nextFocusId =
        focusIncidentId ??
        initialIncidentId ??
        (selectedIncidentId && nextSnapshot.incidents.some((incident) => incident.id === selectedIncidentId)
          ? selectedIncidentId
          : nextSnapshot.incidents[0]?.id);

      if (nextFocusId) {
        startTransition(() => setSelectedIncidentId(nextFocusId));
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load control deck.");
    } finally {
      setIsRefreshing(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      if (cancelled) {
        return;
      }
      await refreshSnapshot(initialIncidentId ?? undefined);
    }

    refresh();
    const interval = window.setInterval(refresh, 6000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [initialIncidentId, selectedIncidentId]);

  useEffect(() => {
    if (!selectedRun?.investigation?.prompt_profile) {
      return;
    }
    setSelectedProfile(selectedRun.investigation.prompt_profile);
  }, [selectedRun?.investigation?.prompt_profile]);

  useEffect(() => {
    if (selectedRun?.investigation?.model_route_id) {
      setSelectedModelRoute(selectedRun.investigation.model_route_id);
      return;
    }
    if (!snapshot.modelRoutes.length) {
      return;
    }
    if (!snapshot.modelRoutes.some((route) => route.id === selectedModelRoute)) {
      setSelectedModelRoute(snapshot.modelRoutes[0].id);
    }
  }, [selectedModelRoute, selectedRun?.investigation?.model_route_id, snapshot.modelRoutes]);

  useEffect(() => {
    let cancelled = false;

    async function loadComparison() {
      if (!selectedRun || !previousRun) {
        setComparison(null);
        return;
      }
      try {
        const result = await compareRuns(previousRun.id, selectedRun.id);
        if (!cancelled) {
          setComparison(result);
        }
      } catch {
        if (!cancelled) {
          setComparison(null);
        }
      }
    }

    loadComparison();
    return () => {
      cancelled = true;
    };
  }, [previousRun?.id, selectedRun?.id]);

  useEffect(() => {
    if (!selectedIncident?.id) {
      return;
    }

    const wsUrl = `${getApiBase().replace(/^http/, "ws")}/ws/incidents/${selectedIncident.id}`;
    const socket = new WebSocket(wsUrl);
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as EventMessage;
      setLiveFeed((current) => [payload, ...current].slice(0, 18));
    };
    socket.onerror = () => {
      socket.close();
    };
    return () => {
      socket.close();
    };
  }, [selectedIncident?.id]);

  const counts = { low: 0, medium: 0, high: 0, critical: 0 };
  snapshot.incidents.forEach((incident) => {
    counts[incident.severity] += 1;
  });
  const severityChart = Object.entries(counts).map(([name, value]) => ({ name, value }));

  const benchmarkChart = snapshot.benchmarks
    .slice()
    .reverse()
    .slice(0, 6)
    .map((item: BenchmarkSummary) => ({
      profile: `${item.profile_id.replace("-v1", "")} / ${(item.model_route_id ?? "default").replace("-route", "")}`,
      score: Number(item.average_score.toFixed(1)),
      match: Number((item.root_cause_match_rate * 100).toFixed(0)),
      confidence: Number(((item.average_confidence ?? 0) * 100).toFixed(0)),
    }));
  const bestBenchmark = [...snapshot.benchmarks].sort((left, right) => right.average_score - left.average_score)[0];
  const averageBenchmarkMatch = snapshot.benchmarks.length
    ? Math.round(
        (snapshot.benchmarks.reduce((total, item) => total + item.root_cause_match_rate, 0) / snapshot.benchmarks.length) *
          100,
      )
    : 0;

  const openIncidents = snapshot.incidents.filter((incident) => incident.status !== "resolved").length;
  const awaitingApproval = snapshot.incidents.filter((incident) => incident.status === "awaiting_approval").length;
  const automationCount = snapshot.runs.reduce(
    (total, run) => total + run.remediation_notes.filter((note) => note.startsWith("Executed")).length,
    0,
  );

  const selectedMemory = useMemo(
    () => snapshot.memory.filter((entry) => entry.service === selectedIncident?.service).slice(0, 4),
    [selectedIncident?.service, snapshot.memory],
  );
  const selectedRemediations = useMemo(
    () => snapshot.remediations.filter((entry) => entry.incident_id === selectedIncident?.id).slice(0, 6),
    [selectedIncident?.id, snapshot.remediations],
  );
  const recentBenchmarks = snapshot.benchmarks.slice(0, 5);
  const currentModelRoute = snapshot.modelRoutes.find((route) => route.id === selectedModelRoute) ?? snapshot.modelRoutes[0] ?? null;

  async function performAction<T>(
    actionKey: string,
    action: () => Promise<T>,
    onSuccess: (result: T) => Promise<void>,
    successMessage: string,
  ) {
    setPendingAction(actionKey);
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      await onSuccess(result);
      setNotice({ tone: "success", message: successMessage });
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Operator action failed.");
    } finally {
      setPendingAction(null);
    }
  }

  async function handleCreateSample(autoProcess = false) {
    await performAction(
      autoProcess ? "create-process" : "create-sample",
      () => createSampleIncident(autoProcess),
      async (incident) => {
        await refreshSnapshot(incident.id);
      },
      autoProcess ? "Sample incident created and investigated." : "Sample incident injected into the queue.",
    );
  }

  async function handleCreateSyntheticPack() {
    await performAction(
      "create-synthetic-pack",
      () =>
        createSyntheticIncidents({
          count: 4,
          difficulty: "hard",
          include_noise: true,
          include_false_positives: true,
          include_incomplete_evidence: true,
          services: [],
        }),
      async (incidents) => {
        await refreshSnapshot(incidents[0]?.id);
      },
      "Synthetic incident pack generated for harder agent evaluation.",
    );
  }

  async function handleProcessSelectedIncident(background = false) {
    if (!selectedIncident) {
      return;
    }
    await performAction(
      background ? "queue-incident" : "process-incident",
      () => processIncident(selectedIncident.id, selectedProfile, selectedModelRoute, { background }),
      async () => {
        await refreshSnapshot(selectedIncident.id);
      },
      background
        ? "Incident job queued for background execution."
        : selectedRun
          ? "Incident workflow re-ran successfully."
          : "Incident workflow started successfully.",
    );
  }

  async function handleApproveSelectedIncident() {
    if (!selectedIncident) {
      return;
    }
    await performAction(
      "approve-incident",
      () => approveIncident(selectedIncident.id, approvalComment),
      async () => {
        setApprovalComment("");
        await refreshSnapshot(selectedIncident.id);
      },
      "Approval granted and remediation continued.",
    );
  }

  async function handleRunSampleBenchmarks(background = false) {
    await performAction(
      background ? "queue-benchmarks" : "run-benchmarks",
      () => runSampleBenchmarks(background),
      async () => {
        await refreshSnapshot(selectedIncident?.id ?? undefined);
      },
      background
        ? "Benchmark pack queued for background execution."
        : "Sample benchmarks completed and dashboard metrics updated.",
    );
  }

  async function handleRunAdversarialLab() {
    await performAction(
      "run-adversarial-lab",
      () =>
        runAdversarialLab({
          count: 6,
          difficulty: "hard",
          profile_ids: [selectedProfile],
          model_route_ids: [selectedModelRoute],
          scenario_mix: [],
          include_noise: true,
          include_false_positives: true,
          include_incomplete_evidence: true,
          services: selectedIncident?.service ? [selectedIncident.service] : [],
        }),
      async (report) => {
        setLabReport(report);
        await refreshSnapshot(selectedIncident?.id ?? undefined);
      },
      "Adversarial evaluation lab completed and optimizer recommendations refreshed.",
    );
  }

  const canApprove = selectedIncident?.status === "awaiting_approval";
  const processLabel = selectedRun ? "Re-run workflow" : "Run workflow";
  const policySummary = selectedRun?.policy_decisions ?? [];

  return (
    <main className="console-shell">
      <div className="console-backdrop" />
      <section className="hero">
        <motion.div
          className="hero-copy"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45 }}
        >
          <p className="kicker">{detailMode ? "Incident Detail" : "AI Incident Commander"}</p>
          <h1>{detailMode ? "Review the incident in context, not in isolation." : "Build the command deck, not a generated-looking dashboard."}</h1>
          <p className="hero-text">
            This console blends live triage, LangGraph orchestration, AutoGen investigation, benchmarking, policy
            governance, and remediation history into one operator workflow.
          </p>
          <div className="benchmark-strip">
            <div className="benchmark-chip">
              <span>Session role</span>
              <strong>{snapshot.auth?.role ?? "viewer"}</strong>
            </div>
            <div className="benchmark-chip">
              <span>Queued jobs</span>
              <strong>{queuedJobs.length}</strong>
            </div>
            {selectedRun ? (
              <a className="ghost-link" href={getPostmortemUrl(selectedRun.id)} target="_blank" rel="noreferrer">
                Export postmortem
              </a>
            ) : null}
          </div>
        </motion.div>

        <motion.div
          className="hero-metrics"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.08 }}
        >
          <article className="hero-card">
            <Siren />
            <div>
              <span>Open incidents</span>
              <strong>{openIncidents}</strong>
            </div>
          </article>
          <article className="hero-card">
            <ShieldCheck />
            <div>
              <span>Awaiting approval</span>
              <strong>{awaitingApproval}</strong>
            </div>
          </article>
          <article className="hero-card">
            <Radar />
            <div>
              <span>Automations executed</span>
              <strong>{automationCount}</strong>
            </div>
          </article>
          <article className="hero-card accent">
            <Gauge />
            <div>
              <span>Refresh cycle</span>
              <strong>{isRefreshing ? "Syncing" : "Live"}</strong>
            </div>
          </article>
        </motion.div>
      </section>

      {error ? <div className="console-alert">API connection issue: {error}</div> : null}
      {notice ? <div className={`console-notice console-notice-${notice.tone}`}>{notice.message}</div> : null}

      <section className="console-grid">
        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.12 }}>
          <Panel
            eyebrow="Live queue"
            title="Incident queue"
            action={
              <div className="button-row">
                <button
                  className="primary-button"
                  type="button"
                  disabled={isViewer || pendingAction === "create-sample" || pendingAction === "create-process"}
                  onClick={() => handleCreateSample(false)}
                >
                  <Plus size={16} />
                  New sample
                </button>
                <button className="ghost-button" type="button" onClick={() => refreshSnapshot(selectedIncident?.id ?? undefined)}>
                  <RefreshCw size={16} />
                  Refresh
                </button>
                <button
                  className="ghost-button"
                  type="button"
                  disabled={isViewer || pendingAction === "create-synthetic-pack"}
                  onClick={handleCreateSyntheticPack}
                >
                  <Bot size={16} />
                  Synthetic pack
                </button>
              </div>
            }
          >
            <div className="incident-list">
              {snapshot.incidents.length === 0 ? (
                <div className="empty-state">
                  <p>No incidents are active yet.</p>
                  <button
                    className="primary-button"
                    type="button"
                    disabled={isViewer || pendingAction === "create-process"}
                    onClick={() => handleCreateSample(true)}
                  >
                    <Bot size={16} />
                    Inject and investigate
                  </button>
                </div>
              ) : (
                snapshot.incidents.map((incident) => (
                  <article
                    key={incident.id}
                    className={`incident-card ${selectedIncident?.id === incident.id ? "incident-card-active" : ""}`}
                  >
                    <button className="incident-card-trigger" type="button" onClick={() => startTransition(() => setSelectedIncidentId(incident.id))}>
                      <div className="incident-card-topline">
                        <StatusBadge tone={severityTone(incident.severity)}>{incident.severity}</StatusBadge>
                        <StatusBadge tone={statusTone(incident.status)}>{incident.status.replaceAll("_", " ")}</StatusBadge>
                      </div>
                      <h3>{incident.title}</h3>
                      <p>{incident.service}</p>
                      <span>{incident.environment}</span>
                    </button>
                    <div className="incident-card-actions">
                      <Link className="ghost-link" href={`/incidents/${incident.id}`}>
                        Open detail
                      </Link>
                    </div>
                  </article>
                ))
              )}
            </div>
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.16 }}>
          <Panel
            eyebrow="Active investigation"
            title={selectedIncident?.title ?? "No incident selected"}
            action={
              selectedIncident ? (
                <div className="button-column">
                  <select
                    className="control-input"
                    value={selectedProfile}
                    onChange={(event) => setSelectedProfile(event.target.value)}
                    disabled={isViewer}
                  >
                    {snapshot.profiles.map((profile: PromptProfile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.label}
                      </option>
                    ))}
                  </select>
                  <select
                    className="control-input"
                    value={selectedModelRoute}
                    onChange={(event) => setSelectedModelRoute(event.target.value)}
                    disabled={isViewer || !snapshot.modelRoutes.length}
                  >
                    {snapshot.modelRoutes.map((route: ModelRoute) => (
                      <option key={route.id} value={route.id}>
                        {route.label}
                      </option>
                    ))}
                  </select>
                  <div className="button-row">
                    {!isViewer ? (
                      <>
                        <button
                          className="primary-button"
                          type="button"
                          disabled={pendingAction === "process-incident"}
                          onClick={() => handleProcessSelectedIncident(false)}
                        >
                          <Play size={16} />
                          {processLabel}
                        </button>
                        <button
                          className="secondary-button"
                          type="button"
                          disabled={pendingAction === "queue-incident"}
                          onClick={() => handleProcessSelectedIncident(true)}
                        >
                          <Bot size={16} />
                          Queue job
                        </button>
                        {canApprove ? (
                          <button
                            className="secondary-button"
                            type="button"
                            disabled={pendingAction === "approve-incident"}
                            onClick={handleApproveSelectedIncident}
                          >
                            <ShieldCheck size={16} />
                            Approve
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <StatusBadge tone="low">Viewer mode</StatusBadge>
                    )}
                  </div>
                </div>
              ) : null
            }
          >
            {selectedIncident ? (
              <div className="stack">
                <div className="incident-detail-head">
                  <div>
                    <p className="mono-label">Service</p>
                    <strong>{selectedIncident.service}</strong>
                  </div>
                  <div>
                    <p className="mono-label">Source</p>
                    <strong>{selectedIncident.source}</strong>
                  </div>
                  <div>
                    <p className="mono-label">Updated</p>
                    <strong>{formatTime(selectedIncident.updated_at)}</strong>
                  </div>
                </div>

                <p className="incident-description">{selectedIncident.description}</p>

                <div className="metric-chip-row">
                  {Object.entries(selectedIncident.metrics ?? {}).map(([label, value]) => (
                    <div className="metric-chip" key={label}>
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
                </div>

                {canApprove ? (
                  <textarea
                    className="control-input control-textarea"
                    placeholder="Add approval context for the audit trail"
                    value={approvalComment}
                    onChange={(event) => setApprovalComment(event.target.value)}
                  />
                ) : null}

                {selectedRun?.investigation ? (
                  <>
                    <div className="metric-chip-row">
                      <div className="metric-chip">
                        <span>Model route</span>
                        <strong>{selectedRun.investigation.model_route_id ?? currentModelRoute?.id ?? "default"}</strong>
                      </div>
                      <div className="metric-chip">
                        <span>Planner model</span>
                        <strong>{selectedRun.investigation.agent_models?.planner ?? currentModelRoute?.planner_model ?? "n/a"}</strong>
                      </div>
                      <div className="metric-chip">
                        <span>Trace id</span>
                        <strong>{selectedRun.trace_id?.slice(0, 12) ?? "pending"}</strong>
                      </div>
                    </div>

                    <div className="highlight-block">
                      <p className="mono-label">Suspected root cause</p>
                      <h3>{selectedRun.investigation.suspected_root_cause}</h3>
                      <p className="confidence-line">
                        Confidence {Math.round(selectedRun.investigation.confidence * 100)}% using{" "}
                        {selectedRun.investigation.prompt_profile}
                        {selectedRun.investigation.used_autogen ? " with live AutoGen" : " with fallback analysis"}
                      </p>
                    </div>

                    {(selectedRun.investigation.causal_analysis ?? selectedRun.causal_analysis) ? (
                      <div className="highlight-block">
                        <p className="mono-label">Causal reasoning</p>
                        <h3>
                          {(selectedRun.investigation.causal_analysis ?? selectedRun.causal_analysis)?.likely_cause}
                        </h3>
                        <p className="confidence-line">
                          Root service {(selectedRun.investigation.causal_analysis ?? selectedRun.causal_analysis)?.likely_root_service ?? "unknown"}
                          {" | "}
                          Confidence {Math.round(((selectedRun.investigation.causal_analysis ?? selectedRun.causal_analysis)?.confidence ?? 0) * 100)}%
                        </p>
                        <ul>
                          {((selectedRun.investigation.causal_analysis ?? selectedRun.causal_analysis)?.reasoning ?? []).map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {selectedRun.investigation.multimodal_analysis ? (
                      <div className="highlight-block">
                        <p className="mono-label">Multimodal reasoning</p>
                        <h3>{selectedRun.investigation.multimodal_analysis.summary}</h3>
                        <p className="confidence-line">
                          {selectedIncident.artifacts?.length ?? 0} artifacts
                          {" | "}
                          Signals {selectedRun.investigation.multimodal_analysis.visual_signals.join(", ") || "none"}
                        </p>
                        <ul>
                          {selectedRun.investigation.multimodal_analysis.evidence.slice(0, 3).map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {selectedRun.investigation.feedback_learning ? (
                      <div className="highlight-block">
                        <p className="mono-label">Human feedback learning</p>
                        <p>
                          Learned guidance confidence {Math.round((selectedRun.investigation.feedback_learning.confidence ?? 0) * 100)}%
                        </p>
                        <div className="dual-list">
                          <div>
                            <p className="mono-label">Preferred actions</p>
                            <ul>
                              {(selectedRun.investigation.feedback_learning.preferred_actions.length
                                ? selectedRun.investigation.feedback_learning.preferred_actions
                                : ["No strong preference yet."]
                              ).map((item) => (
                                <li key={item}>{item}</li>
                              ))}
                            </ul>
                          </div>
                          <div>
                            <p className="mono-label">Discouraged actions</p>
                            <ul>
                              {(selectedRun.investigation.feedback_learning.discouraged_actions.length
                                ? selectedRun.investigation.feedback_learning.discouraged_actions
                                : ["No discouraged action trend yet."]
                              ).map((item) => (
                                <li key={item}>{item}</li>
                              ))}
                            </ul>
                          </div>
                        </div>
                      </div>
                    ) : null}

                    {selectedRun.investigation.service_graph_context ? (
                      <div className="highlight-block">
                        <p className="mono-label">Service knowledge graph</p>
                        <h3>{selectedRun.investigation.service_graph_context.summary}</h3>
                        <div className="metric-chip-row">
                          <div className="metric-chip">
                            <span>Upstream</span>
                            <strong>{selectedRun.investigation.service_graph_context.upstream_services.length}</strong>
                          </div>
                          <div className="metric-chip">
                            <span>Blast radius</span>
                            <strong>{selectedRun.investigation.service_graph_context.blast_radius.length}</strong>
                          </div>
                          <div className="metric-chip">
                            <span>Critical deps</span>
                            <strong>{selectedRun.investigation.service_graph_context.critical_dependencies.length}</strong>
                          </div>
                        </div>
                      </div>
                    ) : null}

                    {selectedRun.investigation.remediation_review ? (
                      <div className="highlight-block">
                        <p className="mono-label">Remediation review</p>
                        <p>{selectedRun.investigation.remediation_review.risk_summary}</p>
                        <div className="metric-chip-row">
                          <div className="metric-chip">
                            <span>Reviewer score</span>
                            <strong>{Math.round(selectedRun.investigation.remediation_review.reviewer_score * 100)}%</strong>
                          </div>
                          <div className="metric-chip">
                            <span>Safe actions</span>
                            <strong>{selectedRun.investigation.remediation_review.safe_actions.length}</strong>
                          </div>
                          <div className="metric-chip">
                            <span>Risky actions</span>
                            <strong>{selectedRun.investigation.remediation_review.risky_actions.length}</strong>
                          </div>
                        </div>
                      </div>
                    ) : null}

                    <div className="dual-list">
                      <div>
                        <p className="mono-label">Evidence</p>
                        <ul>
                          {selectedRun.investigation.evidence.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <p className="mono-label">Recommended actions</p>
                        <ul>
                          {selectedRun.investigation.recommended_actions.map((action) => (
                            <li key={action.id}>
                              <strong>{action.name}</strong> <span>{action.description}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>

                    {selectedRun.investigation.audience_summaries ? (
                      <div className="dual-list">
                        <div>
                          <p className="mono-label">Operator and executive summaries</p>
                          <ul>
                            <li>{selectedRun.investigation.audience_summaries.operator}</li>
                            <li>{selectedRun.investigation.audience_summaries.executive}</li>
                          </ul>
                        </div>
                        <div>
                          <p className="mono-label">Engineering and postmortem summaries</p>
                          <ul>
                            <li>{selectedRun.investigation.audience_summaries.engineering}</li>
                            <li>{selectedRun.investigation.audience_summaries.postmortem}</li>
                          </ul>
                        </div>
                      </div>
                    ) : null}

                    {policySummary.length ? (
                      <div className="mini-table">
                        {policySummary.map((decision) => (
                          <div className="mini-row" key={decision.action_id}>
                            <div>
                              <strong>{decision.policy_rule ?? decision.action_id}</strong>
                              <span>{decision.reasons.join(" ")}</span>
                            </div>
                            <StatusBadge tone={statusTone(decision.verdict)}>{decision.verdict}</StatusBadge>
                          </div>
                        ))}
                      </div>
                    ) : null}

                    {selectedRun.evaluation ? (
                      <div className="evaluation-card">
                        <div>
                          <p className="mono-label">Latest evaluation</p>
                          <strong>{selectedRun.evaluation.score.toFixed(1)} / 100</strong>
                          <p className="confidence-line">
                            Heuristic {selectedRun.evaluation.heuristic_score?.toFixed(1) ?? "n/a"}
                            {" | "}
                            Judge {selectedRun.evaluation.judge_score?.toFixed(1) ?? "n/a"}
                            {selectedRun.evaluation.judge_used_llm ? " with LLM judge" : " with heuristic judge"}
                          </p>
                        </div>
                        <ul>
                          {selectedRun.evaluation.findings.map((finding) => (
                            <li key={finding}>{finding}</li>
                          ))}
                        </ul>
                        {selectedRun.evaluation.sub_scores ? (
                          <div className="metric-chip-row">
                            {Object.entries(selectedRun.evaluation.sub_scores).map(([label, value]) => (
                              <div className="metric-chip" key={label}>
                                <span>{label.replaceAll("_", " ")}</span>
                                <strong>{value.toFixed(0)}</strong>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : null}

                    {selectedRun.investigation.reflection_notes?.length ? (
                      <div className="highlight-block">
                        <p className="mono-label">Reflection and self-correction</p>
                        <p>{selectedRun.investigation.release_assessment}</p>
                        <ul>
                          {selectedRun.investigation.reflection_notes.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                        <p className="confidence-line">
                          Revision count {selectedRun.investigation.revision_count ?? 0}
                          {" | "}
                          {selectedRun.investigation.runbook_excerpt}
                        </p>
                      </div>
                    ) : null}

                    {comparison ? (
                      <div className="highlight-block">
                        <p className="mono-label">Run comparison</p>
                        <p>{comparison.summary}</p>
                        <div className="metric-chip-row">
                          <div className="metric-chip">
                            <span>Score delta</span>
                            <strong>{comparison.score_delta !== null && comparison.score_delta !== undefined ? `${comparison.score_delta >= 0 ? "+" : ""}${comparison.score_delta.toFixed(1)}` : "n/a"}</strong>
                          </div>
                          <div className="metric-chip">
                            <span>Latency delta</span>
                            <strong>{comparison.latency_delta_ms !== null && comparison.latency_delta_ms !== undefined ? `${comparison.latency_delta_ms} ms` : "n/a"}</strong>
                          </div>
                          <div className="metric-chip">
                            <span>Cost delta</span>
                            <strong>{comparison.cost_delta_usd !== null && comparison.cost_delta_usd !== undefined ? `${comparison.cost_delta_usd >= 0 ? "+" : ""}$${comparison.cost_delta_usd.toFixed(4)}` : "n/a"}</strong>
                          </div>
                        </div>
                        <div className="dual-list">
                          <div>
                            <p className="mono-label">Action diff</p>
                            <ul>
                              {comparison.action_diff.added.length || comparison.action_diff.removed.length ? (
                                <>
                                  {comparison.action_diff.added.map((item) => (
                                    <li key={`action-added-${item}`}>Added: {item}</li>
                                  ))}
                                  {comparison.action_diff.removed.map((item) => (
                                    <li key={`action-removed-${item}`}>Removed: {item}</li>
                                  ))}
                                </>
                              ) : (
                                <li>No action change between the two runs.</li>
                              )}
                            </ul>
                          </div>
                          <div>
                            <p className="mono-label">Failure taxonomy diff</p>
                            <ul>
                              {comparison.failure_category_diff.added.length || comparison.failure_category_diff.removed.length ? (
                                <>
                                  {comparison.failure_category_diff.added.map((item) => (
                                    <li key={`failure-added-${item}`}>Added: {item}</li>
                                  ))}
                                  {comparison.failure_category_diff.removed.map((item) => (
                                    <li key={`failure-removed-${item}`}>Removed: {item}</li>
                                  ))}
                                </>
                              ) : (
                                <li>No taxonomy change between the two runs.</li>
                              )}
                            </ul>
                          </div>
                        </div>
                      </div>
                    ) : null}

                    {selectedRun.postmortem_summary ? (
                      <div className="highlight-block">
                        <p className="mono-label">Postmortem summary</p>
                        <p>{selectedRun.postmortem_summary}</p>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="empty-state">
                    <p>No investigation has been run for this incident yet.</p>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={isViewer || pendingAction === "process-incident"}
                      onClick={() => handleProcessSelectedIncident(false)}
                    >
                      <Play size={16} />
                      Run workflow
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-state">
                <p>Select an incident from the queue to inspect the active run.</p>
                <button
                  className="primary-button"
                  type="button"
                  disabled={pendingAction === "create-process"}
                  onClick={() => handleCreateSample(true)}
                >
                  <Bot size={16} />
                  Inject and investigate
                </button>
              </div>
            )}
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.2 }}>
          <Panel eyebrow="Replay rail" title="Live event stream">
            <div className="activity-stream">
              {(liveFeed.length ? liveFeed : selectedRun?.event_log ?? []).slice(0, 12).map((event) => (
                <div className="event-row" key={`${event.timestamp}-${event.stage}-${event.message}`}>
                  <div className="event-icon">
                    <Activity size={14} />
                  </div>
                  <div>
                    <p className="event-stage">{event.stage}</p>
                    <strong>{event.message}</strong>
                    <span>{formatTime(event.timestamp)}</span>
                  </div>
                </div>
              ))}
              {!liveFeed.length && !(selectedRun?.event_log.length) ? (
                <div className="empty-state">Run the incident workflow to see replayable events here.</div>
              ) : null}
            </div>
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.24 }}>
          <Panel
            eyebrow="Benchmarking"
            title="Profile comparison"
            action={
              <div className="button-row">
                {!isViewer ? (
                  <>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={pendingAction === "run-benchmarks"}
                      onClick={() => handleRunSampleBenchmarks(false)}
                    >
                      <FlaskConical size={16} />
                      Run benchmark
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={pendingAction === "queue-benchmarks"}
                      onClick={() => handleRunSampleBenchmarks(true)}
                    >
                      <Bot size={16} />
                      Queue benchmark
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={pendingAction === "run-adversarial-lab"}
                      onClick={handleRunAdversarialLab}
                    >
                      <Radar size={16} />
                      Adversarial lab
                    </button>
                  </>
                ) : (
                  <StatusBadge tone="low">Viewer mode</StatusBadge>
                )}
              </div>
            }
          >
            {benchmarkChart.length ? (
              <>
                <div className="benchmark-strip">
                  <div className="benchmark-chip">
                    <span>Best profile</span>
                    <strong>{bestBenchmark?.profile_id ?? "n/a"}</strong>
                  </div>
                  <div className="benchmark-chip">
                    <span>Avg match rate</span>
                    <strong>{averageBenchmarkMatch}%</strong>
                  </div>
                  <div className="benchmark-chip">
                    <span>Queued jobs</span>
                    <strong>{queuedJobs.length}</strong>
                  </div>
                  <div className="benchmark-chip">
                    <span>Avg judge score</span>
                    <strong>{bestBenchmark?.average_judge_score?.toFixed(1) ?? "0.0"}</strong>
                  </div>
                </div>
                <div className="chart-wrap">
                  <ResponsiveContainer width="100%" height={260}>
                    <ComposedChart data={benchmarkChart}>
                      <defs>
                        <linearGradient id="scoreGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#117b6b" stopOpacity={0.8} />
                          <stop offset="100%" stopColor="#117b6b" stopOpacity={0.05} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(36, 53, 60, 0.12)" />
                      <XAxis dataKey="profile" tickLine={false} axisLine={false} />
                      <YAxis yAxisId="left" tickLine={false} axisLine={false} />
                      <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} />
                      <Tooltip />
                      <Legend />
                      <Area yAxisId="left" type="monotone" dataKey="score" stroke="#117b6b" fill="url(#scoreGradient)" strokeWidth={2.5} name="Score" />
                      <Line yAxisId="right" type="monotone" dataKey="match" stroke="#c46b36" strokeWidth={2.5} dot={{ r: 4 }} name="Match %" />
                      <Bar yAxisId="right" dataKey="confidence" barSize={14} radius={[8, 8, 0, 0]} fill="rgba(17, 123, 107, 0.28)" name="Confidence %" />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
                <div className="mini-table">
                  {recentBenchmarks.map((item) => (
                    <div className="mini-row" key={item.id}>
                      <div>
                        <strong>{item.profile_id} / {item.model_route_id ?? "default"}</strong>
                        <span>{formatTime(item.created_at)}</span>
                      </div>
                      <span>
                        {item.average_score.toFixed(1)} score | {Math.round((item.average_confidence ?? 0) * 100)}% confidence | {(item.average_judge_score ?? 0).toFixed(1)} judge
                        {item.score_delta !== null && item.score_delta !== undefined ? ` | ${item.score_delta >= 0 ? "+" : ""}${item.score_delta.toFixed(1)}` : ""}
                      </span>
                    </div>
                  ))}
                </div>
                {labReport ? (
                  <div className="highlight-block">
                    <p className="mono-label">Latest adversarial lab</p>
                    <p>
                      Evaluated {labReport.total_cases} hard cases across {Object.keys(labReport.scenario_counts).length} scenario families.
                    </p>
                    <ul>
                      {labReport.recommended_focus_areas.slice(0, 3).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="empty-state">
                <p>No benchmark history yet. Run the sample benchmark pack to compare prompt profiles.</p>
                <button
                  className="primary-button"
                  type="button"
                  disabled={isViewer || pendingAction === "run-benchmarks"}
                  onClick={() => handleRunSampleBenchmarks(false)}
                >
                  <FlaskConical size={16} />
                  Run sample benchmark
                </button>
              </div>
            )}
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.28 }}>
          <Panel eyebrow="Incident mix" title="Severity pressure">
            {snapshot.incidents.length ? (
              <div className="chart-wrap">
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={severityChart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(36, 53, 60, 0.12)" />
                    <XAxis dataKey="name" tickLine={false} axisLine={false} />
                    <YAxis tickLine={false} axisLine={false} />
                    <Tooltip />
                    <Bar dataKey="value" radius={[12, 12, 0, 0]} fill="#c46b36" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="empty-state">
                <p>No incidents available yet. Inject a sample incident to populate severity analytics.</p>
              </div>
            )}
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.32 }}>
          <Panel eyebrow="Operations output" title="Tickets, alerts, and remediations">
            <OutputTables
              tickets={snapshot.tickets}
              messages={snapshot.messages}
              remediations={selectedRemediations}
            />
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.36 }}>
          <Panel eyebrow="Governance" title="Profiles, routes, analytics, and memory">
            <div className="benchmark-strip">
              <div className="benchmark-chip">
                <span>Incidents tracked</span>
                <strong>{analytics?.incident_count ?? snapshot.incidents.length}</strong>
              </div>
              <div className="benchmark-chip">
                <span>Runs tracked</span>
                <strong>{analytics?.run_count ?? snapshot.runs.length}</strong>
              </div>
              <div className="benchmark-chip">
                <span>Queued jobs</span>
                <strong>{analytics?.queued_jobs ?? queuedJobs.length}</strong>
              </div>
            </div>

            {optimizerRecommendation ? (
              <div className="highlight-block">
                <p className="mono-label">Optimizer recommendation</p>
                <h3>
                  {optimizerRecommendation.recommended_profile_id} with {optimizerRecommendation.recommended_model_route_id}
                </h3>
                <p>{optimizerRecommendation.rationale}</p>
                <div className="dual-list">
                  <div>
                    <p className="mono-label">Expected improvements</p>
                    <ul>
                      {optimizerRecommendation.expected_improvements.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <p className="mono-label">Optimizer actions</p>
                    <ul>
                      {optimizerRecommendation.optimizer_actions.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            ) : null}

            <div className="profile-grid">
              {snapshot.profiles.map((profile: PromptProfile) => (
                <article className="profile-card" key={profile.id}>
                  <p className="mono-label">{profile.id}</p>
                  <h3>{profile.label}</h3>
                  <p>{profile.description}</p>
                </article>
              ))}
            </div>

            <div className="output-grid output-grid-triple">
              <div>
                <div className="subhead">
                  <Bot size={16} />
                  Model routes
                </div>
                <div className="mini-table">
                  {snapshot.modelRoutes.map((route: ModelRoute) => (
                    <div className="mini-row" key={route.id}>
                      <div>
                        <strong>{route.label}</strong>
                        <span>{route.planner_model} | {route.investigator_model}</span>
                      </div>
                      <StatusBadge tone={route.id === selectedModelRoute ? "success" : "low"}>
                        {route.id === selectedModelRoute ? "selected" : "available"}
                      </StatusBadge>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="subhead">
                  <Activity size={16} />
                  Background jobs
                </div>
                <div className="mini-table">
                  {queuedJobs.length ? (
                    queuedJobs.slice(0, 6).map((job: JobRecord) => (
                      <div className="mini-row" key={job.id}>
                        <div>
                          <strong>{job.kind.replaceAll("_", " ")}</strong>
                          <span>{job.subject_id ?? "benchmark pack"}</span>
                        </div>
                        <StatusBadge tone={job.status === "running" ? "medium" : "low"}>{job.status}</StatusBadge>
                      </div>
                    ))
                  ) : (
                    <div className="empty-state small">No jobs are queued right now.</div>
                  )}
                </div>
              </div>

              <div>
                <div className="subhead">
                  <FlaskConical size={16} />
                  Failure taxonomy
                </div>
                <div className="mini-table">
                  {analytics?.top_failure_categories?.length ? (
                    analytics.top_failure_categories.map((item: AnalyticsSummary["top_failure_categories"][number]) => (
                      <div className="mini-row" key={item.category}>
                        <div>
                          <strong>{item.category}</strong>
                          <span>Observed in evaluation history</span>
                        </div>
                        <small>{item.count}</small>
                      </div>
                    ))
                  ) : (
                    <div className="empty-state small">Failure categories appear after more evaluated runs.</div>
                  )}
                </div>
              </div>
            </div>

            <div className="output-grid output-grid-triple">
              <div>
                <div className="subhead">
                  <Wrench size={16} />
                  Historical memory
                </div>
                <div className="mini-table">
                  {selectedMemory.length ? (
                    selectedMemory.map((entry: MemoryEntry) => (
                      <div className="mini-row" key={entry.id}>
                        <div>
                          <strong>{entry.title}</strong>
                          <span>{entry.service_playbook ?? entry.root_cause}</span>
                        </div>
                        <small>{formatTime(entry.updated_at)}</small>
                      </div>
                    ))
                  ) : (
                    <div className="empty-state small">No matching historical memory recorded yet.</div>
                  )}
                </div>
              </div>

              <div>
                <div className="subhead">
                  <Radar size={16} />
                  Root causes and profile wins
                </div>
                <div className="mini-table">
                  {analytics?.top_root_causes?.length ? (
                    analytics.top_root_causes.map((item: AnalyticsSummary["top_root_causes"][number]) => (
                      <div className="mini-row" key={item.root_cause}>
                        <div>
                          <strong>{item.root_cause}</strong>
                          <span>Recurring root cause</span>
                        </div>
                        <small>{item.count}</small>
                      </div>
                    ))
                  ) : (
                    <div className="empty-state small">Root cause trends appear after repeated runs.</div>
                  )}
                  {analytics?.profile_win_rates?.slice(0, 3).map((item) => (
                    <div className="mini-row" key={item.profile}>
                      <div>
                        <strong>{item.profile}</strong>
                        <span>{item.runs} benchmark runs</span>
                      </div>
                      <small>{item.average_score.toFixed(1)}</small>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="subhead">
                  <ShieldCheck size={16} />
                  Connector status
                </div>
                <div className="mini-table">
                  {selectedRun?.connector_status ? (
                    Object.entries(selectedRun.connector_status).map(([name, rawValue]) => {
                      const value = rawValue as { configured?: boolean; healthy?: boolean; details?: { mode?: string } };
                      return (
                        <div className="mini-row" key={name}>
                          <div>
                            <strong>{name}</strong>
                            <span>{value.details?.mode ?? "unknown mode"}</span>
                          </div>
                          <StatusBadge tone={value.configured ? "success" : "low"}>
                            {value.configured ? "configured" : "fallback"}
                          </StatusBadge>
                        </div>
                      );
                    })
                  ) : (
                    <div className="empty-state small">Connector status appears after the first run.</div>
                  )}
                </div>
              </div>
            </div>
          </Panel>
        </motion.div>
      </section>
    </main>
  );
}

function OutputTables({
  tickets,
  messages,
  remediations,
}: {
  tickets: TicketRecord[];
  messages: ChatMessageRecord[];
  remediations: RemediationReceipt[];
}) {
  return (
    <div className="output-grid output-grid-triple">
      <div>
        <div className="subhead">
          <BriefcaseBusiness size={16} />
          Tickets
        </div>
        <div className="mini-table">
          {tickets.length ? (
            tickets.slice(0, 6).map((ticket) => (
              <div className="mini-row" key={ticket.id}>
                <div>
                  <strong>{ticket.external_key ?? ticket.summary}</strong>
                  <span>{ticket.external_url ?? formatTime(ticket.created_at)}</span>
                </div>
                <StatusBadge tone={severityTone(ticket.severity)}>{ticket.severity}</StatusBadge>
              </div>
            ))
          ) : (
            <div className="empty-state small">No tickets created yet.</div>
          )}
        </div>
      </div>

      <div>
        <div className="subhead">
          <BellRing size={16} />
          Messages
        </div>
        <div className="mini-table">
          {messages.length ? (
            messages.slice(0, 6).map((message) => (
              <div className="mini-row" key={message.id}>
                <div>
                  <strong>{message.channel}</strong>
                  <span>{message.message}</span>
                </div>
                <small>{formatTime(message.created_at)}</small>
              </div>
            ))
          ) : (
            <div className="empty-state small">No chat notifications recorded yet.</div>
          )}
        </div>
      </div>

      <div>
        <div className="subhead">
          <Wrench size={16} />
          Remediations
        </div>
        <div className="mini-table">
          {remediations.length ? (
            remediations.map((item) => (
              <div className="mini-row" key={item.id}>
                <div>
                  <strong>{item.action_name}</strong>
                  <span>{item.message}</span>
                </div>
                <small>{formatTime(item.executed_at)}</small>
              </div>
            ))
          ) : (
            <div className="empty-state small">No controlled remediation receipt recorded yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
