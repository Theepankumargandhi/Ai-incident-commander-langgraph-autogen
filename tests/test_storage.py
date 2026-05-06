import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.storage import IncidentStore, build_store_bundle


def make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=f"incident-storage-{uuid4()}-"))


def test_auto_storage_without_dsn_uses_json_backend() -> None:
    temp_dir = make_temp_dir()
    settings = Settings(
        app_name="AI Incident Commander",
        app_env="test",
        data_dir=temp_dir,
        storage_backend="auto",
        postgres_dsn=None,
    )

    bundle = build_store_bundle(settings)
    health = bundle.healthcheck()

    assert isinstance(bundle.incident_store, IncidentStore)
    assert health.details["mode"] == "json"
