from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

from app.models import CreateIncidentRequest, IncidentSeverity


def verify_hmac_sha256_signature(raw_body: bytes, secret: str, signature_header: str | None) -> bool:
    if not secret or not signature_header:
        return False

    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected_hex = digest.hex()
    expected_base64 = base64.b64encode(digest).decode("ascii")
    candidates = {signature_header.strip()}
    for part in signature_header.split(","):
        token = part.strip()
        candidates.add(token)
        if "=" in token:
            _name, value = token.split("=", 1)
            candidates.add(value.strip())
        for prefix in ("v1=", "sha256="):
            if token.startswith(prefix):
                candidates.add(token[len(prefix):].strip())
    return any(
        hmac.compare_digest(candidate, expected_hex) or hmac.compare_digest(candidate, expected_base64)
        for candidate in candidates
    )


def verify_static_token(secret: str, token_header: str | None) -> bool:
    if not secret or not token_header:
        return False
    candidate = token_header.strip()
    if candidate.lower().startswith("bearer "):
        candidate = candidate[7:].strip()
    return hmac.compare_digest(candidate, secret)


def pagerduty_payload_to_incident(payload: dict[str, Any]) -> CreateIncidentRequest:
    event = _first_event(payload, "messages")
    incident = _first_dict(event.get("incident"), payload.get("incident"), payload)
    title = _first_text(
        incident.get("title"),
        incident.get("summary"),
        event.get("description"),
        "PagerDuty incident",
    )
    service = _extract_service(incident, default="pagerduty-service")
    severity = _pagerduty_severity(incident)
    details = _first_dict(
        incident.get("body", {}).get("details") if isinstance(incident.get("body"), dict) else None,
        event.get("details"),
        incident.get("details"),
    )
    alert_details = {
        "event": event.get("event"),
        "incident_id": incident.get("id"),
        "incident_number": incident.get("incident_number"),
        "status": incident.get("status"),
        "urgency": incident.get("urgency"),
        "priority": _first_dict(incident.get("priority")).get("summary"),
        "html_url": incident.get("html_url"),
        "details": details,
    }
    return CreateIncidentRequest(
        title=title,
        service=service,
        environment=str(details.get("environment") or details.get("env") or "production"),
        severity=severity,
        description=_description(title, details, incident.get("description") or event.get("description")),
        source="pagerduty",
        metrics=_extract_metrics(details),
        labels=["pagerduty", str(event.get("event") or "webhook")],
        context={"webhook_provider": "pagerduty", "alert_details": alert_details},
    )


def opsgenie_payload_to_incident(payload: dict[str, Any]) -> CreateIncidentRequest:
    alert = _first_dict(payload.get("alert"), payload)
    details = _first_dict(alert.get("details"), payload.get("details"))
    title = _first_text(alert.get("message"), alert.get("alias"), payload.get("message"), "OpsGenie alert")
    service = _first_text(details.get("service"), alert.get("entity"), alert.get("source"), "opsgenie-service")
    severity = _priority_severity(_first_text(alert.get("priority"), payload.get("priority"), "P3"))
    alert_details = {
        "action": payload.get("action"),
        "alert_id": alert.get("alertId") or alert.get("id"),
        "alias": alert.get("alias"),
        "tiny_id": alert.get("tinyId"),
        "status": alert.get("status"),
        "priority": alert.get("priority"),
        "source": alert.get("source"),
        "entity": alert.get("entity"),
        "details": details,
    }
    tags = [str(tag) for tag in alert.get("tags", []) if str(tag).strip()] if isinstance(alert.get("tags"), list) else []
    return CreateIncidentRequest(
        title=title,
        service=service,
        environment=str(details.get("environment") or details.get("env") or "production"),
        severity=severity,
        description=_description(title, details, alert.get("description") or payload.get("description")),
        source="opsgenie",
        metrics=_extract_metrics(details),
        labels=["opsgenie", *tags[:5]],
        context={"webhook_provider": "opsgenie", "alert_details": alert_details},
    )


def _first_event(payload: dict[str, Any], key: str) -> dict[str, Any]:
    values = payload.get(key)
    if isinstance(values, list) and values:
        return _first_dict(values[0])
    return _first_dict(payload.get("event"), payload)


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return "unknown"


def _extract_service(incident: dict[str, Any], *, default: str) -> str:
    service = _first_dict(incident.get("service"))
    return _first_text(service.get("summary"), service.get("name"), service.get("id"), incident.get("service"), default)


def _pagerduty_severity(incident: dict[str, Any]) -> IncidentSeverity:
    priority = _first_dict(incident.get("priority"))
    priority_text = _first_text(priority.get("summary"), priority.get("id"), "")
    if priority_text:
        return _priority_severity(priority_text)
    if str(incident.get("urgency", "")).lower() == "high":
        return IncidentSeverity.high
    return IncidentSeverity.medium


def _priority_severity(priority: str) -> IncidentSeverity:
    normalized = priority.strip().lower()
    if normalized in {"p1", "sev1", "severity1", "critical"}:
        return IncidentSeverity.critical
    if normalized in {"p2", "sev2", "severity2", "high"}:
        return IncidentSeverity.high
    if normalized in {"p4", "p5", "sev4", "sev5", "low"}:
        return IncidentSeverity.low
    return IncidentSeverity.medium


def _extract_metrics(details: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    aliases = {
        "latency_ms": "p95_latency_ms",
        "p95_latency": "p95_latency_ms",
        "p95_latency_ms": "p95_latency_ms",
        "error_rate": "error_rate",
        "queue_depth": "queue_depth",
        "cpu_utilization": "cpu_utilization",
        "memory_usage_pct": "memory_usage_pct",
        "heap_usage_pct": "heap_usage_pct",
    }
    for key, metric_name in aliases.items():
        value = details.get(key)
        if isinstance(value, (int, float)):
            metrics[metric_name] = float(value)
        elif isinstance(value, str):
            try:
                metrics[metric_name] = float(value.strip().rstrip("%")) / (100.0 if value.strip().endswith("%") else 1.0)
            except ValueError:
                continue
    return metrics


def _description(title: str, details: dict[str, Any], fallback: Any) -> str:
    if fallback is not None and str(fallback).strip() and str(fallback).strip() != title:
        return str(fallback).strip()
    if details:
        return f"Webhook alert details: {details}"
    return title
