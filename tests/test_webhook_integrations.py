import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.models import CreateIncidentRequest, Incident, IncidentRun, IncidentSeverity


class FakeWebhookService:
    def __init__(self) -> None:
        self.created_payloads: list[CreateIncidentRequest] = []
        self.processed_incident_ids: list[str] = []

    def create_incident(self, payload: CreateIncidentRequest) -> Incident:
        self.created_payloads.append(payload)
        return Incident(**payload.model_dump())

    async def process_incident(self, incident_id: str, *args, **kwargs) -> IncidentRun:
        self.processed_incident_ids.append(incident_id)
        return IncidentRun(incident_id=incident_id, trace_id=kwargs.get("trace_id"))


def _body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _pagerduty_signature(body: bytes, secret: str) -> str:
    return "v1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_pagerduty_webhook_creates_incident_and_triggers_investigation() -> None:
    service = FakeWebhookService()
    settings = Settings(
        app_env="test",
        pagerduty_webhook_secret="pd-secret",
        enable_langgraph=False,
        enable_autogen=False,
    )
    payload = {
        "messages": [
            {
                "event": "incident.triggered",
                "incident": {
                    "id": "PDI123",
                    "incident_number": 42,
                    "title": "Checkout API latency breach",
                    "urgency": "high",
                    "service": {"summary": "checkout-api"},
                    "priority": {"summary": "P1"},
                    "body": {
                        "details": {
                            "environment": "production",
                            "p95_latency_ms": 2600,
                            "error_rate": 0.04,
                            "queue_depth": 120,
                        }
                    },
                    "html_url": "https://example.pagerduty.com/incidents/PDI123",
                },
            }
        ]
    }
    body = _body(payload)

    with TestClient(app) as client:
        client.app.state.service = service
        client.app.state.settings = settings
        response = client.post(
            "/webhooks/pagerduty",
            content=body,
            headers={"x-pagerduty-signature": _pagerduty_signature(body, "pd-secret")},
        )

    assert response.status_code == 200
    incident = response.json()["incident"]
    run = response.json()["run"]
    assert incident["title"] == "Checkout API latency breach"
    assert incident["service"] == "checkout-api"
    assert incident["severity"] == IncidentSeverity.critical
    assert incident["metrics"]["p95_latency_ms"] == 2600.0
    assert incident["context"]["webhook_provider"] == "pagerduty"
    assert run["incident_id"] == incident["id"]
    assert service.processed_incident_ids == [incident["id"]]


def test_pagerduty_webhook_rejects_invalid_signature() -> None:
    with TestClient(app) as client:
        client.app.state.service = FakeWebhookService()
        client.app.state.settings = Settings(app_env="test", pagerduty_webhook_secret="pd-secret")
        response = client.post(
            "/webhooks/pagerduty",
            content=_body({"messages": []}),
            headers={"x-pagerduty-signature": "v1=bad"},
        )

    assert response.status_code == 401


def test_opsgenie_webhook_creates_incident_and_triggers_investigation() -> None:
    service = FakeWebhookService()
    settings = Settings(
        app_env="test",
        opsgenie_webhook_secret="ops-secret",
        opsgenie_webhook_secret_header="x-opsgenie-webhook-secret",
        enable_langgraph=False,
        enable_autogen=False,
    )
    payload = {
        "action": "Create",
        "alert": {
            "alertId": "og-alert-123",
            "message": "Payments API 5xx spike",
            "alias": "payments-5xx",
            "priority": "P2",
            "source": "prometheus",
            "entity": "payments-api",
            "description": "5xx responses exceeded the alert threshold.",
            "tags": ["payments", "production"],
            "details": {
                "service": "payments-api",
                "environment": "production",
                "error_rate": "0.16",
                "p95_latency_ms": "900",
            },
        },
    }

    with TestClient(app) as client:
        client.app.state.service = service
        client.app.state.settings = settings
        response = client.post(
            "/webhooks/opsgenie",
            json=payload,
            headers={"x-opsgenie-webhook-secret": "ops-secret"},
        )

    assert response.status_code == 200
    incident = response.json()["incident"]
    run = response.json()["run"]
    assert incident["title"] == "Payments API 5xx spike"
    assert incident["service"] == "payments-api"
    assert incident["severity"] == IncidentSeverity.high
    assert incident["metrics"]["error_rate"] == 0.16
    assert incident["context"]["webhook_provider"] == "opsgenie"
    assert run["incident_id"] == incident["id"]
    assert service.processed_incident_ids == [incident["id"]]


def test_opsgenie_webhook_rejects_invalid_secret_token() -> None:
    with TestClient(app) as client:
        client.app.state.service = FakeWebhookService()
        client.app.state.settings = Settings(app_env="test", opsgenie_webhook_secret="ops-secret")
        response = client.post(
            "/webhooks/opsgenie",
            json={"alert": {"message": "bad token"}},
            headers={"x-opsgenie-webhook-secret": "wrong"},
        )

    assert response.status_code == 401
