import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.policy import PolicyEngine
from app.core.storage import PolicyDocumentStore
from app.models import Incident, IncidentSeverity, PolicyDocument, PolicyVerdict, ToolAction


def _make_local_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"policy-test-{uuid4()}-"))

# ---------------------------------------------------------------------------
# Rule: approval-prod-disruptive-action
# Production rollback and restart always require human sign-off.
# ---------------------------------------------------------------------------


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

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.approval_required
    assert decision.requires_human_approval is True
    assert decision.policy_rule == "approval-prod-disruptive-action"


def test_policy_requires_approval_for_production_restart() -> None:
    engine = PolicyEngine()
    incident = Incident(
        title="Service unresponsive",
        service="orders-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="Health checks failing on all pods",
        source="pagerduty",
    )
    action = ToolAction(
        name="restart_service",
        description="Restart the orders-api deployment",
        command="kubectl rollout restart deploy orders-api",
        risk_level=IncidentSeverity.high,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.approval_required
    assert decision.requires_human_approval is True
    assert decision.policy_rule == "approval-prod-disruptive-action"


# ---------------------------------------------------------------------------
# Rule: block-shell-command
# Direct shell execution is always blocked regardless of environment.
# ---------------------------------------------------------------------------


def test_policy_blocks_shell_command() -> None:
    engine = PolicyEngine()
    incident = Incident(
        title="Disk usage alert",
        service="data-worker",
        environment="production",
        severity=IncidentSeverity.medium,
        description="Disk utilisation above 90%",
        source="grafana",
    )
    action = ToolAction(
        name="run_shell_command",
        description="Remove old log files",
        command="rm -rf /var/log/app/*.log",
        risk_level=IncidentSeverity.high,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.blocked
    assert decision.requires_human_approval is False
    assert decision.policy_rule == "block-shell-command"


# ---------------------------------------------------------------------------
# Rule: allow-prod-safe-scale
# Scaling is allowed in production without requiring approval.
# ---------------------------------------------------------------------------


def test_policy_allows_safe_scale_in_production() -> None:
    engine = PolicyEngine()
    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="p95 latency above threshold during peak traffic",
        source="grafana",
    )
    action = ToolAction(
        name="scale_service",
        description="Scale checkout-api to 6 replicas",
        command="scale service checkout-api to 6 replicas",
        risk_level=IncidentSeverity.medium,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.allowed
    assert decision.approved is True
    assert decision.policy_rule == "allow-prod-safe-scale"


# ---------------------------------------------------------------------------
# Rule: block-disproportionate-risk
# High/critical-risk actions are blocked when incident severity is low.
# ---------------------------------------------------------------------------


def test_policy_blocks_disproportionate_risk_for_low_severity() -> None:
    engine = PolicyEngine()
    # Staging environment avoids triggering the production-disruptive rule first.
    incident = Incident(
        title="Minor configuration drift",
        service="config-service",
        environment="staging",
        severity=IncidentSeverity.low,
        description="Non-critical config value out of expected range",
        source="prometheus",
    )
    action = ToolAction(
        name="rollback_release",
        description="Full rollback of latest release",
        command="rollback latest deployment",
        risk_level=IncidentSeverity.critical,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.blocked
    assert decision.requires_human_approval is False
    assert decision.policy_rule == "block-disproportionate-risk"


# ---------------------------------------------------------------------------
# Rule: allow-nonprod-restart
# Restarting a service in a non-production environment is always allowed.
# ---------------------------------------------------------------------------


def test_policy_allows_nonprod_restart() -> None:
    engine = PolicyEngine()
    incident = Incident(
        title="Worker process deadlock",
        service="task-worker",
        environment="staging",
        severity=IncidentSeverity.medium,
        description="Worker threads appear deadlocked on a mutex",
        source="logs",
    )
    action = ToolAction(
        name="restart_service",
        description="Restart the staging task-worker",
        command="restart staging task-worker",
        risk_level=IncidentSeverity.medium,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.allowed
    assert decision.approved is True
    assert decision.policy_rule == "allow-nonprod-restart"


# ---------------------------------------------------------------------------
# Default-allow catch-all
# Ordinary, low-risk actions that match no specific rule are allowed.
# ---------------------------------------------------------------------------


def test_policy_default_allows_standard_diagnostic_action() -> None:
    engine = PolicyEngine()
    incident = Incident(
        title="Elevated error rate",
        service="search-api",
        environment="staging",
        severity=IncidentSeverity.medium,
        description="5xx rate at 3% over the last 10 minutes",
        source="grafana",
    )
    action = ToolAction(
        name="collect_diagnostics",
        description="Collect diagnostic bundle for review",
        command="collect diagnostics bundle",
        risk_level=IncidentSeverity.low,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.allowed
    assert decision.approved is True
    assert decision.policy_rule == "default-allow"


def test_policy_document_can_be_updated_and_versioned() -> None:
    store = PolicyDocumentStore(_make_local_temp_dir() / "policy_documents.json")
    engine = PolicyEngine(policy_store=store)

    original = engine.get_policy_document()
    updated = engine.update_policy_document(
        content="Only allow collect_diagnostics automatically. Everything else requires approval.",
        name="strict-policy",
        updated_by="tester",
    )

    assert original.version == 1
    assert updated.version == 2
    assert updated.name == "strict-policy"
    assert updated.updated_by == "tester"
    assert store.get("active").content.startswith("Only allow collect_diagnostics")


def test_policy_prefers_llm_evaluator_when_available() -> None:
    store = PolicyDocumentStore(_make_local_temp_dir() / "policy_documents.json")
    engine = PolicyEngine(
        settings=Settings(app_env="test", openai_api_key="test-key", openai_model="gpt-4.1-mini"),
        policy_store=store,
    )
    engine.update_policy_document(
        content="Block rollback_release in all environments unless manually approved.",
        updated_by="tester",
    )

    async def fake_complete_json(**kwargs):
        assert kwargs["usage_source"] == "policy_evaluation"
        assert "rollback_release" in kwargs["user_prompt"]
        return {
            "verdict": "BLOCK",
            "reasons": ["The policy document explicitly blocks automatic rollback for this class of action."],
            "policy_rule": "policy-doc-block-rollback",
        }

    engine.llm_client.complete_json = fake_complete_json  # type: ignore[method-assign]

    incident = Incident(
        title="Staging rollback request",
        service="payments-api",
        environment="staging",
        severity=IncidentSeverity.medium,
        description="A release introduced errors but the policy document should decide the control.",
        source="grafana",
    )
    action = ToolAction(
        name="rollback_release",
        description="Rollback the latest release",
        command="kubectl rollout undo deploy payments-api",
        risk_level=IncidentSeverity.high,
    )

    decision = asyncio.run(engine.evaluate_action(incident, action))

    assert decision.verdict == PolicyVerdict.blocked
    assert decision.policy_rule == "policy-doc-block-rollback"
    assert "explicitly blocks automatic rollback" in decision.reasons[0]
