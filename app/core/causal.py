from __future__ import annotations

from app.core.service_graph import ServiceKnowledgeGraph
from app.models import CausalAnalysis, Incident, MemoryHit, ObservabilitySnapshot, ServiceGraphContext


class CausalReasoningEngine:
    def __init__(self, knowledge_graph: ServiceKnowledgeGraph | None = None) -> None:
        self.knowledge_graph = knowledge_graph or ServiceKnowledgeGraph()

    def analyze(
        self,
        incident: Incident,
        snapshot: ObservabilitySnapshot,
        memory_hits: list[MemoryHit],
        service_graph_context: ServiceGraphContext | None = None,
    ) -> CausalAnalysis:
        service = incident.service
        multimodal = incident.context.get("multimodal_analysis", {})
        implicated_services = list(multimodal.get("implicated_services", []))
        graph_context = service_graph_context or self.knowledge_graph.describe_service(service, implicated_services)
        dependencies = list(graph_context.upstream_services)
        graph_nodes = [node.id for node in graph_context.graph_nodes]
        graph_edges = [
            {"source": edge.source, "target": edge.target}
            for edge in graph_context.graph_edges
        ]

        likely_root_service = service
        likely_cause = "Broader observability review is still needed."
        reasoning: list[str] = []
        propagation_path = [service]
        release_correlation = bool(snapshot.release_hint)
        knowledge_graph_paths = list(graph_context.candidate_paths)
        metrics = {**incident.metrics, **snapshot.metrics}

        if snapshot.release_hint:
            likely_cause = "Release regression likely introduced the incident."
            reasoning.append(f"Release metadata suggests {snapshot.release_hint}.")
        if metrics.get("error_rate", 0.0) > 0.08:
            likely_cause = "High error rate indicates a bad deployment or failing dependency."
            reasoning.append("Error rate crossed the high-severity threshold.")
        if metrics.get("p95_latency_ms", 0.0) > 1800:
            candidate = dependencies[0] if dependencies else service
            likely_root_service = candidate
            likely_cause = "Dependency saturation is the most likely cause of the latency cascade."
            propagation_path = [candidate, service] if candidate != service else [service]
            reasoning.append("Latency pattern is consistent with upstream dependency saturation.")
        if snapshot.anomaly_detected:
            top_features = snapshot.anomaly_detection.get("top_features", [])
            strongest = top_features[0]["metric"] if top_features else "service telemetry"
            likely_cause = f"Anomaly detection isolated abnormal {strongest} before LLM investigation."
            reasoning.append(snapshot.anomaly_detection.get("summary", "Isolation Forest flagged anomalous telemetry."))

        logs_text = " ".join(snapshot.logs).lower()
        if "timeout" in logs_text or "upstream" in logs_text:
            candidate = dependencies[0] if dependencies else service
            likely_root_service = candidate
            if propagation_path == [service] and candidate != service:
                propagation_path = [candidate, service]
            reasoning.append("Log evidence contains timeout or upstream failure signals.")

        if memory_hits:
            top_hit = memory_hits[0]
            reasoning.append(f"Closest historical analogue was {top_hit.title}.")
            if top_hit.service and top_hit.service != service:
                likely_root_service = top_hit.service
                if likely_root_service not in propagation_path:
                    propagation_path = [likely_root_service, service]

        if implicated_services:
            candidate = implicated_services[0]
            if candidate != service:
                likely_root_service = candidate
                likely_cause = f"Multimodal evidence points to {candidate} as the most likely upstream origin."
                propagation_path = [candidate, service]
                reasoning.append(f"Multimodal artifacts implicated {candidate}.")

        if graph_context.critical_dependencies:
            reasoning.append(
                f"Service graph highlights critical dependencies: {', '.join(graph_context.critical_dependencies[:3])}."
            )
        if knowledge_graph_paths:
            best_path = knowledge_graph_paths[0]
            propagation_path = best_path.nodes if best_path.nodes else propagation_path
            reasoning.append(best_path.explanation)

        confidence = 0.55
        if metrics.get("p95_latency_ms", 0.0) > 1800:
            confidence += 0.12
        if metrics.get("error_rate", 0.0) > 0.08:
            confidence += 0.12
        if snapshot.anomaly_detected:
            confidence += 0.1
        if snapshot.release_hint:
            confidence += 0.08
        if memory_hits:
            confidence += 0.08
        if implicated_services:
            confidence += 0.08
        if knowledge_graph_paths:
            confidence += 0.08

        downstream = set(graph_context.downstream_services)
        impacted_services = sorted({service, *dependencies, *downstream, *implicated_services})

        if not reasoning:
            reasoning.append("No dominant causal chain was identified from the current evidence.")

        return CausalAnalysis(
            likely_root_service=likely_root_service,
            likely_cause=likely_cause,
            confidence=min(confidence, 0.95),
            reasoning=reasoning,
            impacted_services=impacted_services,
            propagation_path=propagation_path,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            release_correlation=release_correlation,
            graph_source="service-knowledge-graph",
            knowledge_graph_paths=knowledge_graph_paths,
        )
