from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class IncidentStatus(str, Enum):
    received = "received"
    investigating = "investigating"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    remediating = "remediating"
    resolved = "resolved"
    failed = "failed"


class RunStatus(str, Enum):
    started = "started"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    failed = "failed"


class JobKind(str, Enum):
    process_incident = "process_incident"
    run_benchmark = "run_benchmark"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class ArtifactKind(str, Enum):
    grafana_screenshot = "grafana_screenshot"
    architecture_diagram = "architecture_diagram"
    trace_visualization = "trace_visualization"
    dashboard_export = "dashboard_export"
    other = "other"


class FeedbackKind(str, Enum):
    approval = "approval"
    override = "override"
    correction = "correction"
    rating = "rating"


class ActionStatus(str, Enum):
    proposed = "proposed"
    blocked = "blocked"
    approved = "approved"
    executed = "executed"
    skipped = "skipped"
    failed = "failed"


class PolicyVerdict(str, Enum):
    allowed = "allowed"
    blocked = "blocked"
    approval_required = "approval_required"


class ToolAction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str
    command: str
    risk_level: IncidentSeverity = IncidentSeverity.medium
    status: ActionStatus = ActionStatus.proposed
    policy_verdict: PolicyVerdict | None = None
    policy_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    service: str
    environment: str
    severity: IncidentSeverity
    description: str
    source: str
    metrics: dict[str, float] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    status: IncidentStatus = IncidentStatus.received
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    context: dict[str, Any] = Field(default_factory=dict)
    artifacts: list["IncidentArtifact"] = Field(default_factory=list)


class MemoryHit(BaseModel):
    incident_id: str
    title: str
    summary: str
    score: float
    service: str | None = None
    action_names: list[str] = Field(default_factory=list)
    postmortem_summary: str | None = None
    resolution_state: str | None = None
    matched_terms: list[str] = Field(default_factory=list)
    resolution_quality: float | None = None
    abstract_lessons: list[str] = Field(default_factory=list)
    service_playbook: str | None = None
    preferred_actions: list[str] = Field(default_factory=list)
    discouraged_actions: list[str] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    title: str
    service: str
    environment: str
    severity: IncidentSeverity
    summary: str
    root_cause: str
    labels: list[str] = Field(default_factory=list)
    action_names: list[str] = Field(default_factory=list)
    outcome: str = "resolved"
    approval_required: bool = False
    approval_notes: list[str] = Field(default_factory=list)
    postmortem_summary: str | None = None
    symptom_tokens: list[str] = Field(default_factory=list)
    metric_snapshot: dict[str, float] = Field(default_factory=dict)
    resolution_quality: float = 0.0
    abstract_lessons: list[str] = Field(default_factory=list)
    remediation_patterns: list[str] = Field(default_factory=list)
    service_playbook: str | None = None
    embedding_vector: list[float] = Field(default_factory=list)
    feedback_score: float = 0.0
    preferred_actions: list[str] = Field(default_factory=list)
    discouraged_actions: list[str] = Field(default_factory=list)
    correction_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: ArtifactKind
    title: str
    description: str = ""
    content_type: str = "image/png"
    source_ref: str | None = None
    extracted_text: str = ""
    annotations: list[str] = Field(default_factory=list)
    related_services: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class MultimodalFinding(BaseModel):
    artifact_id: str
    artifact_kind: ArtifactKind
    summary: str
    visual_signals: list[str] = Field(default_factory=list)
    implicated_services: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class MultimodalAnalysis(BaseModel):
    summary: str = ""
    findings: list[MultimodalFinding] = Field(default_factory=list)
    visual_signals: list[str] = Field(default_factory=list)
    implicated_services: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    generated_with_vision: bool = False


class FeedbackRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    run_id: str
    service: str
    environment: str
    kind: FeedbackKind
    operator: str
    approved: bool | None = None
    comment: str = ""
    corrected_root_cause: str | None = None
    corrected_summary: str | None = None
    accepted_actions: list[str] = Field(default_factory=list)
    rejected_actions: list[str] = Field(default_factory=list)
    reinforcement_score: float = 0.0
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeedbackLearningSnapshot(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    service: str | None = None
    guidance: list[str] = Field(default_factory=list)
    preferred_actions: list[str] = Field(default_factory=list)
    discouraged_actions: list[str] = Field(default_factory=list)
    corrected_root_causes: list[str] = Field(default_factory=list)
    escalation_patterns: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ServiceGraphNode(BaseModel):
    id: str
    label: str | None = None
    node_type: str = "service"
    owner: str | None = None
    tier: str | None = None
    criticality: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceGraphEdge(BaseModel):
    source: str
    target: str
    relation: str = "depends_on"
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceGraphPath(BaseModel):
    nodes: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    explanation: str = ""
    score: float = 0.0


class AddNodeRequest(BaseModel):
    id: str
    label: str | None = None
    service_type: str = "service"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AddEdgeRequest(BaseModel):
    source: str
    target: str
    relationship: str = "depends_on"


class ServiceGraphContext(BaseModel):
    focal_service: str
    summary: str = ""
    upstream_services: list[str] = Field(default_factory=list)
    downstream_services: list[str] = Field(default_factory=list)
    critical_dependencies: list[str] = Field(default_factory=list)
    blast_radius: list[str] = Field(default_factory=list)
    candidate_paths: list[ServiceGraphPath] = Field(default_factory=list)
    graph_nodes: list[ServiceGraphNode] = Field(default_factory=list)
    graph_edges: list[ServiceGraphEdge] = Field(default_factory=list)
    confidence: float = 0.0


class AudienceSummaries(BaseModel):
    operator: str = ""
    executive: str = ""
    engineering: str = ""
    postmortem: str = ""


class RemediationReview(BaseModel):
    risk_summary: str = ""
    safe_actions: list[str] = Field(default_factory=list)
    risky_actions: list[str] = Field(default_factory=list)
    reviewer_score: float = 0.0


class MemoryAbstraction(BaseModel):
    lessons: list[str] = Field(default_factory=list)
    remediation_patterns: list[str] = Field(default_factory=list)
    service_playbook: str | None = None
    embedding_vector: list[float] = Field(default_factory=list)


class CausalAnalysis(BaseModel):
    likely_root_service: str | None = None
    likely_cause: str = ""
    confidence: float = 0.0
    reasoning: list[str] = Field(default_factory=list)
    impacted_services: list[str] = Field(default_factory=list)
    propagation_path: list[str] = Field(default_factory=list)
    graph_nodes: list[str] = Field(default_factory=list)
    graph_edges: list[dict[str, str]] = Field(default_factory=list)
    release_correlation: bool = False
    graph_source: str = "heuristic"
    knowledge_graph_paths: list[ServiceGraphPath] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    run_id: str
    summary: str
    suspected_root_cause: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    agent_notes: dict[str, str] = Field(default_factory=dict)
    recommended_actions: list[ToolAction] = Field(default_factory=list)
    prompt_profile: str
    used_autogen: bool = False
    model_route_id: str | None = None
    agent_models: dict[str, str] = Field(default_factory=dict)
    audience_summaries: AudienceSummaries = Field(default_factory=AudienceSummaries)
    remediation_review: RemediationReview | None = None
    memory_abstractions: MemoryAbstraction | None = None
    causal_analysis: CausalAnalysis | None = None
    multimodal_analysis: MultimodalAnalysis | None = None
    feedback_learning: FeedbackLearningSnapshot | None = None
    service_graph_context: ServiceGraphContext | None = None
    reflection_notes: list[str] = Field(default_factory=list)
    revision_count: int = 0
    runbook_excerpt: str | None = None
    release_assessment: str | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    action_id: str
    verdict: PolicyVerdict
    approved: bool
    requires_human_approval: bool
    reasons: list[str] = Field(default_factory=list)
    policy_rule: str | None = None


class PolicyDocument(BaseModel):
    id: str = "active"
    name: str = "incident-remediation-policy"
    content: str
    version: int = 1
    updated_at: datetime = Field(default_factory=utc_now)
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenAIUsageLineItem(BaseModel):
    source: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    call_count: int = 0


class RunUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    duration_ms: int = 0
    trace_id: str | None = None
    line_items: list[OpenAIUsageLineItem] = Field(default_factory=list)


class RunEvaluation(BaseModel):
    score: float
    heuristic_score: float = 0.0
    judge_score: float | None = None
    judge_used_llm: bool = False
    judge_rationale: str | None = None
    sub_scores: dict[str, float] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    prompt_profile: str
    estimated_cost_usd: float = 0.0
    action_accuracy: float = 0.0
    failure_categories: list[str] = Field(default_factory=list)


class IncidentRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.started
    trace_id: str | None = None
    job_id: str | None = None
    investigation: InvestigationResult | None = None
    causal_analysis: CausalAnalysis | None = None
    multimodal_analysis: MultimodalAnalysis | None = None
    feedback_learning: FeedbackLearningSnapshot | None = None
    service_graph_context: ServiceGraphContext | None = None
    memory_hits: list[MemoryHit] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    evaluation: RunEvaluation | None = None
    remediation_notes: list[str] = Field(default_factory=list)
    event_log: list[dict[str, Any]] = Field(default_factory=list)
    usage: RunUsage | None = None
    approval_notes: list[str] = Field(default_factory=list)
    postmortem_summary: str | None = None
    connector_status: dict[str, Any] = Field(default_factory=dict)


class RunCostSummary(BaseModel):
    run_id: str
    incident_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    currency: str = "USD"
    line_items: list[OpenAIUsageLineItem] = Field(default_factory=list)


class PromptProfile(BaseModel):
    id: str
    label: str
    description: str
    confidence_bias: float = 0.0
    aggressiveness_bias: float = 0.0


class ModelRoute(BaseModel):
    id: str
    label: str
    description: str
    planner_model: str
    investigator_model: str
    critic_model: str
    commander_model: str


class BenchmarkCase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    service: str
    environment: str
    severity: IncidentSeverity
    description: str
    source: str
    metrics: dict[str, float] = Field(default_factory=dict)
    expected_root_cause: str
    expected_actions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class BenchmarkRequest(BaseModel):
    profile_ids: list[str] = Field(default_factory=list)
    model_route_ids: list[str] = Field(default_factory=list)
    cases: list[BenchmarkCase] = Field(default_factory=list)


class SyntheticGenerationRequest(BaseModel):
    count: int = 5
    difficulty: str = "mixed"
    include_noise: bool = True
    include_false_positives: bool = True
    include_incomplete_evidence: bool = True
    services: list[str] = Field(default_factory=list)


class AdversarialLabRequest(BaseModel):
    count: int = 8
    difficulty: str = "hard"
    profile_ids: list[str] = Field(default_factory=list)
    model_route_ids: list[str] = Field(default_factory=list)
    scenario_mix: list[str] = Field(default_factory=list)
    include_noise: bool = True
    include_false_positives: bool = True
    include_incomplete_evidence: bool = True
    services: list[str] = Field(default_factory=list)


class OptimizerRecommendation(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    recommended_profile_id: str = ""
    recommended_model_route_id: str = ""
    rationale: str = ""
    expected_improvements: list[str] = Field(default_factory=list)
    prompt_adjustments: list[str] = Field(default_factory=list)
    memory_strategy: list[str] = Field(default_factory=list)
    optimizer_actions: list[str] = Field(default_factory=list)
    top_failure_categories: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class AdversarialLabReport(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    total_cases: int = 0
    scenario_counts: dict[str, int] = Field(default_factory=dict)
    weakness_counts: dict[str, int] = Field(default_factory=dict)
    summaries: list[BenchmarkSummary] = Field(default_factory=list)
    recommended_focus_areas: list[str] = Field(default_factory=list)
    optimizer_recommendation: OptimizerRecommendation | None = None


class BenchmarkSummary(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=utc_now)
    profile_id: str
    model_route_id: str | None = None
    total_cases: int
    average_score: float
    root_cause_match_rate: float
    average_latency_ms: float = 0.0
    average_cost_usd: float = 0.0
    average_confidence: float = 0.0
    average_judge_score: float = 0.0
    approval_rate: float = 0.0
    executed_action_rate: float = 0.0
    action_accuracy_rate: float = 0.0
    failure_category_counts: dict[str, int] = Field(default_factory=dict)
    score_delta: float | None = None
    match_rate_delta: float | None = None
    details: list[dict[str, Any]] = Field(default_factory=list)


class CreateIncidentRequest(BaseModel):
    title: str
    service: str
    environment: str
    severity: IncidentSeverity
    description: str
    source: str = "api"
    metrics: dict[str, float] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[IncidentArtifact] = Field(default_factory=list)


class ArtifactAttachmentRequest(BaseModel):
    artifacts: list[IncidentArtifact] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str
    comment: str = ""


class FeedbackRequest(BaseModel):
    kind: FeedbackKind
    operator: str
    approved: bool | None = None
    comment: str = ""
    corrected_root_cause: str | None = None
    corrected_summary: str | None = None
    accepted_actions: list[str] = Field(default_factory=list)
    rejected_actions: list[str] = Field(default_factory=list)
    reinforcement_score: float = 0.0


class PolicyDocumentUpdateRequest(BaseModel):
    content: str
    name: str | None = None
    updated_by: str = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventMessage(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    stage: str
    status: str = "completed"  # "started" | "completed" | "awaiting_approval"
    message: str
    trace_id: str | None = None
    job_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ObservabilitySnapshot(BaseModel):
    collected_at: datetime = Field(default_factory=utc_now)
    metrics: dict[str, float] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    traces: list[str] = Field(default_factory=list)
    release_hint: str | None = None
    provider: str = "mock"
    query_details: dict[str, Any] = Field(default_factory=dict)


class TicketRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    summary: str
    severity: IncidentSeverity
    status: str = "open"
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_key: str | None = None
    external_url: str | None = None


class ChatMessageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    channel: str
    message: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_id: str | None = None


class ConnectorHealth(BaseModel):
    name: str
    enabled: bool
    configured: bool
    healthy: bool
    details: dict[str, Any] = Field(default_factory=dict)


class HealthReport(BaseModel):
    status: str
    app_name: str
    app_env: str
    checked_at: datetime = Field(default_factory=utc_now)
    connectors: list[ConnectorHealth] = Field(default_factory=list)


class RemediationRequest(BaseModel):
    action_id: str
    action_name: str
    service: str
    environment: str
    incident_id: str
    requested_by: str
    approval_reference: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RemediationReceipt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    action_id: str
    action_name: str
    incident_id: str
    status: str
    executed_at: datetime = Field(default_factory=utc_now)
    message: str
    service: str | None = None
    environment: str | None = None
    requested_by: str | None = None
    provider: str = "controlled-gateway"
    external_reference: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: JobKind
    status: JobStatus = JobStatus.queued
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trace_id: str | None = None
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AuthSession(BaseModel):
    role: str
    subject: str
    demo_mode: bool = False


class RunComparison(BaseModel):
    left_run_id: str
    right_run_id: str
    incident_id: str | None = None
    score_delta: float | None = None
    latency_delta_ms: int | None = None
    cost_delta_usd: float | None = None
    root_cause_changed: bool = False
    action_diff: dict[str, list[str]] = Field(default_factory=dict)
    failure_category_diff: dict[str, list[str]] = Field(default_factory=dict)
    summary: str = ""


class AnalyticsSummary(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    incident_count: int = 0
    run_count: int = 0
    queued_jobs: int = 0
    top_root_causes: list[dict[str, Any]] = Field(default_factory=list)
    top_failure_categories: list[dict[str, Any]] = Field(default_factory=list)
    action_outcomes: dict[str, int] = Field(default_factory=dict)
    profile_win_rates: list[dict[str, Any]] = Field(default_factory=list)


Incident.model_rebuild()
