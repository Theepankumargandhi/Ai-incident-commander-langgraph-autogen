from __future__ import annotations

from collections import Counter

from app.core.storage import CollectionStore
from app.models import (
    FeedbackKind,
    FeedbackLearningSnapshot,
    FeedbackRecord,
    Incident,
    IncidentRun,
    MemoryEntry,
)


class FeedbackLearningLoop:
    def __init__(self, feedback_store: CollectionStore[FeedbackRecord], memory_store: CollectionStore[MemoryEntry]) -> None:
        self.feedback_store = feedback_store
        self.memory_store = memory_store

    def list_feedback(self) -> list[FeedbackRecord]:
        return sorted(self.feedback_store.list(), key=lambda item: item.created_at, reverse=True)

    def summarize(self, service: str | None = None) -> FeedbackLearningSnapshot:
        records = [
            item
            for item in self.feedback_store.list()
            if service is None or item.service.lower() == service.lower()
        ]
        if not records:
            return FeedbackLearningSnapshot(
                service=service,
                guidance=["No operator feedback has been recorded yet."],
                confidence=0.0,
            )

        accepted = Counter(action for record in records for action in record.accepted_actions)
        rejected = Counter(action for record in records for action in record.rejected_actions)
        corrected_causes = [record.corrected_root_cause for record in records if record.corrected_root_cause]
        escalation_patterns = []
        if any(record.kind in {FeedbackKind.override, FeedbackKind.correction} for record in records):
            escalation_patterns.append("operator_override_present")
        if any(record.approved is False for record in records if record.approved is not None):
            escalation_patterns.append("approval_denial_present")

        guidance: list[str] = []
        if accepted:
            guidance.append(f"Operators frequently keep {accepted.most_common(1)[0][0]} in the final remediation plan.")
        if rejected:
            guidance.append(f"Operators often challenge {rejected.most_common(1)[0][0]} unless evidence is stronger.")
        if corrected_causes:
            guidance.append(f"Corrected root causes commonly mention {corrected_causes[0].lower()}.")
        if not guidance:
            guidance.append("Existing feedback is sparse, so use it as a weak preference signal.")

        confidence = min(0.92, 0.35 + (len(records) * 0.08))
        return FeedbackLearningSnapshot(
            service=service or records[0].service,
            guidance=guidance,
            preferred_actions=[name for name, _count in accepted.most_common(4)],
            discouraged_actions=[name for name, _count in rejected.most_common(4)],
            corrected_root_causes=corrected_causes[:4],
            escalation_patterns=escalation_patterns,
            confidence=round(confidence, 2),
        )

    def record_feedback(
        self,
        incident: Incident,
        run: IncidentRun,
        record: FeedbackRecord,
    ) -> FeedbackRecord:
        saved = self.feedback_store.save(record)
        self._update_memory_entry(incident, run, saved)
        return saved

    def auto_feedback_from_approval(self, incident: Incident, run: IncidentRun, approved: bool, operator: str, comment: str) -> FeedbackRecord:
        return self.record_feedback(
            incident,
            run,
            FeedbackRecord(
                incident_id=incident.id,
                run_id=run.id,
                service=incident.service,
                environment=incident.environment,
                kind=FeedbackKind.approval,
                operator=operator,
                approved=approved,
                comment=comment,
                accepted_actions=[
                    action.name
                    for action in (run.investigation.recommended_actions if run.investigation else [])
                    if action.status in {"approved", "executed"}
                ],
                rejected_actions=[
                    action.name
                    for action in (run.investigation.recommended_actions if run.investigation else [])
                    if action.status in {"blocked", "failed", "skipped"}
                ],
                reinforcement_score=0.4 if approved else -0.4,
            ),
        )

    def _update_memory_entry(self, incident: Incident, run: IncidentRun, feedback: FeedbackRecord) -> None:
        entry = self.memory_store.latest_for_field("incident_id", incident.id)
        if entry is None:
            return

        preferred = list(dict.fromkeys([*entry.preferred_actions, *feedback.accepted_actions]))
        discouraged = list(dict.fromkeys([*entry.discouraged_actions, *feedback.rejected_actions]))
        lessons = list(entry.abstract_lessons)
        if feedback.comment:
            lessons.append(f"Operator feedback: {feedback.comment}")
        if feedback.corrected_root_cause:
            lessons.append(f"Corrected root cause: {feedback.corrected_root_cause}")

        updated = entry.model_copy(
            update={
                "preferred_actions": preferred,
                "discouraged_actions": discouraged,
                "feedback_score": round(entry.feedback_score + feedback.reinforcement_score, 2),
                "correction_count": entry.correction_count + (1 if feedback.kind == FeedbackKind.correction else 0),
                "abstract_lessons": lessons[-8:],
                "metadata": {
                    **entry.metadata,
                    "last_feedback_kind": feedback.kind.value,
                    "last_feedback_operator": feedback.operator,
                },
            }
        )
        self.memory_store.save(MemoryEntry.model_validate(updated))
