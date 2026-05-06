import asyncio
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

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
from app.models import CreateIncidentRequest, RunStatus
from app.services.incident_service import IncidentEventBus, IncidentService
from app.workflow.autogen_team import AutoGenInvestigationTeam
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator

try:
    from langgraph.graph import StateGraph  # type: ignore
except Exception:
    StateGraph = None

# All 10 LangGraph stage names; each must appear as a "completed" event in the run log.
_EXPECTED_STAGES = frozenset({
    "observe", "memory", "graph", "multimodal", "feedback",
    "causal", "investigate", "policy", "execute", "finalize",
})


def _build_service(temp_dir: Path, enable_langgraph: bool = False) -> IncidentService:
    settings = Settings(
        app_env="test",
        data_dir=temp_dir,
        enable_langgraph=enable_langgraph,
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


def _make_temp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-{prefix}-{uuid4()}-"))


@pytest.mark.skipif(StateGraph is None, reason="langgraph not installed")
def test_langgraph_all_10_stages_complete() -> None:
    """With enable_langgraph=True the orchestrator must execute all 10 nodes and
    record a completed event for each one in run.event_log."""
    service = _build_service(_make_temp_dir("langgraph"), enable_langgraph=True)
    # Latency incident produces scale_service + notify_on_call — neither triggers
    # approval_required in production, so all 10 stages execute (no early exit).
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

    run = asyncio.run(service.process_incident(incident.id))

    completed_stages = {
        entry["stage"]
        for entry in run.event_log
        if entry.get("status") == "completed"
    }
    missing = _EXPECTED_STAGES - completed_stages
    assert not missing, f"Stages did not complete: {missing}"
    assert run.status == RunStatus.completed
    assert run.investigation is not None
    assert run.investigation.suspected_root_cause


@pytest.mark.skipif(StateGraph is None, reason="langgraph not installed")
def test_langgraph_stage_spans_include_incident_and_run_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_traced_span(name: str, attributes: dict | None = None, kind=None):
        spans.append((name, dict(attributes or {})))
        yield "trace-test"

    monkeypatch.setattr("app.workflow.langgraph_orchestrator.traced_span", fake_traced_span)

    service = _build_service(_make_temp_dir("langgraph-tracing"), enable_langgraph=True)
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

    run = asyncio.run(service.process_incident(incident.id))

    stage_spans = [(name, attrs) for name, attrs in spans if name.startswith("langgraph.stage.")]
    stage_names = {name.removeprefix("langgraph.stage.") for name, _ in stage_spans}
    assert _EXPECTED_STAGES.issubset(stage_names)
    assert stage_spans, "Expected stage-level tracing spans to be recorded"
    for _, attrs in stage_spans:
        assert attrs.get("incident_id") == incident.id
        assert attrs.get("run_id") == run.id
