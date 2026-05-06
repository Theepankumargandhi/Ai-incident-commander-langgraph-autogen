from __future__ import annotations

import json
import time
from urllib.parse import parse_qs
from contextlib import asynccontextmanager

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.core.analytics import IncidentAnalytics
from app.core.auth import AuthContext, require_operator, require_remediation_token, require_viewer, resolve_auth_context
from app.core.benchmark import BenchmarkEngine
from app.core.causal import CausalReasoningEngine
from app.core.evaluation import RunEvaluator
from app.core.feedback import FeedbackLearningLoop
from app.core.jobs import BackgroundJobManager
from app.core.logging import configure_logging
from app.core.memory import IncidentMemory
from app.core.multimodal import MultimodalReasoningEngine
from app.core.optimizer import AgentOptimizer
from app.core.postmortem import PostmortemExporter
from app.core.policy import PolicyEngine
from app.core.profiles import list_model_routes, list_profiles
from app.core.projections import NormalizedPostgresProjector, NullProjector
from app.core.safe_remediation import SafeRemediationGateway
from app.core.sample_data import sample_benchmark_cases, sample_incident
from app.core.simulation import AdversarialSimulationLab
from app.core.storage import build_store_bundle
from app.core.synthetic import SyntheticIncidentGenerator
from app.core.tracing import configure_tracing, generate_trace_id, get_trace_id, instrument_fastapi, set_trace_id
from app.core.service_graph import ServiceKnowledgeGraph
from app.integrations.chat import SlackChatClient
from app.integrations.executor import ActionExecutor
from app.integrations.observability import PrometheusObservabilityClient
from app.integrations.release_metadata import GitHubReleaseMetadataClient
from app.integrations.remediation import ControlledRemediationClient
from app.integrations.tickets import JiraTicketingClient
from app.models import (
    AddEdgeRequest,
    AddNodeRequest,
    ApprovalRequest,
    AdversarialLabRequest,
    ArtifactAttachmentRequest,
    AuthSession,
    BenchmarkRequest,
    CreateIncidentRequest,
    FeedbackRequest,
    HealthReport,
    PolicyDocumentUpdateRequest,
    RemediationRequest,
    ServiceGraphEdge,
    ServiceGraphNode,
    SyntheticGenerationRequest,
)
from app.services.incident_service import IncidentEventBus, IncidentService
from app.workflow.autogen_team import AutoGenInvestigationTeam
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator

APP_SETTINGS = get_settings()
configure_logging(APP_SETTINGS.log_level)
configure_tracing(APP_SETTINGS)
logger = logging.getLogger(__name__)


class ApiPrefixMiddleware:
    """Allow the app to serve both bare routes and /api-prefixed ingress routes."""

    def __init__(self, app, prefix: str = "/api") -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path == self.prefix or path.startswith(f"{self.prefix}/"):
            stripped_path = path[len(self.prefix) :] or "/"
            scope = dict(scope)
            scope["path"] = stripped_path
            raw_path = scope.get("raw_path")
            if raw_path:
                prefix_bytes = self.prefix.encode("utf-8")
                if raw_path == prefix_bytes or raw_path.startswith(prefix_bytes + b"/"):
                    scope["raw_path"] = raw_path[len(prefix_bytes) :] or b"/"
        await self.app(scope, receive, send)


def build_service(settings: Settings) -> IncidentService:
    store_bundle = build_store_bundle(settings)
    incident_store = store_bundle.incident_store
    run_store = store_bundle.run_store
    benchmark_store = store_bundle.benchmark_store
    ticket_store = store_bundle.ticket_store
    message_store = store_bundle.message_store
    memory_store = store_bundle.memory_store
    remediation_store = store_bundle.remediation_store
    job_store = store_bundle.job_store
    feedback_store = store_bundle.feedback_store
    memory = IncidentMemory(memory_store, api_key=settings.openai_api_key, api_base=settings.openai_api_base)
    policy = PolicyEngine(settings, store_bundle.policy_store)
    service_graph = ServiceKnowledgeGraph()
    causal_engine = CausalReasoningEngine(service_graph)
    multimodal_engine = MultimodalReasoningEngine(settings)
    feedback_loop = FeedbackLearningLoop(feedback_store, memory_store)
    optimizer = AgentOptimizer()
    autogen_team = AutoGenInvestigationTeam(settings)
    release_client = GitHubReleaseMetadataClient(settings)
    observability_client = PrometheusObservabilityClient(settings, release_client)
    chat_client = SlackChatClient(settings, message_store)
    ticketing_client = JiraTicketingClient(settings, ticket_store)
    remediation_client = ControlledRemediationClient(settings, remediation_store)
    safe_remediation_gateway = SafeRemediationGateway(settings, remediation_store)
    action_executor = ActionExecutor(settings, chat_client, ticketing_client, remediation_client)
    orchestrator = LangGraphIncidentOrchestrator(
        settings,
        memory,
        policy,
        RunEvaluator(settings),
        causal_engine,
        multimodal_engine,
        feedback_loop,
        autogen_team,
        observability_client,
        action_executor,
    )
    synthetic_generator = SyntheticIncidentGenerator(settings)
    try:
        projector = NormalizedPostgresProjector(settings) if settings.postgres_dsn else NullProjector()
    except Exception:
        projector = NullProjector()
    return IncidentService(
        settings=settings,
        incident_store=incident_store,
        run_store=run_store,
        benchmark_store=benchmark_store,
        ticket_store=ticket_store,
        message_store=message_store,
        memory_store=memory_store,
        remediation_store=remediation_store,
        job_store=job_store,
        feedback_store=feedback_store,
        orchestrator=orchestrator,
        benchmark_engine=BenchmarkEngine(),
        event_bus=IncidentEventBus(),
        safe_remediation_gateway=safe_remediation_gateway,
        storage_healthcheck=lambda: store_bundle.healthcheck().model_dump(mode="json"),
        analytics=IncidentAnalytics(),
        postmortem_exporter=PostmortemExporter(),
        projector=projector,
        synthetic_generator=synthetic_generator,
        optimizer=optimizer,
        simulation_lab=AdversarialSimulationLab(synthetic_generator, optimizer),
        feedback_loop=feedback_loop,
        service_graph=service_graph,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = APP_SETTINGS
    app.state.service = build_service(APP_SETTINGS)
    app.state.job_manager = BackgroundJobManager(
        app.state.service,
        concurrency=APP_SETTINGS.background_job_concurrency,
    )
    if APP_SETTINGS.enable_background_jobs:
        await app.state.job_manager.start()
    yield
    if APP_SETTINGS.enable_background_jobs:
        await app.state.job_manager.stop()


app = FastAPI(title="AI Incident Commander", version="0.2.0", lifespan=lifespan)
app.add_middleware(ApiPrefixMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=(
        r"https?://("
        r"localhost|"
        r"127\.0\.0\.1|"
        r"10(?:\.\d{1,3}){3}|"
        r"192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
        r")(?::\d+)?"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
instrument_fastapi(app)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    started = time.perf_counter()
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id()
    set_trace_id(trace_id)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = get_trace_id()
    duration_ms = int((time.perf_counter() - started) * 1000)
    if request.url.path not in {"/health", "/health/live"}:
        logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%s trace_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            get_trace_id(),
        )
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    report = HealthReport(
        status="ready",
        app_name=APP_SETTINGS.app_name,
        app_env=APP_SETTINGS.app_env,
        connectors=[item for item in service.connector_health()],
    )
    return report.model_dump(mode="json")


@app.get("/health/connectors")
async def health_connectors(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return service.connector_health()


@app.get("/profiles")
async def get_profiles(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    return [profile.model_dump(mode="json") for profile in list_profiles()]


@app.get("/model-routes")
async def get_model_routes(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    return [route.model_dump(mode="json") for route in list_model_routes()]


@app.get("/policy/document")
async def get_policy_document(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.get_policy_document().model_dump(mode="json")


@app.put("/policy/document")
async def update_policy_document(
    payload: PolicyDocumentUpdateRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.update_policy_document(payload).model_dump(mode="json")


@app.get("/auth/me")
async def auth_me(request: Request) -> dict:
    context = resolve_auth_context(APP_SETTINGS, request.headers.get("Authorization"))
    session = AuthSession(role=context.role, subject=context.subject, demo_mode=not APP_SETTINGS.auth_enabled)
    return session.model_dump(mode="json")


@app.get("/incidents")
async def list_incidents(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [incident.model_dump(mode="json") for incident in service.list_incidents()]


@app.post("/incidents")
async def create_incident(
    payload: CreateIncidentRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    incident = service.create_incident(payload)
    return incident.model_dump(mode="json")


@app.post("/incidents/sample")
async def create_sample_incident(
    auto_process: bool = Query(default=False),
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    incident = service.create_incident(sample_incident())
    if auto_process:
        await service.process_incident(incident.id)
    return incident.model_dump(mode="json")


@app.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    incident = service.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident.model_dump(mode="json")


@app.post("/incidents/{incident_id}/artifacts")
async def attach_incident_artifacts(
    incident_id: str,
    payload: ArtifactAttachmentRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    try:
        incident = service.attach_artifacts(incident_id, payload.artifacts)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return incident.model_dump(mode="json")


@app.get("/incidents/{incident_id}/runs")
async def get_incident_runs(
    incident_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [
        run.model_dump(mode="json")
        for run in service.list_runs()
        if run.incident_id == incident_id
    ]


@app.post("/incidents/{incident_id}/process")
async def process_incident(
    incident_id: str,
    request: Request,
    prompt_profile: str | None = Query(default=None),
    model_route_id: str | None = Query(default=None),
    background: bool = Query(default=False),
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    if background and APP_SETTINGS.enable_background_jobs:
        job = app.state.job_manager.enqueue_incident(
            incident_id,
            prompt_profile,
            model_route_id,
            requested_by=_auth.subject,
        )
        return job.model_dump(mode="json")
    try:
        run = await service.process_incident(
            incident_id,
            prompt_profile,
            model_route_id=model_route_id,
            trace_id=request.headers.get("X-Trace-Id"),
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return run.model_dump(mode="json")


@app.post("/incidents/{incident_id}/approval")
async def approve_incident(
    incident_id: str,
    payload: ApprovalRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    try:
        run = await service.approve_incident(incident_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return run.model_dump(mode="json")


@app.post("/integrations/slack/interactivity")
async def handle_slack_interactivity(request: Request) -> dict:
    service: IncidentService = app.state.service
    settings: Settings = app.state.settings
    chat_client = service.orchestrator.action_executor.chat_client
    if not settings.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack interactivity is not configured.")

    raw_body = await request.body()
    signature = request.headers.get("X-Slack-Signature")
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    if not chat_client.verify_signature(raw_body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature.")

    form_values = parse_qs(raw_body.decode("utf-8"))
    payload_values = form_values.get("payload")
    if not payload_values:
        raise HTTPException(status_code=400, detail="Missing Slack payload.")

    try:
        payload = json.loads(payload_values[0])
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Invalid Slack payload JSON.") from error

    try:
        return await service.handle_slack_interactivity(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/runs")
async def list_runs(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [run.model_dump(mode="json") for run in service.list_runs()]


@app.get("/runs/compare")
async def compare_runs(
    left_run_id: str = Query(...),
    right_run_id: str = Query(...),
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    try:
        comparison = service.compare_runs(left_run_id, right_run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return comparison.model_dump(mode="json")


@app.get("/tickets")
async def list_tickets(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [ticket.model_dump(mode="json") for ticket in service.list_tickets()]


@app.get("/messages")
async def list_messages(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [message.model_dump(mode="json") for message in service.list_messages()]


@app.get("/memory")
async def list_memory_entries(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [entry.model_dump(mode="json") for entry in service.list_memory_entries()]


@app.get("/feedback")
async def list_feedback(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [entry.model_dump(mode="json") for entry in service.list_feedback()]


@app.get("/feedback/summary")
async def get_feedback_summary(
    service_name: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.feedback_summary(service_name).model_dump(mode="json")


@app.get("/remediations")
async def list_remediations(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [entry.model_dump(mode="json") for entry in service.list_remediations()]


@app.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.model_dump(mode="json")


@app.get("/runs/{run_id}/cost")
async def get_run_cost(
    run_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    try:
        summary = service.get_run_cost_summary(run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return summary.model_dump(mode="json")


@app.post("/runs/{run_id}/feedback")
async def submit_run_feedback(
    run_id: str,
    payload: FeedbackRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    try:
        record = service.record_feedback(run_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return record.model_dump(mode="json")


@app.get("/runs/{run_id}/replay")
async def replay_run(
    run_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.event_log


@app.get("/runs/{run_id}/postmortem.md")
async def export_run_postmortem(
    run_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> Response:
    service: IncidentService = app.state.service
    try:
        markdown = service.export_postmortem_markdown(run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(content=markdown, media_type="text/markdown; charset=utf-8")


@app.post("/benchmarks/run")
async def run_benchmark(
    payload: BenchmarkRequest,
    request: Request,
    background: bool = Query(default=False),
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    if background and APP_SETTINGS.enable_background_jobs:
        job = app.state.job_manager.enqueue_benchmark(payload, requested_by=_auth.subject)
        return [job.model_dump(mode="json")]
    try:
        summaries = await service.run_benchmark(
            payload,
            trace_id=request.headers.get("X-Trace-Id"),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return [summary.model_dump(mode="json") for summary in summaries]


@app.get("/benchmarks/history")
async def list_benchmarks(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    return [item.model_dump(mode="json") for item in service.list_benchmarks()]


@app.get("/jobs")
async def list_jobs(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    return [job.model_dump(mode="json") for job in app.state.job_manager.list_jobs()]


@app.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    job = app.state.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json")


@app.get("/analytics/summary")
async def analytics_summary(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.summarize_analytics().model_dump(mode="json")


@app.get("/benchmarks/sample")
async def get_sample_benchmarks(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    return [case.model_dump(mode="json") for case in sample_benchmark_cases()]


@app.post("/synthetic/benchmark-cases")
async def generate_synthetic_benchmark_cases(
    payload: SyntheticGenerationRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    cases = await service.generate_synthetic_cases(payload)
    return [case.model_dump(mode="json") for case in cases]


@app.post("/synthetic/incidents")
async def create_synthetic_incidents(
    payload: SyntheticGenerationRequest,
    persist: bool = Query(default=True),
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    incidents = await service.create_synthetic_incidents(payload, persist=persist)
    return [incident.model_dump(mode="json") for incident in incidents]


@app.post("/lab/adversarial/cases")
async def generate_adversarial_cases(
    payload: AdversarialLabRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    cases = await service.generate_adversarial_cases(payload)
    return [case.model_dump(mode="json") for case in cases]


@app.post("/lab/adversarial/run")
async def run_adversarial_lab(
    payload: AdversarialLabRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    report = await service.run_adversarial_lab(payload)
    return report.model_dump(mode="json")


@app.get("/optimizer/recommendation")
async def get_optimizer_recommendation(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.optimizer_recommendation().model_dump(mode="json")


@app.get("/service-graph")
async def get_service_graph(
    service_name: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.service_graph_snapshot(service_name)


@app.get("/knowledge-graph")
async def get_knowledge_graph(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.service_graph.export()


@app.post("/knowledge-graph/nodes")
async def add_knowledge_graph_node(
    payload: AddNodeRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    node = ServiceGraphNode(
        id=payload.id,
        label=payload.label,
        node_type=payload.service_type,
        metadata=payload.metadata,
    )
    return service.add_graph_node(node)


@app.post("/knowledge-graph/edges")
async def add_knowledge_graph_edge(
    payload: AddEdgeRequest,
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    edge = ServiceGraphEdge(
        source=payload.source,
        target=payload.target,
        relation=payload.relationship,
    )
    return service.add_graph_edge(edge)


@app.post("/benchmarks/sample/run")
async def run_sample_benchmarks(
    request: Request,
    background: bool = Query(default=False),
    _auth: AuthContext = Depends(require_operator(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    payload = BenchmarkRequest(cases=sample_benchmark_cases())
    if background and APP_SETTINGS.enable_background_jobs:
        job = app.state.job_manager.enqueue_benchmark(payload, requested_by=_auth.subject)
        return [job.model_dump(mode="json")]
    summaries = await service.run_benchmark(
        payload,
        trace_id=request.headers.get("X-Trace-Id"),
    )
    return [summary.model_dump(mode="json") for summary in summaries]


@app.post("/sandbox/remediation/execute")
async def execute_controlled_remediation(
    payload: RemediationRequest,
    _auth: AuthContext = Depends(require_remediation_token(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    receipt = service.execute_controlled_remediation(payload)
    return receipt.model_dump(mode="json")


@app.get("/sandbox/remediation/policy")
async def get_controlled_remediation_policy(
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> dict:
    service: IncidentService = app.state.service
    return service.safe_remediation_gateway.policy_snapshot()


@app.get("/memory/search")
async def search_memory(
    q: str | None = Query(default=None),
    service_name: str | None = Query(default=None),
    environment: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_viewer(APP_SETTINGS)),
) -> list[dict]:
    service: IncidentService = app.state.service
    entries = service.search_memory(query=q, service=service_name, environment=environment)
    return [entry.model_dump(mode="json") for entry in entries]


@app.websocket("/ws/incidents/{incident_id}")
async def incident_events(websocket: WebSocket, incident_id: str) -> None:
    await websocket.accept()
    service: IncidentService = app.state.service
    queue = await service.event_bus.subscribe(incident_id)
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        service.event_bus.unsubscribe(incident_id, queue)
