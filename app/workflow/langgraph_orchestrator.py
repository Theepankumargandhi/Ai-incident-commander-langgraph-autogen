from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypedDict

from app.config import Settings
from app.core.causal import CausalReasoningEngine
from app.core.evaluation import RunEvaluator
from app.core.feedback import FeedbackLearningLoop
from app.core.memory import IncidentMemory
from app.core.multimodal import MultimodalReasoningEngine
from app.core.openai_usage import snapshot_openai_usage, track_openai_usage
from app.core.policy import PolicyEngine
from app.core.profiles import get_profile
from app.core.tracing import generate_trace_id, set_trace_id, traced_span
from app.integrations.executor import ActionExecutor
from app.integrations.observability import PrometheusObservabilityClient
from app.models import (
    ActionStatus,
    ApprovalRequest,
    EventMessage,
    Incident,
    IncidentRun,
    IncidentStatus,
    ObservabilitySnapshot,
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
    started_at: float


class LangGraphIncidentOrchestrator:
    def __init__(
        self,
        settings: Settings,
        memory: IncidentMemory,
        policy_engine: PolicyEngine,
        evaluator: RunEvaluator,
        causal_engine: CausalReasoningEngine,
        multimodal_engine: MultimodalReasoningEngine,
        feedback_loop: FeedbackLearningLoop,
        autogen_team: AutoGenInvestigationTeam,
        observability_client: PrometheusObservabilityClient,
        action_executor: ActionExecutor,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.policy_engine = policy_engine
        self.evaluator = evaluator
        self.causal_engine = causal_engine
        self.multimodal_engine = multimodal_engine
        self.feedback_loop = feedback_loop
        self.autogen_team = autogen_team
        self.observability_client = observability_client
        self.action_executor = action_executor

    def _workflow_span_attributes(self, incident: Incident, run: IncidentRun, stage: str, job_id: str | None = None) -> dict[str, str]:
        return {
            "incident_id": incident.id,
            "run_id": run.id,
            "job_id": job_id or run.job_id or "",
            "workflow_stage": stage,
        }

    @staticmethod
    def _sync_run_usage(run: IncidentRun, duration_ms: int | None = None) -> None:
        tracked_usage = snapshot_openai_usage(
            duration_ms=duration_ms or (run.usage.duration_ms if run.usage else 0),
            trace_id=run.trace_id,
        )
        if tracked_usage is not None and tracked_usage.total_tokens > 0:
            run.usage = tracked_usage
        elif run.usage is None:
            run.usage = snapshot_openai_usage(duration_ms=duration_ms or 0, trace_id=run.trace_id)

        if run.usage is not None:
            if duration_ms is not None:
                run.usage.duration_ms = duration_ms
            run.usage.trace_id = run.trace_id
        if run.evaluation is not None and run.usage is not None:
            run.evaluation.estimated_cost_usd = run.usage.estimated_cost_usd

    async def start_run(
        self,
        incident: Incident,
        prompt_profile_id: str | None = None,
        model_route_id: str | None = None,
        job_id: str | None = None,
        trace_id: str | None = None,
        publish_event: Callable[[EventMessage], Awaitable[None]] | None = None,
    ) -> IncidentRun:
        active_trace_id = trace_id or generate_trace_id()
        set_trace_id(active_trace_id)
        started = time.perf_counter()

        async def _publish(event: EventMessage) -> None:
            if publish_event is not None:
                await publish_event(event)

        with track_openai_usage():
            if self.settings.enable_langgraph and StateGraph is not None:
                run = await self._run_with_langgraph(incident, prompt_profile_id, model_route_id, job_id, active_trace_id, _publish)
            else:
                run = await self._run_fallback(incident, prompt_profile_id, model_route_id, job_id, active_trace_id, _publish)
            self._sync_run_usage(run, int((time.perf_counter() - started) * 1000))
            return run

    async def resume_run(self, incident: Incident, run: IncidentRun, approval: ApprovalRequest) -> IncidentRun:
        started = time.perf_counter()
        with track_openai_usage(run.usage):
            with traced_span(
                "workflow.resume",
                {
                    "incident_id": incident.id,
                    "run_id": run.id,
                    "approval.approved": approval.approved,
                },
            ):
                incident.status = IncidentStatus.approved if approval.approved else IncidentStatus.failed
                incident.updated_at = utc_now()
                approval_note = f"{approval.approver}: {approval.comment}" if approval.comment else approval.approver
                run.approval_notes.append(approval_note)

                if not approval.approved:
                    run.status = RunStatus.failed
                    run.remediation_notes.append(f"Approval denied by {approval.approver}: {approval.comment}")
                    run.finished_at = utc_now()
                    run.postmortem_summary = self._build_postmortem(incident, run)
                    self.memory.remember(incident, run)
                    self._sync_run_usage(run, int((time.perf_counter() - started) * 1000))
                    return run

                if run.investigation:
                    for action in run.investigation.recommended_actions:
                        for decision in run.policy_decisions:
                            if decision.action_id == action.id and decision.verdict == PolicyVerdict.approval_required:
                                decision.approved = True
                                action.status = ActionStatus.approved
                                action.metadata["approval_reference"] = approval_note

                run.remediation_notes.append(f"Approval granted by {approval.approver}: {approval.comment}")
                with traced_span(
                    "langgraph.stage.execute",
                    self._workflow_span_attributes(incident, run, "execute"),
                ):
                    self._execute_actions(incident, run)
                with traced_span(
                    "langgraph.stage.finalize",
                    self._workflow_span_attributes(incident, run, "finalize"),
                ):
                    finalized = await self._finalize_run(incident, run)
                self._sync_run_usage(finalized, int((time.perf_counter() - started) * 1000))
                return finalized

    async def _run_fallback(
        self,
        incident: Incident,
        prompt_profile_id: str | None,
        model_route_id: str | None,
        job_id: str | None,
        trace_id: str,
        _publish: Callable[[EventMessage], Awaitable[None]],
    ) -> IncidentRun:
        started = time.perf_counter()
        profile = get_profile(prompt_profile_id or self.settings.default_prompt_profile)
        run = IncidentRun(incident_id=incident.id, connector_status=self._connector_status(), trace_id=trace_id, job_id=job_id)
        incident.status = IncidentStatus.investigating
        incident.updated_at = utc_now()

        def _evt(stage: str, status: str, message: str, payload: dict | None = None) -> EventMessage:
            return EventMessage(stage=stage, status=status, message=message, trace_id=trace_id, job_id=job_id, payload=payload or {})

        with traced_span(
            "workflow.fallback",
            self._workflow_span_attributes(incident, run, "fallback", job_id),
        ):
            # --- observe ---
            with traced_span("langgraph.stage.observe", self._workflow_span_attributes(incident, run, "observe", job_id)):
                await _publish(_evt("observe", "started", "Collecting observability signals."))
                snapshot = self.observability_client.collect(incident)
                incident.context["observability"] = snapshot.model_dump(mode="json")
                run.connector_status = self._connector_status()
                _e = _evt("observe", "completed", "Observability signals collected.", {"provider": snapshot.provider, "log_count": len(snapshot.logs), "has_release_hint": bool(snapshot.release_hint)})
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

            # --- memory ---
            with traced_span("langgraph.stage.memory", self._workflow_span_attributes(incident, run, "memory", job_id)):
                await _publish(_evt("memory", "started", "Searching historical incident memory."))
                run.memory_hits = self.memory.find_similar(incident)
                _e = _evt("memory", "completed", f"Matched {len(run.memory_hits)} historical incidents.", {"count": len(run.memory_hits)})
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

            # --- graph ---
            with traced_span("langgraph.stage.graph", self._workflow_span_attributes(incident, run, "graph", job_id)):
                await _publish(_evt("graph", "started", "Loading service knowledge graph."))
                run.service_graph_context = self.causal_engine.knowledge_graph.describe_service(incident.service)
                incident.context["service_graph_context"] = run.service_graph_context.model_dump(mode="json")
                _e = _evt("graph", "completed", "Service graph context loaded.", run.service_graph_context.model_dump(mode="json"))
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

            # --- multimodal ---
            with traced_span("langgraph.stage.multimodal", self._workflow_span_attributes(incident, run, "multimodal", job_id)):
                await _publish(_evt("multimodal", "started", "Analyzing multimodal artifacts."))
                run.multimodal_analysis = self.multimodal_engine.analyze(incident, run.service_graph_context)
                incident.context["multimodal_analysis"] = run.multimodal_analysis.model_dump(mode="json")
                _e = _evt("multimodal", "completed", "Multimodal analysis completed.", run.multimodal_analysis.model_dump(mode="json"))
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

            # --- feedback ---
            with traced_span("langgraph.stage.feedback", self._workflow_span_attributes(incident, run, "feedback", job_id)):
                await _publish(_evt("feedback", "started", "Loading operator feedback."))
                run.feedback_learning = self.feedback_loop.summarize(incident.service)
                incident.context["feedback_learning"] = run.feedback_learning.model_dump(mode="json")
                _e = _evt("feedback", "completed", "Feedback context loaded.", run.feedback_learning.model_dump(mode="json"))
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

            # --- causal ---
            with traced_span("langgraph.stage.causal", self._workflow_span_attributes(incident, run, "causal", job_id)):
                await _publish(_evt("causal", "started", "Running causal analysis."))
                run.causal_analysis = self.causal_engine.analyze(incident, snapshot, run.memory_hits, run.service_graph_context)
                incident.context["causal_analysis"] = run.causal_analysis.model_dump(mode="json")
                _e = _evt("causal", "completed", "Causal analysis completed.", run.causal_analysis.model_dump(mode="json"))
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

            # --- investigate ---
            with traced_span("langgraph.stage.investigate", self._workflow_span_attributes(incident, run, "investigate", job_id)):
                await _publish(_evt("investigate", "started", "Starting multi-agent investigation."))
                run.investigation, run.usage = await self.autogen_team.investigate(
                    incident,
                    run.memory_hits,
                    profile,
                    model_route_id,
                    run.id,
                )
                run.investigation.causal_analysis = run.causal_analysis
                run.investigation.multimodal_analysis = run.multimodal_analysis
                run.investigation.feedback_learning = run.feedback_learning
                run.investigation.service_graph_context = run.service_graph_context
                _e = _evt("investigate", "completed", "Investigation completed.", {
                    "root_cause": run.investigation.suspected_root_cause,
                    "confidence": run.investigation.confidence,
                    "used_autogen": run.investigation.used_autogen,
                    "action_count": len(run.investigation.recommended_actions),
                    "profile": run.investigation.prompt_profile,
                })
                run.event_log.append(_e.model_dump(mode="json"))
                await _publish(_e)

        # --- policy ---
        with traced_span("langgraph.stage.policy", self._workflow_span_attributes(incident, run, "policy", job_id)):
            await _publish(_evt("policy", "started", "Evaluating policy decisions."))
            run.policy_decisions = []
            for action in run.investigation.recommended_actions:
                run.policy_decisions.append(await self.policy_engine.evaluate_action(incident, action))
            self._apply_policy_to_actions(run)
            _e = _evt("policy", "completed", "Policy decisions applied.", {"decisions": [d.model_dump(mode="json") for d in run.policy_decisions]})
            run.event_log.append(_e.model_dump(mode="json"))
            await _publish(_e)

        approval_needed = any(decision.verdict == PolicyVerdict.approval_required for decision in run.policy_decisions)
        if approval_needed:
            run.status = RunStatus.awaiting_approval
            incident.status = IncidentStatus.awaiting_approval
            _e = _evt("policy", "awaiting_approval", "Human approval required for risky remediation.")
            run.event_log.append(_e.model_dump(mode="json"))
            await _publish(_e)
            return run

        # --- execute ---
        with traced_span("langgraph.stage.execute", self._workflow_span_attributes(incident, run, "execute", job_id)):
            await _publish(_evt("execute", "started", "Executing approved actions."))
            self._execute_actions(incident, run)
            _e = _evt("execute", "completed", "Actions executed.", {"notes": run.remediation_notes[-4:]})
            run.event_log.append(_e.model_dump(mode="json"))
            await _publish(_e)

        # --- finalize ---
        with traced_span("langgraph.stage.finalize", self._workflow_span_attributes(incident, run, "finalize", job_id)):
            await _publish(_evt("finalize", "started", "Finalizing run and scoring outcome."))
            latency_ms = int((time.perf_counter() - started) * 1000)
            run.evaluation = await self.evaluator.evaluate(incident, run, latency_ms, profile.id)
            run.status = RunStatus.completed
            run.finished_at = utc_now()
            run.postmortem_summary = self._build_postmortem(incident, run)
            self.memory.remember(incident, run)
            _e = _evt("finalize", "completed", "Run finalized.", {"score": run.evaluation.score if run.evaluation else None, "status": run.status.value})
            run.event_log.append(_e.model_dump(mode="json"))
            await _publish(_e)

        return run

    async def _run_with_langgraph(
        self,
        incident: Incident,
        prompt_profile_id: str | None,
        model_route_id: str | None,
        job_id: str | None,
        trace_id: str,
        _publish: Callable[[EventMessage], Awaitable[None]],
    ) -> IncidentRun:
        profile_id = prompt_profile_id or self.settings.default_prompt_profile

        def _evt(stage: str, status: str, message: str, payload: dict | None = None) -> EventMessage:
            return EventMessage(stage=stage, status=status, message=message, trace_id=trace_id, job_id=job_id, payload=payload or {})

        async def observe_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.observe", self._workflow_span_attributes(state["incident"], run, "observe", job_id)):
                await _publish(_evt("observe", "started", "Collecting observability signals."))
                state["incident"].status = IncidentStatus.investigating
                state["incident"].updated_at = utc_now()
                snapshot = self.observability_client.collect(state["incident"])
                state["incident"].context["observability"] = snapshot.model_dump(mode="json")
                run.connector_status = self._connector_status()
                e = _evt("observe", "completed", "Observability signals collected.", {"provider": snapshot.provider, "log_count": len(snapshot.logs), "has_release_hint": bool(snapshot.release_hint)})
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def memory_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.memory", self._workflow_span_attributes(state["incident"], run, "memory", job_id)):
                await _publish(_evt("memory", "started", "Searching historical incident memory."))
                run.memory_hits = self.memory.find_similar(state["incident"])
                e = _evt("memory", "completed", f"Matched {len(run.memory_hits)} historical incidents.", {"count": len(run.memory_hits)})
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def graph_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.graph", self._workflow_span_attributes(state["incident"], run, "graph", job_id)):
                await _publish(_evt("graph", "started", "Loading service knowledge graph."))
                run.service_graph_context = self.causal_engine.knowledge_graph.describe_service(state["incident"].service)
                state["incident"].context["service_graph_context"] = run.service_graph_context.model_dump(mode="json")
                e = _evt("graph", "completed", "Service graph context loaded.", run.service_graph_context.model_dump(mode="json"))
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def multimodal_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.multimodal", self._workflow_span_attributes(state["incident"], run, "multimodal", job_id)):
                await _publish(_evt("multimodal", "started", "Analyzing multimodal artifacts."))
                run.multimodal_analysis = self.multimodal_engine.analyze(state["incident"], run.service_graph_context)
                state["incident"].context["multimodal_analysis"] = run.multimodal_analysis.model_dump(mode="json")
                e = _evt("multimodal", "completed", "Multimodal analysis completed.", run.multimodal_analysis.model_dump(mode="json"))
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def feedback_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.feedback", self._workflow_span_attributes(state["incident"], run, "feedback", job_id)):
                await _publish(_evt("feedback", "started", "Loading operator feedback."))
                run.feedback_learning = self.feedback_loop.summarize(state["incident"].service)
                state["incident"].context["feedback_learning"] = run.feedback_learning.model_dump(mode="json")
                e = _evt("feedback", "completed", "Feedback context loaded.", run.feedback_learning.model_dump(mode="json"))
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def causal_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.causal", self._workflow_span_attributes(state["incident"], run, "causal", job_id)):
                await _publish(_evt("causal", "started", "Running causal analysis."))
                snapshot = ObservabilitySnapshot.model_validate(state["incident"].context.get("observability", {}))
                run.causal_analysis = self.causal_engine.analyze(state["incident"], snapshot, run.memory_hits, run.service_graph_context)
                state["incident"].context["causal_analysis"] = run.causal_analysis.model_dump(mode="json")
                e = _evt("causal", "completed", "Causal analysis completed.", run.causal_analysis.model_dump(mode="json"))
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def investigate_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.investigate", self._workflow_span_attributes(state["incident"], run, "investigate", job_id)):
                await _publish(_evt("investigate", "started", "Starting multi-agent investigation."))
                profile = get_profile(state["prompt_profile_id"])
                run.investigation, run.usage = await self.autogen_team.investigate(
                    state["incident"],
                    run.memory_hits,
                    profile,
                    model_route_id,
                    run.id,
                )
                run.investigation.causal_analysis = run.causal_analysis
                run.investigation.multimodal_analysis = run.multimodal_analysis
                run.investigation.feedback_learning = run.feedback_learning
                run.investigation.service_graph_context = run.service_graph_context
                e = _evt("investigate", "completed", "Investigation completed.", {
                    "root_cause": run.investigation.suspected_root_cause,
                    "confidence": run.investigation.confidence,
                    "used_autogen": run.investigation.used_autogen,
                    "action_count": len(run.investigation.recommended_actions),
                    "profile": run.investigation.prompt_profile,
                })
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def policy_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.policy", self._workflow_span_attributes(state["incident"], run, "policy", job_id)):
                await _publish(_evt("policy", "started", "Evaluating policy decisions."))
                run.policy_decisions = []
                for action in run.investigation.recommended_actions:
                    run.policy_decisions.append(await self.policy_engine.evaluate_action(state["incident"], action))
                self._apply_policy_to_actions(run)
                e = _evt("policy", "completed", "Policy decisions applied.", {"decisions": [d.model_dump(mode="json") for d in run.policy_decisions]})
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def execute_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.execute", self._workflow_span_attributes(state["incident"], run, "execute", job_id)):
                await _publish(_evt("execute", "started", "Executing approved actions."))
                self._execute_actions(state["incident"], run)
                e = _evt("execute", "completed", "Actions executed.", {"notes": run.remediation_notes[-4:]})
                run.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return state

        async def finalize_node(state: WorkflowState) -> WorkflowState:
            run = state["run"]
            with traced_span("langgraph.stage.finalize", self._workflow_span_attributes(state["incident"], run, "finalize", job_id)):
                await _publish(_evt("finalize", "started", "Finalizing run and scoring outcome."))
                latency_ms = int((time.perf_counter() - state["started_at"]) * 1000)
                profile_id_local = state["prompt_profile_id"]
                if run.usage:
                    run.usage.duration_ms = latency_ms
                run.evaluation = await self.evaluator.evaluate(state["incident"], run, latency_ms, profile_id_local)
                finalized = await self._finalize_run(state["incident"], run, profile_id_local)
                e = _evt("finalize", "completed", "Run finalized.", {"score": finalized.evaluation.score if finalized.evaluation else None, "status": finalized.status.value})
                finalized.event_log.append(e.model_dump(mode="json"))
                await _publish(e)
            return {
                "incident": state["incident"],
                "run": finalized,
                "prompt_profile_id": state["prompt_profile_id"],
                "started_at": state["started_at"],
            }

        def router(state: WorkflowState) -> str:
            if any(decision.verdict == PolicyVerdict.approval_required for decision in state["run"].policy_decisions):
                state["run"].status = RunStatus.awaiting_approval
                state["incident"].status = IncidentStatus.awaiting_approval
                state["incident"].updated_at = utc_now()
                return "awaiting_approval"
            return "execute"

        graph = StateGraph(WorkflowState)
        graph.add_node("observe", observe_node)
        graph.add_node("memory", memory_node)
        graph.add_node("graph", graph_node)
        graph.add_node("multimodal", multimodal_node)
        graph.add_node("feedback", feedback_node)
        graph.add_node("causal", causal_node)
        graph.add_node("investigate", investigate_node)
        graph.add_node("policy", policy_node)
        graph.add_node("execute", execute_node)
        graph.add_node("finalize", finalize_node)
        graph.add_edge("observe", "memory")
        graph.add_edge("memory", "graph")
        graph.add_edge("graph", "multimodal")
        graph.add_edge("multimodal", "feedback")
        graph.add_edge("feedback", "causal")
        graph.add_edge("causal", "investigate")
        graph.add_edge("investigate", "policy")
        graph.add_conditional_edges("policy", router, {"awaiting_approval": END, "execute": "execute"})
        graph.add_edge("execute", "finalize")
        graph.add_edge("finalize", END)
        graph.set_entry_point("observe")
        app = graph.compile()
        initial = {
            "incident": incident,
            "run": IncidentRun(incident_id=incident.id, connector_status=self._connector_status(), trace_id=trace_id, job_id=job_id),
            "prompt_profile_id": profile_id,
            "started_at": time.perf_counter(),
        }
        with traced_span(
            "workflow.langgraph",
            self._workflow_span_attributes(incident, initial["run"], "workflow", job_id),
        ):
            if hasattr(app, "ainvoke"):
                result = await app.ainvoke(initial)
            else:  # pragma: no cover - compatibility fallback
                result = app.invoke(initial)
        run = result["run"]
        # When the router sends to awaiting_approval, finalize is skipped — publish the gate event now.
        if run.status == RunStatus.awaiting_approval:
            e = _evt("policy", "awaiting_approval", "Human approval required for risky remediation.")
            run.event_log.append(e.model_dump(mode="json"))
            await _publish(e)
        return run

    def _apply_policy_to_actions(self, run: IncidentRun) -> None:
        if not run.investigation:
            return
        decisions_by_id = {decision.action_id: decision for decision in run.policy_decisions}
        for action in run.investigation.recommended_actions:
            decision = decisions_by_id.get(action.id)
            if not decision:
                continue
            action.policy_verdict = decision.verdict
            action.policy_reasons = list(decision.reasons)
            if decision.verdict == PolicyVerdict.blocked:
                action.status = ActionStatus.blocked
            elif decision.verdict == PolicyVerdict.approval_required:
                action.status = ActionStatus.proposed

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

            try:
                action.status = ActionStatus.executed
                execution_note = self.action_executor.execute(incident, action)
                run.remediation_notes.append(f"Executed remediation action: {action.name}. {execution_note}")
            except Exception as exc:  # pragma: no cover - runtime dependent
                action.status = ActionStatus.failed
                run.remediation_notes.append(f"Failed remediation action: {action.name}. {exc}")

    async def _finalize_run(
        self,
        incident: Incident,
        run: IncidentRun,
        prompt_profile_id: str | None = None,
    ) -> IncidentRun:
        incident.status = IncidentStatus.resolved
        incident.updated_at = utc_now()
        run.status = RunStatus.completed
        run.finished_at = utc_now()
        profile_id = prompt_profile_id or self.settings.default_prompt_profile
        if run.evaluation is None:
            run.evaluation = await self.evaluator.evaluate(incident, run, latency_ms=1500, prompt_profile=profile_id)
        if run.usage and not run.usage.duration_ms:
            run.usage.duration_ms = run.evaluation.latency_ms
        run.postmortem_summary = self._build_postmortem(incident, run)
        self.memory.remember(incident, run)
        return run

    def _build_postmortem(self, incident: Incident, run: IncidentRun) -> str:
        root_cause = run.investigation.suspected_root_cause if run.investigation else "No root cause recorded."
        actions = ", ".join(
            action.name for action in (run.investigation.recommended_actions if run.investigation else [])
        ) or "No remediation actions"
        approval_summary = "; ".join(run.approval_notes) if run.approval_notes else "No approval gate used"
        return (
            f"Incident {incident.title} on {incident.service} ended in {incident.status.value}. "
            f"Root cause: {root_cause}. Actions: {actions}. Approval notes: {approval_summary}."
        )

    def connector_health(self) -> list[dict]:
        return [self.observability_client.healthcheck().model_dump(mode="json"), *self.action_executor.connector_health()]

    def _connector_status(self) -> dict[str, dict]:
        connectors = self.connector_health()
        return {item["name"]: item for item in connectors}
