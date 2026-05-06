from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import httpx

from app.config import Settings
from app.core.storage import MessageStore
from app.core.tracing import traced_span
from app.models import ChatMessageRecord, ConnectorHealth, Incident

logger = logging.getLogger(__name__)


class SlackChatClient:
    def __init__(self, settings: Settings, message_store: MessageStore) -> None:
        self.settings = settings
        self.message_store = message_store

    @property
    def configured(self) -> bool:
        return bool(self.settings.slack_bot_token and self.settings.slack_channel)

    @property
    def interactivity_configured(self) -> bool:
        return bool(self.settings.slack_signing_secret)

    def healthcheck(self) -> ConnectorHealth:
        if not self.configured:
            return ConnectorHealth(
                name="slack",
                enabled=True,
                configured=False,
                healthy=True,
                details={"channel": self.settings.slack_channel, "mode": "disabled"},
            )
        url = f"{self.settings.slack_api_base.rstrip('/')}/auth.test"
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {self.settings.slack_bot_token}"},
                )
            if response.status_code >= 300:
                return ConnectorHealth(
                    name="slack",
                    enabled=True,
                    configured=True,
                    healthy=False,
                    details={"channel": self.settings.slack_channel, "mode": "live", "error": f"HTTP {response.status_code}"},
                )
            body = response.json()
            if not body.get("ok"):
                return ConnectorHealth(
                    name="slack",
                    enabled=True,
                    configured=True,
                    healthy=False,
                    details={"channel": self.settings.slack_channel, "mode": "live", "error": body.get("error", "auth.test returned ok=false")},
                )
            return ConnectorHealth(
                name="slack",
                enabled=True,
                configured=True,
                healthy=True,
                details={"channel": self.settings.slack_channel, "mode": "live"},
            )
        except Exception as exc:
            return ConnectorHealth(
                name="slack",
                enabled=True,
                configured=True,
                healthy=False,
                details={"channel": self.settings.slack_channel, "mode": "live", "error": str(exc)},
            )

    def notify(
        self,
        incident: Incident,
        channel: str,
        message: str,
        metadata: dict | None = None,
        blocks: list[dict] | None = None,
    ) -> ChatMessageRecord:
        payload_metadata = metadata or {}
        with traced_span("connector.slack.notify", {"incident.id": incident.id, "channel": channel}):
            return self._notify_inner(incident, channel, message, payload_metadata, blocks)

    def _notify_inner(
        self,
        incident: Incident,
        channel: str,
        message: str,
        payload_metadata: dict,
        blocks: list[dict] | None,
    ) -> ChatMessageRecord:
        if not self.configured:
            record = ChatMessageRecord(
                incident_id=incident.id,
                channel=channel,
                message=message,
                metadata={**payload_metadata, "delivery_mode": "fallback", "blocks": blocks or []},
            )
            return self.message_store.save(record)

        try:
            body: dict = {
                "channel": channel,
                "text": message,
                "unfurl_links": False,
                "unfurl_media": False,
            }
            if blocks:
                body["blocks"] = blocks
            response = httpx.post(
                f"{self.settings.slack_api_base.rstrip('/')}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self.settings.slack_bot_token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError(body.get("error", "Slack API returned an unknown error."))

            record = ChatMessageRecord(
                incident_id=incident.id,
                channel=body.get("channel", channel),
                message=message,
                metadata={**payload_metadata, "delivery_mode": "live", "blocks": blocks or []},
                external_id=body.get("ts"),
            )
            return self.message_store.save(record)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.exception("Slack notification failed; recording fallback message.")
            record = ChatMessageRecord(
                incident_id=incident.id,
                channel=channel,
                message=message,
                metadata={**payload_metadata, "delivery_mode": "fallback", "error": str(exc), "blocks": blocks or []},
            )
            return self.message_store.save(record)

    def verify_signature(self, body: bytes, timestamp: str | None, signature: str | None) -> bool:
        if not self.settings.slack_signing_secret or not timestamp or not signature:
            return False
        try:
            request_ts = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(time.time() - request_ts) > 60 * 5:
            return False
        base = f"v0:{timestamp}:{body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            self.settings.slack_signing_secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def encode_action_value(payload: dict) -> str:
        return json.dumps(payload, separators=(",", ":"))
