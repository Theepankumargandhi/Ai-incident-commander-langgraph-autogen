from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.storage import MessageStore
from app.integrations.chat import SlackChatClient
from app.models import ChatMessageRecord, Incident
from app.plugins.base import NotificationPlugin


class SlackPlugin(NotificationPlugin):
    name = "slack"
    version = "1.0.0"

    def __init__(self, settings: Settings, message_store: MessageStore | None = None) -> None:
        self.settings = settings
        self.client = SlackChatClient(
            settings,
            message_store or MessageStore(settings.data_dir / "plugin_slack_messages.json"),
        )

    def send_alert(
        self,
        incident: Incident,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessageRecord:
        return self.client.notify(incident, self.settings.slack_channel, message, metadata or {})

    def send_resolution(
        self,
        incident: Incident,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessageRecord:
        return self.client.notify(
            incident,
            self.settings.slack_channel,
            message,
            {**(metadata or {}), "notification_kind": "resolution"},
        )


def create_plugin(settings: Settings, message_store: MessageStore | None = None, **_kwargs) -> SlackPlugin:
    return SlackPlugin(settings, message_store)

