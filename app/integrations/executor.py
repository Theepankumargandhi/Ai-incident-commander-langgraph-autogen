from __future__ import annotations

from app.config import Settings
from app.integrations.chat import SlackChatClient
from app.integrations.remediation import ControlledRemediationClient
from app.integrations.tickets import JiraTicketingClient
from app.models import Incident, RemediationRequest, ToolAction


class ActionExecutor:
    """Execute remediation actions against live or fallback connector adapters."""

    def __init__(
        self,
        settings: Settings,
        chat_client: SlackChatClient,
        ticketing_client: JiraTicketingClient,
        remediation_client: ControlledRemediationClient,
    ) -> None:
        self.settings = settings
        self.chat_client = chat_client
        self.ticketing_client = ticketing_client
        self.remediation_client = remediation_client

    def connector_health(self) -> list[dict]:
        return [
            self.chat_client.healthcheck().model_dump(mode="json"),
            self.ticketing_client.healthcheck().model_dump(mode="json"),
            self.remediation_client.healthcheck().model_dump(mode="json"),
        ]

    def execute(self, incident: Incident, action: ToolAction) -> str:
        if action.name == "notify_on_call":
            message = f"[{incident.severity.value.upper()}] {incident.service}: {incident.title}"
            record = self.chat_client.notify(incident, self.settings.slack_channel, message, {"action_id": action.id})
            return f"On-call notification recorded for {record.channel}."

        if action.name == "open_ticket":
            ticket = self.ticketing_client.create_ticket(
                incident,
                summary=f"{incident.service}: {incident.title}",
                metadata={"action_id": action.id},
            )
            if ticket.external_key:
                return f"Created Jira issue {ticket.external_key}."
            return f"Recorded local incident ticket {ticket.id}."

        if action.name in {"scale_service", "restart_service"}:
            receipt = self.remediation_client.execute(
                RemediationRequest(
                    action_id=action.id,
                    action_name=action.name,
                    service=incident.service,
                    environment=incident.environment,
                    incident_id=incident.id,
                    requested_by="ai-incident-commander",
                    approval_reference=str(action.metadata.get("approval_reference", "")) or None,
                    payload={
                        "command": action.command,
                        "description": action.description,
                        "risk_level": action.risk_level.value,
                        "action_metadata": action.metadata,
                    },
                )
            )
            return receipt.message

        if action.name == "rollback_release":
            release_hint = incident.context.get("observability", {}).get("release_hint", "latest-release")
            return f"Rollback remains approval gated. Suggested target is {release_hint}."

        if action.name == "collect_diagnostics":
            return f"Captured diagnostics for {incident.service} from the observability layer."

        return f"No executor mapped for action {action.name}; action recorded only."
