import json
import asyncio
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.judge_tuning import JudgeFineTuneManager
from app.core.evaluation import RunEvaluator
from app.core.storage import JudgeFineTuneStateStore
from app.main import app
from app.models import Incident, IncidentRun, IncidentSeverity


def _make_local_temp_dir() -> Path:
    path = Path.cwd() / "pytest-cache-files-judge-tuning-static" / str(uuid4())
    (path / "judge_tuning").mkdir(parents=True, exist_ok=True)
    return path


def test_judge_training_corpus_writes_50_examples() -> None:
    settings = Settings(app_env="test", data_dir=_make_local_temp_dir())
    manager = JudgeFineTuneManager(settings)

    state = manager.write_training_corpus(count=50)

    assert state["example_count"] == 50
    assert manager.training_jsonl_path.exists()
    assert manager.preview_path.exists()
    jsonl_lines = [line for line in manager.training_jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    preview_rows = json.loads(manager.preview_path.read_text(encoding="utf-8"))
    qualities = {row["quality"] for row in preview_rows}

    assert len(jsonl_lines) == 50
    assert len(preview_rows) == 50
    assert qualities == {"good", "bad"}


def test_resolve_active_judge_model_prefers_fine_tuned_state() -> None:
    settings = Settings(
        app_env="test",
        data_dir=_make_local_temp_dir(),
        judge_model="gpt-4.1-mini",
        openai_model="gpt-4.1-mini",
    )
    manager = JudgeFineTuneManager(settings)
    manager.save_state(
        {
            "job_status": "succeeded",
            "fine_tuned_model": "ft:gpt-4o-mini-2024-07-18:org:incident-judge",
        }
    )

    assert manager.resolve_active_judge_model() == "ft:gpt-4o-mini-2024-07-18:org:incident-judge"


def test_judge_fine_tune_endpoints_start_and_persist_completed_model(monkeypatch) -> None:
    settings = Settings(
        app_env="test",
        data_dir=_make_local_temp_dir(),
        openai_api_key="test-key",
        judge_fine_tune_base_model="gpt-4o-mini-2024-07-18",
    )
    store = JudgeFineTuneStateStore(settings.data_dir / "judge_fine_tune_states.json")
    manager = JudgeFineTuneManager(settings, store)

    async def fake_upload(_path):
        return "file-judge-training"

    async def fake_create(training_file_id: str, *, suffix: str):
        assert training_file_id == "file-judge-training"
        assert suffix == "incident-judge"
        return {
            "id": "ftjob-123",
            "status": "running",
            "model": "gpt-4o-mini-2024-07-18",
            "fine_tuned_model": None,
        }

    async def fake_retrieve(job_id: str):
        assert job_id == "ftjob-123"
        return {
            "id": "ftjob-123",
            "status": "succeeded",
            "model": "gpt-4o-mini-2024-07-18",
            "fine_tuned_model": "ft:gpt-4o-mini-2024-07-18:org:incident-judge",
        }

    monkeypatch.setattr(manager, "_upload_training_file", fake_upload)
    monkeypatch.setattr(manager, "_create_fine_tuning_job", fake_create)
    monkeypatch.setattr(manager, "_retrieve_fine_tuning_job", fake_retrieve)

    with TestClient(app) as client:
        client.app.state.service = SimpleNamespace(judge_fine_tune_manager=manager)
        started = client.post("/judge/fine-tune?count=4&suffix=incident-judge")
        refreshed = client.get("/judge/fine-tune/ftjob-123")

    assert started.status_code == 200
    assert started.json()["job_id"] == "ftjob-123"
    assert started.json()["status"] == "running"
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "succeeded"
    assert refreshed.json()["model_ready"] is True
    assert refreshed.json()["fine_tuned_model"] == "ft:gpt-4o-mini-2024-07-18:org:incident-judge"
    assert store.get("ftjob-123").fine_tuned_model == "ft:gpt-4o-mini-2024-07-18:org:incident-judge"


def test_evaluator_prefers_judge_fine_tuned_model_env() -> None:
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_model="gpt-4.1-mini",
        judge_model="gpt-4.1-mini",
        judge_fine_tuned_model="ft:gpt-4o-mini-2024-07-18:org:incident-judge",
    )
    evaluator = RunEvaluator(settings)
    seen: dict[str, str] = {}

    async def fake_complete_json(**kwargs):
        seen["model"] = kwargs["model"]
        return {
            "overall_score": 91,
            "rationale": "Fine-tuned judge used.",
            "findings": [],
            "sub_scores": {},
        }

    evaluator.llm_client.complete_json = fake_complete_json  # type: ignore[method-assign]
    incident = Incident(
        title="Checkout latency",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="Latency is elevated.",
        source="test",
    )
    run = IncidentRun(incident_id=incident.id)

    asyncio.run(evaluator._judge_with_llm(incident, run, 100, "balanced-v1", {}))

    assert seen["model"] == "ft:gpt-4o-mini-2024-07-18:org:incident-judge"
