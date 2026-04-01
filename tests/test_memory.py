from app.core.memory import IncidentMemory
from app.models import Incident, IncidentRun, IncidentSeverity, InvestigationResult


def test_memory_returns_most_relevant_hit() -> None:
    memory = IncidentMemory()
    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="Latency increased after a dependency slowdown",
        source="grafana",
        labels=["latency", "checkout"],
    )
    matching_run = IncidentRun(
        incident_id="past-1",
        investigation=InvestigationResult(
            run_id="run-1",
            summary="checkout-api latency incident investigation",
            suspected_root_cause="Latency spike caused by upstream dependency saturation.",
            confidence=0.8,
            prompt_profile="balanced-v1",
        ),
    )
    unrelated_run = IncidentRun(
        incident_id="past-2",
        investigation=InvestigationResult(
            run_id="run-2",
            summary="background worker memory issue",
            suspected_root_cause="Heap fragmentation in worker pool.",
            confidence=0.7,
            prompt_profile="balanced-v1",
        ),
    )

    hits = memory.find_similar(incident, [unrelated_run, matching_run])

    assert hits
    assert hits[0].incident_id == "past-1"
