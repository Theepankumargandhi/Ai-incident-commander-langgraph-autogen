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


class ActionStatus(str, Enum):
    proposed = "proposed"
    blocked = "blocked"
    approved = "approved"
    executed = "executed"
    skipped = "skipped"


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


class MemoryHit(BaseModel):
    incident_id: str
    title: str
    summary: str
    score: float


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


class PolicyDecision(BaseModel):
    action_id: str
    verdict: PolicyVerdict
    approved: bool
    requires_human_approval: bool
    reasons: list[str] = Field(default_factory=list)


class RunEvaluation(BaseModel):
    score: float
    findings: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    prompt_profile: str


class IncidentRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.started
    investigation: InvestigationResult | None = None
    memory_hits: list[MemoryHit] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    evaluation: RunEvaluation | None = None
    remediation_notes: list[str] = Field(default_factory=list)
    event_log: list[dict[str, Any]] = Field(default_factory=list)


class PromptProfile(BaseModel):
    id: str
    label: str
    description: str
    confidence_bias: float = 0.0
    aggressiveness_bias: float = 0.0


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


class BenchmarkRequest(BaseModel):
    profile_ids: list[str] = Field(default_factory=list)
    cases: list[BenchmarkCase] = Field(default_factory=list)


class BenchmarkSummary(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=utc_now)
    profile_id: str
    total_cases: int
    average_score: float
    root_cause_match_rate: float
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


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str
    comment: str = ""


class EventMessage(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    stage: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ObservabilitySnapshot(BaseModel):
    collected_at: datetime = Field(default_factory=utc_now)
    metrics: dict[str, float] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    traces: list[str] = Field(default_factory=list)
    release_hint: str | None = None


class TicketRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    summary: str
    severity: IncidentSeverity
    status: str = "open"
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    channel: str
    message: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
