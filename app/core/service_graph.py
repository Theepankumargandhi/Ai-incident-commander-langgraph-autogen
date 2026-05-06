from __future__ import annotations

from collections import defaultdict, deque

from app.models import ServiceGraphContext, ServiceGraphEdge, ServiceGraphNode, ServiceGraphPath


DEFAULT_GRAPH_NODES: tuple[ServiceGraphNode, ...] = (
    ServiceGraphNode(id="checkout-api", owner="commerce", tier="edge", criticality="high", tags=["customer-path"]),
    ServiceGraphNode(id="payments-api", owner="payments", tier="core", criticality="high", tags=["money-flow"]),
    ServiceGraphNode(id="inventory-api", owner="supply", tier="core", criticality="high"),
    ServiceGraphNode(id="pricing-api", owner="commerce", tier="core", criticality="medium"),
    ServiceGraphNode(id="orders-api", owner="commerce", tier="edge", criticality="high"),
    ServiceGraphNode(id="search-api", owner="discovery", tier="edge", criticality="medium"),
    ServiceGraphNode(id="catalog-api", owner="catalog", tier="core", criticality="medium"),
    ServiceGraphNode(id="cache-service", owner="platform", tier="shared", criticality="medium"),
    ServiceGraphNode(id="ledger-api", owner="finance", tier="core", criticality="high"),
    ServiceGraphNode(id="auth-api", owner="identity", tier="shared", criticality="high"),
    ServiceGraphNode(id="warehouse-worker", owner="supply", tier="worker", criticality="medium"),
    ServiceGraphNode(id="postgres-primary", owner="data", tier="storage", criticality="high"),
    ServiceGraphNode(id="invoice-worker", owner="finance", tier="worker", criticality="medium"),
)

DEFAULT_GRAPH_EDGES: tuple[ServiceGraphEdge, ...] = (
    ServiceGraphEdge(source="checkout-api", target="payments-api"),
    ServiceGraphEdge(source="checkout-api", target="inventory-api"),
    ServiceGraphEdge(source="checkout-api", target="pricing-api"),
    ServiceGraphEdge(source="orders-api", target="inventory-api"),
    ServiceGraphEdge(source="orders-api", target="payments-api"),
    ServiceGraphEdge(source="payments-api", target="ledger-api"),
    ServiceGraphEdge(source="payments-api", target="auth-api"),
    ServiceGraphEdge(source="inventory-api", target="warehouse-worker"),
    ServiceGraphEdge(source="inventory-api", target="postgres-primary"),
    ServiceGraphEdge(source="billing-api", target="ledger-api"),
    ServiceGraphEdge(source="billing-api", target="invoice-worker"),
    ServiceGraphEdge(source="search-api", target="catalog-api"),
    ServiceGraphEdge(source="search-api", target="cache-service"),
)


class ServiceKnowledgeGraph:
    def __init__(
        self,
        nodes: list[ServiceGraphNode] | None = None,
        edges: list[ServiceGraphEdge] | None = None,
    ) -> None:
        self.nodes = {node.id: node for node in (nodes or list(DEFAULT_GRAPH_NODES))}
        self.edges = list(edges or list(DEFAULT_GRAPH_EDGES))
        self._upstream: dict[str, list[ServiceGraphEdge]] = defaultdict(list)
        self._downstream: dict[str, list[ServiceGraphEdge]] = defaultdict(list)
        for edge in self.edges:
            self._upstream[edge.source].append(edge)
            self._downstream[edge.target].append(edge)

    def describe_service(self, service: str, implicated_services: list[str] | None = None) -> ServiceGraphContext:
        implicated = [item for item in (implicated_services or []) if item in self.nodes]
        upstream = [edge.target for edge in self._upstream.get(service, [])]
        downstream = [edge.source for edge in self._downstream.get(service, [])]
        blast_radius = sorted({service, *downstream})
        candidate_paths = self._build_candidate_paths(service, implicated or upstream[:2])
        critical_dependencies = [
            dependency
            for dependency in upstream
            if self.nodes.get(dependency) and self.nodes[dependency].criticality in {"high", "critical"}
        ]
        summary = self._build_summary(service, upstream, downstream, implicated, critical_dependencies)
        context_nodes = [self.nodes[item] for item in sorted({service, *upstream, *downstream, *implicated}) if item in self.nodes]
        context_edges = [
            edge
            for edge in self.edges
            if edge.source in {node.id for node in context_nodes} and edge.target in {node.id for node in context_nodes}
        ]
        confidence = 0.55 + min(0.25, len(candidate_paths) * 0.08) + min(0.1, len(critical_dependencies) * 0.05)
        return ServiceGraphContext(
            focal_service=service,
            summary=summary,
            upstream_services=upstream,
            downstream_services=downstream,
            critical_dependencies=critical_dependencies,
            blast_radius=blast_radius,
            candidate_paths=candidate_paths,
            graph_nodes=context_nodes,
            graph_edges=context_edges,
            confidence=min(confidence, 0.95),
        )

    def add_node(self, node: ServiceGraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: ServiceGraphEdge) -> None:
        for existing in self.edges:
            if existing.source == edge.source and existing.target == edge.target and existing.relation == edge.relation:
                return
        self.edges.append(edge)
        self._upstream[edge.source].append(edge)
        self._downstream[edge.target].append(edge)

    def export(self) -> dict[str, list[dict]]:
        return {
            "nodes": [node.model_dump(mode="json") for node in self.nodes.values()],
            "edges": [edge.model_dump(mode="json") for edge in self.edges],
        }

    def _build_candidate_paths(self, service: str, targets: list[str]) -> list[ServiceGraphPath]:
        paths: list[ServiceGraphPath] = []
        for target in targets:
            if target == service:
                continue
            path = self._shortest_path(service, target)
            if path:
                paths.append(path)
        return sorted(paths, key=lambda item: item.score, reverse=True)

    def _shortest_path(self, source: str, target: str) -> ServiceGraphPath | None:
        if source not in self.nodes or target not in self.nodes:
            return None
        queue: deque[tuple[str, list[str], list[str]]] = deque([(source, [source], [])])
        seen = {source}
        while queue:
            current, nodes, relations = queue.popleft()
            if current == target:
                score = max(0.2, 1.0 - ((len(nodes) - 1) * 0.15))
                return ServiceGraphPath(
                    nodes=nodes,
                    relations=relations,
                    explanation=f"Dependency path from {source} to {target}.",
                    score=round(score, 2),
                )
            for edge in self._upstream.get(current, []):
                if edge.target in seen:
                    continue
                seen.add(edge.target)
                queue.append((edge.target, [*nodes, edge.target], [*relations, edge.relation]))
        return None

    @staticmethod
    def _build_summary(
        service: str,
        upstream: list[str],
        downstream: list[str],
        implicated: list[str],
        critical_dependencies: list[str],
    ) -> str:
        if implicated:
            return (
                f"{service} is connected to implicated services {', '.join(implicated)}. "
                f"Critical upstream dependencies include {', '.join(critical_dependencies or upstream or [service])}."
            )
        if upstream:
            return (
                f"{service} depends on {', '.join(upstream)} and can impact {', '.join(downstream or [service])} when degradation propagates."
            )
        return f"{service} has no explicit dependency metadata yet, so graph reasoning stays service-local."
