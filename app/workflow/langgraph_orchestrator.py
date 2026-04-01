from __future__ import annotations

import time
from typing import TypedDict

from app.config import Settings
from app.core.evaluation import RunEvaluator
from app.core.memory import IncidentMemory
from app.core.policy import PolicyEngine
from app.core.profiles import get_profile
from app.integrations.executor import ActionExecutor
from app.integrations.observability import MockObservabilityClient
from app.models import (
    ActionStatus,
    ApprovalRequest,
    EventMessage,
    Incident,
    IncidentRun,
    IncidentStatus,
    PolicyVerdict,
    RunStatus,
    utc_now,
)
from app.workflow.autogen_team import AutoGenInvestigationTeam

try:
    from langgraph.graph import END, StateGraph  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    END = "__end__"
    StateGraph = None


class WorkflowState(TypedDict, total=False):
    incident: Incident
    run: IncidentRun
    prompt_profile_id: str


class LangGraphIncidentOrchestrator:
    def __init__(
        self,
        settings: Settings,
        memory: IncidentMemory,
        policy_engine: PolicyEngine,
        evaluator: RunEvaluator,
        autogen_team: AutoGenInvestigationTeam,
        observability_client: MockObservabilityClient,
        action_executor: ActionExecutor,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.policy_engine = policy_engine
        self.evaluator = evaluator
        self.autogen_team = autogen_team
        self.observability_client = observability_client
        self.action_executor = action_executor

    def start_run(self, incident: Incident, prior_runs: list[IncidentRun], prompt_profile_id: str | None = None) -> IncidentRun:
        if self.settings.enable_langgraph and StateGraph is not None:
            return self._run_with_langgraph(incident, prior_runs, prompt_profile_id)
        return self._run_fallback(incident, prior_runs, prompt_profile_id)

    def resume_run(self, incident: Incident, run: IncidentRun, approval: ApprovalRequest) -> IncidentRun:
        incident.status = IncidentStatus.approved if approval.approved else IncidentStatus.failed
        incident.updated_at = utc_now()
        if not approval.approved:
            run.status = RunStatus.failed
            run.remediation_notes.append(f"Approval denied by {approval.approver}: {approval.comment}")
            run.finished_at = utc_now()
            return run

        if run.investigation:
            for action in run.investigation.recommended_actions:
                for decision in run.policy_decisions:
                    if decision.action_id == action.id and decision.verdict == PolicyVerdict.approval_required:
                        decision.approved = True
                        action.status = ActionStatus.approved

        run.remediation_notes.append(f"Approval granted by {approval.approver}: {approval.comment}")
        self._execute_actions(incident, run)
        return self._finalize_run(incident, run)

    def _run_fallback(self, incident: Incident, prior_runs: list[IncidentRun], prompt_profile_id: str | None) -> IncidentRun:
        started = time.perf_counter()
        profile = get_profile(prompt_profile_id or self.settings.default_prompt_profile)
        run = IncidentRun(incident_id=incident.id)
        incident.status = IncidentStatus.investigating
        incident.updated_at = utc_now()
        incident.context["observability"] = self.observability_client.collect(incident).model_dump(mode="json")
        run.memory_hits = self.memory.find_similar(incident, prior_runs)
        run.investigation = self.autogen_team.investigate(incident, run.memory_hits, profile)
        run.policy_decisions = [
            self.policy_engine.evaluate_action(incident, action)
            for action in run.investigation.recommended_actions
        ]
        approval_needed = any(decision.verdict == PolicyVerdict.approval_required for decision in run.policy_decisions)
        if approval_needed:
            run.status = RunStatus.awaiting_approval
            incident.status = IncidentStatus.awaiting_approval
            run.event_log.append(
                EventMessage(stage="approval", message="Human approval required for risky remediation.").model_dump(mode="json")
            )
            return run

        self._execute_actions(incident, run)
        latency_ms = int((time.perf_counter() - started) * 1000)
        run.evaluation = self.evaluator.evaluate(incident, run, latency_ms, profile.id)
        run.status = RunStatus.completed
        run.finished_at = utc_now()
        return run

    def _run_with_langgraph(self, incident: Incident, prior_runs: list[IncidentRun], prompt_profile_id: str | None) -> IncidentRun:
        profile_id = prompt_profile_id or self.settings.default_prompt_profile

        def investigate_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            profile = get_profile(state["prompt_profile_id"])
            state["incident"].context["observability"] = self.observability_client.collect(state["incident"]).model_dump(mode="json")
            run.memory_hits = self.memory.find_similar(state["incident"], prior_runs)
            run.investigation = self.autogen_team.investigate(state["incident"], run.memory_hits, profile)
            return {"incident": state["incident"], "run": run, "prompt_profile_id": profile.id}

        def policy_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            run.policy_decisions = [
                self.policy_engine.evaluate_action(state["incident"], action)
                for action in run.investigation.recommended_actions
            ]
            return state

        def execute_node(state: WorkflowState) -> WorkflowState:
            self._execute_actions(state["incident"], state["run"])
            return state

        def finalize_node(state: WorkflowState) -> WorkflowState:
            finalized = self._finalize_run(state["incident"], state["run"], state["prompt_profile_id"])
            return {"incident": state["incident"], "run": finalized, "prompt_profile_id": state["prompt_profile_id"]}

        def router(state: WorkflowState) -> str:
            if any(decision.verdict == PolicyVerdict.approval_required for decision in state["run"].policy_decisions):
                state["run"].status = RunStatus.awaiting_approval
                state["incident"].status = IncidentStatus.awaiting_approval
                state["incident"].updated_at = utc_now()
                return "awaiting_approval"
            return "execute"

        graph = StateGraph(WorkflowState)
        graph.add_node("investigate", investigate_node)
        graph.add_node("policy", policy_node)
        graph.add_node("execute", execute_node)
        graph.add_node("finalize", finalize_node)
        graph.add_edge("investigate", "policy")
        graph.add_conditional_edges("policy", router, {"awaiting_approval": END, "execute": "execute"})
        graph.add_edge("execute", "finalize")
        graph.add_edge("finalize", END)
        graph.set_entry_point("investigate")
        app = graph.compile()
        initial = {"incident": incident, "run": IncidentRun(incident_id=incident.id), "prompt_profile_id": profile_id}
        result = app.invoke(initial)
        return result["run"]

    def _execute_actions(self, incident: Incident, run: IncidentRun) -> None:
        incident.status = IncidentStatus.remediating
        incident.updated_at = utc_now()
        if not run.investigation:
            return

        for action in run.investigation.recommended_actions:
            decision = next((item for item in run.policy_decisions if item.action_id == action.id), None)
            if decision and decision.verdict == PolicyVerdict.blocked:
                action.status = ActionStatus.blocked
                run.remediation_notes.append(f"Blocked action: {action.name}")
                continue
            if decision and decision.verdict == PolicyVerdict.approval_required and not decision.approved:
                action.status = ActionStatus.skipped
                run.remediation_notes.append(f"Awaiting approval for action: {action.name}")
                continue

            action.status = ActionStatus.executed
            execution_note = self.action_executor.execute(incident, action)
            run.remediation_notes.append(f"Executed remediation action: {action.name}. {execution_note}")

    def _finalize_run(self, incident: Incident, run: IncidentRun, prompt_profile_id: str | None = None) -> IncidentRun:
        incident.status = IncidentStatus.resolved
        incident.updated_at = utc_now()
        run.status = RunStatus.completed
        run.finished_at = utc_now()
        profile_id = prompt_profile_id or self.settings.default_prompt_profile
        if run.evaluation is None:
            run.evaluation = self.evaluator.evaluate(incident, run, latency_ms=1500, prompt_profile=profile_id)
        return run
