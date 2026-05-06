from __future__ import annotations

from app.models import Incident, IncidentRun


class PostmortemExporter:
    def render_markdown(self, incident: Incident, run: IncidentRun) -> str:
        investigation = run.investigation
        findings = run.evaluation.findings if run.evaluation else []
        actions = investigation.recommended_actions if investigation else []
        event_lines = [
            f"- `{event.get('stage', 'event')}` {event.get('message', '')}"
            for event in run.event_log[-12:]
        ]
        if not event_lines:
            event_lines = ["- No replay events were recorded."]

        action_lines = [
            f"- `{action.name}`: {action.description} (`{action.status}`)"
            for action in actions
        ]
        if not action_lines:
            action_lines = ["- No remediation actions were proposed."]

        approval_lines = [f"- {note}" for note in run.approval_notes] or ["- No approval gate was triggered."]

        finding_lines = [f"- {finding}" for finding in findings] or ["- No evaluation findings were recorded."]
        usage = run.usage
        cost_lines = [
            f"- Prompt tokens: `{usage.prompt_tokens}`",
            f"- Completion tokens: `{usage.completion_tokens}`",
            f"- Total tokens: `{usage.total_tokens}`",
            f"- Duration: `{usage.duration_ms} ms`",
            f"- Estimated cost: `${usage.estimated_cost_usd:.6f}`",
        ] if usage is not None else ["- No OpenAI usage was recorded for this run."]

        line_item_lines = (
            [
                (
                    f"- `{item.source}` on `{item.model}`: "
                    f"{item.prompt_tokens} input + {item.completion_tokens} output tokens across "
                    f"{item.call_count} call(s), "
                    f"${item.total_cost_usd:.6f}"
                )
                for item in usage.line_items
            ]
            if usage is not None and usage.line_items
            else ["- No per-model usage breakdown was recorded."]
        )

        return "\n".join(
            [
                f"# Postmortem: {incident.title}",
                "",
                "## Incident Context",
                f"- Service: `{incident.service}`",
                f"- Environment: `{incident.environment}`",
                f"- Severity: `{incident.severity.value}`",
                f"- Source: `{incident.source}`",
                f"- Final status: `{incident.status.value}`",
                "",
                "## Summary",
                run.postmortem_summary or "No postmortem summary has been generated yet.",
                "",
                "## Root Cause",
                investigation.suspected_root_cause if investigation else "No root cause recorded.",
                "",
                "## Recommended Actions",
                *action_lines,
                "",
                "## Approval Notes",
                *approval_lines,
                "",
                "## Evaluation Findings",
                *finding_lines,
                "",
                "## Cost Summary",
                *cost_lines,
                "",
                "## Usage Breakdown",
                *line_item_lines,
                "",
                "## Event Timeline",
                *event_lines,
                "",
            ]
        )
