from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.benchmark import BenchmarkEngine
from app.core.evaluation import RunEvaluator
from app.core.memory import IncidentMemory
from app.core.policy import PolicyEngine
from app.core.profiles import list_profiles
from app.core.sample_data import sample_benchmark_cases, sample_incident
from app.core.storage import BenchmarkStore, IncidentStore, MessageStore, RunStore, TicketStore
from app.integrations.chat import MockChatClient
from app.integrations.executor import ActionExecutor
from app.integrations.observability import MockObservabilityClient
from app.integrations.tickets import MockTicketingClient
from app.models import ApprovalRequest, BenchmarkRequest, CreateIncidentRequest
from app.services.incident_service import IncidentEventBus, IncidentService
from app.workflow.autogen_team import AutoGenInvestigationTeam
from app.workflow.langgraph_orchestrator import LangGraphIncidentOrchestrator


def build_service() -> IncidentService:
    settings = get_settings()
    incident_store = IncidentStore(settings.data_dir / "incidents.json")
    run_store = RunStore(settings.data_dir / "runs.json")
    benchmark_store = BenchmarkStore(settings.data_dir / "benchmarks.json")
    ticket_store = TicketStore(settings.data_dir / "tickets.json")
    message_store = MessageStore(settings.data_dir / "messages.json")
    memory = IncidentMemory()
    policy = PolicyEngine()
    evaluator = RunEvaluator()
    autogen_team = AutoGenInvestigationTeam(settings)
    observability_client = MockObservabilityClient()
    chat_client = MockChatClient(message_store)
    ticketing_client = MockTicketingClient(ticket_store)
    action_executor = ActionExecutor(chat_client, ticketing_client)
    orchestrator = LangGraphIncidentOrchestrator(
        settings,
        memory,
        policy,
        evaluator,
        autogen_team,
        observability_client,
        action_executor,
    )
    return IncidentService(
        settings=settings,
        incident_store=incident_store,
        run_store=run_store,
        benchmark_store=benchmark_store,
        ticket_store=ticket_store,
        message_store=message_store,
        orchestrator=orchestrator,
        benchmark_engine=BenchmarkEngine(),
        event_bus=IncidentEventBus(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = build_service()
    yield


app = FastAPI(title="AI Incident Commander", version="0.1.0", lifespan=lifespan)
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/profiles")
async def get_profiles() -> list[dict]:
    return [profile.model_dump(mode="json") for profile in list_profiles()]


@app.get("/incidents")
async def list_incidents() -> list[dict]:
    service: IncidentService = app.state.service
    return [incident.model_dump(mode="json") for incident in service.list_incidents()]


@app.post("/incidents")
async def create_incident(payload: CreateIncidentRequest) -> dict:
    service: IncidentService = app.state.service
    incident = service.create_incident(payload)
    return incident.model_dump(mode="json")


@app.post("/incidents/sample")
async def create_sample_incident() -> dict:
    service: IncidentService = app.state.service
    incident = service.create_incident(sample_incident())
    return incident.model_dump(mode="json")


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: str) -> dict:
    service: IncidentService = app.state.service
    incident = service.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident.model_dump(mode="json")


@app.post("/incidents/{incident_id}/process")
async def process_incident(incident_id: str, prompt_profile: str | None = Query(default=None)) -> dict:
    service: IncidentService = app.state.service
    try:
        run = await service.process_incident(incident_id, prompt_profile)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return run.model_dump(mode="json")


@app.post("/incidents/{incident_id}/approval")
async def approve_incident(incident_id: str, payload: ApprovalRequest) -> dict:
    service: IncidentService = app.state.service
    try:
        run = await service.approve_incident(incident_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return run.model_dump(mode="json")


@app.get("/runs")
async def list_runs() -> list[dict]:
    service: IncidentService = app.state.service
    return [run.model_dump(mode="json") for run in service.list_runs()]


@app.get("/tickets")
async def list_tickets() -> list[dict]:
    service: IncidentService = app.state.service
    return [ticket.model_dump(mode="json") for ticket in service.list_tickets()]


@app.get("/messages")
async def list_messages() -> list[dict]:
    service: IncidentService = app.state.service
    return [message.model_dump(mode="json") for message in service.list_messages()]


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    service: IncidentService = app.state.service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.model_dump(mode="json")


@app.get("/runs/{run_id}/replay")
async def replay_run(run_id: str) -> list[dict]:
    service: IncidentService = app.state.service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.event_log


@app.post("/benchmarks/run")
async def run_benchmark(payload: BenchmarkRequest) -> list[dict]:
    service: IncidentService = app.state.service
    try:
        summaries = await service.run_benchmark(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return [summary.model_dump(mode="json") for summary in summaries]


@app.get("/benchmarks/history")
async def list_benchmarks() -> list[dict]:
    service: IncidentService = app.state.service
    return [item.model_dump(mode="json") for item in service.list_benchmarks()]


@app.get("/benchmarks/sample")
async def get_sample_benchmarks() -> list[dict]:
    return [case.model_dump(mode="json") for case in sample_benchmark_cases()]


@app.post("/benchmarks/sample/run")
async def run_sample_benchmarks() -> list[dict]:
    service: IncidentService = app.state.service
    payload = BenchmarkRequest(cases=sample_benchmark_cases())
    summaries = await service.run_benchmark(payload)
    return [summary.model_dump(mode="json") for summary in summaries]


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
