from __future__ import annotations

from collections import Counter, defaultdict

from app.models import AnalyticsSummary, BenchmarkSummary, Incident, IncidentRun, JobRecord, RunComparison


class IncidentAnalytics:
    def summarize(
        self,
        incidents: list[Incident],
        runs: list[IncidentRun],
        benchmarks: list[BenchmarkSummary],
        jobs: list[JobRecord],
    ) -> AnalyticsSummary:
        root_causes: Counter[str] = Counter()
        failure_categories: Counter[str] = Counter()
        action_outcomes: Counter[str] = Counter()
        profile_scores: dict[str, list[float]] = defaultdict(list)

        for run in runs:
            if run.investigation and run.investigation.suspected_root_cause:
                root_causes[run.investigation.suspected_root_cause] += 1
                for action in run.investigation.recommended_actions:
                    action_outcomes[action.status.value if hasattr(action.status, "value") else str(action.status)] += 1
            if run.evaluation:
                failure_categories.update(run.evaluation.failure_categories)

        for benchmark in benchmarks:
            profile_key = benchmark.profile_id if benchmark.model_route_id is None else f"{benchmark.profile_id} @ {benchmark.model_route_id}"
            profile_scores[profile_key].append(benchmark.average_score)

        return AnalyticsSummary(
            incident_count=len(incidents),
            run_count=len(runs),
            queued_jobs=sum(1 for job in jobs if job.status.value == "queued"),
            top_root_causes=[
                {"root_cause": cause, "count": count}
                for cause, count in root_causes.most_common(5)
            ],
            top_failure_categories=[
                {"category": category, "count": count}
                for category, count in failure_categories.most_common(6)
            ],
            action_outcomes=dict(sorted(action_outcomes.items())),
            profile_win_rates=[
                {
                    "profile": profile,
                    "average_score": round(sum(scores) / len(scores), 2),
                    "runs": len(scores),
                }
                for profile, scores in sorted(
                    profile_scores.items(),
                    key=lambda item: (sum(item[1]) / len(item[1])) if item[1] else 0.0,
                    reverse=True,
                )
            ],
        )

    def compare_runs(self, left: IncidentRun, right: IncidentRun) -> RunComparison:
        left_actions = {action.name for action in (left.investigation.recommended_actions if left.investigation else [])}
        right_actions = {action.name for action in (right.investigation.recommended_actions if right.investigation else [])}
        left_failures = set(left.evaluation.failure_categories if left.evaluation else [])
        right_failures = set(right.evaluation.failure_categories if right.evaluation else [])

        left_score = left.evaluation.score if left.evaluation else None
        right_score = right.evaluation.score if right.evaluation else None
        left_latency = left.evaluation.latency_ms if left.evaluation else None
        right_latency = right.evaluation.latency_ms if right.evaluation else None
        left_cost = left.evaluation.estimated_cost_usd if left.evaluation else None
        right_cost = right.evaluation.estimated_cost_usd if right.evaluation else None
        left_root = left.investigation.suspected_root_cause if left.investigation else ""
        right_root = right.investigation.suspected_root_cause if right.investigation else ""

        summary_parts: list[str] = []
        if left_score is not None and right_score is not None:
            delta = round(right_score - left_score, 2)
            summary_parts.append(f"Score changed by {delta:+.2f}.")
        if left_root != right_root:
            summary_parts.append("Root cause interpretation changed.")
        if left_actions != right_actions:
            summary_parts.append("Recommended actions changed.")
        if left_failures != right_failures:
            summary_parts.append("Failure taxonomy changed.")

        return RunComparison(
            left_run_id=left.id,
            right_run_id=right.id,
            incident_id=right.incident_id,
            score_delta=(round(right_score - left_score, 2) if left_score is not None and right_score is not None else None),
            latency_delta_ms=((right_latency or 0) - (left_latency or 0)) if left_latency is not None and right_latency is not None else None,
            cost_delta_usd=(
                round((right_cost or 0.0) - (left_cost or 0.0), 6)
                if left_cost is not None and right_cost is not None
                else None
            ),
            root_cause_changed=left_root != right_root,
            action_diff={
                "added": sorted(right_actions - left_actions),
                "removed": sorted(left_actions - right_actions),
            },
            failure_category_diff={
                "added": sorted(right_failures - left_failures),
                "removed": sorted(left_failures - right_failures),
            },
            summary=" ".join(summary_parts) if summary_parts else "The two runs are operationally similar.",
        )
