from app.core.policy import PolicyEngine
from app.models import Incident, IncidentSeverity, PolicyVerdict, ToolAction


def test_policy_requires_approval_for_production_rollback() -> None:
    engine = PolicyEngine()
    incident = Incident(
        title="API 5xx spike",
        service="payments-api",
        environment="production",
        severity=IncidentSeverity.critical,
        description="Customer requests failing with elevated 5xx rate",
        source="grafana",
    )
    action = ToolAction(
        name="rollback_release",
        description="Rollback the latest release",
        command="kubectl rollout undo deploy payments-api",
        risk_level=IncidentSeverity.high,
    )

    decision = engine.evaluate_action(incident, action)

    assert decision.verdict == PolicyVerdict.approval_required
    assert decision.requires_human_approval is True
