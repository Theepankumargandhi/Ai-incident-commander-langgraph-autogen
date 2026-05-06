from __future__ import annotations

from collections import Counter

from app.models import BenchmarkCase, BenchmarkSummary, Incident, IncidentRun, PromptProfile


class BenchmarkEngine:
    """Summarize benchmark runs across prompt profiles."""

    def summarize(
        self,
        profile: PromptProfile,
        model_route_id: str | None,
        cases: list[BenchmarkCase],
        runs: list[tuple[BenchmarkCase, Incident, IncidentRun]],
        previous_summary: BenchmarkSummary | None = None,
    ) -> BenchmarkSummary:
        matches = 0
        details: list[dict] = []
        scores: list[float] = []
        latencies: list[int] = []
        costs: list[float] = []
        confidences: list[float] = []
        judge_scores: list[float] = []
        action_accuracies: list[float] = []
        failure_counts: Counter[str] = Counter()
        approval_count = 0
        executed_count = 0
        total_action_sets = 0

        for case, incident, run in runs:
            suspected = run.investigation.suspected_root_cause.lower() if run.investigation else ""
            expected = case.expected_root_cause.lower()
            matched = expected in suspected or suspected in expected
            matches += 1 if matched else 0
            if run.investigation:
                confidences.append(run.investigation.confidence)
            if run.evaluation:
                scores.append(run.evaluation.score)
                latencies.append(run.evaluation.latency_ms)
                costs.append(run.evaluation.estimated_cost_usd)
                if run.evaluation.judge_score is not None:
                    judge_scores.append(run.evaluation.judge_score)
                action_accuracies.append(run.evaluation.action_accuracy)
                failure_counts.update(run.evaluation.failure_categories)
            if run.policy_decisions:
                approval_count += sum(1 for decision in run.policy_decisions if decision.requires_human_approval)
            if run.investigation and run.investigation.recommended_actions:
                total_action_sets += len(run.investigation.recommended_actions)
                executed_count += sum(
                    1
                    for action in run.investigation.recommended_actions
                    if getattr(action.status, "value", action.status) == "executed"
                )

            action_names = [action.name for action in run.investigation.recommended_actions] if run.investigation else []
            details.append(
                {
                    "case_id": case.id,
                    "incident_id": incident.id,
                    "run_id": run.id,
                    "matched_root_cause": matched,
                    "score": run.evaluation.score if run.evaluation else None,
                    "latency_ms": run.evaluation.latency_ms if run.evaluation else None,
                    "estimated_cost_usd": run.evaluation.estimated_cost_usd if run.evaluation else None,
                    "judge_score": run.evaluation.judge_score if run.evaluation else None,
                    "action_accuracy": run.evaluation.action_accuracy if run.evaluation else None,
                    "recommended_actions": action_names,
                    "failure_categories": run.evaluation.failure_categories if run.evaluation else [],
                    "tags": list(case.tags),
                    "model_route_id": run.investigation.model_route_id if run.investigation else model_route_id,
                    "agent_models": run.investigation.agent_models if run.investigation else {},
                }
            )

        average_score = sum(scores) / len(scores) if scores else 0.0
        average_latency = sum(latencies) / len(latencies) if latencies else 0.0
        average_cost = sum(costs) / len(costs) if costs else 0.0
        match_rate = (matches / len(cases)) if cases else 0.0
        average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        average_judge_score = sum(judge_scores) / len(judge_scores) if judge_scores else 0.0
        approval_rate = approval_count / max(len(cases), 1)
        executed_action_rate = executed_count / max(total_action_sets, 1) if total_action_sets else 0.0
        action_accuracy_rate = sum(action_accuracies) / len(action_accuracies) if action_accuracies else 0.0

        return BenchmarkSummary(
            profile_id=profile.id,
            model_route_id=model_route_id,
            total_cases=len(cases),
            average_score=average_score,
            root_cause_match_rate=match_rate,
            average_latency_ms=average_latency,
            average_cost_usd=average_cost,
            average_confidence=average_confidence,
            average_judge_score=average_judge_score,
            approval_rate=approval_rate,
            executed_action_rate=executed_action_rate,
            action_accuracy_rate=action_accuracy_rate,
            failure_category_counts=dict(sorted(failure_counts.items())),
            score_delta=(average_score - previous_summary.average_score) if previous_summary else None,
            match_rate_delta=(match_rate - previous_summary.root_cause_match_rate) if previous_summary else None,
            details=details,
        )
