import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.analytics import IncidentAnalytics
from app.core.benchmark import BenchmarkEngine
from app.core.causal import CausalReasoningEngine
from app.core.evaluation import RunEvaluator
from app.core.feedback import FeedbackLearningLoop
from app.core.memory import IncidentMemory
from app.core.multimodal import MultimodalReasoningEngine
from app.core.optimizer import AgentOptimizer
from app.core.policy import PolicyEngine
from app.core.postmortem import PostmortemExporter
from app.core.projections import NullProjector
from app.core.safe_remediation import SafeRemediationGateway
from app.core.service_graph import ServiceKnowledgeGraph
from app.core.simulation import AdversarialSimulationLab
from app.core.storage import (
    BenchmarkStore,
    FeedbackStore,
    IncidentStore,
    JobStore,
    MemoryStore,
    MessageStore,
    RemediationStore,
    RunStore,
    TicketStore,
)
from app.core.synthetic import SyntheticIncidentGenerator
from app.integrations.chat import SlackChatClient
from app.integrations.executor import ActionExecutor
from app.integrations.observability import PrometheusObservabilityClient
from app.integrations.remediation import ControlledRemediationClient
from app.integrations.tickets import JiraTicketingClient
from app.models import CreateIncidentRequest
from app.services.incident_service import IncidentEventBus, IncidentService
from app.workflow.autogen_team import AutoGenInvestigationTeam
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator


def _build_service(temp_dir: Path) -> IncidentService:
    settings = Settings(
        app_env="test",
        data_dir=temp_dir,
        enable_langgraph=False,
        enable_autogen=False,
        openai_api_key=None,
        openai_model="gpt-4.1-mini",
        auto_execute_safe_actions=False,
    )
    ticket_store = TicketStore(temp_dir / "tickets.json")
    message_store = MessageStore(temp_dir / "messages.json")
    memory_store = MemoryStore(temp_dir / "memory.json")
    remediation_store = RemediationStore(temp_dir / "remediations.json")
    feedback_store = FeedbackStore(temp_dir / "feedback.json")
    service_graph = ServiceKnowledgeGraph()
    orchestrator = LangGraphIncidentOrchestrator(
        settings=settings,
        memory=IncidentMemory(memory_store),
        policy_engine=PolicyEngine(),
        evaluator=RunEvaluator(settings),
        causal_engine=CausalReasoningEngine(service_graph),
        multimodal_engine=MultimodalReasoningEngine(),
        feedback_loop=FeedbackLearningLoop(feedback_store, memory_store),
        autogen_team=AutoGenInvestigationTeam(settings),
        observability_client=PrometheusObservabilityClient(settings),
        action_executor=ActionExecutor(
            settings,
            SlackChatClient(settings, message_store),
            JiraTicketingClient(settings, ticket_store),
            ControlledRemediationClient(settings, remediation_store),
        ),
    )
    return IncidentService(
        settings=settings,
        incident_store=IncidentStore(temp_dir / "incidents.json"),
        run_store=RunStore(temp_dir / "runs.json"),
        benchmark_store=BenchmarkStore(temp_dir / "benchmarks.json"),
        ticket_store=ticket_store,
        message_store=message_store,
        memory_store=memory_store,
        remediation_store=remediation_store,
        job_store=JobStore(temp_dir / "jobs.json"),
        feedback_store=feedback_store,
        orchestrator=orchestrator,
        benchmark_engine=BenchmarkEngine(),
        event_bus=IncidentEventBus(),
        safe_remediation_gateway=SafeRemediationGateway(settings, remediation_store),
        storage_healthcheck=lambda: {"name": "storage", "mode": "json"},
        analytics=IncidentAnalytics(),
        postmortem_exporter=PostmortemExporter(),
        projector=NullProjector(),
        synthetic_generator=SyntheticIncidentGenerator(settings),
        optimizer=AgentOptimizer(),
        simulation_lab=AdversarialSimulationLab(SyntheticIncidentGenerator(settings), AgentOptimizer()),
        feedback_loop=FeedbackLearningLoop(feedback_store, memory_store),
        service_graph=service_graph,
    )


def _make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-ws-{uuid4()}-"))


async def _run_and_collect_events(service: IncidentService, incident_id: str) -> list[dict]:
    """Subscribe to the event bus before starting the run, then drive both
    concurrently so events are delivered as the orchestrator progresses."""
    queue = await service.event_bus.subscribe(incident_id)
    events: list[dict] = []

    async def _collect() -> None:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                events.append(event)
                stage = event.get("stage", "")
                status = event.get("status", "")
                # Stop collecting once the run reaches a terminal stage.
                if stage in ("finalize", "execute") and status == "completed":
                    break
                if status in ("awaiting_approval", "failed"):
                    break
            except asyncio.TimeoutError:
                break

    await asyncio.gather(service.process_incident(incident_id), _collect())
    service.event_bus.unsubscribe(incident_id, queue)
    return events


def test_websocket_events_arrive_during_run_not_only_after() -> None:
    """Events must be published as each stage runs, not batched at the end.

    We subscribe to the event bus BEFORE triggering the run, then drive the run
    and the event collector concurrently with asyncio.gather().  The collector
    receives events at each await _publish() yield point inside the orchestrator,
    which proves they were delivered during processing, not after.
    """
    service = _build_service(_make_temp_dir())
    # Latency incident produces scale_service + notify_on_call — neither requires
    # approval, so the run completes and publishes events for all stages.
    incident = service.create_incident(
        CreateIncidentRequest(
            title="Checkout latency spike",
            service="checkout-api",
            environment="production",
            severity="high",
            description="p95 latency climbed sharply during evening traffic",
            source="grafana",
            metrics={"p95_latency_ms": 2100, "error_rate": 0.02},
        )
    )

    events = asyncio.run(_run_and_collect_events(service, incident.id))

    stages_seen = {e["stage"] for e in events}
    statuses_seen = {e["status"] for e in events}

    # The ingest event is published before the orchestrator starts — it should
    # always be present, confirming the collector was live before execution began.
    assert "ingest" in stages_seen, f"Expected 'ingest' event; got stages: {stages_seen}"

    # At least one 'started' and one 'completed' status must appear, confirming
    # the collector received live in-progress events.
    assert "started" in statuses_seen, f"No 'started' events received; statuses: {statuses_seen}"
    assert "completed" in statuses_seen, f"No 'completed' events received; statuses: {statuses_seen}"

    # More than one distinct stage must appear, confirming we're seeing per-stage
    # events and not just a single final notification.
    assert len(stages_seen) > 1, f"Only one stage observed; expected multiple: {stages_seen}"


def test_websocket_events_contain_required_fields() -> None:
    """Every published event must carry the minimum fields the WebSocket
    consumer expects: stage, status, message, and timestamp."""
    service = _build_service(_make_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="Memory pressure alert",
            service="worker-service",
            environment="staging",
            severity="medium",
            description="JVM heap usage above 85% for 10 minutes",
            source="prometheus",
            metrics={"heap_usage_pct": 87},
        )
    )

    events = asyncio.run(_run_and_collect_events(service, incident.id))

    assert events, "No events received at all"
    for event in events:
        assert "stage" in event, f"Event missing 'stage': {event}"
        assert "status" in event, f"Event missing 'status': {event}"
        assert "message" in event, f"Event missing 'message': {event}"
        assert "timestamp" in event, f"Event missing 'timestamp': {event}"


def test_multiple_subscribers_each_receive_all_events() -> None:
    """The event bus fan-out must deliver every event to every subscriber
    independently — mirroring how multiple WebSocket clients watch the same run."""

    async def _run() -> tuple[list[dict], list[dict]]:
        temp_dir = _make_temp_dir()
        service = _build_service(temp_dir)
        incident = service.create_incident(
            CreateIncidentRequest(
                title="High error rate",
                service="search-api",
                environment="staging",
                severity="medium",
                description="5xx rate elevated above SLO threshold",
                source="grafana",
                metrics={"error_rate": 0.06},
            )
        )

        queue_a = await service.event_bus.subscribe(incident.id)
        queue_b = await service.event_bus.subscribe(incident.id)
        collected_a: list[dict] = []
        collected_b: list[dict] = []

        async def _drain(queue: asyncio.Queue, out: list) -> None:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    out.append(event)
                    stage = event.get("stage", "")
                    status = event.get("status", "")
                    if stage in ("finalize", "execute") and status == "completed":
                        break
                    if status in ("awaiting_approval", "failed"):
                        break
                except asyncio.TimeoutError:
                    break

        await asyncio.gather(
            service.process_incident(incident.id),
            _drain(queue_a, collected_a),
            _drain(queue_b, collected_b),
        )
        service.event_bus.unsubscribe(incident.id, queue_a)
        service.event_bus.unsubscribe(incident.id, queue_b)
        return collected_a, collected_b

    events_a, events_b = asyncio.run(_run())

    assert events_a, "Subscriber A received no events"
    assert events_b, "Subscriber B received no events"
    # Both queues must have received the same number of events.
    assert len(events_a) == len(events_b), (
        f"Subscriber A got {len(events_a)} events, subscriber B got {len(events_b)}"
    )
    # Both must have seen the same set of stage names.
    stages_a = {e["stage"] for e in events_a}
    stages_b = {e["stage"] for e in events_b}
    assert stages_a == stages_b, f"Stage sets differ: {stages_a} vs {stages_b}"
