import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.synthetic import SyntheticIncidentGenerator
from app.models import SyntheticGenerationRequest


def make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-synthetic-{uuid4()}-"))


def test_synthetic_generator_fallback_produces_cases_and_incidents() -> None:
    settings = Settings(
        app_name="AI Incident Commander",
        app_env="test",
        data_dir=make_temp_dir(),
        openai_api_key=None,
    )
    generator = SyntheticIncidentGenerator(settings)
    request = SyntheticGenerationRequest(
        count=3,
        difficulty="hard",
        include_noise=True,
        include_false_positives=True,
        include_incomplete_evidence=True,
    )

    cases = asyncio.run(generator.generate_cases(request))
    incidents = asyncio.run(generator.generate_incidents(request))

    assert len(cases) == 3
    assert len(incidents) == 3
    assert all("hard" in case.tags for case in cases)
    assert all(incident.context.get("synthetic") is True for incident in incidents)
