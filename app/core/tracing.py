from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from app.config import Settings

try:
    from opentelemetry import propagate, trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.propagators.aws import AwsXRayPropagator
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import Span, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
    from opentelemetry.trace import SpanKind, Status, StatusCode
except Exception:  # pragma: no cover - optional dependency
    propagate = None
    OTLPSpanExporter = None
    FastAPIInstrumentor = None
    HTTPXClientInstrumentor = None
    AwsXRayPropagator = None
    Resource = None
    Span = None
    TracerProvider = None
    BatchSpanProcessor = None
    SpanExporter = None
    SpanExportResult = None
    ParentBased = None
    TraceIdRatioBased = None
    AwsXRayIdGenerator = None
    SpanKind = None
    Status = None
    StatusCode = None
    trace = None


_TRACE_ID: ContextVar[str | None] = ContextVar("trace_id", default=None)
_TRACING_CONFIGURED = False
_HTTPX_INSTRUMENTED = False
_SPAN_COLLECTOR: list[dict[str, Any]] | None = None


if SpanExporter is not None:

    class _MemorySpanExporter(SpanExporter):  # pragma: no cover - test helper exercised indirectly
        def export(self, spans) -> SpanExportResult:
            if _SPAN_COLLECTOR is not None:
                for span in spans:
                    _SPAN_COLLECTOR.append(
                        {
                            "name": span.name,
                            "attributes": dict(span.attributes),
                        }
                    )
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

else:  # pragma: no cover - no OTel exporter available
    _MemorySpanExporter = None


def _normalize_otlp_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    normalized = endpoint.strip()
    if not normalized:
        return None
    if normalized.startswith("http://") or normalized.startswith("https://"):
        if not normalized.rstrip("/").endswith("/v1/traces"):
            normalized = normalized.rstrip("/") + "/v1/traces"
    return normalized


def _resource_attributes(settings: Settings) -> dict[str, str]:
    return {
        "service.name": settings.tracing_service_name,
        "deployment.environment": settings.app_env,
        "service.version": "0.2.0",
    }


def _set_span_attributes(span: Span, attributes: dict[str, Any] | None) -> None:
    if not attributes:
        return
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)
            continue
        if isinstance(value, (tuple, list)):
            serializable = [item for item in value if isinstance(item, (str, bool, int, float))]
            if serializable:
                span.set_attribute(key, serializable)
            continue
        span.set_attribute(key, str(value))


def configure_tracing(settings: Settings) -> None:
    global _TRACING_CONFIGURED
    if not settings.enable_tracing:
        return
    if _TRACING_CONFIGURED:
        return
    if (
        trace is None
        or TracerProvider is None
        or Resource is None
        or BatchSpanProcessor is None
        or ParentBased is None
        or TraceIdRatioBased is None
    ):
        return

    provider_kwargs: dict[str, Any] = {
        "resource": Resource.create(_resource_attributes(settings)),
        "sampler": ParentBased(TraceIdRatioBased(1.0)),
    }
    if AwsXRayIdGenerator is not None:
        provider_kwargs["id_generator"] = AwsXRayIdGenerator()
    provider = TracerProvider(**provider_kwargs)

    endpoint = _normalize_otlp_endpoint(settings.otlp_endpoint)
    if endpoint and OTLPSpanExporter is not None:
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    if _SPAN_COLLECTOR is not None and _MemorySpanExporter is not None:
        provider.add_span_processor(BatchSpanProcessor(_MemorySpanExporter()))
    trace.set_tracer_provider(provider)
    if propagate is not None and AwsXRayPropagator is not None:
        propagate.set_global_textmap(AwsXRayPropagator())
    _TRACING_CONFIGURED = True


def get_tracer_provider():
    if trace is None:
        return None
    return trace.get_tracer_provider()


def instrument_fastapi(app) -> None:
    global _HTTPX_INSTRUMENTED
    if not _TRACING_CONFIGURED:
        return
    if trace is None or FastAPIInstrumentor is None or HTTPXClientInstrumentor is None:
        return
    if getattr(app.state, "_otel_instrumented", False):
        return
    FastAPIInstrumentor.instrument_app(app, tracer_provider=get_tracer_provider())
    if not _HTTPX_INSTRUMENTED:
        HTTPXClientInstrumentor().instrument(tracer_provider=get_tracer_provider())
        _HTTPX_INSTRUMENTED = True
    app.state._otel_instrumented = True


def set_test_span_collector(collector: list[dict[str, Any]] | None) -> None:
    global _SPAN_COLLECTOR
    _SPAN_COLLECTOR = collector


def generate_trace_id() -> str:
    return uuid4().hex


def set_trace_id(trace_id: str | None) -> None:
    _TRACE_ID.set(trace_id)


def get_trace_id() -> str:
    current = _TRACE_ID.get()
    if current:
        return current
    generated = generate_trace_id()
    _TRACE_ID.set(generated)
    return generated


@contextmanager
def traced_span(name: str, attributes: dict | None = None, kind: SpanKind | None = None):
    trace_id = get_trace_id()
    if trace is None or not _TRACING_CONFIGURED:
        yield trace_id
        return

    tracer = trace.get_tracer("ai-incident-commander")
    span_kind = kind or (SpanKind.INTERNAL if SpanKind is not None else None)
    with tracer.start_as_current_span(name, kind=span_kind) as span:
        _set_span_attributes(
            span,
            {
                **(attributes or {}),
                "app.trace_id": trace_id,
                "trace_id": trace_id,
            },
        )
        try:
            yield trace_id
        except Exception as exc:
            if hasattr(span, "record_exception"):
                span.record_exception(exc)
            if Status is not None and StatusCode is not None:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
