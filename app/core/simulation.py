from __future__ import annotations

from collections import Counter

from app.core.optimizer import AgentOptimizer
from app.core.synthetic import SyntheticIncidentGenerator
from app.models import AdversarialLabReport, AdversarialLabRequest, BenchmarkCase, BenchmarkRequest, SyntheticGenerationRequest


DEFAULT_SCENARIO_MIX = [
    "noise",
    "partial_observability",
    "misleading_release",
    "cascading_failure",
    "false_positive",
    "conflicting_evidence",
]


class AdversarialSimulationLab:
    def __init__(self, synthetic_generator: SyntheticIncidentGenerator, optimizer: AgentOptimizer) -> None:
        self.synthetic_generator = synthetic_generator
        self.optimizer = optimizer

    async def generate_cases(self, request: AdversarialLabRequest) -> list[BenchmarkCase]:
        base_cases = await self.synthetic_generator.generate_cases(
            SyntheticGenerationRequest(
                count=request.count,
                difficulty=request.difficulty,
                include_noise=request.include_noise,
                include_false_positives=request.include_false_positives,
                include_incomplete_evidence=request.include_incomplete_evidence,
                services=request.services,
            )
        )
        scenario_mix = request.scenario_mix or list(DEFAULT_SCENARIO_MIX)
        mutated: list[BenchmarkCase] = []

        for index, case in enumerate(base_cases):
            scenario = scenario_mix[index % len(scenario_mix)]
            tags = list(case.tags)
            metrics = dict(case.metrics)
            description = case.description
            expected_root_cause = case.expected_root_cause

            if scenario == "noise":
                metrics.setdefault("cpu_utilization", 0.93)
                description += " Secondary telemetry reports elevated CPU, but customer impact is still dominated by the primary alert."
                tags.extend(["scenario:noise", "adversarial"])
            elif scenario == "partial_observability":
                metrics = {key: value for idx, (key, value) in enumerate(metrics.items()) if idx == 0} or metrics
                description += " Only one strong signal is available and logs are incomplete."
                tags.extend(["scenario:partial_observability", "adversarial"])
            elif scenario == "misleading_release":
                description += " A release happened recently, but it may be unrelated to the core incident."
                tags.extend(["scenario:misleading_release", "adversarial", "release"])
            elif scenario == "cascading_failure":
                description += " Downstream services are also reporting degraded behavior, suggesting propagation across dependencies."
                tags.extend(["scenario:cascading_failure", "adversarial", "cascade"])
            elif scenario == "false_positive":
                description += " Customer impact is not yet confirmed, raising the possibility of a false positive."
                metrics["error_rate"] = min(metrics.get("error_rate", 0.03), 0.03)
                expected_root_cause = "broader observability review"
                tags.extend(["scenario:false_positive", "adversarial"])
            elif scenario == "conflicting_evidence":
                metrics["error_rate"] = max(metrics.get("error_rate", 0.01), 0.09)
                metrics["p95_latency_ms"] = max(metrics.get("p95_latency_ms", 0.0), 1900.0)
                description += " Error and latency signals disagree, so the agent must resolve conflicting evidence."
                tags.extend(["scenario:conflicting_evidence", "adversarial"])

            mutated.append(
                case.model_copy(
                    update={
                        "description": description,
                        "metrics": metrics,
                        "expected_root_cause": expected_root_cause,
                        "tags": tags,
                    }
                )
            )

        return mutated[: request.count]

    async def run_lab(self, service, request: AdversarialLabRequest) -> AdversarialLabReport:
        cases = await self.generate_cases(request)
        summaries = await service.run_benchmark(
            BenchmarkRequest(
                profile_ids=request.profile_ids,
                model_route_ids=request.model_route_ids,
                cases=cases,
            )
        )
        scenario_counts: Counter[str] = Counter()
        weakness_counts: Counter[str] = Counter()
        for case in cases:
            for tag in case.tags:
                if tag.startswith("scenario:"):
                    scenario_counts[tag.removeprefix("scenario:")] += 1
        for summary in summaries:
            weakness_counts.update(summary.failure_category_counts)

        focus_areas: list[str] = []
        for category, _ in weakness_counts.most_common(4):
            if category == "thin_evidence":
                focus_areas.append("Strengthen evidence collection before remediation planning.")
            elif category == "low_confidence":
                focus_areas.append("Route ambiguous cases to stronger models and causal reasoning earlier.")
            elif category == "high_latency":
                focus_areas.append("Trim reflection depth for routine incidents.")
            elif category == "weak_explanations":
                focus_areas.append("Increase structure in postmortem and executive summaries.")
            else:
                focus_areas.append(f"Reduce occurrences of {category}.")

        recommendation = self.optimizer.recommend(summaries, service.list_runs())
        return AdversarialLabReport(
            total_cases=len(cases),
            scenario_counts=dict(sorted(scenario_counts.items())),
            weakness_counts=dict(sorted(weakness_counts.items())),
            summaries=summaries,
            recommended_focus_areas=focus_areas,
            optimizer_recommendation=recommendation,
        )
