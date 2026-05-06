import asyncio
from contextlib import contextmanager

from app.config import Settings
from app.core.profiles import get_profile
from app.models import Incident, IncidentSeverity
from app.workflow import autogen_team as autogen_team_module
from app.workflow.autogen_team import AutoGenInvestigationTeam


def test_autogen_deterministic_fallback_returns_full_investigation() -> None:
    """When enable_autogen=True but no API key is present the team falls back to
    _heuristic_result(), which is deterministic and requires no network access.
    The result must contain evidence, a confidence score inside (0, 1], and at
    least one recommended action — the minimum contract for the LangGraph
    investigate_node to consume."""
    settings = Settings(
        app_env="test",
        enable_autogen=True,
        openai_api_key=None,  # forces the heuristic path in _can_use_autogen()
    )
    team = AutoGenInvestigationTeam(settings)
    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="p95 latency climbed after recent deployment",
        source="grafana",
        metrics={"p95_latency_ms": 2100, "error_rate": 0.03},
    )
    profile = get_profile("balanced-v1")

    result, usage = asyncio.run(team.investigate(incident, [], profile))

    assert result.evidence, "Expected at least one evidence item"
    assert 0 < result.confidence <= 1.0, f"Confidence out of range: {result.confidence}"
    assert result.recommended_actions, "Expected at least one recommended action"
    assert result.suspected_root_cause, "Expected a root cause string"
    assert not result.used_autogen, "Should not have used the AutoGen runtime without an API key"


def test_autogen_fallback_evidence_grows_with_memory_hits() -> None:
    """Passing historical memory hits should add an evidence item for the hit
    count and increase confidence relative to the no-history baseline."""
    from app.models import MemoryHit

    settings = Settings(app_env="test", enable_autogen=True, openai_api_key=None)
    team = AutoGenInvestigationTeam(settings)
    incident = Incident(
        title="Checkout latency spike",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="latency elevated for 30 minutes",
        source="grafana",
        metrics={"p95_latency_ms": 1800},
    )
    profile = get_profile("balanced-v1")
    memory_hits = [
        MemoryHit(
            incident_id="past-1",
            title="checkout-api latency incident",
            summary="Latency spike caused by upstream dependency saturation.",
            score=4.5,
            service="checkout-api",
            action_names=["scale_service", "notify_on_call"],
        )
    ]

    result_no_mem, _ = asyncio.run(team.investigate(incident, [], profile))
    result_with_mem, _ = asyncio.run(team.investigate(incident, memory_hits, profile))

    mem_evidence = " ".join(result_with_mem.evidence)
    assert "historical" in mem_evidence.lower() or "memory" in mem_evidence.lower(), (
        "Memory hits should surface a historical/memory evidence item"
    )
    assert result_with_mem.confidence >= result_no_mem.confidence, (
        "Memory hits should not decrease confidence"
    )


def test_traced_autogen_client_emits_agent_span(monkeypatch) -> None:
    if autogen_team_module.TracedChatCompletionClient is None:
        return

    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_traced_span(name: str, attributes: dict | None = None, kind=None):
        spans.append((name, dict(attributes or {})))
        yield "trace-test"

    monkeypatch.setattr(autogen_team_module, "traced_span", fake_traced_span)

    class DummyClient(autogen_team_module.ChatCompletionClient):
        async def create(self, messages, *, tools=(), tool_choice="auto", json_output=None, extra_create_args={}, cancellation_token=None):
            return autogen_team_module.CreateResult(
                content="ok",
                finish_reason="stop",
                usage=autogen_team_module.RequestUsage(prompt_tokens=3, completion_tokens=2),
                cached=False,
            )

        def create_stream(self, messages, *, tools=(), tool_choice="auto", json_output=None, extra_create_args={}, cancellation_token=None):
            async def _stream():
                yield autogen_team_module.CreateResult(
                    content="ok",
                    finish_reason="stop",
                    usage=autogen_team_module.RequestUsage(prompt_tokens=3, completion_tokens=2),
                    cached=False,
                )

            return _stream()

        async def close(self) -> None:
            return None

        def actual_usage(self):
            return autogen_team_module.RequestUsage(prompt_tokens=0, completion_tokens=0)

        def total_usage(self):
            return autogen_team_module.RequestUsage(prompt_tokens=0, completion_tokens=0)

        def count_tokens(self, messages, *, tools=()):
            return 0

        def remaining_tokens(self, messages, *, tools=()):
            return 0

        @property
        def capabilities(self):
            return {"vision": False, "function_calling": True, "json_output": True, "family": "unknown"}

        @property
        def model_info(self):
            return {"vision": False, "function_calling": True, "json_output": True, "family": "unknown"}

    wrapped = autogen_team_module.TracedChatCompletionClient(
        DummyClient(),
        agent_name="PlannerAgent",
        incident_id="incident-123",
        run_id="run-456",
        model_name="gpt-4.1-mini",
    )

    result = asyncio.run(wrapped.create([]))

    assert result.content == "ok"
    assert spans, "Expected the traced wrapper to create a span per agent call"
    name, attrs = spans[0]
    assert name == "autogen.agent.call"
    assert attrs["incident_id"] == "incident-123"
    assert attrs["run_id"] == "run-456"
    assert attrs["agent_name"] == "PlannerAgent"
