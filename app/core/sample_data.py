from __future__ import annotations

from app.models import BenchmarkCase, CreateIncidentRequest, IncidentSeverity


def sample_incident() -> CreateIncidentRequest:
    return CreateIncidentRequest(
        title="Checkout latency spike during evening traffic",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="Customer checkout requests are timing out and latency rose after traffic increased.",
        source="prometheus",
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
            source="prometheus",
            metrics={"error_rate": 0.14},
            expected_root_cause="bad deployment",
            expected_actions=["rollback_release", "open_ticket", "notify_on_call"],
            tags=["release", "payments", "5xx"],
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
            expected_actions=["scale_service", "notify_on_call"],
            tags=["latency", "traffic", "search"],
        ),
        BenchmarkCase(
            title="Inventory staging restart candidate",
            service="inventory-api",
            environment="staging",
            severity=IncidentSeverity.medium,
            description="After a schema refresh, background workers are stuck and a restart is expected to be safe.",
            source="prometheus",
            metrics={"error_rate": 0.02},
            expected_root_cause="broader observability review",
            expected_actions=["collect_diagnostics", "notify_on_call"],
            tags=["staging", "restart", "inventory"],
        ),
        BenchmarkCase(
            title="Orders API customer timeouts",
            service="orders-api",
            environment="production",
            severity=IncidentSeverity.high,
            description="Customer requests are timing out and p95 latency is above the SLO.",
            source="prometheus",
            metrics={"p95_latency_ms": 2600.0, "error_rate": 0.05},
            expected_root_cause="dependency saturation",
            expected_actions=["scale_service", "notify_on_call"],
            tags=["orders", "latency"],
        ),
        BenchmarkCase(
            title="Billing API high error rate with release hint",
            service="billing-api",
            environment="production",
            severity=IncidentSeverity.critical,
            description="5xx error rate is elevated and traffic is stable after the latest deployment.",
            source="prometheus",
            metrics={"error_rate": 0.17},
            expected_root_cause="bad deployment",
            expected_actions=["rollback_release", "open_ticket"],
            tags=["billing", "release", "critical"],
        ),
    ]
