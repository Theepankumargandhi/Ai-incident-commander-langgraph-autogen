from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import TypeVar

from pydantic import BaseModel

from app.models import BenchmarkSummary, ChatMessageRecord, Incident, IncidentRun, TicketRecord

ModelT = TypeVar("ModelT", bound=BaseModel)


class JsonListStore:
    def __init__(self, path: Path, model_type: type[ModelT]) -> None:
        self.path = path
        self.model_type = model_type
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _read_raw(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as file_handle:
            return json.load(file_handle)

    def _write_raw(self, rows: list[dict]) -> None:
        with self.path.open("w", encoding="utf-8") as file_handle:
            json.dump(rows, file_handle, indent=2, default=str)

    def list(self) -> list[ModelT]:
        with self._lock:
            return [self.model_type.model_validate(row) for row in self._read_raw()]

    def get(self, record_id: str) -> ModelT | None:
        for item in self.list():
            if item.id == record_id:
                return item
        return None

    def save(self, record: ModelT) -> ModelT:
        with self._lock:
            records = self._read_raw()
            updated = False
            new_payload = record.model_dump(mode="json")
            for index, item in enumerate(records):
                if item.get("id") == record.id:
                    records[index] = new_payload
                    updated = True
                    break
            if not updated:
                records.append(new_payload)
            self._write_raw(records)
        return record

    def latest_for_field(self, field_name: str, field_value: str) -> ModelT | None:
        items = [item for item in self.list() if getattr(item, field_name, None) == field_value]
        if not items:
            return None

        def sort_key(item: ModelT):
            return getattr(item, "started_at", getattr(item, "created_at", None))

        return sorted(items, key=sort_key)[-1]


class IncidentStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, Incident)


class RunStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, IncidentRun)


class BenchmarkStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, BenchmarkSummary)


class TicketStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, TicketRecord)


class MessageStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, ChatMessageRecord)
