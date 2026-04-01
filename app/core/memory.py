from __future__ import annotations

import re

from app.models import Incident, IncidentRun, MemoryHit


class IncidentMemory:
    """Retrieve simple similarity matches from past incident runs."""

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}

    def find_similar(self, incident: Incident, prior_runs: list[IncidentRun], limit: int = 3) -> list[MemoryHit]:
        query_tokens = self._tokenize(f"{incident.title} {incident.description} {' '.join(incident.labels)}")
        results: list[MemoryHit] = []

        for run in prior_runs:
            if not run.investigation:
                continue

            investigation = run.investigation
            candidate_tokens = self._tokenize(
                f"{investigation.summary} {investigation.suspected_root_cause}"
            )
            overlap = len(query_tokens & candidate_tokens)
            if overlap == 0:
                continue

            score = float(overlap)
            if incident.service.lower() in investigation.summary.lower():
                score += 1.5
            if incident.severity.value in investigation.summary.lower():
                score += 0.5

            results.append(
                MemoryHit(
                    incident_id=run.incident_id,
                    title=investigation.summary[:80],
                    summary=investigation.suspected_root_cause,
                    score=score,
                )
            )

        return sorted(results, key=lambda item: item.score, reverse=True)[:limit]
