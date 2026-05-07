from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.storage import TicketStore
from app.integrations.tickets import JiraTicketingClient
from app.models import Incident, TicketRecord
from app.plugins.base import NotificationPlugin


class JiraPlugin(NotificationPlugin):
    name = "jira"
    version = "1.0.0"

    def __init__(self, settings: Settings, ticket_store: TicketStore | None = None) -> None:
        self.client = JiraTicketingClient(
            settings,
            ticket_store or TicketStore(settings.data_dir / "plugin_jira_tickets.json"),
        )

    def send_alert(
        self,
        incident: Incident,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> TicketRecord:
        return self.client.create_ticket(incident, message, metadata or {})

    def send_resolution(
        self,
        incident: Incident,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> TicketRecord:
        return self.client.create_ticket(
            incident,
            message,
            {**(metadata or {}), "notification_kind": "resolution"},
        )


def create_plugin(settings: Settings, ticket_store: TicketStore | None = None, **_kwargs) -> JiraPlugin:
    return JiraPlugin(settings, ticket_store)

