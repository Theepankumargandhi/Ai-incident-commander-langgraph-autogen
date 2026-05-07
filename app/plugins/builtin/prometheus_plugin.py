from __future__ import annotations

from app.config import Settings
from app.integrations.observability import PrometheusObservabilityClient
from app.integrations.release_metadata import GitHubReleaseMetadataClient
from app.models import Incident, ObservabilitySnapshot
from app.plugins.base import ObservabilityPlugin


class PrometheusPlugin(ObservabilityPlugin):
    name = "prometheus"
    version = "1.0.0"

    def __init__(self, settings: Settings) -> None:
        self.client = PrometheusObservabilityClient(settings, GitHubReleaseMetadataClient(settings))
        self._last_snapshot_by_incident: dict[str, ObservabilitySnapshot] = {}

    def collect_metrics(self, incident: Incident) -> dict[str, float]:
        snapshot = self._collect(incident)
        return snapshot.metrics

    def collect_logs(self, incident: Incident) -> list[str]:
        snapshot = self._collect(incident)
        return snapshot.logs

    def _collect(self, incident: Incident) -> ObservabilitySnapshot:
        snapshot = self.client.collect(incident)
        self._last_snapshot_by_incident[incident.id] = snapshot
        return snapshot


def create_plugin(settings: Settings, **_kwargs) -> PrometheusPlugin:
    return PrometheusPlugin(settings)

