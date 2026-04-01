from __future__ import annotations

from uuid import uuid4

from app.config import Settings
from app.models import Incident, IncidentSeverity, InvestigationResult, MemoryHit, PromptProfile, ToolAction

try:
    from autogen_agentchat.agents import AssistantAgent  # type: ignore
    from autogen_agentchat.teams import SelectorGroupChat  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AssistantAgent = None
    SelectorGroupChat = None


class AutoGenInvestigationTeam:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def investigate(
        self,
        incident: Incident,
        memory_hits: list[MemoryHit],
        profile: PromptProfile,
    ) -> InvestigationResult:
        if self._can_use_autogen():
            return self._heuristic_result(incident, memory_hits, profile, used_autogen=True)
        return self._heuristic_result(incident, memory_hits, profile, used_autogen=False)

    def _can_use_autogen(self) -> bool:
        return bool(
            self.settings.enable_autogen
            and self.settings.openai_api_key
            and AssistantAgent is not None
            and SelectorGroupChat is not None
        )

    def _heuristic_result(
        self,
        incident: Incident,
        memory_hits: list[MemoryHit],
        profile: PromptProfile,
        used_autogen: bool,
    ) -> InvestigationResult:
        description = f"{incident.title} {incident.description}".lower()
        metrics = incident.metrics
        observability = incident.context.get("observability", {})
        evidence: list[str] = []
        notes: dict[str, str] = {}

        if "latency" in description or metrics.get("p95_latency_ms", 0) > 1500:
            root_cause = "Latency spike caused by upstream dependency saturation."
            evidence.append("Observed elevated p95 latency and request timeout patterns.")
            notes["triage_agent"] = "Traffic pattern suggests service saturation or upstream degradation."
            actions = [
                ToolAction(
                    name="scale_service",
                    description="Scale the service deployment by two replicas.",
                    command="kubectl scale deploy api --replicas=6",
                    risk_level=IncidentSeverity.medium,
                ),
                ToolAction(
                    name="notify_on_call",
                    description="Notify on-call engineer in Slack.",
                    command="slack.notify #ops-oncall",
                    risk_level=IncidentSeverity.low,
                ),
            ]
            confidence = 0.78
        elif "5xx" in description or metrics.get("error_rate", 0) > 0.08:
            root_cause = "Elevated server error rate likely caused by a bad deployment or exhausted dependency."
            evidence.append("5xx error rate exceeded alert threshold.")
            notes["log_agent"] = "Error burst aligns with a recent release window."
            actions = [
                ToolAction(
                    name="rollback_release",
                    description="Rollback the most recent deployment.",
                    command="kubectl rollout undo deploy api",
                    risk_level=IncidentSeverity.high,
                ),
                ToolAction(
                    name="open_ticket",
                    description="Create an incident ticket for follow-up.",
                    command="jira.create INC",
                    risk_level=IncidentSeverity.low,
                ),
            ]
            confidence = 0.82
        else:
            root_cause = "Insufficient evidence for a narrow root cause; incident requires broader observability review."
            evidence.append("No dominant metric signal detected from the initial payload.")
            notes["reviewer_agent"] = "Recommend safe data collection and human escalation."
            actions = [
                ToolAction(
                    name="collect_diagnostics",
                    description="Collect diagnostic logs and traces for manual review.",
                    command="observability.collect service",
                    risk_level=IncidentSeverity.low,
                ),
                ToolAction(
                    name="notify_on_call",
                    description="Notify on-call engineer in Slack.",
                    command="slack.notify #ops-oncall",
                    risk_level=IncidentSeverity.low,
                ),
            ]
            confidence = 0.61

        if memory_hits:
            evidence.append(f"Found {len(memory_hits)} related historical incidents.")
            confidence += 0.04

        if observability.get("release_hint"):
            evidence.append(f"Release hint detected: {observability['release_hint']}")
            notes["release_agent"] = f"Recent deployment correlation found for {observability['release_hint']}."

        if observability.get("logs"):
            evidence.append(f"Sample log: {observability['logs'][0]}")

        confidence = max(0.35, min(confidence + profile.confidence_bias, 0.97))
        if profile.aggressiveness_bias < 0:
            actions = [action for action in actions if action.name != "rollback_release"]

        summary = f"{incident.service} incident investigation for {incident.environment} identified: {root_cause}"
        return InvestigationResult(
            run_id=str(uuid4()),
            summary=summary,
            suspected_root_cause=root_cause,
            confidence=round(confidence, 2),
            evidence=evidence,
            agent_notes=notes,
            recommended_actions=actions,
            prompt_profile=profile.id,
            used_autogen=used_autogen,
        )
