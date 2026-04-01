from __future__ import annotations

from app.models import Incident, ObservabilitySnapshot


class MockObservabilityClient:
    """Local development adapter that synthesizes observability context."""

    def collect(self, incident: Incident) -> ObservabilitySnapshot:
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
            logs.append("No strong log signal found from initial telemetry sweep.")
        if not traces:
            traces.append("No trace anomalies found in the first-pass sample.")

        return ObservabilitySnapshot(
            metrics=metrics,
            logs=logs,
            traces=traces,
            release_hint=release_hint,
        )
