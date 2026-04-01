from __future__ import annotations

from app.models import BenchmarkCase, BenchmarkSummary, Incident, IncidentRun, PromptProfile


class BenchmarkEngine:
    """Summarize benchmark runs across prompt profiles."""

    def summarize(
        self,
        profile: PromptProfile,
        cases: list[BenchmarkCase],
        runs: list[tuple[BenchmarkCase, Incident, IncidentRun]],
    ) -> BenchmarkSummary:
        matches = 0
        details: list[dict] = []
        scores: list[float] = []

        for case, incident, run in runs:
            suspected = run.investigation.suspected_root_cause.lower() if run.investigation else ""
            expected = case.expected_root_cause.lower()
            matched = expected in suspected or suspected in expected
            matches += 1 if matched else 0
            if run.evaluation:
                scores.append(run.evaluation.score)
            details.append(
                {
                    "case_id": case.id,
                    "incident_id": incident.id,
                    "run_id": run.id,
                    "matched_root_cause": matched,
                    "score": run.evaluation.score if run.evaluation else None,
                }
            )

        average_score = sum(scores) / len(scores) if scores else 0.0
        match_rate = (matches / len(cases)) if cases else 0.0
        return BenchmarkSummary(
            profile_id=profile.id,
            total_cases=len(cases),
            average_score=average_score,
            root_cause_match_rate=match_rate,
            details=details,
        )
