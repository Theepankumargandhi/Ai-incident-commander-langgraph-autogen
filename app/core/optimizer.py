from __future__ import annotations

from collections import Counter

from app.models import BenchmarkSummary, IncidentRun, OptimizerRecommendation


class AgentOptimizer:
    def recommend(
        self,
        summaries: list[BenchmarkSummary],
        runs: list[IncidentRun],
    ) -> OptimizerRecommendation:
        if not summaries:
            return OptimizerRecommendation(
                recommended_profile_id="balanced-v1",
                recommended_model_route_id="balanced-route",
                rationale="Not enough benchmark history exists yet, so the optimizer is keeping the current balanced defaults.",
                expected_improvements=["Run more benchmarks to produce optimization evidence."],
                prompt_adjustments=["Increase benchmark coverage before auto-tuning prompts."],
                memory_strategy=["Keep weighted memory retrieval enabled."],
                optimizer_actions=["Collect more benchmark data."],
                confidence=0.32,
            )

        def composite(summary: BenchmarkSummary) -> float:
            return (
                summary.average_score * 0.52
                + summary.average_judge_score * 0.18
                + (summary.root_cause_match_rate * 100.0) * 0.18
                + (summary.action_accuracy_rate * 100.0) * 0.12
            )

        best = max(summaries, key=composite)
        failure_counts: Counter[str] = Counter()
        for summary in summaries:
            failure_counts.update(summary.failure_category_counts)

        top_failures = [item for item, _ in failure_counts.most_common(4)]
        expected_improvements: list[str] = []
        prompt_adjustments: list[str] = []
        memory_strategy: list[str] = []
        optimizer_actions: list[str] = []

        if "thin_evidence" in failure_counts:
            expected_improvements.append("Raise evidence sufficiency and reduce weak investigations.")
            prompt_adjustments.append("Require at least three concrete evidence signals before recommending risky actions.")
            memory_strategy.append("Increase the weight of abstract lessons and playbook retrieval for sparse evidence cases.")
            optimizer_actions.append("Bias the planner toward runbook and causal-agent turns earlier in the conversation.")
        if "low_confidence" in failure_counts:
            expected_improvements.append("Improve root-cause confidence on ambiguous incidents.")
            prompt_adjustments.append("Route ambiguous incidents to stronger investigator and commander models.")
            optimizer_actions.append("Prefer the quality route when confidence collapses on benchmark cases.")
        if "high_latency" in failure_counts:
            expected_improvements.append("Reduce total workflow latency during complex runs.")
            prompt_adjustments.append("Shorten reflection loops when the critic does not add new evidence.")
            optimizer_actions.append("Prefer balanced or cost-aware routes for low-severity incidents.")
        if "weak_explanations" in failure_counts:
            expected_improvements.append("Improve operator, executive, and postmortem summaries.")
            prompt_adjustments.append("Strengthen postmortem-writer and commander formatting instructions.")
        if "high_cost" in failure_counts:
            expected_improvements.append("Lower cost without losing too much investigation quality.")
            optimizer_actions.append("Move critic and postmortem roles to lighter models for routine incidents.")

        if not memory_strategy:
            memory_strategy.append("Keep current memory weighting, but prioritize service playbooks for recurring incidents.")
        if not expected_improvements:
            expected_improvements.append("Consolidate current gains and benchmark on harder adversarial scenarios.")
        if not prompt_adjustments:
            prompt_adjustments.append("Keep the current prompts and use the best-scoring profile as the default baseline.")
        if not optimizer_actions:
            optimizer_actions.append("Adopt the current best profile and route for the next benchmark cycle.")

        rationale = (
            f"The optimizer selected {best.profile_id} with {best.model_route_id or 'default'} because it leads on the "
            f"current composite score across overall quality, judge score, root-cause match rate, and action accuracy."
        )

        return OptimizerRecommendation(
            recommended_profile_id=best.profile_id,
            recommended_model_route_id=best.model_route_id or "balanced-route",
            rationale=rationale,
            expected_improvements=expected_improvements,
            prompt_adjustments=prompt_adjustments,
            memory_strategy=memory_strategy,
            optimizer_actions=optimizer_actions,
            top_failure_categories=top_failures,
            confidence=min(0.95, 0.45 + (len(summaries) * 0.06) + (len(runs) * 0.01)),
        )
