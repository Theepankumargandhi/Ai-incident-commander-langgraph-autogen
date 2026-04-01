from __future__ import annotations

from app.core.storage import MessageStore
from app.models import ChatMessageRecord, Incident


class MockChatClient:
    def __init__(self, message_store: MessageStore) -> None:
        self.message_store = message_store

    def notify(self, incident: Incident, channel: str, message: str, metadata: dict | None = None) -> ChatMessageRecord:
        record = ChatMessageRecord(
            incident_id=incident.id,
            channel=channel,
            message=message,
            metadata=metadata or {},
        )
        return self.message_store.save(record)
