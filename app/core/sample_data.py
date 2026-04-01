from __future__ import annotations

from app.models import BenchmarkCase, CreateIncidentRequest, IncidentSeverity


def sample_incident() -> CreateIncidentRequest:
    return CreateIncidentRequest(
        title="Checkout latency spike during evening traffic",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="Customer checkout requests are timing out and latency rose after traffic increased.",
        source="grafana",
        metrics={"p95_latency_ms": 2100.0, "error_rate": 0.03},
        labels=["latency", "traffic", "checkout"],
    )


def sample_benchmark_cases() -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            title="Payments API 5xx spike after release",
            service="payments-api",
            environment="production",
            severity=IncidentSeverity.critical,
            description="5xx errors spiked after the latest deployment and customers cannot complete payments.",
            source="grafana",
            metrics={"error_rate": 0.14},
            expected_root_cause="bad deployment",
        ),
        BenchmarkCase(
            title="Search API latency incident",
            service="search-api",
            environment="production",
            severity=IncidentSeverity.high,
            description="Latency and timeouts increased during a traffic surge.",
            source="prometheus",
            metrics={"p95_latency_ms": 2200.0},
            expected_root_cause="dependency saturation",
        ),
    ]
