import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.benchmark import BenchmarkEngine
from app.core.causal import CausalReasoningEngine
from app.core.evaluation import RunEvaluator
from app.core.analytics import IncidentAnalytics
from app.core.feedback import FeedbackLearningLoop
from app.core.memory import IncidentMemory
from app.core.multimodal import MultimodalReasoningEngine
from app.core.optimizer import AgentOptimizer
from app.core.policy import PolicyEngine
from app.core.postmortem import PostmortemExporter
from app.core.projections import NullProjector
from app.core.safe_remediation import SafeRemediationGateway
from app.core.simulation import AdversarialSimulationLab
from app.core.synthetic import SyntheticIncidentGenerator
from app.core.sample_data import sample_benchmark_cases
from app.core.service_graph import ServiceKnowledgeGraph
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
from app.integrations.chat import SlackChatClient
from app.integrations.executor import ActionExecutor
from app.integrations.observability import PrometheusObservabilityClient
from app.integrations.remediation import ControlledRemediationClient
from app.integrations.tickets import JiraTicketingClient
from app.models import AdversarialLabRequest, BenchmarkRequest
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
    return Path(tempfile.mkdtemp(prefix=f"incident-benchmark-{uuid4()}-"))


def test_benchmark_runner_generates_profile_summaries() -> None:
    service = build_service(make_local_temp_dir())
    summaries = asyncio.run(
        service.run_benchmark(
            BenchmarkRequest(
                profile_ids=["balanced-v1", "conservative-v1"],
                model_route_ids=["balanced-route"],
                cases=sample_benchmark_cases(),
            )
        )
    )

    assert len(summaries) == 2
    assert all(summary.total_cases == 5 for summary in summaries)
    assert all("failure_category_counts" in summary.model_dump(mode="json") for summary in summaries)


def test_optimizer_recommendation_is_available_after_benchmarks() -> None:
    service = build_service(make_local_temp_dir())
    asyncio.run(
        service.run_benchmark(
            BenchmarkRequest(
                profile_ids=["balanced-v1", "conservative-v1"],
                model_route_ids=["balanced-route", "quality-route"],
                cases=sample_benchmark_cases(),
            )
        )
    )

    recommendation = service.optimizer_recommendation()

    assert recommendation.recommended_profile_id
    assert recommendation.recommended_model_route_id
    assert recommendation.expected_improvements


def test_adversarial_lab_produces_scenario_counts() -> None:
    service = build_service(make_local_temp_dir())
    report = asyncio.run(
        service.run_adversarial_lab(
            AdversarialLabRequest(
                count=4,
                difficulty="hard",
                profile_ids=["balanced-v1"],
                model_route_ids=["balanced-route"],
                scenario_mix=["noise", "partial_observability", "misleading_release", "cascading_failure"],
            )
        )
    )

    assert report.total_cases == 4
    assert report.scenario_counts
    assert report.optimizer_recommendation is not None
