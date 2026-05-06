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
from app.core.openai_usage import snapshot_openai_usage, track_openai_usage
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
from app.models import ArtifactKind, CreateIncidentRequest, FeedbackKind, FeedbackRequest, Incident, IncidentArtifact
from app.services.incident_service import IncidentEventBus, IncidentService
from app.workflow.autogen_team import AutoGenInvestigationTeam
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator


def build_service(temp_dir: Path) -> IncidentService:
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


def make_local_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-intelligence-{uuid4()}-"))


def test_multimodal_graph_reasoning_is_added_to_runs() -> None:
    service = build_service(make_local_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="Checkout latency spike with trace evidence",
            service="checkout-api",
            environment="production",
            severity="high",
            description="Latency jumped and downstream spans started to time out.",
            source="grafana",
            metrics={"p95_latency_ms": 2400, "error_rate": 0.03},
            artifacts=[
                IncidentArtifact(
                    kind=ArtifactKind.grafana_screenshot,
                    title="Grafana latency panel",
                    description="p95 latency panel with clear saturation markers",
                    extracted_text="checkout-api p95 latency inventory-api timeout dependency",
                    annotations=["Latency hotspot", "inventory-api timeout"],
                    related_services=["inventory-api"],
                )
            ],
        )
    )

    run = asyncio.run(service.process_incident(incident.id))

    assert run.multimodal_analysis is not None
    assert "inventory-api" in run.multimodal_analysis.implicated_services
    assert run.service_graph_context is not None
    assert run.service_graph_context.upstream_services
    assert run.causal_analysis is not None
    assert run.causal_analysis.graph_source == "service-knowledge-graph"


def test_feedback_learning_updates_summary() -> None:
    service = build_service(make_local_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="Checkout latency spike",
            service="checkout-api",
            environment="production",
            severity="high",
            description="Latency increased after a dependency slowdown",
            source="grafana",
            metrics={"p95_latency_ms": 2100, "error_rate": 0.03},
        )
    )

    run = asyncio.run(service.process_incident(incident.id))
    record = service.record_feedback(
        run.id,
        FeedbackRequest(
            kind=FeedbackKind.correction,
            operator="ops@example.com",
            comment="Scale first, avoid rollback without deployment evidence.",
            corrected_root_cause="Inventory dependency saturation",
            accepted_actions=["scale_service"],
            rejected_actions=["rollback_release"],
            reinforcement_score=0.7,
        ),
    )
    summary = service.feedback_summary("checkout-api")

    assert record.kind == FeedbackKind.correction
    assert "scale_service" in summary.preferred_actions
    assert "rollback_release" in summary.discouraged_actions
    assert summary.corrected_root_causes


def test_service_graph_snapshot_is_available() -> None:
    service = build_service(make_local_temp_dir())

    snapshot = service.service_graph_snapshot("checkout-api")

    assert snapshot["focal_service"] == "checkout-api"
    assert snapshot["upstream_services"]
    assert snapshot["graph_nodes"]


def test_multimodal_engine_uses_gpt4o_vision_for_image_artifacts(monkeypatch) -> None:
    captured: dict = {}

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "model": "gpt-4o-2024-11-20",
                "usage": {
                    "prompt_tokens": 420,
                    "completion_tokens": 120,
                    "total_tokens": 540,
                },
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"summary":"Grafana panel shows checkout latency spiking around inventory dependency timeouts.",'
                                '"visual_signals":["latency_hotspot","timeout_chain"],'
                                '"implicated_services":["checkout-api","inventory-api"],'
                                '"evidence":["The chart highlights elevated p95 latency and an inventory timeout annotation."],'
                                '"confidence":0.87}'
                            )
                        }
                    }
                ],
            }

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, endpoint: str, headers=None, json=None):
            captured["endpoint"] = endpoint
            captured["headers"] = headers
            captured["payload"] = json
            return DummyResponse()

    monkeypatch.setattr("app.core.multimodal.httpx.Client", DummyClient)

    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_api_base="https://api.openai.com/v1",
        request_timeout_seconds=10.0,
    )
    engine = MultimodalReasoningEngine(settings)
    incident = Incident(
        title="Checkout latency spike with dashboard screenshot",
        service="checkout-api",
        environment="production",
        severity="high",
        description="Latency jumped and dashboards show dependency timeouts.",
        source="grafana",
        metrics={"p95_latency_ms": 2400, "error_rate": 0.03},
        artifacts=[
            IncidentArtifact(
                kind=ArtifactKind.grafana_screenshot,
                title="Grafana latency panel",
                description="Dashboard screenshot attached",
                content_type="image/png",
                extracted_text="checkout-api p95 latency inventory-api timeout",
                annotations=["latency hotspot", "inventory timeout"],
                related_services=["inventory-api"],
                metadata={
                    "base64_data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2ZyJYAAAAASUVORK5CYII="
                },
            )
        ],
    )

    with track_openai_usage():
        analysis = engine.analyze(incident)
        usage = snapshot_openai_usage()

    assert analysis.generated_with_vision is True
    assert analysis.findings
    assert analysis.findings[0].summary.startswith("Grafana panel shows checkout latency")
    assert "inventory-api" in analysis.implicated_services
    assert captured["endpoint"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "gpt-4o"
    image_part = captured["payload"]["messages"][1]["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert usage is not None
    assert usage.total_tokens == 540
    assert any(item.source == "multimodal_vision" and item.model == "gpt-4o" for item in usage.line_items)
