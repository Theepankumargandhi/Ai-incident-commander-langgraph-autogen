from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

from app.config import Settings
from app.plugins.base import PLUGIN_BASE_CLASSES, PluginBase

logger = logging.getLogger(__name__)

BUILTIN_PLUGIN_MODULES = (
    "app.plugins.builtin.prometheus_plugin",
    "app.plugins.builtin.slack_plugin",
    "app.plugins.builtin.jira_plugin",
)

_default_registry: "PluginRegistry | None" = None


class PluginRegistry:
    def __init__(self, settings: Settings, **dependencies: Any) -> None:
        self.settings = settings
        self.dependencies = dependencies
        self._plugins: dict[str, dict[str, PluginBase]] = {
            "observability": {},
            "notification": {},
            "remediation": {},
            "agent": {},
        }
        self._errors: list[dict[str, str]] = []

    @property
    def errors(self) -> list[dict[str, str]]:
        return list(self._errors)

    def discover(self) -> "PluginRegistry":
        self._plugins = {key: {} for key in self._plugins}
        self._errors = []
        for module_name in BUILTIN_PLUGIN_MODULES:
            try:
                self._load_module(importlib.import_module(module_name), module_name)
            except Exception as exc:
                self._record_error(module_name, exc)

        plugin_dir = self.settings.plugin_dir
        if plugin_dir:
            self.discover_from_dir(plugin_dir)
        return self

    def discover_from_dir(self, plugin_dir: str | Path) -> None:
        directory = Path(plugin_dir).expanduser()
        if not directory.exists():
            self._errors.append({"plugin": str(directory), "error": "PLUGIN_DIR does not exist."})
            return
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"external_plugin_{path.stem}_{abs(hash(path.resolve()))}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("Unable to create import spec.")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._load_module(module, str(path))
            except Exception as exc:
                self._record_error(str(path), exc)

    def get_plugin(self, plugin_type: str, name: str) -> PluginBase | None:
        return self._plugins.get(plugin_type, {}).get(name)

    def list_plugins(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for plugin_type in sorted(self._plugins):
            for plugin in sorted(self._plugins[plugin_type].values(), key=lambda item: item.name):
                rows.append(plugin.metadata())
        rows.extend(
            {
                "name": error["plugin"],
                "type": "unknown",
                "version": "",
                "status": f"error: {error['error']}",
            }
            for error in self._errors
        )
        return rows

    def _load_module(self, module: ModuleType, source: str) -> None:
        candidates = self._instantiate_candidates(module)
        if not candidates:
            raise RuntimeError("No concrete plugin class or create_plugin factory found.")
        for plugin in candidates:
            self.register(plugin, source)

    def _instantiate_candidates(self, module: ModuleType) -> list[PluginBase]:
        factory = getattr(module, "create_plugin", None)
        if callable(factory):
            return [self._call_factory(factory)]

        plugins: list[PluginBase] = []
        for _name, candidate in inspect.getmembers(module, inspect.isclass):
            if candidate.__module__ != module.__name__:
                continue
            if not issubclass(candidate, PLUGIN_BASE_CLASSES) or inspect.isabstract(candidate):
                continue
            plugins.append(self._instantiate_class(candidate))
        return plugins

    def _call_factory(self, factory) -> PluginBase:
        kwargs = {"settings": self.settings, **self.dependencies}
        try:
            plugin = factory(**kwargs)
        except TypeError:
            plugin = factory(self.settings)
        if not isinstance(plugin, PLUGIN_BASE_CLASSES):
            raise TypeError("create_plugin did not return a valid plugin base class.")
        return plugin

    def _instantiate_class(self, plugin_class: type[PluginBase]) -> PluginBase:
        try:
            return plugin_class(self.settings, **self.dependencies)
        except TypeError:
            try:
                return plugin_class(self.settings)
            except TypeError:
                return plugin_class()

    def register(self, plugin: PluginBase, source: str = "") -> None:
        if not isinstance(plugin, PLUGIN_BASE_CLASSES):
            raise TypeError(f"{plugin!r} is not a supported plugin type.")
        if not getattr(plugin, "name", ""):
            raise ValueError(f"Plugin from {source} is missing a name.")
        plugin_type = plugin.plugin_type
        if plugin_type not in self._plugins:
            raise ValueError(f"Unsupported plugin type {plugin_type}.")
        self._plugins[plugin_type][plugin.name] = plugin

    def _record_error(self, plugin: str, exc: Exception) -> None:
        logger.exception("Plugin load failed for %s", plugin)
        self._errors.append({"plugin": plugin, "error": str(exc)})


def set_default_registry(registry: PluginRegistry) -> None:
    global _default_registry
    _default_registry = registry


def get_default_registry() -> PluginRegistry | None:
    return _default_registry


def get_plugin(plugin_type: str, name: str) -> PluginBase | None:
    if _default_registry is None:
        return None
    return _default_registry.get_plugin(plugin_type, name)

