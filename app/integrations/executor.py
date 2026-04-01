from __future__ import annotations

from app.integrations.chat import MockChatClient
from app.integrations.tickets import MockTicketingClient
from app.models import Incident, ToolAction


class ActionExecutor:
    """Execute remediation actions against connector adapters."""

    def __init__(self, chat_client: MockChatClient, ticketing_client: MockTicketingClient) -> None:
        self.chat_client = chat_client
        self.ticketing_client = ticketing_client

    def execute(self, incident: Incident, action: ToolAction) -> str:
        if action.name == "notify_on_call":
            message = f"[{incident.severity.value.upper()}] {incident.service}: {incident.title}"
            record = self.chat_client.notify(incident, "#ops-oncall", message, {"action_id": action.id})
            return f"On-call notified in {record.channel}."

        if action.name == "open_ticket":
            ticket = self.ticketing_client.create_ticket(
                incident,
                summary=f"{incident.service}: {incident.title}",
                metadata={"action_id": action.id},
            )
            return f"Created incident ticket {ticket.id}."

        if action.name == "scale_service":
            return f"Simulated scale action for {incident.service} with command: {action.command}"

        if action.name == "rollback_release":
            release_hint = incident.context.get("observability", {}).get("release_hint", "latest-release")
            return f"Simulated rollback for {incident.service} targeting {release_hint}."

        if action.name == "collect_diagnostics":
            return f"Captured diagnostics for {incident.service} from local observability adapter."

        return f"No executor mapped for action {action.name}; action recorded only."
