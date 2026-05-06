import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.safe_remediation import SafeRemediationGateway
from app.core.storage import MessageStore, RemediationStore, TicketStore
from app.integrations.chat import SlackChatClient
from app.integrations.executor import ActionExecutor
from app.integrations.remediation import ControlledRemediationClient
from app.integrations.tickets import JiraTicketingClient
from app.models import Incident, IncidentSeverity, RemediationRequest, ToolAction


def make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-remediation-{uuid4()}-"))


def test_scale_service_records_simulated_remediation_when_endpoint_is_disabled() -> None:
    temp_dir = make_temp_dir()
    settings = Settings(
        app_name="AI Incident Commander",
        app_env="test",
        data_dir=temp_dir,
        safe_remediation_enabled=False,
    )
    executor = ActionExecutor(
        settings,
        SlackChatClient(settings, MessageStore(temp_dir / "messages.json")),
        JiraTicketingClient(settings, TicketStore(temp_dir / "tickets.json")),
        ControlledRemediationClient(settings, RemediationStore(temp_dir / "remediations.json")),
    )

    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="staging",
        severity=IncidentSeverity.high,
        description="Latency rose after a traffic replay.",
        source="prometheus",
    )
    action = ToolAction(
        name="scale_service",
        description="Scale the staging deployment by one replica.",
        command="scale service checkout-api to 4 replicas",
        risk_level=IncidentSeverity.medium,
    )

    result = executor.execute(incident, action)
    receipts = executor.remediation_client.remediation_store.list()

    assert "fallback remediation request" in result.lower()
    assert len(receipts) == 1
    assert receipts[0].status == "simulated"


def test_gateway_rejects_production_restart_and_accepts_staging_scale() -> None:
    temp_dir = make_temp_dir()
    settings = Settings(
        app_name="AI Incident Commander",
        app_env="test",
        data_dir=temp_dir,
        safe_remediation_enabled=True,
        safe_remediation_allowed_services=("checkout-api",),
    )
    store = RemediationStore(temp_dir / "remediations.json")
    gateway = SafeRemediationGateway(settings, store)

    rejected = gateway.execute(
        RemediationRequest(
            action_id="restart-prod",
            action_name="restart_service",
            service="checkout-api",
            environment="production",
            incident_id="incident-1",
            requested_by="operator",
            payload={"command": "restart service checkout-api"},
        )
    )
    accepted = gateway.execute(
        RemediationRequest(
            action_id="scale-staging",
            action_name="scale_service",
            service="checkout-api",
            environment="staging",
            incident_id="incident-2",
            requested_by="operator",
            payload={"command": "scale service checkout-api to 4 replicas", "action_metadata": {"replicas": 4}},
        )
    )

    assert rejected.status == "rejected"
    assert accepted.status == "executed"
    assert accepted.external_reference
