from __future__ import annotations

from app.models import Incident, IncidentRun, IncidentStatus, PolicyVerdict, RunEvaluation


class RunEvaluator:
    """Score runs using lightweight heuristics until full judges are added."""

    def evaluate(self, incident: Incident, run: IncidentRun, latency_ms: int, prompt_profile: str) -> RunEvaluation:
        score = 55.0
        findings: list[str] = []

        if run.investigation and run.investigation.confidence >= 0.75:
            score += 15
            findings.append("Investigation confidence is strong.")
        else:
            findings.append("Investigation confidence is moderate or low.")

        if run.investigation and run.investigation.recommended_actions:
            score += 10
            findings.append("Run produced concrete remediation actions.")
        else:
            findings.append("Run did not produce remediation actions.")

        blocked_actions = sum(1 for decision in run.policy_decisions if decision.verdict == PolicyVerdict.blocked)
        if blocked_actions:
            findings.append("Policy engine blocked unsafe remediation actions.")
            score += min(10, blocked_actions * 2)

        if incident.status == IncidentStatus.resolved:
            score += 10
            findings.append("Incident reached a resolved state.")

        if latency_ms > 5000:
            score -= 10
            findings.append("Workflow latency exceeded target threshold.")

        return RunEvaluation(
            score=max(0.0, min(score, 100.0)),
            findings=findings,
            latency_ms=latency_ms,
            prompt_profile=prompt_profile,
        )
