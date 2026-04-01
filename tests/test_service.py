import asyncio
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.benchmark import BenchmarkEngine
from app.core.evaluation import RunEvaluator
from app.core.memory import IncidentMemory
from app.core.policy import PolicyEngine
from app.core.storage import BenchmarkStore, IncidentStore, MessageStore, RunStore, TicketStore
from app.integrations.chat import MockChatClient
from app.integrations.executor import ActionExecutor
from app.integrations.observability import MockObservabilityClient
from app.integrations.tickets import MockTicketingClient
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
    orchestrator = LangGraphIncidentOrchestrator(
        settings=settings,
        memory=IncidentMemory(),
        policy_engine=PolicyEngine(),
        evaluator=RunEvaluator(),
        autogen_team=AutoGenInvestigationTeam(settings),
        observability_client=MockObservabilityClient(),
        action_executor=ActionExecutor(MockChatClient(message_store), MockTicketingClient(ticket_store)),
    )
    return IncidentService(
        settings=settings,
        incident_store=IncidentStore(temp_dir / "incidents.json"),
        run_store=RunStore(temp_dir / "runs.json"),
        benchmark_store=BenchmarkStore(temp_dir / "benchmarks.json"),
        ticket_store=ticket_store,
        message_store=message_store,
        orchestrator=orchestrator,
        benchmark_engine=BenchmarkEngine(),
        event_bus=IncidentEventBus(),
    )


def make_local_temp_dir() -> Path:
    temp_dir = Path("data") / "test_artifacts" / str(uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


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
    updated = asyncio.run(
        service.approve_incident(
            incident.id,
            ApprovalRequest(approved=True, approver="ops@example.com", comment="Proceed with rollback."),
        )
    )

    assert updated.status == RunStatus.completed
    assert service.get_incident(incident.id).status == IncidentStatus.resolved
    assert service.list_tickets()
