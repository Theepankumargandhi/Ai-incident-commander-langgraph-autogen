import json
import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.judge_tuning import JudgeFineTuneManager


def _make_local_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"judge-tuning-{uuid4()}-"))


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
