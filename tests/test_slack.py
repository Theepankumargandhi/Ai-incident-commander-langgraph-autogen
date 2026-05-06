import hashlib
import hmac
import json
import tempfile
import time
import asyncio
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi.testclient import TestClient

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


def make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-slack-{uuid4()}-"))


def build_service(temp_dir: Path) -> IncidentService:
    settings = Settings(
        app_name="AI Incident Commander",
        app_env="test",
        data_dir=temp_dir,
        enable_langgraph=False,
        enable_autogen=False,
        slack_signing_secret="slack-secret",
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


def test_slack_signature_verification_accepts_valid_request() -> None:
    settings = Settings(data_dir=make_temp_dir(), slack_signing_secret="slack-secret")
    client = SlackChatClient(settings, MessageStore(make_temp_dir() / "messages.json"))
    body = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    signature = "v0=" + hmac.new(b"slack-secret", base, hashlib.sha256).hexdigest()

    assert client.verify_signature(body, timestamp, signature) is True
    assert client.verify_signature(body, timestamp, "v0=bad") is False


def test_fastapi_slack_interactivity_endpoint_updates_run() -> None:
    service = build_service(make_temp_dir())
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

    payload = {
        "type": "block_actions",
        "user": {"id": "U123", "username": "alice"},
        "actions": [
            {
                "action_id": "reject_remediation",
                "value": json.dumps({"incident_id": incident.id, "run_id": run.id}),
            }
        ],
    }
    body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = "v0=" + hmac.new(
        service.settings.slack_signing_secret.encode("utf-8"),
        f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    with TestClient(app) as client:
        app.state.service = service
        app.state.settings = service.settings
        response = client.post(
            "/integrations/slack/interactivity",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json()["response_action"] == "update"
    assert service.get_run(run.id).status.value == "failed"
