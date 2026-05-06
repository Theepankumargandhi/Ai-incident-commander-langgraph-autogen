import tempfile
from pathlib import Path
from uuid import uuid4

from app.core.memory import IncidentMemory
from app.core.storage import MemoryStore
from app.models import Incident, IncidentRun, IncidentSeverity, InvestigationResult


def test_memory_returns_most_relevant_hit() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix=f"incident-memory-{uuid4()}-"))
    memory = IncidentMemory(MemoryStore(temp_dir / "memory.json"))
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
        postmortem_summary="Scaled checkout-api and service recovered.",
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

    memory.remember(incident, matching_run)
    memory.remember(
        Incident(
            title="Worker memory issue",
            service="workers",
            environment="production",
            severity=IncidentSeverity.medium,
            description="Background worker heap growth",
            source="grafana",
        ),
        unrelated_run,
    )

    hits = memory.find_similar(incident)

    assert hits
    assert hits[0].service == "checkout-api"
    assert "dependency saturation" in hits[0].summary.lower()
