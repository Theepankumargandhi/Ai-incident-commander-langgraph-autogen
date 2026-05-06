from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.core.openai_usage import record_openai_usage
from app.models import (
    ArtifactKind,
    Incident,
    IncidentArtifact,
    MultimodalAnalysis,
    MultimodalFinding,
    ServiceGraphContext,
)

logger = logging.getLogger(__name__)

_VISION_MODEL = "gpt-4o"
_SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_SUPPORTED_IMAGE_MIME_PREFIXES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}


class MultimodalReasoningEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def analyze(self, incident: Incident, graph_context: ServiceGraphContext | None = None) -> MultimodalAnalysis:
        if not incident.artifacts:
            return MultimodalAnalysis(
                summary="No multimodal artifacts were attached to this incident.",
                findings=[],
                confidence=0.0,
                generated_with_vision=False,
            )

        findings: list[MultimodalFinding] = []
        visual_signals: list[str] = []
        implicated_services: set[str] = {incident.service}
        evidence: list[str] = []
        generated_with_vision = False

        for artifact in incident.artifacts:
            text = self._artifact_text(artifact).lower()
            services = set(artifact.related_services)
            services.update(self._extract_services(text, incident, graph_context))

            vision_payload = self._analyze_with_vision(artifact, incident, graph_context)
            if vision_payload is not None:
                generated_with_vision = True
                finding = self._build_vision_finding(artifact, incident, graph_context, text, services, vision_payload)
            else:
                finding = self._build_text_finding(artifact, incident, graph_context, text, services)

            findings.append(finding)
            visual_signals.extend(finding.visual_signals)
            implicated_services.update(finding.implicated_services)
            evidence.extend(finding.evidence)

        unique_signals = list(dict.fromkeys(visual_signals))
        unique_evidence = list(dict.fromkeys(evidence))
        implicated = sorted(implicated_services)
        confidence = round(sum(item.confidence for item in findings) / max(1, len(findings)), 2)
        summary = self._build_summary(findings, implicated, graph_context)
        return MultimodalAnalysis(
            summary=summary,
            findings=findings,
            visual_signals=unique_signals,
            implicated_services=implicated,
            evidence=unique_evidence,
            confidence=confidence,
            generated_with_vision=generated_with_vision,
        )

    def _build_text_finding(
        self,
        artifact: IncidentArtifact,
        incident: Incident,
        graph_context: ServiceGraphContext | None,
        text: str,
        services: set[str],
    ) -> MultimodalFinding:
        summary = self._artifact_summary(artifact.kind, text, incident.service)
        signals = self._extract_signals(text, artifact.kind)
        item_evidence = self._extract_evidence(text, artifact.kind)
        confidence = self._artifact_confidence(artifact.kind, text, services)
        return MultimodalFinding(
            artifact_id=artifact.id,
            artifact_kind=artifact.kind,
            summary=summary,
            visual_signals=signals,
            implicated_services=sorted(services),
            confidence=confidence,
            evidence=item_evidence,
        )

    def _build_vision_finding(
        self,
        artifact: IncidentArtifact,
        incident: Incident,
        graph_context: ServiceGraphContext | None,
        text: str,
        services: set[str],
        vision_payload: dict[str, Any],
    ) -> MultimodalFinding:
        services.update(self._parse_string_list(vision_payload.get("implicated_services")))
        services.update(self._extract_services(text, incident, graph_context))

        heuristic_signals = self._extract_signals(text, artifact.kind)
        model_signals = self._parse_string_list(vision_payload.get("visual_signals"))
        signals = list(dict.fromkeys([*model_signals, *heuristic_signals]))

        heuristic_evidence = self._extract_evidence(text, artifact.kind)
        model_evidence = self._parse_string_list(vision_payload.get("evidence"))
        item_evidence = list(dict.fromkeys([*model_evidence, *heuristic_evidence]))

        summary = str(vision_payload.get("summary", "")).strip() or self._artifact_summary(artifact.kind, text, incident.service)
        confidence = self._coerce_confidence(
            vision_payload.get("confidence"),
            fallback=self._artifact_confidence(artifact.kind, text, services),
        )
        return MultimodalFinding(
            artifact_id=artifact.id,
            artifact_kind=artifact.kind,
            summary=summary,
            visual_signals=signals,
            implicated_services=sorted(item for item in services if item),
            confidence=confidence,
            evidence=item_evidence or heuristic_evidence,
        )

    def _analyze_with_vision(
        self,
        artifact: IncidentArtifact,
        incident: Incident,
        graph_context: ServiceGraphContext | None,
    ) -> dict[str, Any] | None:
        if self.settings is None or not self.settings.openai_api_key:
            return None
        if not self._is_image_artifact(artifact):
            return None

        image_data_url = self._artifact_image_data_url(artifact)
        if image_data_url is None:
            return None

        payload = {
            "model": _VISION_MODEL,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an SRE multimodal analyst. Study the incident image and return only JSON with keys "
                        "summary, visual_signals, implicated_services, evidence, and confidence. "
                        "confidence must be a number between 0 and 1. Keep claims grounded in visible dashboard, "
                        "diagram, or trace evidence."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._vision_prompt(incident, artifact, graph_context),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url,
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.settings.openai_api_base.rstrip('/')}/chat/completions"

        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception:
            logger.warning("Vision analysis failed for artifact %s; falling back to text reasoning.", artifact.id, exc_info=True)
            return None

        usage = data.get("usage", {})
        record_openai_usage(
            source="multimodal_vision",
            model=data.get("model", _VISION_MODEL),
            prompt_tokens=int(usage.get("prompt_tokens", usage.get("total_tokens", 0)) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        try:
            return json.loads(content)
        except Exception:
            logger.warning("Vision response for artifact %s was not valid JSON; falling back to text reasoning.", artifact.id)
            return None

    def _vision_prompt(
        self,
        incident: Incident,
        artifact: IncidentArtifact,
        graph_context: ServiceGraphContext | None,
    ) -> str:
        candidate_services = [incident.service]
        if artifact.related_services:
            candidate_services.extend(artifact.related_services)
        if graph_context is not None:
            candidate_services.extend(graph_context.upstream_services)
            candidate_services.extend(graph_context.downstream_services)
            candidate_services.extend(graph_context.critical_dependencies)
        known_services = ", ".join(sorted(dict.fromkeys(item for item in candidate_services if item)))
        notes = self._artifact_text(artifact)
        return (
            f"Incident: {incident.title}\n"
            f"Service: {incident.service}\n"
            f"Environment: {incident.environment}\n"
            f"Severity: {incident.severity.value}\n"
            f"Description: {incident.description}\n"
            f"Known services: {known_services}\n"
            f"Artifact kind: {artifact.kind.value}\n"
            f"Artifact notes: {notes or 'None'}\n"
            "Extract concrete dashboard, trace, or architecture insights that help incident triage. "
            "Prefer implicated services from the known service list when they are visually supported."
        )

    @staticmethod
    def _artifact_text(artifact: IncidentArtifact) -> str:
        return " ".join(
            part
            for part in [artifact.title, artifact.description, artifact.extracted_text, " ".join(artifact.annotations)]
            if part
        )

    def _artifact_image_data_url(self, artifact: IncidentArtifact) -> str | None:
        metadata = artifact.metadata or {}

        for key in ("image_data_url", "data_url"):
            value = metadata.get(key)
            if isinstance(value, str) and value.startswith("data:image/"):
                return value

        for key in ("image_base64", "base64_data", "base64"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                mime = artifact.content_type if artifact.content_type in _SUPPORTED_IMAGE_MIME_PREFIXES else "image/png"
                return f"data:{mime};base64,{value.strip()}"

        if artifact.source_ref and artifact.source_ref.startswith("data:image/"):
            return artifact.source_ref

        image_path = self._artifact_image_path(artifact)
        if image_path is None:
            return None

        try:
            raw = image_path.read_bytes()
        except Exception:
            logger.warning("Unable to read image artifact at %s for vision analysis.", image_path, exc_info=True)
            return None

        mime = self._artifact_mime_type(artifact, image_path)
        if mime not in _SUPPORTED_IMAGE_MIME_PREFIXES:
            return None
        encoded = base64.b64encode(raw).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _artifact_image_path(self, artifact: IncidentArtifact) -> Path | None:
        source_ref = artifact.source_ref
        if not source_ref:
            return None
        if re.match(r"^https?://", source_ref, re.IGNORECASE):
            return None

        path = Path(source_ref).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists() or not path.is_file():
            return None
        return path

    @staticmethod
    def _artifact_mime_type(artifact: IncidentArtifact, image_path: Path | None) -> str:
        if artifact.content_type in _SUPPORTED_IMAGE_MIME_PREFIXES:
            return artifact.content_type
        suffix = (image_path.suffix if image_path is not None else "").lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix in _SUPPORTED_IMAGE_EXTENSIONS:
            return f"image/{suffix.lstrip('.')}"
        return artifact.content_type or "image/png"

    @staticmethod
    def _is_image_artifact(artifact: IncidentArtifact) -> bool:
        if artifact.content_type in _SUPPORTED_IMAGE_MIME_PREFIXES:
            return True
        if artifact.source_ref and artifact.source_ref.startswith("data:image/"):
            return True
        if artifact.source_ref:
            suffix = Path(artifact.source_ref).suffix.lower()
            if suffix in _SUPPORTED_IMAGE_EXTENSIONS:
                return True
        metadata = artifact.metadata or {}
        return any(isinstance(metadata.get(key), str) and metadata.get(key) for key in ("image_data_url", "data_url", "image_base64", "base64_data", "base64"))

    def _extract_services(self, text: str, incident: Incident, graph_context: ServiceGraphContext | None) -> set[str]:
        candidates = {incident.service}
        if graph_context:
            candidates.update(graph_context.upstream_services)
            candidates.update(graph_context.downstream_services)
            candidates.update(graph_context.critical_dependencies)
        return {item for item in candidates if item and item.lower() in text}

    @staticmethod
    def _artifact_summary(kind: ArtifactKind, text: str, focal_service: str) -> str:
        if kind == ArtifactKind.grafana_screenshot:
            return f"Grafana evidence for {focal_service} highlights visual metrics and dashboard annotations."
        if kind == ArtifactKind.architecture_diagram:
            return f"Architecture evidence shows how {focal_service} interacts with adjacent services."
        if kind == ArtifactKind.trace_visualization:
            return f"Trace evidence suggests where latency or failure propagates around {focal_service}."
        return f"Additional multimodal evidence was attached for {focal_service}."

    @staticmethod
    def _extract_signals(text: str, kind: ArtifactKind) -> list[str]:
        signals: list[str] = []
        if "latency" in text or "p95" in text:
            signals.append("latency_hotspot")
        if "error" in text or "5xx" in text:
            signals.append("error_cluster")
        if "timeout" in text:
            signals.append("timeout_chain")
        if "deploy" in text or "release" in text:
            signals.append("release_marker")
        if "fan-out" in text or "dependency" in text or kind == ArtifactKind.architecture_diagram:
            signals.append("dependency_path")
        if "queue" in text or "backlog" in text:
            signals.append("backpressure")
        return list(dict.fromkeys(signals))

    @staticmethod
    def _extract_evidence(text: str, kind: ArtifactKind) -> list[str]:
        evidence: list[str] = []
        if kind == ArtifactKind.grafana_screenshot and "latency" in text:
            evidence.append("Dashboard artifact shows elevated latency or saturation indicators.")
        if kind == ArtifactKind.trace_visualization and ("timeout" in text or "span" in text):
            evidence.append("Trace artifact points to a latency or timeout concentration in the request path.")
        if kind == ArtifactKind.architecture_diagram and ("dependency" in text or "edge" in text):
            evidence.append("Architecture artifact highlights dependency relationships that align with the incident.")
        if not evidence:
            snippet = re.sub(r"\s+", " ", text).strip()
            if snippet:
                evidence.append(f"Artifact note: {snippet[:140]}")
        return evidence

    @staticmethod
    def _artifact_confidence(kind: ArtifactKind, text: str, services: set[str]) -> float:
        base = 0.48
        if kind == ArtifactKind.trace_visualization:
            base += 0.14
        elif kind == ArtifactKind.architecture_diagram:
            base += 0.1
        elif kind == ArtifactKind.grafana_screenshot:
            base += 0.12
        if services:
            base += 0.08
        if any(token in text for token in ("latency", "timeout", "error", "release")):
            base += 0.08
        return round(min(base, 0.92), 2)

    @staticmethod
    def _coerce_confidence(value: Any, fallback: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return round(fallback, 2)
        if numeric > 1.0:
            numeric = numeric / 100.0
        return round(min(max(numeric, 0.0), 1.0), 2)

    @staticmethod
    def _parse_string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _build_summary(findings: list[MultimodalFinding], implicated: list[str], graph_context: ServiceGraphContext | None) -> str:
        if not findings:
            return "No visual findings were extracted."
        dominant = max(findings, key=lambda item: item.confidence)
        if graph_context and implicated:
            return (
                f"Multimodal evidence most strongly supports {dominant.summary.lower()} "
                f"and implicates {', '.join(implicated[:4])} in the service graph."
            )
        return f"Multimodal evidence most strongly supports {dominant.summary.lower()}."
