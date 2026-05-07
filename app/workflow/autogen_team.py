from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Any, Literal, Mapping
from uuid import uuid4

from app.config import Settings
from app.core.embeddings import get_embedding
from app.core.openai_usage import record_openai_usage, snapshot_openai_usage
from app.core.profiles import get_model_route
from app.core.tracing import traced_span
from app.models import (
    AudienceSummaries,
    CausalAnalysis,
    FeedbackLearningSnapshot,
    Incident,
    IncidentSeverity,
    InvestigationResult,
    MemoryAbstraction,
    MemoryHit,
    ModelRoute,
    MultimodalAnalysis,
    PromptProfile,
    RemediationReview,
    RunUsage,
    ServiceGraphContext,
    ToolAction,
)

try:
    from autogen_agentchat.agents import AssistantAgent  # type: ignore
    from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination  # type: ignore
    from autogen_agentchat.teams import SelectorGroupChat  # type: ignore
    from autogen_core import CancellationToken  # type: ignore
    from autogen_core.models import (  # type: ignore
        ChatCompletionClient,
        CreateResult,
        LLMMessage,
        ModelCapabilities,
        ModelInfo,
        RequestUsage,
    )
    from autogen_core.tools import Tool, ToolSchema  # type: ignore
    from autogen_ext.models.openai import OpenAIChatCompletionClient  # type: ignore
    from pydantic import BaseModel  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AssistantAgent = None
    BaseModel = None
    CancellationToken = None
    ChatCompletionClient = None
    CreateResult = None
    LLMMessage = None
    MaxMessageTermination = None
    ModelCapabilities = None
    ModelInfo = None
    OpenAIChatCompletionClient = None
    RequestUsage = None
    SelectorGroupChat = None
    Tool = None
    ToolSchema = None
    TextMentionTermination = None

logger = logging.getLogger(__name__)


def _agent_span_attributes(
    *,
    agent_name: str,
    incident_id: str,
    run_id: str | None,
    model_name: str,
    tool_count: int = 0,
    message_count: int = 0,
) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "run_id": run_id or "",
        "agent_name": agent_name,
        "autogen.agent_name": agent_name,
        "autogen.model": model_name,
        "autogen.tool_count": tool_count,
        "autogen.message_count": message_count,
    }


if ChatCompletionClient is not None:

    class TracedChatCompletionClient(ChatCompletionClient):
        def __init__(
            self,
            base_client: ChatCompletionClient,
            *,
            agent_name: str,
            incident_id: str,
            run_id: str | None,
            model_name: str,
        ) -> None:
            self.base_client = base_client
            self.agent_name = agent_name
            self.incident_id = incident_id
            self.run_id = run_id
            self.model_name = model_name

        async def create(
            self,
            messages: Sequence[LLMMessage],
            *,
            tools: Sequence[Tool | ToolSchema] = [],
            tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
            json_output: bool | type[BaseModel] | None = None,
            extra_create_args: Mapping[str, Any] = {},
            cancellation_token: CancellationToken | None = None,
        ) -> CreateResult:
            with traced_span(
                "autogen.agent.call",
                _agent_span_attributes(
                    agent_name=self.agent_name,
                    incident_id=self.incident_id,
                    run_id=self.run_id,
                    model_name=self.model_name,
                    tool_count=len(tools),
                    message_count=len(messages),
                ),
            ):
                result = await self.base_client.create(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    json_output=json_output,
                    extra_create_args=extra_create_args,
                    cancellation_token=cancellation_token,
                )
                usage = getattr(result, "usage", None) or getattr(result, "models_usage", None)
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
                record_openai_usage(
                    source=f"autogen:{self.agent_name}",
                    model=self.model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return result

        def create_stream(
            self,
            messages: Sequence[LLMMessage],
            *,
            tools: Sequence[Tool | ToolSchema] = [],
            tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
            json_output: bool | type[BaseModel] | None = None,
            extra_create_args: Mapping[str, Any] = {},
            cancellation_token: CancellationToken | None = None,
        ) -> AsyncGenerator[str | CreateResult, None]:
            async def _stream() -> AsyncGenerator[str | CreateResult, None]:
                with traced_span(
                    "autogen.agent.call",
                    _agent_span_attributes(
                        agent_name=self.agent_name,
                        incident_id=self.incident_id,
                        run_id=self.run_id,
                        model_name=self.model_name,
                        tool_count=len(tools),
                        message_count=len(messages),
                    ),
                ):
                    async for chunk in self.base_client.create_stream(
                        messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        json_output=json_output,
                        extra_create_args=extra_create_args,
                        cancellation_token=cancellation_token,
                    ):
                        if CreateResult is not None and isinstance(chunk, CreateResult):
                            usage = getattr(chunk, "usage", None) or getattr(chunk, "models_usage", None)
                            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
                            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
                            record_openai_usage(
                                source=f"autogen:{self.agent_name}",
                                model=self.model_name,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                            )
                        yield chunk

            return _stream()

        async def close(self) -> None:
            await self.base_client.close()

        def actual_usage(self) -> RequestUsage:
            return self.base_client.actual_usage()

        def total_usage(self) -> RequestUsage:
            return self.base_client.total_usage()

        def count_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
            return self.base_client.count_tokens(messages, tools=tools)

        def remaining_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
            return self.base_client.remaining_tokens(messages, tools=tools)

        @property
        def capabilities(self) -> ModelCapabilities:  # type: ignore[override]
            return self.base_client.capabilities

        @property
        def model_info(self) -> ModelInfo:
            return self.base_client.model_info

else:
    TracedChatCompletionClient = None


class AutoGenInvestigationTeam:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def investigate(
        self,
        incident: Incident,
        memory_hits: list[MemoryHit],
        profile: PromptProfile,
        model_route_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[InvestigationResult, RunUsage]:
        started = time.perf_counter()
        model_route = get_model_route(model_route_id or self.settings.default_model_route)
        with traced_span(
            "autogen.investigate",
            {
                "incident_id": incident.id,
                "run_id": run_id or "",
                "incident.service": incident.service,
                "prompt_profile": profile.id,
                "model_route": model_route.id,
            },
        ) as trace_id:
            if self._can_use_autogen():
                try:
                    result, usage = await self._run_autogen(incident, memory_hits, profile, model_route, run_id)
                    usage.duration_ms = int((time.perf_counter() - started) * 1000)
                    usage.trace_id = trace_id
                    return result, usage
                except Exception:
                    logger.exception("AutoGen team execution failed; falling back to local investigation.")

            fallback = self._heuristic_result(incident, memory_hits, profile, model_route, used_autogen=False)
            fallback = self._apply_reflection(fallback, incident)
            return fallback, RunUsage(duration_ms=int((time.perf_counter() - started) * 1000), trace_id=trace_id)

    def _can_use_autogen(self) -> bool:
        return bool(
            self.settings.enable_autogen
            and self.settings.openai_api_key
            and AssistantAgent is not None
            and SelectorGroupChat is not None
            and OpenAIChatCompletionClient is not None
            and TracedChatCompletionClient is not None
            and TextMentionTermination is not None
            and MaxMessageTermination is not None
        )

    async def _run_autogen(
        self,
        incident: Incident,
        memory_hits: list[MemoryHit],
        profile: PromptProfile,
        model_route: ModelRoute,
        run_id: str | None,
    ) -> tuple[InvestigationResult, RunUsage]:
        async def _wrap_tool(tool_name: str, agent_name: str, func: Callable[[], Awaitable[str]]) -> str:
            with traced_span(
                "autogen.agent.tool",
                {
                    "incident_id": incident.id,
                    "run_id": run_id or "",
                    "agent_name": agent_name,
                    "tool_name": tool_name,
                },
            ):
                return await func()

        def _tool(tool_name: str, agent_name: str, func: Callable[[], Awaitable[str]]) -> Callable[[], Awaitable[str]]:
            async def _wrapped() -> str:
                return await _wrap_tool(tool_name, agent_name, func)

            _wrapped.__name__ = tool_name
            return _wrapped

        async def observability_tool_impl() -> str:
            return json.dumps(incident.context.get("observability", {}), indent=2, default=str)

        async def memory_tool_impl() -> str:
            payload = [item.model_dump(mode="json") for item in memory_hits]
            return json.dumps(payload, indent=2, default=str)

        async def causal_tool_impl() -> str:
            return json.dumps(incident.context.get("causal_analysis", {}), indent=2, default=str)

        async def multimodal_tool_impl() -> str:
            return json.dumps(incident.context.get("multimodal_analysis", {}), indent=2, default=str)

        async def feedback_tool_impl() -> str:
            return json.dumps(incident.context.get("feedback_learning", {}), indent=2, default=str)

        async def service_graph_tool_impl() -> str:
            return json.dumps(incident.context.get("service_graph_context", {}), indent=2, default=str)

        async def policy_tool_impl() -> str:
            return (
                "Allowed actions are notify_on_call, open_ticket, scale_service, rollback_release, "
                "collect_diagnostics, and restart_service. Use rollback_release only when release evidence is strong. "
                "Use restart_service only for non production or when explicitly justified."
            )

        async def runbook_tool_impl() -> str:
            runbook_hits = incident.context.get("runbook_hits", [])
            if runbook_hits:
                return json.dumps(
                    {
                        "source": "runbook_rag",
                        "citations": runbook_hits[:5],
                    },
                    indent=2,
                    default=str,
                )
            if memory_hits:
                snippets = []
                for item in memory_hits[:3]:
                    snippets.append(
                        {
                            "title": item.title,
                            "playbook": item.service_playbook,
                            "lessons": item.abstract_lessons,
                            "actions": item.action_names,
                        }
                    )
                return json.dumps(snippets, indent=2, default=str)
            return json.dumps(
                {
                    "service": incident.service,
                    "playbook": f"Review observability, confirm customer impact, compare to recent deployments, and prefer safe reversible actions first for {incident.service}.",
                },
                indent=2,
            )

        async def release_tool_impl() -> str:
            observability = incident.context.get("observability", {})
            release_context = {
                "release_hint": observability.get("release_hint"),
                "release_metadata": observability.get("release_metadata"),
                "traces": observability.get("traces", []),
            }
            return json.dumps(release_context, indent=2, default=str)

        planner_model = self.settings.autogen_planner_model or model_route.planner_model or self.settings.openai_model
        investigator_model = self.settings.autogen_investigator_model or model_route.investigator_model or self.settings.openai_model
        critic_model = self.settings.autogen_critic_model or model_route.critic_model or self.settings.openai_model
        commander_model = self.settings.autogen_commander_model or model_route.commander_model or self.settings.openai_model

        planner_client_base = OpenAIChatCompletionClient(model=planner_model, api_key=self.settings.openai_api_key)
        investigator_client_base = OpenAIChatCompletionClient(model=investigator_model, api_key=self.settings.openai_api_key)
        critic_client_base = OpenAIChatCompletionClient(model=critic_model, api_key=self.settings.openai_api_key)
        commander_client_base = OpenAIChatCompletionClient(model=commander_model, api_key=self.settings.openai_api_key)

        planner_client = TracedChatCompletionClient(
            planner_client_base,
            agent_name="PlannerAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=planner_model,
        )
        selector_client = TracedChatCompletionClient(
            planner_client_base,
            agent_name="SelectorCoordinator",
            incident_id=incident.id,
            run_id=run_id,
            model_name=planner_model,
        )
        investigator_client = TracedChatCompletionClient(
            investigator_client_base,
            agent_name="InvestigatorAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=investigator_model,
        )
        runbook_client = TracedChatCompletionClient(
            investigator_client_base,
            agent_name="RunbookAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=investigator_model,
        )
        release_client = TracedChatCompletionClient(
            investigator_client_base,
            agent_name="ReleaseCorrelationAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=investigator_model,
        )
        causal_client = TracedChatCompletionClient(
            investigator_client_base,
            agent_name="CausalAnalystAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=investigator_model,
        )
        visual_reasoning_client = TracedChatCompletionClient(
            investigator_client_base,
            agent_name="VisualReasoningAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=investigator_model,
        )
        graph_reasoning_client = TracedChatCompletionClient(
            investigator_client_base,
            agent_name="GraphReasoningAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=investigator_model,
        )
        critic_client = TracedChatCompletionClient(
            critic_client_base,
            agent_name="CriticAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=critic_model,
        )
        remediation_reviewer_client = TracedChatCompletionClient(
            critic_client_base,
            agent_name="RemediationReviewerAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=critic_model,
        )
        feedback_learning_client = TracedChatCompletionClient(
            critic_client_base,
            agent_name="FeedbackLearningAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=critic_model,
        )
        postmortem_writer_client = TracedChatCompletionClient(
            commander_client_base,
            agent_name="PostmortemWriterAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=commander_model,
        )
        commander_client = TracedChatCompletionClient(
            commander_client_base,
            agent_name="CommanderAgent",
            incident_id=incident.id,
            run_id=run_id,
            model_name=commander_model,
        )

        planner_agent = AssistantAgent(
            "PlannerAgent",
            description="Breaks the incident into investigation steps and delegates the next move.",
            model_client=planner_client,
            system_message=(
                "You are the planning lead for an incident response team. Start by deciding what evidence "
                "the team needs, which teammate should inspect it, and what outputs the commander must produce."
            ),
        )
        investigator_agent = AssistantAgent(
            "InvestigatorAgent",
            description="Pulls observability and historical memory evidence using tools.",
            model_client=investigator_client,
            tools=[
                _tool("observability_tool", "InvestigatorAgent", observability_tool_impl),
                _tool("memory_tool", "InvestigatorAgent", memory_tool_impl),
                _tool("policy_tool", "InvestigatorAgent", policy_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You are the investigation specialist. Use the tools before making claims. Pull observability, "
                "historical incident memory, and policy context. Only state evidence you can support."
            ),
        )
        runbook_agent = AssistantAgent(
            "RunbookAgent",
            description="Extracts reusable remediation patterns and runbook hints from memory and service context.",
            model_client=runbook_client,
            tools=[
                _tool("runbook_tool", "RunbookAgent", runbook_tool_impl),
                _tool("memory_tool", "RunbookAgent", memory_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You are the runbook specialist. Use tools to extract reusable patterns, service playbook hints, "
                "and incident lessons that should shape the final recommendation."
            ),
        )
        release_agent = AssistantAgent(
            "ReleaseCorrelationAgent",
            description="Checks whether recent deployments or release metadata correlate with the incident.",
            model_client=release_client,
            tools=[
                _tool("release_tool", "ReleaseCorrelationAgent", release_tool_impl),
                _tool("observability_tool", "ReleaseCorrelationAgent", observability_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You analyze release correlation. Use the tools to decide whether deployment context should influence "
                "the final root cause and remediation plan."
            ),
        )
        causal_agent = AssistantAgent(
            "CausalAnalystAgent",
            description="Reasons over dependency chains, propagation paths, and causal signals.",
            model_client=causal_client,
            tools=[
                _tool("causal_tool", "CausalAnalystAgent", causal_tool_impl),
                _tool("observability_tool", "CausalAnalystAgent", observability_tool_impl),
                _tool("release_tool", "CausalAnalystAgent", release_tool_impl),
                _tool("service_graph_tool", "CausalAnalystAgent", service_graph_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You are the causal analyst. Use the tools to explain which service or dependency is most likely "
                "to sit at the root of the incident and how the failure propagated."
            ),
        )
        visual_reasoning_agent = AssistantAgent(
            "VisualReasoningAgent",
            description="Interprets Grafana screenshots, architecture diagrams, and trace visualizations as structured evidence.",
            model_client=visual_reasoning_client,
            tools=[
                _tool("multimodal_tool", "VisualReasoningAgent", multimodal_tool_impl),
                _tool("observability_tool", "VisualReasoningAgent", observability_tool_impl),
                _tool("service_graph_tool", "VisualReasoningAgent", service_graph_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You are the multimodal reasoning specialist. Use the tools to interpret visual incident evidence, "
                "connect it to services, and surface signals that should influence the final root-cause narrative."
            ),
        )
        graph_reasoning_agent = AssistantAgent(
            "GraphReasoningAgent",
            description="Explains the service dependency graph, blast radius, and propagation paths.",
            model_client=graph_reasoning_client,
            tools=[
                _tool("service_graph_tool", "GraphReasoningAgent", service_graph_tool_impl),
                _tool("causal_tool", "GraphReasoningAgent", causal_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You reason over the service knowledge graph. Use the graph to explain upstream dependencies, "
                "downstream blast radius, and the most plausible propagation path."
            ),
        )
        critic_agent = AssistantAgent(
            "CriticAgent",
            description="Challenges assumptions, looks for missing evidence, and pressure tests remediation choices.",
            model_client=critic_client,
            system_message=(
                "You are the critic. Inspect the investigation for weak assumptions, missing evidence, or unsafe "
                "actions. Explicitly call out what should be revised before the final answer."
            ),
        )
        remediation_reviewer_agent = AssistantAgent(
            "RemediationReviewerAgent",
            description="Scores remediation safety and separates safe actions from risky ones.",
            model_client=remediation_reviewer_client,
            tools=[_tool("policy_tool", "RemediationReviewerAgent", policy_tool_impl)],
            reflect_on_tool_use=True,
            system_message=(
                "You are the remediation reviewer. Distinguish safe actions from risky actions, summarize risk, "
                "and push the team toward reversible and policy-aligned remediation."
            ),
        )
        feedback_learning_agent = AssistantAgent(
            "FeedbackLearningAgent",
            description="Brings operator approvals, overrides, and corrections into the investigation.",
            model_client=feedback_learning_client,
            tools=[
                _tool("feedback_tool", "FeedbackLearningAgent", feedback_tool_impl),
                _tool("memory_tool", "FeedbackLearningAgent", memory_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You specialize in operator feedback learning. Use the tools to surface which remediations operators "
                "prefer, which root-cause patterns they corrected before, and how current recommendations should adapt."
            ),
        )
        postmortem_writer_agent = AssistantAgent(
            "PostmortemWriterAgent",
            description="Drafts audience-specific explanations and postmortem-ready summaries.",
            model_client=postmortem_writer_client,
            tools=[
                _tool("runbook_tool", "PostmortemWriterAgent", runbook_tool_impl),
                _tool("release_tool", "PostmortemWriterAgent", release_tool_impl),
            ],
            reflect_on_tool_use=True,
            system_message=(
                "You produce audience-specific communication: operator, executive, engineering, and postmortem summaries. "
                "Keep them concise and operationally useful."
            ),
        )
        commander_agent = AssistantAgent(
            "CommanderAgent",
            description="Produces the final structured incident summary for operators.",
            model_client=commander_client,
            system_message=(
                "You are the final incident commander. Produce JSON only, followed by TERMINATE. "
                "Schema: {"
                '"summary": string, '
                '"suspected_root_cause": string, '
                '"confidence": number between 0 and 1, '
                '"evidence": [string], '
                '"agent_notes": {string: string}, '
                '"recommended_actions": ['
                '{"name": string, "description": string, "command": string, "risk_level": "low|medium|high|critical", "metadata": object}'
                "], "
                '"audience_summaries": {"operator": string, "executive": string, "engineering": string, "postmortem": string}, '
                '"remediation_review": {"risk_summary": string, "safe_actions": [string], "risky_actions": [string], "reviewer_score": number}, '
                '"memory_abstractions": {"lessons": [string], "remediation_patterns": [string], "service_playbook": string}, '
                '"causal_analysis": {"likely_root_service": string, "likely_cause": string, "confidence": number, "reasoning": [string], "impacted_services": [string], "propagation_path": [string], "graph_nodes": [string], "graph_edges": [{"source": string, "target": string}], "release_correlation": boolean}, '
                '"multimodal_analysis": {"summary": string, "visual_signals": [string], "implicated_services": [string], "evidence": [string], "confidence": number}, '
                '"feedback_learning": {"guidance": [string], "preferred_actions": [string], "discouraged_actions": [string], "corrected_root_causes": [string], "escalation_patterns": [string], "confidence": number}, '
                '"service_graph_context": {"focal_service": string, "summary": string, "upstream_services": [string], "downstream_services": [string], "critical_dependencies": [string], "blast_radius": [string]}, '
                '"reflection_notes": [string], '
                '"revision_count": integer, '
                '"runbook_excerpt": string, '
                '"release_assessment": string'
                "}."
            ),
        )

        termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(16)
        selector_prompt = (
            "Available team roles:\n{roles}\n\n"
            "Current conversation:\n{history}\n\n"
            "Select the single best participant from {participants} to speak next. Start with PlannerAgent. "
            "Route through InvestigatorAgent, RunbookAgent, ReleaseCorrelationAgent, CausalAnalystAgent, "
            "VisualReasoningAgent, GraphReasoningAgent, CriticAgent, FeedbackLearningAgent, "
            "RemediationReviewerAgent, and PostmortemWriterAgent before CommanderAgent finalizes."
        )

        team = SelectorGroupChat(
            [
                planner_agent,
                investigator_agent,
                runbook_agent,
                release_agent,
                causal_agent,
                visual_reasoning_agent,
                graph_reasoning_agent,
                critic_agent,
                feedback_learning_agent,
                remediation_reviewer_agent,
                postmortem_writer_agent,
                commander_agent,
            ],
            model_client=selector_client,
            termination_condition=termination,
            allow_repeated_speaker=True,
            selector_prompt=selector_prompt,
        )

        task = (
            f"Investigate the following incident.\n"
            f"Title: {incident.title}\n"
            f"Service: {incident.service}\n"
            f"Environment: {incident.environment}\n"
            f"Severity: {incident.severity.value}\n"
            f"Description: {incident.description}\n"
            f"Metrics: {json.dumps(incident.metrics, default=str)}\n"
            f"Labels: {incident.labels}\n"
            f"Prompt profile: {profile.id}.\n"
            "You must collaborate, use tools, challenge weak evidence, separate safe and risky remediations, "
            "generate audience-specific summaries, include causal reasoning, use multimodal evidence, learn from feedback, "
            "use the service graph, and end with the required JSON plus TERMINATE."
        )

        try:
            task_result = await team.run(task=task)
            transcript = self._extract_transcript(task_result)
            final_payload = self._normalize_payload(self._parse_final_payload(transcript), incident, memory_hits)
            result = self._payload_to_result(final_payload, profile, model_route, True, transcript)
            result = self._apply_reflection(result, incident)
            usage = self._extract_usage(task_result)
            return result, usage
        finally:
            for client in [
                planner_client_base,
                investigator_client_base,
                critic_client_base,
                commander_client_base,
            ]:
                close_callable = getattr(client, "close", None)
                if close_callable is not None:
                    maybe_coro = close_callable()
                    if hasattr(maybe_coro, "__await__"):
                        await maybe_coro

    def _extract_transcript(self, task_result: object) -> list[dict[str, str]]:
        transcript: list[dict[str, str]] = []
        messages = getattr(task_result, "messages", [])
        for message in messages:
            source = getattr(message, "source", "unknown")
            content = getattr(message, "content", "")
            transcript.append({"source": str(source), "content": self._stringify_content(content)})
        return transcript

    def _extract_usage(self, task_result: object) -> RunUsage:
        tracked_usage = snapshot_openai_usage()
        if tracked_usage is not None and tracked_usage.total_tokens > 0:
            return tracked_usage
        prompt_tokens = 0
        completion_tokens = 0
        messages = getattr(task_result, "messages", [])
        for message in messages:
            usage = getattr(message, "models_usage", None)
            if usage is None:
                continue
            prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

        total_tokens = prompt_tokens + completion_tokens
        return RunUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=0.0,
            duration_ms=0,
        )

    def _parse_final_payload(self, transcript: list[dict[str, str]]) -> dict:
        for item in reversed(transcript):
            content = item["content"]
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                continue
            candidate = json_match.group(0).replace("TERMINATE", "").strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise ValueError("AutoGen team did not return a valid JSON payload.")

    def _normalize_payload(self, payload: dict, incident: Incident, memory_hits: list[MemoryHit]) -> dict:
        payload.setdefault("summary", f"{incident.service} incident under investigation.")
        payload.setdefault("suspected_root_cause", "Broader observability review is still needed.")
        payload.setdefault("confidence", 0.62)
        payload.setdefault("evidence", [])
        payload.setdefault("agent_notes", {})
        payload.setdefault("recommended_actions", [])
        payload.setdefault("audience_summaries", self._build_audience_summaries(incident, payload).model_dump(mode="json"))
        payload.setdefault("remediation_review", self._build_remediation_review(payload.get("recommended_actions", [])).model_dump(mode="json"))
        payload.setdefault(
            "memory_abstractions",
            self._build_memory_abstractions(incident, payload, memory_hits).model_dump(mode="json"),
        )
        payload.setdefault(
            "causal_analysis",
            self._build_causal_analysis(incident, payload).model_dump(mode="json"),
        )
        payload.setdefault(
            "multimodal_analysis",
            self._build_multimodal_analysis(incident).model_dump(mode="json"),
        )
        payload.setdefault(
            "feedback_learning",
            self._build_feedback_learning(incident).model_dump(mode="json"),
        )
        payload.setdefault(
            "service_graph_context",
            self._build_service_graph_context(incident).model_dump(mode="json"),
        )
        payload.setdefault("reflection_notes", ["Critic and remediation reviewer completed a final pass."])
        payload.setdefault("revision_count", 1)
        payload.setdefault("runbook_excerpt", self._build_runbook_excerpt(incident, payload.get("recommended_actions", [])))
        payload.setdefault("release_assessment", self._build_release_assessment(incident))
        return payload

    def _payload_to_result(
        self,
        final_payload: dict,
        profile: PromptProfile,
        model_route: ModelRoute,
        used_autogen: bool,
        transcript: list[dict[str, str]],
    ) -> InvestigationResult:
        return InvestigationResult(
            run_id=str(uuid4()),
            summary=str(final_payload["summary"]),
            suspected_root_cause=str(final_payload["suspected_root_cause"]),
            confidence=float(final_payload["confidence"]),
            evidence=[str(item) for item in final_payload.get("evidence", [])],
            agent_notes={str(key): str(value) for key, value in dict(final_payload.get("agent_notes", {})).items()},
            recommended_actions=self._parse_actions(final_payload.get("recommended_actions", [])),
            prompt_profile=profile.id,
            used_autogen=used_autogen,
            model_route_id=model_route.id,
            agent_models={
                "planner": self.settings.autogen_planner_model or model_route.planner_model,
                "investigator": self.settings.autogen_investigator_model or model_route.investigator_model,
                "critic": self.settings.autogen_critic_model or model_route.critic_model,
                "commander": self.settings.autogen_commander_model or model_route.commander_model,
            },
            audience_summaries=AudienceSummaries.model_validate(final_payload.get("audience_summaries", {})),
            remediation_review=RemediationReview.model_validate(final_payload.get("remediation_review", {})),
            memory_abstractions=MemoryAbstraction.model_validate(final_payload.get("memory_abstractions", {})),
            causal_analysis=CausalAnalysis.model_validate(final_payload.get("causal_analysis", {})),
            multimodal_analysis=MultimodalAnalysis.model_validate(final_payload.get("multimodal_analysis", {})),
            feedback_learning=FeedbackLearningSnapshot.model_validate(final_payload.get("feedback_learning", {})),
            service_graph_context=ServiceGraphContext.model_validate(final_payload.get("service_graph_context", {})),
            reflection_notes=[str(item) for item in final_payload.get("reflection_notes", [])],
            revision_count=int(final_payload.get("revision_count", 0) or 0),
            runbook_excerpt=str(final_payload.get("runbook_excerpt", "")) or None,
            release_assessment=str(final_payload.get("release_assessment", "")) or None,
            transcript=transcript,
        )

    def _parse_actions(self, actions: list[dict]) -> list[ToolAction]:
        parsed_actions: list[ToolAction] = []
        for item in actions:
            risk_value = str(item.get("risk_level", "medium")).lower()
            risk_level = IncidentSeverity(risk_value) if risk_value in IncidentSeverity._value2member_map_ else IncidentSeverity.medium
            parsed_actions.append(
                ToolAction(
                    name=item.get("name", "collect_diagnostics"),
                    description=item.get("description", "Follow up on the incident."),
                    command=item.get("command", "observability.collect service"),
                    risk_level=risk_level,
                    metadata=dict(item.get("metadata", {})),
                )
            )
        return parsed_actions

    @staticmethod
    def _stringify_content(content: object) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, default=str)
        except TypeError:
            return str(content)

    def _heuristic_result(
        self,
        incident: Incident,
        memory_hits: list[MemoryHit],
        profile: PromptProfile,
        model_route: ModelRoute,
        used_autogen: bool,
    ) -> InvestigationResult:
        description = f"{incident.title} {incident.description}".lower()
        metrics = incident.metrics
        observability = incident.context.get("observability", {})
        causal_context = incident.context.get("causal_analysis", {})
        evidence: list[str] = []
        notes: dict[str, str] = {}

        if "latency" in description or metrics.get("p95_latency_ms", 0) > 1500:
            root_cause = "Latency spike caused by upstream dependency saturation."
            evidence.append("Observed elevated p95 latency and request timeout patterns.")
            notes["triage_agent"] = "Traffic pattern suggests service saturation or upstream degradation."
            actions = [
                ToolAction(
                    name="scale_service",
                    description="Scale the service deployment by two replicas.",
                    command=f"scale service {incident.service} to 6 replicas",
                    risk_level=IncidentSeverity.medium,
                    metadata={"replicas": 6},
                ),
                ToolAction(
                    name="notify_on_call",
                    description="Notify the on-call engineer in Slack.",
                    command="slack notify ops on call",
                    risk_level=IncidentSeverity.low,
                ),
            ]
            confidence = 0.78
        elif "5xx" in description or metrics.get("error_rate", 0) > 0.08:
            root_cause = "Elevated server error rate likely caused by a bad deployment or exhausted dependency."
            evidence.append("5xx error rate exceeded the alert threshold.")
            notes["log_agent"] = "Error burst aligns with a recent release window."
            actions = [
                ToolAction(
                    name="rollback_release",
                    description="Rollback the most recent deployment.",
                    command="rollback latest deployment",
                    risk_level=IncidentSeverity.high,
                ),
                ToolAction(
                    name="open_ticket",
                    description="Create an incident ticket for follow-up.",
                    command="jira create issue",
                    risk_level=IncidentSeverity.low,
                ),
                ToolAction(
                    name="notify_on_call",
                    description="Notify the on-call engineer in Slack.",
                    command="slack notify ops on call",
                    risk_level=IncidentSeverity.low,
                ),
            ]
            confidence = 0.82
        else:
            root_cause = "Insufficient evidence for a narrow root cause; broader observability review is needed."
            evidence.append("No dominant metric signal was detected from the initial payload.")
            notes["reviewer_agent"] = "Recommend safe data collection and human escalation."
            actions = [
                ToolAction(
                    name="collect_diagnostics",
                    description="Collect diagnostic logs and traces for manual review.",
                    command="collect diagnostics bundle",
                    risk_level=IncidentSeverity.low,
                ),
                ToolAction(
                    name="notify_on_call",
                    description="Notify the on-call engineer in Slack.",
                    command="slack notify ops on call",
                    risk_level=IncidentSeverity.low,
                ),
            ]
            confidence = 0.61

        if incident.environment.lower() != "production" and "restart" in description:
            actions.append(
                ToolAction(
                    name="restart_service",
                    description="Restart the non production service after capturing diagnostics.",
                    command="restart non production service",
                    risk_level=IncidentSeverity.medium,
                )
            )

        if memory_hits:
            evidence.append(f"Found {len(memory_hits)} related historical incidents.")
            notes["runbook_agent"] = "Historical memory points to reusable remediation patterns."
            confidence += 0.04

        if observability.get("release_hint"):
            evidence.append(f"Release hint detected: {observability['release_hint']}")
            notes["release_agent"] = f"Recent deployment correlation found for {observability['release_hint']}."

        if observability.get("anomaly_detected"):
            anomaly = observability.get("anomaly_detection", {})
            evidence.append(f"Isolation Forest anomaly signal: {anomaly.get('summary', 'anomalous telemetry detected')}")
            notes["anomaly_agent"] = (
                "Real ML anomaly detection ran on Prometheus metrics before AutoGen investigation."
            )
            confidence += 0.05

        if causal_context.get("likely_cause"):
            evidence.append(f"Causal reasoning suggests: {causal_context['likely_cause']}")
            notes["causal_agent"] = "Dependency-path analysis contributed to the root-cause shortlist."
            if "dependency" in str(causal_context["likely_cause"]).lower() and "latency" not in root_cause.lower():
                root_cause = str(causal_context["likely_cause"])
                confidence = max(confidence, float(causal_context.get("confidence", 0.72)))

        if observability.get("logs"):
            evidence.append(f"Sample log: {observability['logs'][0]}")

        runbook_hits = incident.context.get("runbook_hits", [])
        if runbook_hits:
            top = runbook_hits[0]
            evidence.append(f"Runbook citation: {top.get('title')} - {top.get('excerpt')}")
            notes["runbook_agent"] = "Runbook RAG retrieved operator procedures before final recommendations."

        confidence = max(0.35, min(confidence + profile.confidence_bias, 0.97))
        if profile.aggressiveness_bias < 0:
            actions = [action for action in actions if action.name != "rollback_release"]

        payload = {
            "summary": f"{incident.service} incident investigation for {incident.environment} identified: {root_cause}",
            "suspected_root_cause": root_cause,
            "confidence": round(confidence, 2),
            "evidence": evidence,
            "agent_notes": notes,
            "recommended_actions": [
                {
                    "name": action.name,
                    "description": action.description,
                    "command": action.command,
                    "risk_level": action.risk_level.value,
                    "metadata": action.metadata,
                }
                for action in actions
            ],
            "audience_summaries": self._build_audience_summaries(
                incident,
                {"summary": root_cause, "suspected_root_cause": root_cause, "recommended_actions": [a.name for a in actions]},
            ).model_dump(mode="json"),
            "remediation_review": self._build_remediation_review(
                [
                    {
                        "name": action.name,
                        "risk_level": action.risk_level.value,
                    }
                    for action in actions
                ]
            ).model_dump(mode="json"),
            "memory_abstractions": self._build_memory_abstractions(
                incident,
                {"summary": root_cause, "suspected_root_cause": root_cause},
                memory_hits,
            ).model_dump(mode="json"),
            "causal_analysis": self._build_causal_analysis(
                incident,
                {"suspected_root_cause": root_cause},
            ).model_dump(mode="json"),
            "multimodal_analysis": self._build_multimodal_analysis(incident).model_dump(mode="json"),
            "feedback_learning": self._build_feedback_learning(incident).model_dump(mode="json"),
            "service_graph_context": self._build_service_graph_context(incident).model_dump(mode="json"),
            "reflection_notes": ["Critic reviewed the draft and kept only the most defensible actions."],
            "revision_count": 1,
            "runbook_excerpt": self._build_runbook_excerpt(
                incident,
                [{"name": action.name} for action in actions],
            ),
            "release_assessment": self._build_release_assessment(incident),
        }
        return self._payload_to_result(payload, profile, model_route, used_autogen, transcript=[])

    def _apply_reflection(self, result: InvestigationResult, incident: Incident) -> InvestigationResult:
        risky_actions = [
            action for action in result.recommended_actions if action.risk_level in {IncidentSeverity.high, IncidentSeverity.critical}
        ]
        release_hint = incident.context.get("observability", {}).get("release_hint")

        if risky_actions and not release_hint:
            revised_actions = []
            removed = []
            for action in result.recommended_actions:
                if action.name == "rollback_release":
                    removed.append(action.name)
                    continue
                revised_actions.append(action)
            if removed:
                result.recommended_actions = revised_actions or result.recommended_actions
                result.reflection_notes.append(
                    "Remediation reviewer removed rollback_release because release evidence was not strong enough."
                )
                if result.remediation_review:
                    result.remediation_review.risky_actions = [
                        action for action in result.remediation_review.risky_actions if action != "rollback_release"
                    ]
                    if "collect_diagnostics" not in result.remediation_review.safe_actions:
                        result.remediation_review.safe_actions.append("collect_diagnostics")
                result.revision_count += 1

        if len(result.evidence) < 2:
            result.evidence.append("Reflection pass requested broader data collection before aggressive remediation.")
            if all(action.name != "collect_diagnostics" for action in result.recommended_actions):
                result.recommended_actions.insert(
                    0,
                    ToolAction(
                        name="collect_diagnostics",
                        description="Collect broader telemetry before executing higher risk changes.",
                        command="collect diagnostics bundle",
                        risk_level=IncidentSeverity.low,
                    ),
                )
            result.reflection_notes.append("Critic expanded the evidence plan because initial support was thin.")
            result.revision_count += 1

        if result.remediation_review is None:
            result.remediation_review = self._build_remediation_review(
                [{"name": action.name, "risk_level": action.risk_level.value} for action in result.recommended_actions]
            )
        if result.memory_abstractions is None:
            result.memory_abstractions = self._build_memory_abstractions(
                incident,
                {"summary": result.summary, "suspected_root_cause": result.suspected_root_cause},
                [],
            )
        if result.causal_analysis is None:
            result.causal_analysis = self._build_causal_analysis(
                incident,
                {"suspected_root_cause": result.suspected_root_cause},
            )
        if result.multimodal_analysis is None:
            result.multimodal_analysis = self._build_multimodal_analysis(incident)
        if result.feedback_learning is None:
            result.feedback_learning = self._build_feedback_learning(incident)
        if result.service_graph_context is None:
            result.service_graph_context = self._build_service_graph_context(incident)
        if not result.runbook_excerpt:
            result.runbook_excerpt = self._build_runbook_excerpt(
                incident,
                [{"name": action.name} for action in result.recommended_actions],
            )
        if not result.release_assessment:
            result.release_assessment = self._build_release_assessment(incident)
        if not result.audience_summaries.operator:
            result.audience_summaries = self._build_audience_summaries(
                incident,
                {
                    "summary": result.summary,
                    "suspected_root_cause": result.suspected_root_cause,
                    "recommended_actions": [action.name for action in result.recommended_actions],
                },
            )
        if result.causal_analysis and result.causal_analysis.reasoning:
            for reason in result.causal_analysis.reasoning[:2]:
                if reason not in result.evidence:
                    result.evidence.append(reason)
        if result.multimodal_analysis and result.multimodal_analysis.evidence:
            for item in result.multimodal_analysis.evidence[:2]:
                if item not in result.evidence:
                    result.evidence.append(item)
        if result.feedback_learning and result.feedback_learning.guidance:
            result.reflection_notes.extend(
                guidance
                for guidance in result.feedback_learning.guidance[:2]
                if guidance not in result.reflection_notes
            )
        return result

    def _build_audience_summaries(self, incident: Incident, payload: dict) -> AudienceSummaries:
        actions = payload.get("recommended_actions", [])
        if actions and isinstance(actions[0], dict):
            action_names = [str(item.get("name", "")) for item in actions]
        else:
            action_names = [str(item) for item in actions]
        action_summary = ", ".join(item for item in action_names if item) or "manual triage"
        root_cause = str(payload.get("suspected_root_cause", "further diagnosis required"))
        summary = str(payload.get("summary", root_cause))
        return AudienceSummaries(
            operator=f"{incident.service} is showing {incident.severity.value} symptoms. Likely issue: {root_cause}. Next actions: {action_summary}.",
            executive=f"{incident.service} is under investigation. The current view is {root_cause.lower()} and the team is following a controlled remediation plan.",
            engineering=f"Engineering focus: validate {root_cause.lower()}, confirm customer impact, and execute {action_summary} with policy checks.",
            postmortem=f"{summary} Final working theory: {root_cause}. Action path: {action_summary}.",
        )

    def _build_remediation_review(self, actions: list[dict]) -> RemediationReview:
        safe_actions: list[str] = []
        risky_actions: list[str] = []
        for item in actions:
            name = str(item.get("name", "collect_diagnostics"))
            risk = str(item.get("risk_level", "medium")).lower()
            if risk in {"high", "critical"}:
                risky_actions.append(name)
            else:
                safe_actions.append(name)

        if risky_actions:
            summary = "The plan contains higher-risk actions that should stay behind approval or stronger evidence."
            score = 0.62
        else:
            summary = "The recommended plan is mostly reversible and operationally safe."
            score = 0.84

        return RemediationReview(
            risk_summary=summary,
            safe_actions=safe_actions,
            risky_actions=risky_actions,
            reviewer_score=score,
        )

    def _build_memory_abstractions(self, incident: Incident, payload: dict, memory_hits: list[MemoryHit]) -> MemoryAbstraction:
        root_cause = str(payload.get("suspected_root_cause", "diagnostic review"))
        lessons = [
            f"{incident.service} incidents benefit from validating {root_cause.lower()} against recent observability context.",
        ]
        patterns = ["evidence_first_pattern"]
        if "latency" in root_cause.lower():
            patterns.append("traffic_saturation_pattern")
        if "deployment" in root_cause.lower() or "release" in root_cause.lower():
            patterns.append("release_regression_pattern")
        if memory_hits:
            lessons.append("Historical incidents narrowed the search space before remediation was selected.")
            patterns.append("memory_guided_triage_pattern")
        playbook = (
            f"For {incident.service}, confirm the primary signal, inspect release correlation, "
            f"and prefer safe reversible remediation before higher-risk actions."
        )
        embedding_terms = f"{incident.title} {incident.description} {root_cause} {' '.join(patterns)}"
        embedding_vector = self._simple_embedding(embedding_terms)
        return MemoryAbstraction(
            lessons=lessons,
            remediation_patterns=patterns,
            service_playbook=playbook,
            embedding_vector=embedding_vector,
        )

    def _build_runbook_excerpt(self, incident: Incident, actions: list[dict]) -> str:
        action_names = [str(item.get("name", item)) for item in actions]
        action_summary = ", ".join(name for name in action_names if name) or "collect diagnostics"
        return (
            f"Runbook focus for {incident.service}: validate customer impact, confirm the leading signal, "
            f"review release context, then {action_summary}."
        )

    def _build_release_assessment(self, incident: Incident) -> str:
        release_hint = incident.context.get("observability", {}).get("release_hint")
        if release_hint:
            return f"Release correlation is plausible because observability reported {release_hint}."
        return "No strong release correlation was found in the current observability context."

    def _build_causal_analysis(self, incident: Incident, payload: dict) -> CausalAnalysis:
        raw = incident.context.get("causal_analysis", {})
        if raw:
            merged = dict(raw)
            merged["likely_cause"] = merged.get("likely_cause") or str(payload.get("suspected_root_cause", ""))
            return CausalAnalysis.model_validate(merged)
        likely_cause = str(payload.get("suspected_root_cause", "broader observability review"))
        return CausalAnalysis(
            likely_root_service=incident.service,
            likely_cause=likely_cause,
            confidence=0.58,
            reasoning=["No explicit causal graph was available, so the team used service-local evidence first."],
            impacted_services=[incident.service],
            propagation_path=[incident.service],
            graph_nodes=[incident.service],
            graph_edges=[],
            release_correlation=bool(incident.context.get("observability", {}).get("release_hint")),
        )

    def _build_multimodal_analysis(self, incident: Incident) -> MultimodalAnalysis:
        raw = incident.context.get("multimodal_analysis", {})
        if raw:
            return MultimodalAnalysis.model_validate(raw)
        return MultimodalAnalysis(
            summary="No multimodal evidence was attached.",
            findings=[],
            visual_signals=[],
            implicated_services=[incident.service],
            evidence=[],
            confidence=0.0,
            generated_with_vision=False,
        )

    def _build_feedback_learning(self, incident: Incident) -> FeedbackLearningSnapshot:
        raw = incident.context.get("feedback_learning", {})
        if raw:
            return FeedbackLearningSnapshot.model_validate(raw)
        return FeedbackLearningSnapshot(
            service=incident.service,
            guidance=["No operator feedback has been recorded yet."],
            confidence=0.0,
        )

    def _build_service_graph_context(self, incident: Incident) -> ServiceGraphContext:
        raw = incident.context.get("service_graph_context", {})
        if raw:
            return ServiceGraphContext.model_validate(raw)
        return ServiceGraphContext(
            focal_service=incident.service,
            summary=f"No service graph context has been recorded yet for {incident.service}.",
            blast_radius=[incident.service],
            confidence=0.0,
        )

    def _simple_embedding(self, text: str) -> list[float]:
        return get_embedding(
            text,
            self.settings.openai_api_key,
            self.settings.openai_api_base,
            usage_source="autogen_memory_abstraction_embedding",
        )
