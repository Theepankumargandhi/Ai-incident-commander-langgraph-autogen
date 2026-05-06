from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.core.storage import TicketStore
from app.core.tracing import traced_span
from app.models import ConnectorHealth, Incident, TicketRecord

logger = logging.getLogger(__name__)


def _adf_paragraph(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


class JiraTicketingClient:
    def __init__(self, settings: Settings, ticket_store: TicketStore) -> None:
        self.settings = settings
        self.ticket_store = ticket_store

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.jira_base_url
            and self.settings.jira_email
            and self.settings.jira_api_token
            and self.settings.jira_project_key
            and self.settings.jira_issue_type
        )

    def healthcheck(self) -> ConnectorHealth:
        base_details: dict = {
            "project_key": self.settings.jira_project_key,
            "issue_type": self.settings.jira_issue_type,
        }
        if not self.configured:
            return ConnectorHealth(
                name="jira",
                enabled=True,
                configured=False,
                healthy=True,
                details={**base_details, "mode": "disabled"},
            )
        url = f"{self.settings.jira_base_url.rstrip('/')}/rest/api/3/myself"
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(
                    url,
                    auth=(self.settings.jira_email or "", self.settings.jira_api_token or ""),
                    headers={"Accept": "application/json"},
                )
            if response.status_code < 300:
                return ConnectorHealth(
                    name="jira",
                    enabled=True,
                    configured=True,
                    healthy=True,
                    details={**base_details, "mode": "live"},
                )
            return ConnectorHealth(
                name="jira",
                enabled=True,
                configured=True,
                healthy=False,
                details={**base_details, "mode": "live", "error": f"HTTP {response.status_code}"},
            )
        except Exception as exc:
            return ConnectorHealth(
                name="jira",
                enabled=True,
                configured=True,
                healthy=False,
                details={**base_details, "mode": "live", "error": str(exc)},
            )

    def create_ticket(self, incident: Incident, summary: str, metadata: dict | None = None) -> TicketRecord:
        payload_metadata = metadata or {}
        with traced_span("connector.jira.create_ticket", {"incident.id": incident.id, "summary": summary}):
            return self._create_ticket_inner(incident, summary, payload_metadata)

    def _create_ticket_inner(self, incident: Incident, summary: str, payload_metadata: dict) -> TicketRecord:
        if not self.configured:
            ticket = TicketRecord(
                incident_id=incident.id,
                summary=summary,
                severity=incident.severity,
                metadata={**payload_metadata, "delivery_mode": "fallback"},
            )
            return self.ticket_store.save(ticket)

        try:
            response = httpx.post(
                f"{self.settings.jira_base_url.rstrip('/')}/rest/api/3/issue",
                auth=(self.settings.jira_email or "", self.settings.jira_api_token or ""),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "fields": {
                        "project": {"key": self.settings.jira_project_key},
                        "summary": summary,
                        "issuetype": {"name": self.settings.jira_issue_type},
                        "labels": list(self.settings.jira_labels),
                        "description": _adf_paragraph(
                            f"{incident.title}\n\n{incident.description}\n\nService: {incident.service}\nEnvironment: {incident.environment}"
                        ),
                    }
                },
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            issue_key = body.get("key")
            issue_url = (
                f"{self.settings.jira_base_url.rstrip('/')}/browse/{issue_key}" if issue_key else None
            )
            ticket = TicketRecord(
                incident_id=incident.id,
                summary=summary,
                severity=incident.severity,
                metadata={**payload_metadata, "delivery_mode": "live"},
                external_key=issue_key,
                external_url=issue_url,
            )
            return self.ticket_store.save(ticket)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.exception("Jira issue creation failed; recording fallback ticket.")
            ticket = TicketRecord(
                incident_id=incident.id,
                summary=summary,
                severity=incident.severity,
                metadata={**payload_metadata, "delivery_mode": "fallback", "error": str(exc)},
            )
            return self.ticket_store.save(ticket)
