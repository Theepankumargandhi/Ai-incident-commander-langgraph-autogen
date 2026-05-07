from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.models import Incident, IncidentSeverity
from app.plugins.base import ObservabilityPlugin
from app.plugins.registry import PluginRegistry, get_plugin, set_default_registry


def _plugin_temp_dir() -> Path:
    path = Path.cwd() / "pytest-cache-files-plugin-static" / str(uuid4())
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_registry_loads_builtin_plugins_and_lookup() -> None:
    settings = Settings(app_env="test", data_dir=_plugin_temp_dir())
    registry = PluginRegistry(settings).discover()

    prometheus = registry.get_plugin("observability", "prometheus")
    slack = registry.get_plugin("notification", "slack")
    jira = registry.get_plugin("notification", "jira")

    assert prometheus is not None
    assert slack is not None
    assert jira is not None
    assert any(row["name"] == "prometheus" and row["type"] == "observability" for row in registry.list_plugins())


def test_registry_discovers_external_plugin_from_plugin_dir() -> None:
    plugin_dir = _plugin_temp_dir()
    (plugin_dir / "datadog_plugin.py").write_text(
        """
from app.plugins.base import ObservabilityPlugin

class DatadogPlugin(ObservabilityPlugin):
    name = "datadog"
    version = "0.1.0"

    def collect_metrics(self, incident):
        return {"error_rate": 0.12}

    def collect_logs(self, incident):
        return [f"datadog log for {incident.service}"]
""".strip(),
        encoding="utf-8",
    )
    settings = Settings(app_env="test", data_dir=_plugin_temp_dir(), plugin_dir=plugin_dir)
    registry = PluginRegistry(settings).discover()
    plugin = registry.get_plugin("observability", "datadog")
    incident = Incident(
        title="Checkout errors",
        service="checkout-api",
        environment="production",
        severity=IncidentSeverity.high,
        description="Errors are elevated.",
        source="test",
    )

    assert isinstance(plugin, ObservabilityPlugin)
    assert plugin.collect_metrics(incident)["error_rate"] == 0.12
    assert plugin.collect_logs(incident) == ["datadog log for checkout-api"]


def test_registry_rejects_invalid_external_plugin() -> None:
    plugin_dir = _plugin_temp_dir()
    (plugin_dir / "bad_plugin.py").write_text("class BadPlugin:\n    name = 'bad'\n", encoding="utf-8")
    registry = PluginRegistry(Settings(app_env="test", data_dir=_plugin_temp_dir(), plugin_dir=plugin_dir)).discover()

    assert registry.get_plugin("observability", "bad") is None
    assert registry.errors
    assert registry.errors[0]["plugin"].endswith("bad_plugin.py")


def test_module_level_get_plugin_uses_default_registry() -> None:
    registry = PluginRegistry(Settings(app_env="test", data_dir=_plugin_temp_dir())).discover()
    set_default_registry(registry)

    assert get_plugin("notification", "slack") is not None


def test_plugins_endpoint_lists_loaded_plugins() -> None:
    with TestClient(app) as client:
        response = client.get("/plugins")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert {"prometheus", "slack", "jira"}.issubset(names)
