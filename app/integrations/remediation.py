from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.core.storage import RemediationStore
from app.core.tracing import traced_span
from app.models import ConnectorHealth, RemediationReceipt, RemediationRequest

logger = logging.getLogger(__name__)


class ControlledRemediationClient:
    """Execute safe remediation actions by calling a controlled HTTP endpoint."""

    def __init__(self, settings: Settings, remediation_store: RemediationStore) -> None:
        self.settings = settings
        self.remediation_store = remediation_store

    @property
    def configured(self) -> bool:
        return bool(self.settings.safe_remediation_enabled and self.settings.safe_remediation_url)

    def healthcheck(self) -> ConnectorHealth:
        base_details: dict = {
            "url": self.settings.safe_remediation_url,
            "provider": self.settings.safe_remediation_provider_name,
        }
        if not self.configured:
            return ConnectorHealth(
                name="safe_remediation",
                enabled=self.settings.safe_remediation_enabled,
                configured=False,
                healthy=True,
                details={**base_details, "mode": "disabled"},
            )
        parsed = urlparse(self.settings.safe_remediation_url)
        probe_url = f"{parsed.scheme}://{parsed.netloc}/health"
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(probe_url)
            if response.status_code < 300:
                return ConnectorHealth(
                    name="safe_remediation",
                    enabled=True,
                    configured=True,
                    healthy=True,
                    details={**base_details, "mode": "live"},
                )
            return ConnectorHealth(
                name="safe_remediation",
                enabled=True,
                configured=True,
                healthy=False,
                details={**base_details, "mode": "live", "error": f"HTTP {response.status_code}"},
            )
        except Exception as exc:
            return ConnectorHealth(
                name="safe_remediation",
                enabled=True,
                configured=True,
                healthy=False,
                details={**base_details, "mode": "live", "error": str(exc)},
            )

    def execute(self, request: RemediationRequest) -> RemediationReceipt:
        with traced_span(
            "connector.remediation.execute",
            {"incident.id": request.incident_id, "action.name": request.action_name},
        ):
            return self._execute_inner(request)

    def _execute_inner(self, request: RemediationRequest) -> RemediationReceipt:
        if not self.configured:
            receipt = RemediationReceipt(
                action_id=request.action_id,
                action_name=request.action_name,
                incident_id=request.incident_id,
                status="simulated",
                message=f"Recorded fallback remediation request for {request.action_name}.",
                service=request.service,
                environment=request.environment,
                requested_by=request.requested_by,
                provider=self.settings.safe_remediation_provider_name,
                metadata=request.payload,
            )
            return self.remediation_store.save(receipt)

        try:
            headers = {"Content-Type": "application/json"}
            if self.settings.remediation_token:
                headers["X-Remediation-Token"] = self.settings.remediation_token

            response = httpx.post(
                self.settings.safe_remediation_url,
                headers=headers,
                json=request.model_dump(mode="json"),
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            receipt = RemediationReceipt.model_validate(body)
            return self.remediation_store.save(receipt)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.exception("Controlled remediation call failed; falling back to local receipt.")
            receipt = RemediationReceipt(
                action_id=request.action_id,
                action_name=request.action_name,
                incident_id=request.incident_id,
                status="fallback",
                message=f"Controlled remediation endpoint failed: {exc}",
                service=request.service,
                environment=request.environment,
                requested_by=request.requested_by,
                provider=self.settings.safe_remediation_provider_name,
                metadata=request.payload,
            )
            return self.remediation_store.save(receipt)
