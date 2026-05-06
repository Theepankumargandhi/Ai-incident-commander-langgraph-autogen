import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.analytics import IncidentAnalytics
from app.core.benchmark import BenchmarkEngine
from app.core.causal import CausalReasoningEngine
from app.core.evaluation import RunEvaluator
from app.core.feedback import FeedbackLearningLoop
from app.core.memory import IncidentMemory
from app.core.multimodal import MultimodalReasoningEngine
from app.core.openai_usage import record_openai_usage, track_openai_usage
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
from app.main import app
from app.models import CreateIncidentRequest
from app.services.incident_service import IncidentEventBus, IncidentService
from app.workflow.autogen_team import AutoGenInvestigationTeam
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator


def _make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-costs-{uuid4()}-"))


def _build_service(temp_dir: Path) -> IncidentService:
    settings = Settings(
        app_name="AI Incident Commander",
        app_env="test",
        data_dir=temp_dir,
        enable_langgraph=False,
        enable_autogen=False,
        default_prompt_profile="balanced-v1",
        auto_execute_safe_actions=False,
        openai_api_key=None,
        openai_model="gpt-4.1-mini",
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


def test_openai_usage_tracker_aggregates_tokens_and_cost_by_source() -> None:
    with track_openai_usage() as usage:
        record_openai_usage(
            source="autogen:PlannerAgent",
            model="gpt-4.1-mini",
            prompt_tokens=1_000,
            completion_tokens=500,
        )
        record_openai_usage(
            source="autogen:PlannerAgent",
            model="gpt-4.1-mini",
            prompt_tokens=500,
            completion_tokens=250,
        )
        record_openai_usage(
            source="memory_embedding",
            model="text-embedding-3-small",
            prompt_tokens=2_000,
            completion_tokens=0,
        )

        snapshot = usage.to_run_usage(duration_ms=1234, trace_id="trace-costs")

    assert snapshot.prompt_tokens == 3_500
    assert snapshot.completion_tokens == 750
    assert snapshot.total_tokens == 4_250
    assert snapshot.duration_ms == 1234
    assert snapshot.trace_id == "trace-costs"
    assert snapshot.estimated_cost_usd > 0
    assert len(snapshot.line_items) == 2

    planner = next(item for item in snapshot.line_items if item.source == "autogen:PlannerAgent")
    embedding = next(item for item in snapshot.line_items if item.source == "memory_embedding")
    assert planner.call_count == 2
    assert planner.total_tokens == 2_250
    assert planner.total_cost_usd > embedding.total_cost_usd
    assert embedding.model == "text-embedding-3-small"


def test_service_cost_summary_and_postmortem_include_usage() -> None:
    service = _build_service(_make_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="Checkout latency spike",
            service="checkout-api",
            environment="production",
            severity="high",
            description="p95 latency climbed sharply during evening traffic.",
            source="grafana",
            metrics={"p95_latency_ms": 2100, "error_rate": 0.03},
        )
    )

    with track_openai_usage():
        record_openai_usage(
            source="judge_evaluation",
            model="gpt-4.1-mini",
            prompt_tokens=1200,
            completion_tokens=300,
        )
        run = asyncio.run(service.process_incident(incident.id, prompt_profile_id="balanced-v1"))

    run = service.get_run(run.id)
    summary = service.get_run_cost_summary(run.id)
    postmortem = service.export_postmortem_markdown(run.id)

    assert summary.run_id == run.id
    assert summary.incident_id == incident.id
    assert summary.total_tokens >= 1500
    assert summary.estimated_cost_usd > 0
    assert summary.line_items
    assert "## Cost Summary" in postmortem
    assert "Estimated cost" in postmortem
    assert "Usage Breakdown" in postmortem


def test_run_cost_endpoint_returns_summary() -> None:
    service = _build_service(_make_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="Checkout latency spike",
            service="checkout-api",
            environment="production",
            severity="high",
            description="p95 latency climbed sharply during evening traffic.",
            source="grafana",
            metrics={"p95_latency_ms": 2100, "error_rate": 0.03},
        )
    )

    with track_openai_usage():
        record_openai_usage(
            source="autogen:CommanderAgent",
            model="gpt-4.1",
            prompt_tokens=2_000,
            completion_tokens=500,
        )
        run = asyncio.run(service.process_incident(incident.id, prompt_profile_id="balanced-v1"))

    with TestClient(app) as client:
        client.app.state.service = service
        response = client.get(f"/runs/{run.id}/cost")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run.id
    assert payload["incident_id"] == incident.id
    assert payload["total_tokens"] >= 2_500
    assert payload["estimated_cost_usd"] > 0
    assert payload["line_items"]
