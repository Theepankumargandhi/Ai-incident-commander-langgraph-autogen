"use client";

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
import { startTransition, useDeferredValue, useEffect, useState } from "react";

import { Panel } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import {
  approveIncident,
  createSampleIncident,
  getApiBase,
  loadSnapshot,
  processIncident,
  runSampleBenchmarks,
} from "@/lib/api";
import type {
  BenchmarkSummary,
  ChatMessageRecord,
  DashboardSnapshot,
  EventMessage,
  IncidentRun,
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

function latestRunForIncident(runs: IncidentRun[], incidentId: string | null) {
  if (!incidentId) {
    return null;
  }

  return [...runs]
    .filter((run) => run.incident_id === incidentId)
    .sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at))[0] ?? null;
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

export function IncidentConsole() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>(emptySnapshot);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);
  const [liveFeed, setLiveFeed] = useState<EventMessage[]>([]);
  const [isRefreshing, setIsRefreshing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const deferredIncidentId = useDeferredValue(selectedIncidentId);
  const selectedIncident =
    snapshot.incidents.find((incident) => incident.id === deferredIncidentId) ?? snapshot.incidents[0] ?? null;
  const selectedRun = latestRunForIncident(snapshot.runs, selectedIncident?.id ?? null);

  async function refreshSnapshot(focusIncidentId?: string) {
    setIsRefreshing(true);
    try {
      const nextSnapshot = await loadSnapshot();
      setSnapshot(nextSnapshot);
      setError(null);

      const nextFocusId =
        focusIncidentId ??
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
      await refreshSnapshot();
    }

    refresh();
    const interval = window.setInterval(refresh, 6000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selectedIncidentId]);

  useEffect(() => {
    if (!selectedIncident?.id) {
      return;
    }

    const wsUrl = `${getApiBase().replace(/^http/, "ws")}/ws/incidents/${selectedIncident.id}`;
    const socket = new WebSocket(wsUrl);
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as EventMessage;
      setLiveFeed((current) => [payload, ...current].slice(0, 12));
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

  const benchmarkChart = snapshot.benchmarks.map((item: BenchmarkSummary) => ({
    profile: item.profile_id.replace("-v1", ""),
    score: Number(item.average_score.toFixed(1)),
    match: Number((item.root_cause_match_rate * 100).toFixed(0)),
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
      () => createSampleIncident(),
      async (incident) => {
        if (autoProcess) {
          await processIncident(incident.id);
        }
        await refreshSnapshot(incident.id);
      },
      autoProcess ? "Sample incident created and investigated." : "Sample incident injected into the queue.",
    );
  }

  async function handleProcessSelectedIncident() {
    if (!selectedIncident) {
      return;
    }
    await performAction(
      "process-incident",
      () => processIncident(selectedIncident.id, selectedRun?.investigation?.prompt_profile),
      async () => {
        await refreshSnapshot(selectedIncident.id);
      },
      selectedRun ? "Incident workflow re-ran successfully." : "Incident workflow started successfully.",
    );
  }

  async function handleApproveSelectedIncident() {
    if (!selectedIncident) {
      return;
    }
    await performAction(
      "approve-incident",
      () => approveIncident(selectedIncident.id),
      async () => {
        await refreshSnapshot(selectedIncident.id);
      },
      "Approval granted and remediation continued.",
    );
  }

  async function handleRunSampleBenchmarks() {
    await performAction(
      "run-benchmarks",
      () => runSampleBenchmarks(),
      async () => {
        await refreshSnapshot(selectedIncident?.id ?? undefined);
      },
      "Sample benchmarks completed and dashboard metrics updated.",
    );
  }

  const canApprove = selectedIncident?.status === "awaiting_approval";
  const processLabel = selectedRun ? "Re-run workflow" : "Run workflow";

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
          <p className="kicker">AI Incident Commander</p>
          <h1>Build the command deck, not a generated-looking dashboard.</h1>
          <p className="hero-text">
            This frontend is designed as an operations console for live triage, replayable agent runs, benchmark
            comparison, and action governance across LangGraph and AutoGen workflows.
          </p>
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
                  disabled={pendingAction === "create-sample" || pendingAction === "create-process"}
                  onClick={() => handleCreateSample(false)}
                >
                  <Plus size={16} />
                  New sample
                </button>
                <button className="ghost-button" type="button" onClick={() => refreshSnapshot(selectedIncident?.id ?? undefined)}>
                  <RefreshCw size={16} />
                  Refresh
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
                    disabled={pendingAction === "create-process"}
                    onClick={() => handleCreateSample(true)}
                  >
                    <Bot size={16} />
                    Inject and investigate
                  </button>
                </div>
              ) : (
                snapshot.incidents.map((incident) => (
                  <button
                    key={incident.id}
                    className={`incident-card ${selectedIncident?.id === incident.id ? "incident-card-active" : ""}`}
                    type="button"
                    onClick={() => startTransition(() => setSelectedIncidentId(incident.id))}
                  >
                    <div className="incident-card-topline">
                      <StatusBadge tone={severityTone(incident.severity)}>{incident.severity}</StatusBadge>
                      <StatusBadge tone={statusTone(incident.status)}>{incident.status.replaceAll("_", " ")}</StatusBadge>
                    </div>
                    <h3>{incident.title}</h3>
                    <p>{incident.service}</p>
                    <span>{incident.environment}</span>
                  </button>
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
                <div className="button-row">
                  <button
                    className="primary-button"
                    type="button"
                    disabled={pendingAction === "process-incident"}
                    onClick={handleProcessSelectedIncident}
                  >
                    <Play size={16} />
                    {processLabel}
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

                {selectedRun?.investigation ? (
                  <>
                    <div className="highlight-block">
                      <p className="mono-label">Suspected root cause</p>
                      <h3>{selectedRun.investigation.suspected_root_cause}</h3>
                      <p className="confidence-line">
                        Confidence {Math.round(selectedRun.investigation.confidence * 100)}% using{" "}
                        {selectedRun.investigation.prompt_profile}
                      </p>
                    </div>

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
                  </>
                ) : (
                  <div className="empty-state">
                    <p>No investigation has been run for this incident yet.</p>
                    <button
                      className="primary-button"
                      type="button"
                      disabled={pendingAction === "process-incident"}
                      onClick={handleProcessSelectedIncident}
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
                <div className="event-row" key={`${event.timestamp}-${event.stage}`}>
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
              <button
                className="secondary-button"
                type="button"
                disabled={pendingAction === "run-benchmarks"}
                onClick={handleRunSampleBenchmarks}
              >
                <FlaskConical size={16} />
                Run sample benchmark
              </button>
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
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </>
            ) : (
              <div className="empty-state">
                <p>No benchmark history yet. Run the sample benchmark pack to compare prompt profiles.</p>
                <button
                  className="primary-button"
                  type="button"
                  disabled={pendingAction === "run-benchmarks"}
                  onClick={handleRunSampleBenchmarks}
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
          <Panel eyebrow="Operations output" title="Tickets and alerts">
            <OutputTables tickets={snapshot.tickets} messages={snapshot.messages} />
          </Panel>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.36 }}>
          <Panel eyebrow="Governance" title="Profiles and scoring">
            <div className="profile-grid">
              {snapshot.profiles.map((profile) => (
                <article className="profile-card" key={profile.id}>
                  <p className="mono-label">{profile.id}</p>
                  <h3>{profile.label}</h3>
                  <p>{profile.description}</p>
                </article>
              ))}
            </div>
            {selectedRun?.evaluation ? (
              <div className="evaluation-card">
                <div>
                  <p className="mono-label">Latest evaluation</p>
                  <strong>{selectedRun.evaluation.score.toFixed(1)} / 100</strong>
                </div>
                <ul>
                  {selectedRun.evaluation.findings.map((finding) => (
                    <li key={finding}>{finding}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Panel>
        </motion.div>
      </section>
    </main>
  );
}

function OutputTables({
  tickets,
  messages,
}: {
  tickets: TicketRecord[];
  messages: ChatMessageRecord[];
}) {
  return (
    <div className="output-grid">
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
                  <strong>{ticket.summary}</strong>
                  <span>{formatTime(ticket.created_at)}</span>
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
    </div>
  );
}
