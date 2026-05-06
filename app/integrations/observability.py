from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.core.tracing import traced_span
from app.integrations.release_metadata import GitHubReleaseMetadataClient
from app.models import ConnectorHealth, Incident, ObservabilitySnapshot

logger = logging.getLogger(__name__)


class PrometheusObservabilityClient:
    """Collect observability context from Prometheus when configured, otherwise fall back locally."""

    def __init__(self, settings: Settings, release_client: GitHubReleaseMetadataClient | None = None) -> None:
        self.settings = settings
        self.release_client = release_client

    @property
    def configured(self) -> bool:
        return bool(self.settings.prometheus_base_url)

    def healthcheck(self) -> ConnectorHealth:
        details = {
            "base_url": self.settings.prometheus_base_url,
            "mode": "live" if self.configured else "fallback",
        }
        if self.release_client is not None:
            details["release_correlation"] = self.release_client.healthcheck().details
        return ConnectorHealth(
            name="prometheus",
            enabled=True,
            configured=self.configured,
            healthy=True,
            details=details,
        )

    def collect(self, incident: Incident) -> ObservabilitySnapshot:
        with traced_span(
            "observability.collect",
            {"incident.service": incident.service, "incident.environment": incident.environment},
        ):
            return self._collect_inner(incident)

    def _collect_inner(self, incident: Incident) -> ObservabilitySnapshot:
        if not self.configured:
            return self._fallback_collect(incident)

        try:
            metrics = dict(incident.metrics)
            query_details: dict[str, dict] = {}

            latency_query = incident.context.get("prometheus_latency_query") or self.settings.prometheus_latency_query_template.format(
                service=incident.service
            )
            error_query = incident.context.get("prometheus_error_query") or self.settings.prometheus_error_query_template.format(
                service=incident.service
            )
            uptime_query = incident.context.get("prometheus_uptime_query") or self.settings.prometheus_uptime_query_template.format(
                service=incident.service
            )

            latency_value = self._query_value(latency_query)
            error_value = self._query_value(error_query)
            uptime_value = self._query_value(uptime_query)

            query_details["latency"] = {"query": latency_query, "value": latency_value}
            query_details["error_rate"] = {"query": error_query, "value": error_value}
            query_details["uptime"] = {"query": uptime_query, "value": uptime_value}

            if latency_value is not None:
                metrics["p95_latency_ms"] = round(latency_value * 1000, 2)
            if error_value is not None:
                metrics["error_rate"] = round(error_value, 4)
            if uptime_value is not None:
                metrics["up"] = round(uptime_value, 4)

            logs = [
                f"Prometheus p95 latency query returned {query_details['latency']['value']}.",
                f"Prometheus error rate query returned {query_details['error_rate']['value']}.",
                f"Prometheus uptime query returned {query_details['uptime']['value']}.",
            ]
            traces = incident.context.get("trace_hints", [])
            release_metadata = self.release_client.latest_release() if self.release_client else None
            release_hint = incident.context.get("release_hint")
            if release_metadata and release_metadata.get("head_sha"):
                release_hint = f"{release_metadata.get('workflow_name', 'workflow')}#{release_metadata.get('run_number')}:{release_metadata.get('head_sha')[:7]}"
                query_details["release_metadata"] = release_metadata

            return ObservabilitySnapshot(
                metrics=metrics,
                logs=logs,
                traces=traces if traces else ["Prometheus metrics collected successfully."],
                release_hint=release_hint,
                provider="prometheus",
                query_details=query_details,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            logger.exception("Prometheus query failed; falling back to synthetic observability data.")
            snapshot = self._fallback_collect(incident)
            snapshot.query_details["error"] = str(exc)
            return snapshot

    def _query_value(self, query: str) -> float | None:
        headers = {"Accept": "application/json"}
        if self.settings.prometheus_bearer_token:
            headers["Authorization"] = f"Bearer {self.settings.prometheus_bearer_token}"

        response = httpx.get(
            f"{self.settings.prometheus_base_url.rstrip('/')}/api/v1/query",
            params={"query": query},
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        result = body.get("data", {}).get("result", [])
        if not result:
            return None

        value = result[0].get("value")
        if not value or len(value) < 2:
            return None
        return float(value[1])

    def _fallback_collect(self, incident: Incident) -> ObservabilitySnapshot:
        description = f"{incident.title} {incident.description}".lower()
        metrics = dict(incident.metrics)
        logs: list[str] = []
        traces: list[str] = []
        release_hint: str | None = None

        if "latency" in description or metrics.get("p95_latency_ms", 0) > 1500:
            logs.extend(
                [
                    "nginx upstream timed out after 2.1s",
                    "checkout-api p95 latency breached SLO window",
                ]
            )
            traces.extend(
                [
                    "trace_id=abc123 span=db.query duration_ms=820",
                    "trace_id=abc123 span=inventory.lookup duration_ms=640",
                ]
            )

        if "5xx" in description or metrics.get("error_rate", 0) > 0.08:
            logs.extend(
                [
                    "payments-api error burst detected after deployment revision 2026.03.31.4",
                    "HTTP 500 responses increased in service mesh telemetry",
                ]
            )
            traces.append("trace_id=rel001 span=release.validation duration_ms=420")
            release_hint = "release-2026.03.31.4"

        if not logs:
            logs.append("No strong log signal found from the initial telemetry sweep.")
        if not traces:
            traces.append("No trace anomalies found in the first-pass sample.")

        return ObservabilitySnapshot(
            metrics=metrics,
            logs=logs,
            traces=traces,
            release_hint=release_hint,
            provider="fallback",
            query_details={"release_metadata": self.release_client.latest_release() if self.release_client else None},
        )
