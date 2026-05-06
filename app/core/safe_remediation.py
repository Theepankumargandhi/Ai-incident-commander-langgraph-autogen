from __future__ import annotations

import re
from datetime import datetime, timezone

from app.config import Settings
from app.core.storage import CollectionStore
from app.models import RemediationReceipt, RemediationRequest


class SafeRemediationGateway:
    """Controlled remediation endpoint for non production automation."""

    def __init__(self, settings: Settings, remediation_store: CollectionStore[RemediationReceipt]) -> None:
        self.settings = settings
        self.remediation_store = remediation_store

    def policy_snapshot(self) -> dict:
        return {
            "provider": self.settings.safe_remediation_provider_name,
            "allow_production": self.settings.safe_remediation_allow_production,
            "allowed_actions": list(self.settings.safe_remediation_allowed_actions),
            "allowed_environments": list(self.settings.safe_remediation_allowed_environments),
            "allowed_services": list(self.settings.safe_remediation_allowed_services),
            "replica_bounds": {
                "min": self.settings.safe_remediation_min_replicas,
                "max": self.settings.safe_remediation_max_replicas,
            },
        }

    def execute(self, request: RemediationRequest) -> RemediationReceipt:
        reasons = self._validate(request)
        target_replicas = self._extract_target_replicas(request)
        if reasons:
            return self.remediation_store.save(
                RemediationReceipt(
                    action_id=request.action_id,
                    action_name=request.action_name,
                    incident_id=request.incident_id,
                    status="rejected",
                    message="Controlled remediation request rejected by gateway policy.",
                    service=request.service,
                    environment=request.environment,
                    requested_by=request.requested_by,
                    provider=self.settings.safe_remediation_provider_name,
                    metadata={
                        "reasons": reasons,
                        "request_payload": request.payload,
                        "target_replicas": target_replicas,
                        "policy": self.policy_snapshot(),
                    },
                )
            )

        external_reference = (
            f"{self.settings.safe_remediation_provider_name}-"
            f"{request.action_name}-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        if request.action_name == "scale_service":
            message = (
                f"Scaled {request.service} in {request.environment} to {target_replicas} replicas "
                f"through the controlled gateway."
            )
        elif request.action_name == "restart_service":
            message = f"Restarted {request.service} in {request.environment} through the controlled gateway."
        else:
            message = f"Executed {request.action_name} for {request.service} through the controlled gateway."

        return self.remediation_store.save(
            RemediationReceipt(
                action_id=request.action_id,
                action_name=request.action_name,
                incident_id=request.incident_id,
                status="executed",
                message=message,
                service=request.service,
                environment=request.environment,
                requested_by=request.requested_by,
                provider=self.settings.safe_remediation_provider_name,
                external_reference=external_reference,
                metadata={
                    "request_payload": request.payload,
                    "target_replicas": target_replicas,
                    "policy": self.policy_snapshot(),
                    "approval_reference": request.approval_reference,
                },
            )
        )

    def _validate(self, request: RemediationRequest) -> list[str]:
        reasons: list[str] = []
        action_name = request.action_name.strip().lower()
        environment = request.environment.strip().lower()
        service = request.service.strip().lower()

        if action_name not in {item.lower() for item in self.settings.safe_remediation_allowed_actions}:
            reasons.append(f"Action {request.action_name} is not allowlisted.")

        if environment == "production" and not self.settings.safe_remediation_allow_production:
            reasons.append("Production remediation is disabled for the controlled gateway.")

        allowed_environments = {item.lower() for item in self.settings.safe_remediation_allowed_environments}
        if allowed_environments and environment not in allowed_environments:
            reasons.append(f"Environment {request.environment} is outside the gateway allowlist.")

        allowed_services = {item.lower() for item in self.settings.safe_remediation_allowed_services}
        if allowed_services and service not in allowed_services:
            reasons.append(f"Service {request.service} is outside the gateway allowlist.")

        if action_name == "scale_service":
            target_replicas = self._extract_target_replicas(request)
            if target_replicas is None:
                reasons.append("Scale action is missing a target replica count.")
            else:
                if target_replicas < self.settings.safe_remediation_min_replicas:
                    reasons.append(
                        f"Target replicas {target_replicas} are below the allowed minimum "
                        f"{self.settings.safe_remediation_min_replicas}."
                    )
                if target_replicas > self.settings.safe_remediation_max_replicas:
                    reasons.append(
                        f"Target replicas {target_replicas} exceed the allowed maximum "
                        f"{self.settings.safe_remediation_max_replicas}."
                    )
        return reasons

    @staticmethod
    def _extract_target_replicas(request: RemediationRequest) -> int | None:
        action_metadata = request.payload.get("action_metadata", {})
        candidate = action_metadata.get("replicas") or request.payload.get("replicas") or request.payload.get("target_replicas")
        if candidate is not None:
            try:
                return int(candidate)
            except (TypeError, ValueError):
                return None

        command = str(request.payload.get("command", ""))
        match = re.search(r"\bto\s+(\d+)\s+replicas\b", command)
        if match:
            return int(match.group(1))
        return None
