from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Callable

from app.core.analytics import IncidentAnalytics
from app.core.optimizer import AgentOptimizer
from app.config import Settings
from app.core.benchmark import BenchmarkEngine
from app.core.feedback import FeedbackLearningLoop
from app.core.postmortem import PostmortemExporter
from app.core.profiles import DEFAULT_PROFILES, get_profile
from app.core.projections import NullProjector
from app.core.safe_remediation import SafeRemediationGateway
from app.core.simulation import AdversarialSimulationLab
from app.core.synthetic import SyntheticIncidentGenerator
from app.core.storage import CollectionStore
from app.core.service_graph import ServiceKnowledgeGraph
from app.models import (
    AnalyticsSummary,
    ApprovalRequest,
    BenchmarkRequest,
    BenchmarkSummary,
    CreateIncidentRequest,
    AdversarialLabReport,
    AdversarialLabRequest,
    EventMessage,
    FeedbackRecord,
    FeedbackRequest,
    Incident,
    IncidentArtifact,
    IncidentRun,
    ChatMessageRecord,
    JobRecord,
    MemoryEntry,
    OptimizerRecommendation,
    PolicyDocument,
    PolicyDocumentUpdateRequest,
    PolicyVerdict,
    RemediationReceipt,
    RemediationRequest,
    RunStatus,
    RunComparison,
    RunCostSummary,
    ServiceGraphEdge,
    ServiceGraphNode,
    SyntheticGenerationRequest,
)
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator


class IncidentEventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[asyncio.Queue[dict]]] = defaultdict(list)

    async def subscribe(self, incident_id: str) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._listeners[incident_id].append(queue)
        return queue

    def unsubscribe(self, incident_id: str, queue: asyncio.Queue[dict]) -> None:
        listeners = self._listeners.get(incident_id, [])
        if queue in listeners:
            listeners.remove(queue)

    async def publish(self, incident_id: str, event: EventMessage) -> None:
        listeners = list(self._listeners.get(incident_id, []))
        payload = event.model_dump(mode="json")
        for queue in listeners:
            await queue.put(payload)


class IncidentService:
    def __init__(
        self,
        settings: Settings,
        incident_store: CollectionStore[Incident],
        run_store: CollectionStore[IncidentRun],
        benchmark_store: CollectionStore[BenchmarkSummary],
        ticket_store: CollectionStore,
        message_store: CollectionStore,
        memory_store: CollectionStore[MemoryEntry],
        remediation_store: CollectionStore[RemediationReceipt],
        job_store: CollectionStore[JobRecord],
        feedback_store: CollectionStore[FeedbackRecord],
        orchestrator: LangGraphIncidentOrchestrator,
        benchmark_engine: BenchmarkEngine,
        event_bus: IncidentEventBus,
        safe_remediation_gateway: SafeRemediationGateway,
        storage_healthcheck: Callable[[], dict],
        analytics: IncidentAnalytics,
        postmortem_exporter: PostmortemExporter,
        projector: NullProjector,
        synthetic_generator: SyntheticIncidentGenerator,
        optimizer: AgentOptimizer,
        simulation_lab: AdversarialSimulationLab,
        feedback_loop: FeedbackLearningLoop,
        service_graph: ServiceKnowledgeGraph,
    ) -> None:
        self.settings = settings
        self.incident_store = incident_store
        self.run_store = run_store
        self.benchmark_store = benchmark_store
        self.ticket_store = ticket_store
        self.message_store = message_store
        self.memory_store = memory_store
        self.remediation_store = remediation_store
        self.job_store = job_store
        self.feedback_store = feedback_store
        self.orchestrator = orchestrator
        self.benchmark_engine = benchmark_engine
        self.event_bus = event_bus
        self.safe_remediation_gateway = safe_remediation_gateway
        self.storage_healthcheck = storage_healthcheck
        self.analytics = analytics
        self.postmortem_exporter = postmortem_exporter
        self.projector = projector
        self.synthetic_generator = synthetic_generator
        self.optimizer = optimizer
        self.simulation_lab = simulation_lab
        self.feedback_loop = feedback_loop
        self.service_graph = service_graph

    def list_incidents(self) -> list[Incident]:
        return sorted(self.incident_store.list(), key=lambda incident: incident.updated_at, reverse=True)

    def list_runs(self) -> list[IncidentRun]:
        return sorted(self.run_store.list(), key=lambda run: run.started_at, reverse=True)

    def get_incident(self, incident_id: str) -> Incident | None:
        return self.incident_store.get(incident_id)

    def get_run(self, run_id: str) -> IncidentRun | None:
        return self.run_store.get(run_id)

    def list_tickets(self) -> list:
        return sorted(self.ticket_store.list(), key=lambda ticket: ticket.created_at, reverse=True)

    def list_messages(self) -> list:
        return sorted(self.message_store.list(), key=lambda message: message.created_at, reverse=True)

    def list_memory_entries(self) -> list[MemoryEntry]:
        return sorted(self.memory_store.list(), key=lambda entry: entry.updated_at, reverse=True)

    def list_remediations(self) -> list[RemediationReceipt]:
        return sorted(self.remediation_store.list(), key=lambda item: item.executed_at, reverse=True)

    def list_jobs(self) -> list[JobRecord]:
        return sorted(self.job_store.list(), key=lambda job: job.created_at, reverse=True)

    def list_feedback(self) -> list[FeedbackRecord]:
        return sorted(self.feedback_store.list(), key=lambda record: record.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.job_store.get(job_id)

    def create_incident(self, payload: CreateIncidentRequest) -> Incident:
        incident = Incident(**payload.model_dump())
        return self.incident_store.save(incident)

    async def process_incident(
        self,
        incident_id: str,
        prompt_profile_id: str | None = None,
        model_route_id: str | None = None,
        job_id: str | None = None,
        trace_id: str | None = None,
    ) -> IncidentRun:
        incident = self._require_incident(incident_id)

        # Publish the ingest event before the workflow starts so subscribers know processing has begun.
        ingest_event = EventMessage(stage="ingest", status="started", message="Incident received for processing.", trace_id=trace_id, job_id=job_id)
        await self.event_bus.publish(incident.id, ingest_event)

        # Each orchestrator stage publishes its own started/completed events directly to the bus.
        async def _publish(event: EventMessage) -> None:
            await self.event_bus.publish(incident.id, event)

        run = await self.orchestrator.start_run(
            incident,
            prompt_profile_id,
            model_route_id,
            job_id=job_id,
            trace_id=trace_id,
            publish_event=_publish,
        )

        # Prepend the ingest event to the log so the REST replay endpoint includes it.
        run.event_log.insert(0, ingest_event.model_dump(mode="json"))

        self.incident_store.save(incident)
        self.run_store.save(run)
        self.projector.sync_incident(incident)
        self.projector.sync_run(incident, run)
        self.projector.sync_memory(self.memory_store.latest_for_field("incident_id", incident.id))
        for receipt in [item for item in self.remediation_store.list() if item.incident_id == incident.id]:
            self.projector.sync_remediation(receipt)
        if run.status == RunStatus.awaiting_approval:
            approval_message = self._notify_slack_approval_request(incident, run)
            if approval_message is not None:
                approval_event = EventMessage(
                    stage="approval",
                    status="awaiting_approval",
                    message="Slack approval request sent to operators.",
                    trace_id=run.trace_id,
                    job_id=run.job_id,
                    payload={
                        "channel": approval_message.channel,
                        "message_id": approval_message.id,
                        "external_id": approval_message.external_id,
                    },
                )
                self._append_event(run, approval_event)
                self.run_store.save(run)
                self.projector.sync_run(incident, run)
                await self.event_bus.publish(incident.id, approval_event)
        return run

    async def approve_incident(self, incident_id: str, approval: ApprovalRequest, run_id: str | None = None) -> IncidentRun:
        incident = self._require_incident(incident_id)
        if run_id:
            run = self.get_run(run_id)
            if run is None or run.incident_id != incident_id:
                raise ValueError(f"Run {run_id} not found for incident {incident_id}")
        else:
            run = self.run_store.latest_for_field("incident_id", incident_id)
            if run is None:
                raise ValueError(f"No run found for incident {incident_id}")
        approval_event = EventMessage(
            stage="approval",
            message="Approval decision received.",
            trace_id=run.trace_id,
            job_id=run.job_id,
            payload={"approved": approval.approved, "approver": approval.approver, "comment": approval.comment},
        )
        await self.event_bus.publish(incident.id, approval_event)
        updated = await self.orchestrator.resume_run(incident, run, approval)
        self.feedback_loop.auto_feedback_from_approval(incident, updated, approval.approved, approval.approver, approval.comment)
        self._append_event(updated, approval_event)
        remediation_event = EventMessage(
            stage="remediation",
            message="Remediation workflow updated.",
            trace_id=updated.trace_id,
            job_id=updated.job_id,
            payload={"status": updated.status.value, "notes": updated.remediation_notes[-3:]},
        )
        self._append_event(updated, remediation_event)
        self.incident_store.save(incident)
        self.run_store.save(updated)
        self.projector.sync_incident(incident)
        self.projector.sync_run(incident, updated)
        self.projector.sync_memory(self.memory_store.latest_for_field("incident_id", incident.id))
        for receipt in [item for item in self.remediation_store.list() if item.incident_id == incident.id]:
            self.projector.sync_remediation(receipt)
        await self.event_bus.publish(incident.id, remediation_event)
        return updated

    async def handle_slack_interactivity(self, payload: dict) -> dict:
        action = (payload.get("actions") or [{}])[0]
        action_id = action.get("action_id")
        if action_id not in {"approve_remediation", "reject_remediation"}:
            return self._slack_message_update(
                header="Unsupported action",
                body="This Slack interaction is not mapped to an incident approval action.",
                tone="warning",
            )

        try:
            action_value = json.loads(action.get("value") or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("Invalid Slack action payload.") from error

        incident_id = action_value.get("incident_id")
        run_id = action_value.get("run_id")
        if not incident_id or not run_id:
            raise ValueError("Slack action payload is missing incident_id or run_id.")

        incident = self.get_incident(incident_id)
        run = self.get_run(run_id)
        if incident is None or run is None:
            return self._slack_message_update(
                header="Incident not found",
                body="The referenced incident or run no longer exists.",
                tone="danger",
            )
        if run.status != RunStatus.awaiting_approval:
            return self._slack_message_update(
                header="Already processed",
                body=f"Run `{run.id}` is already `{run.status.value}`. No new approval action was applied.",
                tone="neutral",
            )

        approved = action_id == "approve_remediation"
        user = payload.get("user") or {}
        approver = self._format_slack_approver(user)
        verdict = "approved" if approved else "rejected"
        comment = f"{verdict.capitalize()} from Slack by {approver}."
        updated = await self.approve_incident(
            incident_id,
            ApprovalRequest(
                approved=approved,
                approver=approver,
                comment=comment,
            ),
            run_id=run_id,
        )
        return self._slack_message_update(
            header=f"Remediation {verdict}",
            body=(
                f"*Incident:* {incident.title}\n"
                f"*Service:* `{incident.service}`\n"
                f"*Run:* `{updated.id}`\n"
                f"*Operator:* {approver}\n"
                f"*Status:* `{updated.status.value}`"
            ),
            tone="success" if approved else "danger",
        )

    def attach_artifacts(self, incident_id: str, artifacts: list[IncidentArtifact]) -> Incident:
        incident = self._require_incident(incident_id)
        incident.artifacts = [*incident.artifacts, *artifacts]
        self.incident_store.save(incident)
        self.projector.sync_incident(incident)
        return incident

    def record_feedback(self, run_id: str, payload: FeedbackRequest) -> FeedbackRecord:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found.")
        incident = self.get_incident(run.incident_id)
        if incident is None:
            raise ValueError(f"Incident {run.incident_id} not found.")
        record = FeedbackRecord(
            incident_id=incident.id,
            run_id=run.id,
            service=incident.service,
            environment=incident.environment,
            kind=payload.kind,
            operator=payload.operator,
            approved=payload.approved,
            comment=payload.comment,
            corrected_root_cause=payload.corrected_root_cause,
            corrected_summary=payload.corrected_summary,
            accepted_actions=payload.accepted_actions,
            rejected_actions=payload.rejected_actions,
            reinforcement_score=payload.reinforcement_score,
        )
        saved = self.feedback_loop.record_feedback(incident, run, record)
        self._append_event(
            run,
            EventMessage(
                stage="feedback",
                message="Operator feedback recorded for this run.",
                trace_id=run.trace_id,
                job_id=run.job_id,
                payload=saved.model_dump(mode="json"),
            ),
        )
        self.run_store.save(run)
        self.projector.sync_run(incident, run)
        self.projector.sync_memory(self.memory_store.latest_for_field("incident_id", incident.id))
        return saved

    def feedback_summary(self, service: str | None = None):
        return self.feedback_loop.summarize(service)

    def service_graph_snapshot(self, service: str | None = None) -> dict:
        if service:
            return self.service_graph.describe_service(service).model_dump(mode="json")
        return self.service_graph.export()

    def add_graph_node(self, node: ServiceGraphNode) -> dict:
        self.service_graph.add_node(node)
        return self.service_graph.export()

    def add_graph_edge(self, edge: ServiceGraphEdge) -> dict:
        self.service_graph.add_edge(edge)
        return self.service_graph.export()

    async def run_benchmark(
        self,
        request: BenchmarkRequest,
        job_id: str | None = None,
        trace_id: str | None = None,
    ) -> list[BenchmarkSummary]:
        cases = request.cases
        if not cases:
            raise ValueError("Benchmark request must include at least one case.")

        profile_ids = request.profile_ids or list(DEFAULT_PROFILES.keys())
        model_route_ids = request.model_route_ids or [self.settings.default_model_route]
        summaries: list[BenchmarkSummary] = []
        existing_summaries = self.benchmark_store.list()
        for profile_id in profile_ids:
            profile = get_profile(profile_id)
            for model_route_id in model_route_ids:
                previous = next(
                    (
                        item
                        for item in reversed(existing_summaries)
                        if item.profile_id == profile.id and item.model_route_id == model_route_id
                    ),
                    None,
                )
                run_results = []
                for case in cases:
                    incident = Incident(
                        title=case.title,
                        service=case.service,
                        environment=case.environment,
                        severity=case.severity,
                        description=case.description,
                        source=case.source,
                        metrics=case.metrics,
                        labels=case.tags,
                    )
                    run = await self.orchestrator.start_run(
                        incident,
                        prompt_profile_id=profile.id,
                        model_route_id=model_route_id,
                        job_id=job_id,
                        trace_id=trace_id,
                    )
                    if run.status.value == "awaiting_approval":
                        run = await self.orchestrator.resume_run(
                            incident,
                            run,
                            ApprovalRequest(
                                approved=True,
                                approver="benchmark-engine",
                                comment="Auto-approved benchmark execution.",
                            ),
                        )
                    run_results.append((case, incident, run))
                summary = self.benchmark_engine.summarize(
                    profile,
                    model_route_id,
                    cases,
                    run_results,
                    previous_summary=previous,
                )
                self.benchmark_store.save(summary)
                self.projector.sync_benchmark(summary)
                summaries.append(summary)
        return summaries

    def list_benchmarks(self) -> list[BenchmarkSummary]:
        return sorted(self.benchmark_store.list(), key=lambda item: item.created_at, reverse=True)

    def connector_health(self) -> list[dict]:
        return [self.storage_healthcheck(), self.projector.healthcheck(), *self.orchestrator.connector_health()]

    def get_policy_document(self) -> PolicyDocument:
        return self.orchestrator.policy_engine.get_policy_document()

    def update_policy_document(self, payload: PolicyDocumentUpdateRequest) -> PolicyDocument:
        return self.orchestrator.policy_engine.update_policy_document(
            content=payload.content,
            name=payload.name,
            updated_by=payload.updated_by,
            metadata=payload.metadata,
        )

    def execute_controlled_remediation(self, request: RemediationRequest) -> RemediationReceipt:
        receipt = self.safe_remediation_gateway.execute(request)
        self.projector.sync_remediation(receipt)
        return receipt

    def search_memory(self, query: str | None = None, service: str | None = None, environment: str | None = None) -> list[MemoryEntry]:
        return self.orchestrator.memory.search(query=query, service=service, environment=environment)

    def summarize_analytics(self) -> AnalyticsSummary:
        return self.analytics.summarize(
            self.list_incidents(),
            self.list_runs(),
            self.list_benchmarks(),
            self.list_jobs(),
        )

    def compare_runs(self, left_run_id: str, right_run_id: str) -> RunComparison:
        left = self.get_run(left_run_id)
        right = self.get_run(right_run_id)
        if left is None or right is None:
            raise ValueError("Both runs must exist for comparison.")
        return self.analytics.compare_runs(left, right)

    def get_run_cost_summary(self, run_id: str) -> RunCostSummary:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found.")
        usage = run.usage
        return RunCostSummary(
            run_id=run.id,
            incident_id=run.incident_id,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            estimated_cost_usd=usage.estimated_cost_usd if usage else 0.0,
            line_items=[item.model_copy(deep=True) for item in (usage.line_items if usage else [])],
        )

    def export_postmortem_markdown(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found.")
        incident = self.get_incident(run.incident_id)
        if incident is None:
            raise ValueError(f"Incident {run.incident_id} not found.")
        return self.postmortem_exporter.render_markdown(incident, run)

    async def generate_synthetic_cases(self, request: SyntheticGenerationRequest):
        return await self.synthetic_generator.generate_cases(request)

    async def create_synthetic_incidents(self, request: SyntheticGenerationRequest, persist: bool = True) -> list[Incident]:
        payloads = await self.synthetic_generator.generate_incidents(request)
        incidents = [Incident(**payload.model_dump()) for payload in payloads]
        if persist:
            for incident in incidents:
                self.incident_store.save(incident)
                self.projector.sync_incident(incident)
        return incidents

    async def run_adversarial_lab(self, request: AdversarialLabRequest) -> AdversarialLabReport:
        return await self.simulation_lab.run_lab(self, request)

    async def generate_adversarial_cases(self, request: AdversarialLabRequest):
        return await self.simulation_lab.generate_cases(request)

    def optimizer_recommendation(self) -> OptimizerRecommendation:
        return self.optimizer.recommend(self.list_benchmarks(), self.list_runs())

    def _notify_slack_approval_request(self, incident: Incident, run: IncidentRun) -> ChatMessageRecord | None:
        chat_client = self.orchestrator.action_executor.chat_client
        pending_actions = [
            action
            for action in (run.investigation.recommended_actions if run.investigation else [])
            if action.policy_verdict == PolicyVerdict.approval_required
        ]
        if not pending_actions:
            return None

        blocks = self._build_slack_approval_blocks(incident, run, pending_actions)
        message = (
            f"[{incident.severity.value.upper()}] Approval required for "
            f"{incident.service} remediation on incident {incident.title}"
        )
        return chat_client.notify(
            incident,
            self.settings.slack_channel,
            message,
            metadata={
                "kind": "approval_request",
                "run_id": run.id,
                "interactive": True,
                "pending_action_ids": [action.id for action in pending_actions],
            },
            blocks=blocks,
        )

    def _build_slack_approval_blocks(self, incident: Incident, run: IncidentRun, pending_actions: list) -> list[dict]:
        root_cause = run.investigation.suspected_root_cause if run.investigation else "No root cause recorded yet."
        confidence = f"{(run.investigation.confidence * 100):.0f}%" if run.investigation else "0%"
        action_lines = [
            f"• *{action.name}*: {action.description}"
            for action in pending_actions[:5]
        ]
        value = self.orchestrator.action_executor.chat_client.encode_action_value(
            {
                "incident_id": incident.id,
                "run_id": run.id,
            }
        )
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Incident remediation approval required"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident*\n{incident.title}"},
                    {"type": "mrkdwn", "text": f"*Service*\n`{incident.service}`"},
                    {"type": "mrkdwn", "text": f"*Environment*\n`{incident.environment}`"},
                    {"type": "mrkdwn", "text": f"*Severity*\n`{incident.severity.value}`"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Suspected root cause*\n{root_cause}\n\n"
                        f"*Confidence*\n{confidence}\n\n"
                        f"*Pending actions*\n" + "\n".join(action_lines)
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve remediation"},
                        "action_id": "approve_remediation",
                        "value": value,
                    },
                    {
                        "type": "button",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject remediation"},
                        "action_id": "reject_remediation",
                        "value": value,
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Run `{run.id}` is waiting for an operator decision."},
                ],
            },
        ]

    @staticmethod
    def _format_slack_approver(user: dict) -> str:
        display_name = user.get("username") or user.get("name") or user.get("id") or "slack-operator"
        return f"slack:{display_name}"

    @staticmethod
    def _slack_message_update(header: str, body: str, tone: str) -> dict:
        emoji = {
            "success": ":white_check_mark:",
            "danger": ":no_entry:",
            "warning": ":warning:",
            "neutral": ":information_source:",
        }.get(tone, ":information_source:")
        return {
            "response_action": "update",
            "text": f"{header}: {body}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{emoji} *{header}*\n{body}"},
                }
            ],
        }

    def _require_incident(self, incident_id: str) -> Incident:
        incident = self.incident_store.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found.")
        return incident

    @staticmethod
    def _append_event(run: IncidentRun, event: EventMessage) -> None:
        run.event_log.append(event.model_dump(mode="json"))
