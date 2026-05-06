from __future__ import annotations

import re

from app.core.embeddings import get_embedding
from app.core.storage import MemoryStore
from app.models import Incident, IncidentRun, MemoryEntry, MemoryHit, PolicyVerdict, utc_now


class IncidentMemory:
    """Persist incident learnings and retrieve relevant historical context."""

    def __init__(
        self,
        store: MemoryStore,
        api_key: str | None = None,
        api_base: str = "https://api.openai.com/v1",
    ) -> None:
        self.store = store
        self._api_key = api_key
        self._api_base = api_base

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}

    @staticmethod
    def _metric_similarity(left: dict[str, float], right: dict[str, float]) -> float:
        shared_keys = set(left) & set(right)
        if not shared_keys:
            return 0.0
        score = 0.0
        for key in shared_keys:
            left_value = left.get(key, 0.0)
            right_value = right.get(key, 0.0)
            baseline = max(abs(left_value), abs(right_value), 1.0)
            score += max(0.0, 1.0 - (abs(left_value - right_value) / baseline))
        return score / len(shared_keys)

    def _embedding_vector(self, text: str) -> list[float]:
        return get_embedding(text, self._api_key, self._api_base, usage_source="memory_embedding")

    @staticmethod
    def _vector_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        return sum(a * b for a, b in zip(left, right))

    @staticmethod
    def _derive_lessons(incident: Incident, run: IncidentRun) -> list[str]:
        actions = ", ".join(action.name for action in (run.investigation.recommended_actions if run.investigation else [])) or "manual review"
        lessons = [
            f"{incident.service} incidents should prioritize {actions} when {incident.severity.value} symptoms match historical behavior.",
        ]
        if run.memory_hits:
            lessons.append("Historical precedents improved confidence and reduced search space.")
        if run.approval_notes:
            lessons.append("High-risk remediation still requires explicit operator sign-off.")
        if run.feedback_learning and run.feedback_learning.guidance:
            lessons.extend(run.feedback_learning.guidance[:2])
        return lessons

    @staticmethod
    def _derive_patterns(incident: Incident, run: IncidentRun) -> list[str]:
        patterns = []
        if incident.metrics.get("p95_latency_ms", 0) > 1500:
            patterns.append("latency_spike_pattern")
        if incident.metrics.get("error_rate", 0) > 0.08:
            patterns.append("release_regression_pattern")
        if run.investigation and run.investigation.release_assessment:
            patterns.append("release_correlation_review")
        if run.feedback_learning and run.feedback_learning.discouraged_actions:
            patterns.append("operator_feedback_pattern")
        if not patterns:
            patterns.append("diagnostic_collection_pattern")
        return patterns

    @staticmethod
    def _derive_service_playbook(incident: Incident, run: IncidentRun) -> str:
        actions = [action.name for action in (run.investigation.recommended_actions if run.investigation else [])]
        action_text = ", then ".join(actions) if actions else "collect diagnostics and escalate"
        return (
            f"For {incident.service} in {incident.environment}, start with observability confirmation, "
            f"review recent release context, and {action_text}."
        )

    def remember(self, incident: Incident, run: IncidentRun) -> MemoryEntry | None:
        if not run.investigation:
            return None

        approval_required = any(
            decision.verdict == PolicyVerdict.approval_required for decision in run.policy_decisions
        )
        entry = MemoryEntry(
            incident_id=incident.id,
            title=incident.title,
            service=incident.service,
            environment=incident.environment,
            severity=incident.severity,
            summary=run.investigation.summary,
            root_cause=run.investigation.suspected_root_cause,
            labels=incident.labels,
            action_names=[action.name for action in run.investigation.recommended_actions],
            outcome=incident.status.value,
            approval_required=approval_required,
            approval_notes=list(run.approval_notes),
            postmortem_summary=run.postmortem_summary,
            symptom_tokens=sorted(
                self._tokenize(
                    f"{incident.title} {incident.description} {run.investigation.summary} {run.investigation.suspected_root_cause}"
                )
            ),
            metric_snapshot=dict(incident.metrics),
            resolution_quality=(run.evaluation.score / 100.0) if run.evaluation else 0.0,
            abstract_lessons=(
                list(run.investigation.memory_abstractions.lessons)
                if run.investigation.memory_abstractions and run.investigation.memory_abstractions.lessons
                else self._derive_lessons(incident, run)
            ),
            remediation_patterns=(
                list(run.investigation.memory_abstractions.remediation_patterns)
                if run.investigation.memory_abstractions and run.investigation.memory_abstractions.remediation_patterns
                else self._derive_patterns(incident, run)
            ),
            service_playbook=(
                run.investigation.memory_abstractions.service_playbook
                if run.investigation.memory_abstractions and run.investigation.memory_abstractions.service_playbook
                else self._derive_service_playbook(incident, run)
            ),
            embedding_vector=(
                list(run.investigation.memory_abstractions.embedding_vector)
                if run.investigation.memory_abstractions and run.investigation.memory_abstractions.embedding_vector
                else self._embedding_vector(
                    f"{incident.title} {incident.description} {run.investigation.summary} {run.investigation.suspected_root_cause}"
                )
            ),
            feedback_score=run.feedback_learning.confidence if run.feedback_learning else 0.0,
            preferred_actions=list(run.feedback_learning.preferred_actions) if run.feedback_learning else [],
            discouraged_actions=list(run.feedback_learning.discouraged_actions) if run.feedback_learning else [],
            correction_count=len(run.feedback_learning.corrected_root_causes) if run.feedback_learning else 0,
            metadata={
                "prompt_profile": run.investigation.prompt_profile,
                "confidence": run.investigation.confidence,
            },
            updated_at=utc_now(),
        )
        return self.store.save(entry)

    def find_similar(self, incident: Incident, limit: int = 5) -> list[MemoryHit]:
        query_tokens = self._tokenize(f"{incident.title} {incident.description} {' '.join(incident.labels)}")
        query_vector = self._embedding_vector(f"{incident.title} {incident.description} {' '.join(incident.labels)}")
        results: list[MemoryHit] = []

        for entry in self.store.list():
            candidate_tokens = self._tokenize(
                f"{entry.title} {entry.summary} {entry.root_cause} {' '.join(entry.labels)} {' '.join(entry.symptom_tokens)}"
            )
            matched_terms = sorted(query_tokens & candidate_tokens)
            overlap = len(matched_terms)
            if overlap == 0:
                continue

            score = float(overlap)
            if incident.service.lower() == entry.service.lower():
                score += 2.0
            if incident.environment.lower() == entry.environment.lower():
                score += 0.75
            if incident.severity == entry.severity:
                score += 0.5
            if incident.labels:
                score += len(set(incident.labels) & set(entry.labels)) * 0.5

            score += self._metric_similarity(incident.metrics, entry.metric_snapshot) * 2.5
            score += self._vector_similarity(query_vector, entry.embedding_vector) * 1.8
            score += entry.resolution_quality * 1.5
            score += entry.feedback_score * 1.1
            if entry.outcome == "resolved":
                score += 0.5
            if entry.approval_required:
                score -= 0.2

            results.append(
                MemoryHit(
                    incident_id=entry.incident_id,
                    title=entry.title,
                    summary=entry.root_cause,
                    score=score,
                    service=entry.service,
                    action_names=entry.action_names,
                    postmortem_summary=entry.postmortem_summary,
                    resolution_state=entry.outcome,
                    matched_terms=matched_terms,
                    resolution_quality=entry.resolution_quality,
                    abstract_lessons=list(entry.abstract_lessons),
                    service_playbook=entry.service_playbook,
                    preferred_actions=list(entry.preferred_actions),
                    discouraged_actions=list(entry.discouraged_actions),
                )
            )

        return sorted(results, key=lambda item: item.score, reverse=True)[:limit]

    def list_entries(self) -> list[MemoryEntry]:
        return self.store.list()

    def search(
        self,
        query: str | None = None,
        service: str | None = None,
        environment: str | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        query_tokens = self._tokenize(query or "")
        results = []
        for entry in self.store.list():
            if service and entry.service.lower() != service.lower():
                continue
            if environment and entry.environment.lower() != environment.lower():
                continue
            if query_tokens:
                haystack = self._tokenize(
                    f"{entry.title} {entry.summary} {entry.root_cause} {' '.join(entry.symptom_tokens)} "
                    f"{' '.join(entry.abstract_lessons)} {entry.service_playbook or ''}"
                )
                if not (query_tokens & haystack):
                    continue
            results.append(entry)

        return sorted(results, key=lambda item: item.updated_at, reverse=True)[:limit]
