from __future__ import annotations

from app.models import Incident, IncidentSeverity, PolicyDecision, PolicyVerdict, ToolAction


class PolicyEngine:
    """Apply guardrails to remediation actions before execution."""

    def evaluate_action(self, incident: Incident, action: ToolAction) -> PolicyDecision:
        reasons: list[str] = []

        if incident.environment.lower() == "production" and action.name in {"restart_service", "rollback_release"}:
            reasons.append("Production-impacting remediations require human approval.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.approval_required,
                approved=False,
                requires_human_approval=True,
                reasons=reasons,
            )

        if action.name == "run_shell_command":
            reasons.append("Direct shell execution is blocked by policy.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.blocked,
                approved=False,
                requires_human_approval=False,
                reasons=reasons,
            )

        if incident.severity == IncidentSeverity.low and action.risk_level in {IncidentSeverity.high, IncidentSeverity.critical}:
            reasons.append("High-risk remediation is disproportionate for a low-severity incident.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.blocked,
                approved=False,
                requires_human_approval=False,
                reasons=reasons,
            )

        reasons.append("Action is allowed under current policy.")
        return PolicyDecision(
            action_id=action.id,
            verdict=PolicyVerdict.allowed,
            approved=True,
            requires_human_approval=False,
            reasons=reasons,
        )
