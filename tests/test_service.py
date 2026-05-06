import asyncio
import json
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
from app.models import ApprovalRequest, CreateIncidentRequest, IncidentStatus, RunStatus
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
    return Path(tempfile.mkdtemp(prefix=f"incident-service-{uuid4()}-"))


def test_incident_workflow_requires_approval_for_production_rollback() -> None:
    service = build_service(make_local_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="5xx errors after release",
            service="orders-api",
            environment="production",
            severity="critical",
            description="5xx errors spiked after the last deployment",
            source="grafana",
            metrics={"error_rate": 0.12},
        )
    )

    run = asyncio.run(service.process_incident(incident.id))

    assert run.status == RunStatus.awaiting_approval
    messages = service.list_messages()
    assert len(messages) == 1
    assert messages[0].metadata["kind"] == "approval_request"
    assert messages[0].metadata["interactive"] is True
    assert run.causal_analysis is not None
    assert run.investigation.causal_analysis is not None
    assert run.service_graph_context is not None
    assert run.feedback_learning is not None
    updated = asyncio.run(
        service.approve_incident(
            incident.id,
            ApprovalRequest(approved=True, approver="ops@example.com", comment="Proceed with rollback."),
        )
    )

    assert updated.status == RunStatus.completed
    assert service.get_incident(incident.id).status == IncidentStatus.resolved
    assert service.list_tickets()
    assert service.list_feedback()


def test_slack_interactivity_can_approve_remediation_without_frontend() -> None:
    service = build_service(make_local_temp_dir())
    incident = service.create_incident(
        CreateIncidentRequest(
            title="5xx errors after release",
            service="orders-api",
            environment="production",
            severity="critical",
            description="5xx errors spiked after the last deployment",
            source="grafana",
            metrics={"error_rate": 0.12},
        )
    )
    run = asyncio.run(service.process_incident(incident.id))

    response = asyncio.run(
        service.handle_slack_interactivity(
            {
                "type": "block_actions",
                "user": {"id": "U123", "username": "alice"},
                "actions": [
                    {
                        "action_id": "approve_remediation",
                        "value": json.dumps({"incident_id": incident.id, "run_id": run.id}),
                    }
                ],
            }
        )
    )

    updated_run = service.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == RunStatus.completed
    assert response["response_action"] == "update"
    assert "Remediation approved" in response["text"]


def test_run_comparison_and_postmortem_export_are_available() -> None:
    service = build_service(make_local_temp_dir())
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

    first_run = asyncio.run(service.process_incident(incident.id, prompt_profile_id="balanced-v1"))
    second_run = asyncio.run(service.process_incident(incident.id, prompt_profile_id="aggressive-v1"))

    comparison = service.compare_runs(first_run.id, second_run.id)
    postmortem = service.export_postmortem_markdown(second_run.id)

    assert comparison.incident_id == incident.id
    assert comparison.summary
    assert "Checkout latency spike" in postmortem
    assert "Root cause" in postmortem
