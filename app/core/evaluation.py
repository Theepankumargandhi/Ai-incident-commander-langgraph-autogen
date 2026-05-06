from __future__ import annotations

import json

from app.config import Settings
from app.core.judge_tuning import JUDGE_SYSTEM_PROMPT, JudgeFineTuneManager, build_judge_prompt_payload
from app.core.llm_json import OpenAIJsonClient
from app.core.openai_usage import snapshot_openai_usage
from app.models import Incident, IncidentRun, IncidentStatus, PolicyVerdict, RunEvaluation


class RunEvaluator:
    """Score runs with heuristic signals first, then optionally blend in an LLM judge."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm_client = OpenAIJsonClient(settings)
        self.judge_tuning = JudgeFineTuneManager(settings)

    async def evaluate(self, incident: Incident, run: IncidentRun, latency_ms: int, prompt_profile: str) -> RunEvaluation:
        heuristic_score = 52.0
        findings: list[str] = []
        failure_categories: list[str] = []

        evidence_count = len(run.investigation.evidence) if run.investigation else 0
        executed_actions = [
            action
            for action in (run.investigation.recommended_actions if run.investigation else [])
            if getattr(action.status, "value", action.status) == "executed"
        ]
        total_actions = len(run.investigation.recommended_actions) if run.investigation else 0
        action_accuracy = (len(executed_actions) / total_actions) if total_actions else 0.0

        if run.investigation and run.investigation.confidence >= 0.75:
            heuristic_score += 14
            findings.append("Investigation confidence is strong.")
        else:
            findings.append("Investigation confidence is moderate or low.")
            failure_categories.append("low_confidence")

        if run.investigation and run.investigation.recommended_actions:
            heuristic_score += 10
            findings.append("Run produced concrete remediation actions.")
        else:
            findings.append("Run did not produce remediation actions.")
            failure_categories.append("missing_actions")

        if evidence_count >= 3:
            heuristic_score += 6
            findings.append("Evidence coverage is broad enough for an operator handoff.")
        else:
            failure_categories.append("thin_evidence")
            findings.append("Evidence coverage is still shallow.")

        explanation_quality = 0.0
        if run.investigation and run.investigation.audience_summaries:
            filled = [
                run.investigation.audience_summaries.operator,
                run.investigation.audience_summaries.executive,
                run.investigation.audience_summaries.engineering,
                run.investigation.audience_summaries.postmortem,
            ]
            explanation_quality = sum(1 for item in filled if item) / len(filled)
            if explanation_quality >= 0.75:
                heuristic_score += 5
                findings.append("Audience-specific explanations were generated.")
            else:
                failure_categories.append("weak_explanations")

        blocked_actions = sum(1 for decision in run.policy_decisions if decision.verdict == PolicyVerdict.blocked)
        if blocked_actions:
            findings.append("Policy engine blocked unsafe remediation actions.")
            heuristic_score += min(10, blocked_actions * 2)

        approval_gates = sum(
            1 for decision in run.policy_decisions if decision.verdict == PolicyVerdict.approval_required
        )
        if approval_gates:
            findings.append("Approval gates were inserted for risky actions.")
            heuristic_score += min(8, approval_gates * 2)

        if executed_actions:
            heuristic_score += min(8, len(executed_actions) * 2)
            findings.append("At least one remediation action was executed.")

        if run.investigation and run.investigation.reflection_notes:
            heuristic_score += 4
            findings.append("Reflection loop revised the investigation before finalization.")

        if incident.status == IncidentStatus.resolved:
            heuristic_score += 10
            findings.append("Incident reached a resolved state.")
        else:
            failure_categories.append("unresolved")

        if latency_ms > 7000:
            heuristic_score -= 10
            findings.append("Workflow latency exceeded the target threshold.")
            failure_categories.append("high_latency")
        elif latency_ms > 3500:
            heuristic_score -= 4
            findings.append("Workflow latency is above the preferred target.")

        if not run.memory_hits:
            findings.append("No strong historical memory match was found.")

        heuristic_score = max(0.0, min(heuristic_score, 100.0))
        sub_scores = {
            "root_cause_quality": 85.0 if run.investigation and run.investigation.confidence >= 0.8 else 68.0,
            "evidence_sufficiency": min(100.0, evidence_count * 24.0),
            "action_safety": 88.0 if blocked_actions or approval_gates else 72.0,
            "explanation_quality": round(explanation_quality * 100.0, 2),
            "postmortem_usefulness": 80.0 if run.postmortem_summary else 62.0,
        }

        judge_score = heuristic_score
        judge_rationale = "Heuristic judge fallback used because the LLM judge was unavailable."
        judge_used_llm = False
        if self.llm_client.is_available():
            try:
                judge_payload = await self._judge_with_llm(incident, run, latency_ms, prompt_profile, sub_scores)
                judge_score = float(judge_payload.get("overall_score", heuristic_score))
                judge_rationale = str(judge_payload.get("rationale", "")).strip() or None
                judge_used_llm = True
                llm_sub_scores = {
                    key: float(value)
                    for key, value in dict(judge_payload.get("sub_scores", {})).items()
                    if isinstance(value, (int, float))
                }
                if llm_sub_scores:
                    sub_scores.update(llm_sub_scores)
                extra_findings = [str(item) for item in judge_payload.get("findings", []) if str(item).strip()]
                findings.extend(extra_findings[:3])
            except Exception:
                judge_score = heuristic_score
                judge_rationale = "LLM judge unavailable; heuristic score used."

        tracked_usage = snapshot_openai_usage(trace_id=run.trace_id)
        if tracked_usage is not None and tracked_usage.total_tokens > 0:
            usage_cost = tracked_usage.estimated_cost_usd
        else:
            usage_cost = run.usage.estimated_cost_usd if run.usage else 0.0

        if usage_cost > 0.08:
            heuristic_score = max(0.0, heuristic_score - 3)
            findings.append("Model cost is high relative to the task size.")
            failure_categories.append("high_cost")

        score = heuristic_score
        if judge_score is not None:
            score = round((heuristic_score * 0.55) + (judge_score * 0.45), 2)

        return RunEvaluation(
            score=max(0.0, min(score, 100.0)),
            heuristic_score=heuristic_score,
            judge_score=judge_score,
            judge_used_llm=judge_used_llm,
            judge_rationale=judge_rationale,
            sub_scores={key: round(value, 2) for key, value in sub_scores.items()},
            findings=findings,
            latency_ms=latency_ms,
            prompt_profile=prompt_profile,
            estimated_cost_usd=usage_cost,
            action_accuracy=round(action_accuracy, 4),
            failure_categories=sorted(set(failure_categories)),
        )

    async def _judge_with_llm(
        self,
        incident: Incident,
        run: IncidentRun,
        latency_ms: int,
        prompt_profile: str,
        sub_scores: dict[str, float],
    ) -> dict:
        user_prompt = json.dumps(
            build_judge_prompt_payload(
                incident=incident.model_dump(mode="json"),
                run=run.model_dump(mode="json"),
                latency_ms=latency_ms,
                prompt_profile=prompt_profile,
                heuristic_sub_scores=sub_scores,
            ),
            indent=2,
            default=str,
        )
        model = self.judge_tuning.resolve_active_judge_model() or self.settings.openai_model
        return await self.llm_client.complete_json(
            model=model,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            usage_source="judge_evaluation",
        )
