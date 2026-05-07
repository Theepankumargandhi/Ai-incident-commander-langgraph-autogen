from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.analytics import IncidentAnalytics
from app.core.runbooks import RunbookRAGEngine
from app.core.storage import RunbookStore
from app.models import (
    ActionStatus,
    Incident,
    IncidentRun,
    IncidentSeverity,
    IncidentStatus,
    InvestigationResult,
    RemediationReceipt,
    RunEvaluation,
    RunStatus,
    ToolAction,
    utc_now,
)


def _temp_dir() -> Path:
    path = Path.cwd() / "pytest-cache-files-runbooks-dora" / str(uuid4())
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_runbook_rag_ingests_and_retrieves_service_citation() -> None:
    data_dir = _temp_dir()
    engine = RunbookRAGEngine(RunbookStore(data_dir / "runbooks.json"), Settings(app_env="test", data_dir=data_dir))
    engine.ingest_document(
        title="Checkout API Latency Runbook",
        service="checkout-api",
        environment="production",
        tags=["latency", "checkout-api"],
        content=(
            "# Checkout API Latency Runbook\n"
            "When checkout-api p95 latency exceeds the SLO, verify inventory dependency latency, "
            "scale checkout workers, and notify the on-call engineer."
        ),
    )
    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="p95 latency is above SLO.",
        source="test",
        metrics={"p95_latency_ms": 2400.0},
    )

    hits = engine.retrieve_for_incident(incident)

    assert hits
    assert hits[0].title == "Checkout API Latency Runbook"
    assert "inventory dependency" in hits[0].excerpt
    assert hits[0].score > 0


def test_runbook_rag_ingests_directory_markdown_files() -> None:
    data_dir = _temp_dir()
    runbook_dir = data_dir / "runbooks"
    runbook_dir.mkdir()
    (runbook_dir / "payments-api-release.md").write_text(
        "# Payments API Release Rollback\nIf payments-api 5xx errors spike after release, rollback latest deployment.",
        encoding="utf-8",
    )
    settings = Settings(app_env="test", data_dir=data_dir, runbook_dir=runbook_dir)
    engine = RunbookRAGEngine(RunbookStore(data_dir / "runbooks.json"), settings)

    documents = engine.ingest_directory()

    assert len(documents) == 1
    assert documents[0].title == "Payments API Release Rollback"
    assert documents[0].service == "payments-api"


def test_dora_sre_dashboard_computes_core_metrics() -> None:
    now = utc_now()
    incident = Incident(
        title="Payments 5xx after release",
        service="payments-api",
        environment="production",
        severity=IncidentSeverity.critical,
        description="5xx errors spiked after deployment release-42.",
        source="pagerduty",
        labels=["release:release-42"],
        status=IncidentStatus.resolved,
        created_at=now - timedelta(minutes=30),
        updated_at=now,
        context={"observability": {"release_hint": "release-42"}},
    )
    action = ToolAction(
        name="rollback_release",
        description="Rollback latest deployment.",
        command="rollback latest",
        risk_level=IncidentSeverity.high,
        status=ActionStatus.executed,
    )
    run = IncidentRun(
        incident_id=incident.id,
        status=RunStatus.completed,
        started_at=incident.created_at + timedelta(minutes=1),
        finished_at=incident.updated_at,
        investigation=InvestigationResult(
            run_id="run-1",
            summary="Release regression.",
            suspected_root_cause="bad deployment",
            confidence=0.91,
            recommended_actions=[action],
            prompt_profile="balanced-v1",
        ),
        evaluation=RunEvaluation(
            score=94,
            heuristic_score=90,
            latency_ms=1200,
            prompt_profile="balanced-v1",
            estimated_cost_usd=0.012,
        ),
    )
    receipt = RemediationReceipt(
        action_id=action.id,
        action_name=action.name,
        incident_id=incident.id,
        status="executed",
        message="Rollback executed.",
        executed_at=now,
    )

    dashboard = IncidentAnalytics().dora_sre_dashboard([incident], [run], [], [receipt], window_days=30)

    assert dashboard.dora["deployment_related_incidents"] == 1
    assert dashboard.dora["change_failure_rate"] == 1.0
    assert dashboard.sre["mttr_minutes"] == 30.0
    assert dashboard.sre["automation_success_rate"] == 1.0
    assert dashboard.sre["p95_run_latency_ms"] == 1200.0
    metric_keys = {metric.key for metric in dashboard.metrics}
    assert {"deployment_frequency_per_day", "mttr_minutes", "p95_run_latency_ms"}.issubset(metric_keys)
