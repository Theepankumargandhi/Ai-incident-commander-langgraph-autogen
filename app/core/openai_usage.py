from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.models import OpenAIUsageLineItem, RunUsage


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float


_MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing(input_per_1m=2.50, output_per_1m=10.00),
    "gpt-4.1": ModelPricing(input_per_1m=2.00, output_per_1m=8.00),
    "gpt-4.1-mini": ModelPricing(input_per_1m=0.40, output_per_1m=1.60),
    "gpt-4.1-nano": ModelPricing(input_per_1m=0.10, output_per_1m=0.40),
    "gpt-4": ModelPricing(input_per_1m=30.00, output_per_1m=60.00),
    "text-embedding-3-small": ModelPricing(input_per_1m=0.02, output_per_1m=0.0),
    "text-embedding-3-large": ModelPricing(input_per_1m=0.13, output_per_1m=0.0),
}

_ACTIVE_USAGE: ContextVar["_UsageAccumulator | None"] = ContextVar("active_openai_usage", default=None)


def normalize_model_name(model: str | None) -> str:
    if not model:
        return "unknown"
    normalized = model.strip()
    if not normalized:
        return "unknown"
    if normalized.startswith("ft:"):
        for base_model in sorted(_MODEL_PRICING.keys(), key=len, reverse=True):
            if base_model in normalized:
                return base_model
    for base_model in sorted(_MODEL_PRICING.keys(), key=len, reverse=True):
        if normalized == base_model or normalized.startswith(f"{base_model}-"):
            return base_model
    return normalized


def estimate_openai_cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> tuple[float, float, float]:
    pricing = _MODEL_PRICING.get(normalize_model_name(model))
    if pricing is None:
        return 0.0, 0.0, 0.0
    input_cost = (max(prompt_tokens, 0) / 1_000_000) * pricing.input_per_1m
    output_cost = (max(completion_tokens, 0) / 1_000_000) * pricing.output_per_1m
    total_cost = input_cost + output_cost
    return round(input_cost, 8), round(output_cost, 8), round(total_cost, 8)


@dataclass(slots=True)
class _UsageAccumulator:
    entries: dict[tuple[str, str], OpenAIUsageLineItem] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def record(self, source: str, model: str | None, prompt_tokens: int, completion_tokens: int) -> None:
        normalized_model = normalize_model_name(model)
        prompt = max(int(prompt_tokens or 0), 0)
        completion = max(int(completion_tokens or 0), 0)
        total = prompt + completion
        input_cost, output_cost, total_cost = estimate_openai_cost(normalized_model, prompt, completion)
        key = (source, normalized_model)
        entry = self.entries.get(key)
        if entry is None:
            entry = OpenAIUsageLineItem(source=source, model=normalized_model)
            self.entries[key] = entry
        entry.prompt_tokens += prompt
        entry.completion_tokens += completion
        entry.total_tokens += total
        entry.input_cost_usd = round(entry.input_cost_usd + input_cost, 8)
        entry.output_cost_usd = round(entry.output_cost_usd + output_cost, 8)
        entry.total_cost_usd = round(entry.total_cost_usd + total_cost, 8)
        entry.call_count += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.estimated_cost_usd = round(self.estimated_cost_usd + total_cost, 8)

    def to_run_usage(self, *, duration_ms: int = 0, trace_id: str | None = None) -> RunUsage:
        line_items = sorted(self.entries.values(), key=lambda item: (item.source, item.model))
        return RunUsage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            estimated_cost_usd=round(self.estimated_cost_usd, 8),
            duration_ms=duration_ms,
            trace_id=trace_id,
            line_items=[item.model_copy(deep=True) for item in line_items],
        )

    @classmethod
    def from_run_usage(cls, usage: RunUsage | None) -> "_UsageAccumulator":
        accumulator = cls()
        if usage is None:
            return accumulator
        for item in usage.line_items:
            accumulator.entries[(item.source, item.model)] = item.model_copy(deep=True)
        accumulator.prompt_tokens = usage.prompt_tokens
        accumulator.completion_tokens = usage.completion_tokens
        accumulator.total_tokens = usage.total_tokens
        accumulator.estimated_cost_usd = usage.estimated_cost_usd
        return accumulator


@contextmanager
def track_openai_usage(initial_usage: RunUsage | None = None):
    existing = _ACTIVE_USAGE.get()
    if existing is not None and initial_usage is None:
        yield existing
        return
    accumulator = _UsageAccumulator.from_run_usage(initial_usage)
    token = _ACTIVE_USAGE.set(accumulator)
    try:
        yield accumulator
    finally:
        _ACTIVE_USAGE.reset(token)


def record_openai_usage(
    *,
    source: str,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int = 0,
) -> OpenAIUsageLineItem | None:
    accumulator = _ACTIVE_USAGE.get()
    if accumulator is None:
        return None
    accumulator.record(source, model, prompt_tokens, completion_tokens)
    return accumulator.entries[(source, normalize_model_name(model))]


def snapshot_openai_usage(*, duration_ms: int = 0, trace_id: str | None = None) -> RunUsage | None:
    accumulator = _ACTIVE_USAGE.get()
    if accumulator is None:
        return None
    return accumulator.to_run_usage(duration_ms=duration_ms, trace_id=trace_id)
