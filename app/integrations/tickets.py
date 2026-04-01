from __future__ import annotations

from app.core.storage import TicketStore
from app.models import Incident, TicketRecord


class MockTicketingClient:
    def __init__(self, ticket_store: TicketStore) -> None:
        self.ticket_store = ticket_store

    def create_ticket(self, incident: Incident, summary: str, metadata: dict | None = None) -> TicketRecord:
        ticket = TicketRecord(
            incident_id=incident.id,
            summary=summary,
            severity=incident.severity,
            metadata=metadata or {},
        )
        return self.ticket_store.save(ticket)
