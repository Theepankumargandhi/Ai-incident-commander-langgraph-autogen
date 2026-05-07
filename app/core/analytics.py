from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from app.models import (
    AnalyticsSummary,
    BenchmarkSummary,
    DoraSreDashboard,
    DoraSreMetric,
    Incident,
    IncidentRun,
    IncidentStatus,
    JobRecord,
    RemediationReceipt,
    RunComparison,
    RunStatus,
    utc_now,
)


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

    def dora_sre_dashboard(
        self,
        incidents: list[Incident],
        runs: list[IncidentRun],
        jobs: list[JobRecord],
        remediations: list[RemediationReceipt],
        *,
        window_days: int = 30,
    ) -> DoraSreDashboard:
        window_days = max(1, min(window_days, 365))
        now = utc_now()
        window_start = now - timedelta(days=window_days)
        window_incidents = [incident for incident in incidents if incident.created_at >= window_start]
        window_runs = [run for run in runs if run.started_at >= window_start]
        window_jobs = [job for job in jobs if job.created_at >= window_start]
        window_remediations = [receipt for receipt in remediations if receipt.executed_at >= window_start]

        deployment_related = [incident for incident in window_incidents if self._is_deployment_related(incident)]
        release_keys = {self._release_key(incident) for incident in deployment_related}
        release_keys.discard("")
        inferred_deployments = max(len(release_keys), len(deployment_related))
        deployment_frequency_per_day = round(inferred_deployments / window_days, 4)
        change_failure_rate = round((len(deployment_related) / inferred_deployments), 4) if inferred_deployments else 0.0

        completed_runs = [run for run in window_runs if run.status == RunStatus.completed and run.finished_at is not None]
        failed_runs = [run for run in window_runs if run.status == RunStatus.failed]
        resolved_incidents = [incident for incident in window_incidents if incident.status == IncidentStatus.resolved]

        recovery_minutes = [
            max(0.0, (incident.updated_at - incident.created_at).total_seconds() / 60.0)
            for incident in resolved_incidents
        ]
        deployment_recovery_minutes = [
            max(0.0, (incident.updated_at - incident.created_at).total_seconds() / 60.0)
            for incident in deployment_related
            if incident.status == IncidentStatus.resolved
        ]
        run_latency_ms = [
            run.evaluation.latency_ms
            for run in completed_runs
            if run.evaluation is not None and run.evaluation.latency_ms >= 0
        ]
        run_costs = [
            run.evaluation.estimated_cost_usd
            for run in completed_runs
            if run.evaluation is not None and run.evaluation.estimated_cost_usd >= 0
        ]
        action_statuses = [
            action.status.value if hasattr(action.status, "value") else str(action.status)
            for run in window_runs
            for action in (run.investigation.recommended_actions if run.investigation else [])
        ]
        executed_actions = sum(1 for status in action_statuses if status == "executed")
        blocked_actions = sum(1 for status in action_statuses if status == "blocked")
        services = Counter(incident.service for incident in window_incidents)
        recurring_services = sum(1 for _service, count in services.items() if count > 1)

        automation_success_rate = round(executed_actions / len(action_statuses), 4) if action_statuses else 0.0
        run_success_rate = round(len(completed_runs) / len(window_runs), 4) if window_runs else 0.0
        blocked_action_rate = round(blocked_actions / len(action_statuses), 4) if action_statuses else 0.0
        mttr_minutes = round(self._avg(recovery_minutes), 2)
        deployment_recovery_time = round(self._avg(deployment_recovery_minutes), 2)
        average_run_latency_ms = round(self._avg(run_latency_ms), 2)
        p95_run_latency_ms = round(self._percentile(run_latency_ms, 0.95), 2)
        average_run_cost_usd = round(self._avg(run_costs), 6)
        recurring_service_rate = round(recurring_services / len(services), 4) if services else 0.0

        metrics = [
            DoraSreMetric(
                key="deployment_frequency_per_day",
                label="Deployment frequency",
                value=deployment_frequency_per_day,
                unit="deployments/day",
                target=0.1,
                status="good" if deployment_frequency_per_day >= 0.1 else "watch",
                description="Estimated unique deployment or release-related incidents per day in the selected window.",
            ),
            DoraSreMetric(
                key="change_failure_rate",
                label="Change failure rate",
                value=round(change_failure_rate * 100, 2),
                unit="%",
                target=15.0,
                status="good" if change_failure_rate <= 0.15 else "risk",
                description="Deployment-related incidents divided by inferred deployment count.",
            ),
            DoraSreMetric(
                key="failed_deployment_recovery_time_minutes",
                label="Failed deploy recovery",
                value=deployment_recovery_time,
                unit="minutes",
                target=60.0,
                status="good" if deployment_recovery_time <= 60 else "risk",
                description="Average recovery time for resolved deployment-related incidents.",
            ),
            DoraSreMetric(
                key="mttr_minutes",
                label="MTTR",
                value=mttr_minutes,
                unit="minutes",
                target=60.0,
                status="good" if mttr_minutes <= 60 else "risk",
                description="Average time from incident creation to resolved update timestamp.",
            ),
            DoraSreMetric(
                key="automation_success_rate",
                label="Automation success",
                value=round(automation_success_rate * 100, 2),
                unit="%",
                target=80.0,
                status="good" if automation_success_rate >= 0.8 else "watch",
                description="Share of recommended remediation actions that were executed.",
            ),
            DoraSreMetric(
                key="run_success_rate",
                label="Run success",
                value=round(run_success_rate * 100, 2),
                unit="%",
                target=95.0,
                status="good" if run_success_rate >= 0.95 else "watch",
                description="Share of incident investigation runs that completed successfully.",
            ),
            DoraSreMetric(
                key="blocked_action_rate",
                label="Blocked actions",
                value=round(blocked_action_rate * 100, 2),
                unit="%",
                target=20.0,
                status="good" if blocked_action_rate <= 0.2 else "risk",
                description="Share of recommended remediation actions blocked by policy.",
            ),
            DoraSreMetric(
                key="p95_run_latency_ms",
                label="p95 run latency",
                value=p95_run_latency_ms,
                unit="ms",
                target=2000.0,
                status="good" if p95_run_latency_ms <= 2000 else "risk",
                description="p95 latency for completed incident investigations with evaluation data.",
            ),
            DoraSreMetric(
                key="average_run_cost_usd",
                label="Average run cost",
                value=average_run_cost_usd,
                unit="usd",
                target=0.05,
                status="good" if average_run_cost_usd <= 0.05 else "watch",
                description="Average estimated LLM cost for completed evaluated runs.",
            ),
            DoraSreMetric(
                key="recurring_service_rate",
                label="Recurring services",
                value=round(recurring_service_rate * 100, 2),
                unit="%",
                target=30.0,
                status="good" if recurring_service_rate <= 0.3 else "watch",
                description="Share of impacted services with more than one incident in the selected window.",
            ),
        ]

        return DoraSreDashboard(
            window_days=window_days,
            metrics=metrics,
            dora={
                "deployment_frequency_per_day": deployment_frequency_per_day,
                "change_failure_rate": change_failure_rate,
                "failed_deployment_recovery_time_minutes": deployment_recovery_time,
                "inferred_deployments": inferred_deployments,
                "deployment_related_incidents": len(deployment_related),
            },
            sre={
                "mttr_minutes": mttr_minutes,
                "incident_count": len(window_incidents),
                "resolved_incidents": len(resolved_incidents),
                "run_success_rate": run_success_rate,
                "failed_runs": len(failed_runs),
                "automation_success_rate": automation_success_rate,
                "blocked_action_rate": blocked_action_rate,
                "average_run_latency_ms": average_run_latency_ms,
                "p95_run_latency_ms": p95_run_latency_ms,
                "average_run_cost_usd": average_run_cost_usd,
                "recurring_service_rate": recurring_service_rate,
                "remediation_receipts": len(window_remediations),
                "queued_jobs": sum(1 for job in window_jobs if job.status.value == "queued"),
            },
            methodology=[
                "Deployment frequency is inferred from unique release hints and deployment-related incident markers.",
                "Change failure rate is deployment-related incidents divided by inferred deployment count.",
                "MTTR is resolved incident updated_at minus created_at.",
                "Automation success rate is executed recommended actions divided by all recommended actions.",
                "Latency and cost are calculated from completed runs with evaluation data.",
            ],
            details={
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "top_services_by_incidents": [
                    {"service": service, "count": count}
                    for service, count in services.most_common(8)
                ],
            },
        )

    @staticmethod
    def _avg(values: list[float | int]) -> float:
        return (sum(values) / len(values)) if values else 0.0

    @staticmethod
    def _percentile(values: list[float | int], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(value) for value in values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
        return ordered[index]

    @staticmethod
    def _release_key(incident: Incident) -> str:
        observability = incident.context.get("observability", {})
        release_hint = observability.get("release_hint") or incident.context.get("release_hint")
        if release_hint:
            return str(release_hint)
        for label in incident.labels:
            if label.lower().startswith(("release:", "deploy:", "deployment:")):
                return label
        return ""

    @classmethod
    def _is_deployment_related(cls, incident: Incident) -> bool:
        text = " ".join(
            [
                incident.title,
                incident.description,
                incident.source,
                " ".join(incident.labels),
                str(incident.context.get("release_hint", "")),
                str(incident.context.get("observability", {}).get("release_hint", "")),
            ]
        ).lower()
        return any(token in text for token in ("deploy", "deployment", "release", "rollback", "change"))
