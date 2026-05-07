from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import ChatMessageRecord, Incident, ObservabilitySnapshot, RemediationReceipt, ToolAction


class PluginBase(ABC):
    """Common metadata contract for all extension plugins."""

    name: str
    version: str = "0.1.0"
    status: str = "loaded"

    @property
    @abstractmethod
    def plugin_type(self) -> str:
        raise NotImplementedError

    def metadata(self) -> dict[str, str]:
        return {
            "name": self.name,
            "type": self.plugin_type,
            "version": self.version,
            "status": self.status,
        }


class ObservabilityPlugin(PluginBase):
    @property
    def plugin_type(self) -> str:
        return "observability"

    @abstractmethod
    def collect_metrics(self, incident: Incident) -> dict[str, float]:
        raise NotImplementedError

    @abstractmethod
    def collect_logs(self, incident: Incident) -> list[str]:
        raise NotImplementedError


class NotificationPlugin(PluginBase):
    @property
    def plugin_type(self) -> str:
        return "notification"

    @abstractmethod
    def send_alert(self, incident: Incident, message: str, metadata: dict[str, Any] | None = None) -> ChatMessageRecord | Any:
        raise NotImplementedError

    @abstractmethod
    def send_resolution(self, incident: Incident, message: str, metadata: dict[str, Any] | None = None) -> ChatMessageRecord | Any:
        raise NotImplementedError


class RemediationPlugin(PluginBase):
    @property
    def plugin_type(self) -> str:
        return "remediation"

    @abstractmethod
    def validate_action(self, incident: Incident, action: ToolAction) -> bool:
        raise NotImplementedError

    @abstractmethod
    def execute_action(self, incident: Incident, action: ToolAction) -> RemediationReceipt | Any:
        raise NotImplementedError


class AgentPlugin(PluginBase):
    @property
    def plugin_type(self) -> str:
        return "agent"

    @abstractmethod
    def get_agent_config(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_system_prompt(self) -> str:
        raise NotImplementedError


PLUGIN_BASE_CLASSES = (ObservabilityPlugin, NotificationPlugin, RemediationPlugin, AgentPlugin)

