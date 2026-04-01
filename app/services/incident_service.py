from __future__ import annotations

import asyncio
from collections import defaultdict

from app.config import Settings
from app.core.benchmark import BenchmarkEngine
from app.core.profiles import DEFAULT_PROFILES, get_profile
from app.core.storage import BenchmarkStore, IncidentStore, MessageStore, RunStore, TicketStore
from app.models import (
    ApprovalRequest,
    BenchmarkRequest,
    BenchmarkSummary,
    CreateIncidentRequest,
    EventMessage,
    Incident,
    IncidentRun,
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
        incident_store: IncidentStore,
        run_store: RunStore,
        benchmark_store: BenchmarkStore,
        ticket_store: TicketStore,
        message_store: MessageStore,
        orchestrator: LangGraphIncidentOrchestrator,
        benchmark_engine: BenchmarkEngine,
        event_bus: IncidentEventBus,
    ) -> None:
        self.settings = settings
        self.incident_store = incident_store
        self.run_store = run_store
        self.benchmark_store = benchmark_store
        self.ticket_store = ticket_store
        self.message_store = message_store
        self.orchestrator = orchestrator
        self.benchmark_engine = benchmark_engine
        self.event_bus = event_bus

    def list_incidents(self) -> list[Incident]:
        return self.incident_store.list()

    def list_runs(self) -> list[IncidentRun]:
        return self.run_store.list()

    def get_incident(self, incident_id: str) -> Incident | None:
        return self.incident_store.get(incident_id)

    def get_run(self, run_id: str) -> IncidentRun | None:
        return self.run_store.get(run_id)

    def list_tickets(self) -> list:
        return self.ticket_store.list()

    def list_messages(self) -> list:
        return self.message_store.list()

    def create_incident(self, payload: CreateIncidentRequest) -> Incident:
        incident = Incident(**payload.model_dump())
        return self.incident_store.save(incident)

    async def process_incident(self, incident_id: str, prompt_profile_id: str | None = None) -> IncidentRun:
        incident = self._require_incident(incident_id)
        ingest_event = EventMessage(stage="ingest", message="Incident received for processing.")
        await self.event_bus.publish(incident.id, ingest_event)
        prior_runs = [run for run in self.run_store.list() if run.incident_id != incident.id]
        memory_event = EventMessage(stage="memory", message="Searching similar historical incidents.")
        await self.event_bus.publish(incident.id, memory_event)
        run = self.orchestrator.start_run(incident, prior_runs, prompt_profile_id)
        self._append_event(run, ingest_event)
        self._append_event(run, memory_event)
        investigation_event = EventMessage(
            stage="investigation",
            message="Incident investigation completed.",
            payload={
                "status": run.status.value,
                "root_cause": run.investigation.suspected_root_cause if run.investigation else "",
                "profile": run.investigation.prompt_profile if run.investigation else self.settings.default_prompt_profile,
            },
        )
        self._append_event(run, investigation_event)
        await self.event_bus.publish(incident.id, investigation_event)
        self.incident_store.save(incident)
        self.run_store.save(run)
        return run

    async def approve_incident(self, incident_id: str, approval: ApprovalRequest) -> IncidentRun:
        incident = self._require_incident(incident_id)
        run = self.run_store.latest_for_field("incident_id", incident_id)
        if run is None:
            raise ValueError(f"No run found for incident {incident_id}")
        approval_event = EventMessage(stage="approval", message="Approval decision received.")
        await self.event_bus.publish(incident.id, approval_event)
        updated = self.orchestrator.resume_run(incident, run, approval)
        self._append_event(updated, approval_event)
        remediation_event = EventMessage(stage="remediation", message="Remediation workflow updated.", payload={"status": updated.status.value})
        self._append_event(updated, remediation_event)
        self.incident_store.save(incident)
        self.run_store.save(updated)
        await self.event_bus.publish(incident.id, remediation_event)
        return updated

    async def run_benchmark(self, request: BenchmarkRequest) -> list[BenchmarkSummary]:
        cases = request.cases
        if not cases:
            raise ValueError("Benchmark request must include at least one case.")

        profile_ids = request.profile_ids or list(DEFAULT_PROFILES.keys())
        summaries: list[BenchmarkSummary] = []
        for profile_id in profile_ids:
            profile = get_profile(profile_id)
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
                )
                run = self.orchestrator.start_run(incident, prior_runs=[], prompt_profile_id=profile.id)
                if run.status.value == "awaiting_approval":
                    run = self.orchestrator.resume_run(
                        incident,
                        run,
                        ApprovalRequest(approved=True, approver="benchmark-engine", comment="Auto-approved benchmark execution."),
                    )
                run_results.append((case, incident, run))
            summary = self.benchmark_engine.summarize(profile, cases, run_results)
            self.benchmark_store.save(summary)
            summaries.append(summary)
        return summaries

    def list_benchmarks(self) -> list[BenchmarkSummary]:
        return self.benchmark_store.list()

    def _require_incident(self, incident_id: str) -> Incident:
        incident = self.incident_store.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found.")
        return incident

    @staticmethod
    def _append_event(run: IncidentRun, event: EventMessage) -> None:
        run.event_log.append(event.model_dump(mode="json"))
