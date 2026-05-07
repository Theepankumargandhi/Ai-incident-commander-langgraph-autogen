from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Generic, TypeVar

from pydantic import BaseModel

from app.config import Settings
from app.models import (
    BenchmarkSummary,
    ChatMessageRecord,
    ConnectorHealth,
    FeedbackRecord,
    Incident,
    IncidentRun,
    JudgeFineTuneState,
    JobRecord,
    MemoryEntry,
    PolicyDocument,
    RemediationReceipt,
    RunbookDocument,
    TicketRecord,
)

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - optional dependency
    Jsonb = None
    dict_row = None
    psycopg = None
    sql = None

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


class CollectionStore(Generic[ModelT]):
    def list(self) -> list[ModelT]:
        raise NotImplementedError

    def get(self, record_id: str) -> ModelT | None:
        for item in self.list():
            if item.id == record_id:
                return item
        return None

    def save(self, record: ModelT) -> ModelT:
        raise NotImplementedError

    def latest_for_field(self, field_name: str, field_value: str) -> ModelT | None:
        items = [item for item in self.list() if getattr(item, field_name, None) == field_value]
        if not items:
            return None

        def sort_key(item: ModelT):
            return getattr(item, "started_at", getattr(item, "created_at", None))

        return sorted(items, key=sort_key)[-1]


class JsonListStore(CollectionStore[ModelT]):
    def __init__(self, path: Path, model_type: type[ModelT]) -> None:
        self.path = path
        self.model_type = model_type
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._memory_rows: list[dict] | None = None

    def _read_raw(self) -> list[dict]:
        if self._memory_rows is not None:
            return list(self._memory_rows)
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except PermissionError:
            self._memory_rows = []
            return []

    def _write_raw(self, rows: list[dict]) -> None:
        try:
            with self.path.open("w", encoding="utf-8") as file_handle:
                json.dump(rows, file_handle, indent=2, default=str)
            self._memory_rows = None
        except PermissionError:
            self._memory_rows = list(rows)

    def list(self) -> list[ModelT]:
        with self._lock:
            return [self.model_type.model_validate(row) for row in self._read_raw()]

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


class PostgresListStore(CollectionStore[ModelT]):
    def __init__(self, dsn: str, schema: str, table_name: str, model_type: type[ModelT]) -> None:
        if psycopg is None or sql is None or Jsonb is None or dict_row is None:
            raise RuntimeError("psycopg is required for the Postgres storage backend.")
        self.dsn = dsn
        self.schema = schema
        self.table_name = table_name
        self.model_type = model_type
        self._lock = Lock()
        self._ensure_table()

    def _connect(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _ensure_table(self) -> None:
        with self._lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {}.{} (
                                record_id TEXT PRIMARY KEY,
                                payload JSONB NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(sql.Identifier(self.schema), sql.Identifier(self.table_name))
                    )

    def list(self) -> list[ModelT]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT payload FROM {}.{} ORDER BY updated_at DESC").format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    )
                )
                rows = cursor.fetchall()
        return [self.model_type.model_validate(row["payload"]) for row in rows]

    def get(self, record_id: str) -> ModelT | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT payload FROM {}.{} WHERE record_id = %s").format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    ),
                    (record_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self.model_type.model_validate(row["payload"])

    def save(self, record: ModelT) -> ModelT:
        payload = record.model_dump(mode="json")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.{} (record_id, payload)
                        VALUES (%s, %s)
                        ON CONFLICT (record_id)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                        """
                    ).format(sql.Identifier(self.schema), sql.Identifier(self.table_name)),
                    (record.id, Jsonb(payload)),
                )
        return record

    def healthcheck(self) -> ConnectorHealth:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            healthy = True
            details: dict[str, str] = {
                "mode": "postgres",
                "schema": self.schema,
                "dsn": _redact_dsn(self.dsn),
            }
        except Exception as exc:  # pragma: no cover - runtime dependent
            healthy = False
            details = {
                "mode": "postgres",
                "schema": self.schema,
                "dsn": _redact_dsn(self.dsn),
                "error": str(exc),
            }

        return ConnectorHealth(
            name="storage",
            enabled=True,
            configured=True,
            healthy=healthy,
            details=details,
        )


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


class MemoryStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, MemoryEntry)


class RunbookStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, RunbookDocument)


class RemediationStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, RemediationReceipt)


class JobStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, JobRecord)


class FeedbackStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, FeedbackRecord)


class PolicyDocumentStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, PolicyDocument)


class JudgeFineTuneStateStore(JsonListStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, JudgeFineTuneState)


class PostgresIncidentStore(PostgresListStore[Incident]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "incidents", Incident)


class PostgresRunStore(PostgresListStore[IncidentRun]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "runs", IncidentRun)


class PostgresBenchmarkStore(PostgresListStore[BenchmarkSummary]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "benchmarks", BenchmarkSummary)


class PostgresTicketStore(PostgresListStore[TicketRecord]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "tickets", TicketRecord)


class PostgresMessageStore(PostgresListStore[ChatMessageRecord]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "messages", ChatMessageRecord)


class PostgresMemoryStore(PostgresListStore[MemoryEntry]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "memory_entries", MemoryEntry)


class PostgresRunbookStore(PostgresListStore[RunbookDocument]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "runbook_documents", RunbookDocument)


class PostgresRemediationStore(PostgresListStore[RemediationReceipt]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "remediation_receipts", RemediationReceipt)


class PostgresJobStore(PostgresListStore[JobRecord]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "jobs", JobRecord)


class PostgresFeedbackStore(PostgresListStore[FeedbackRecord]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "feedback_records", FeedbackRecord)


class PostgresPolicyDocumentStore(PostgresListStore[PolicyDocument]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "policy_documents", PolicyDocument)


class PostgresJudgeFineTuneStateStore(PostgresListStore[JudgeFineTuneState]):
    def __init__(self, dsn: str, schema: str) -> None:
        super().__init__(dsn, schema, "judge_fine_tune_states", JudgeFineTuneState)


@dataclass(slots=True)
class StoreBundle:
    incident_store: CollectionStore[Incident]
    run_store: CollectionStore[IncidentRun]
    benchmark_store: CollectionStore[BenchmarkSummary]
    ticket_store: CollectionStore[TicketRecord]
    message_store: CollectionStore[ChatMessageRecord]
    memory_store: CollectionStore[MemoryEntry]
    runbook_store: CollectionStore[RunbookDocument]
    remediation_store: CollectionStore[RemediationReceipt]
    job_store: CollectionStore[JobRecord]
    feedback_store: CollectionStore[FeedbackRecord]
    policy_store: CollectionStore[PolicyDocument]
    judge_fine_tune_store: CollectionStore[JudgeFineTuneState]
    healthcheck: Callable[[], ConnectorHealth]


def build_store_bundle(settings: Settings) -> StoreBundle:
    backend = (settings.storage_backend or "auto").lower()
    if backend in {"postgres", "auto"} and settings.postgres_dsn:
        if psycopg is None:
            if backend == "postgres":
                raise RuntimeError("STORAGE_BACKEND=postgres requires psycopg to be installed.")
            logger.warning("psycopg is unavailable; falling back to JSON storage.")
            return _build_json_bundle(settings, fallback_reason="psycopg unavailable")

        try:
            incident_store = PostgresIncidentStore(settings.postgres_dsn, settings.postgres_schema)
            run_store = PostgresRunStore(settings.postgres_dsn, settings.postgres_schema)
            benchmark_store = PostgresBenchmarkStore(settings.postgres_dsn, settings.postgres_schema)
            ticket_store = PostgresTicketStore(settings.postgres_dsn, settings.postgres_schema)
            message_store = PostgresMessageStore(settings.postgres_dsn, settings.postgres_schema)
            memory_store = PostgresMemoryStore(settings.postgres_dsn, settings.postgres_schema)
            runbook_store = PostgresRunbookStore(settings.postgres_dsn, settings.postgres_schema)
            remediation_store = PostgresRemediationStore(settings.postgres_dsn, settings.postgres_schema)
            job_store = PostgresJobStore(settings.postgres_dsn, settings.postgres_schema)
            feedback_store = PostgresFeedbackStore(settings.postgres_dsn, settings.postgres_schema)
            policy_store = PostgresPolicyDocumentStore(settings.postgres_dsn, settings.postgres_schema)
            judge_fine_tune_store = PostgresJudgeFineTuneStateStore(settings.postgres_dsn, settings.postgres_schema)
            return StoreBundle(
                incident_store=incident_store,
                run_store=run_store,
                benchmark_store=benchmark_store,
                ticket_store=ticket_store,
                message_store=message_store,
                memory_store=memory_store,
                runbook_store=runbook_store,
                remediation_store=remediation_store,
                job_store=job_store,
                feedback_store=feedback_store,
                policy_store=policy_store,
                judge_fine_tune_store=judge_fine_tune_store,
                healthcheck=incident_store.healthcheck,
            )
        except Exception as exc:
            if backend == "postgres":
                raise RuntimeError(f"Failed to initialize Postgres storage: {exc}") from exc
            logger.exception("Postgres storage initialization failed; falling back to JSON storage.")
            return _build_json_bundle(settings, fallback_reason=f"postgres unavailable: {exc}")

    if backend == "postgres":
        raise RuntimeError("STORAGE_BACKEND=postgres requires POSTGRES_DSN to be configured.")

    return _build_json_bundle(settings)


def _build_json_bundle(settings: Settings, fallback_reason: str | None = None) -> StoreBundle:
    incident_store = IncidentStore(settings.data_dir / "incidents.json")
    run_store = RunStore(settings.data_dir / "runs.json")
    benchmark_store = BenchmarkStore(settings.data_dir / "benchmarks.json")
    ticket_store = TicketStore(settings.data_dir / "tickets.json")
    message_store = MessageStore(settings.data_dir / "messages.json")
    memory_store = MemoryStore(settings.data_dir / "memory.json")
    runbook_store = RunbookStore(settings.data_dir / "runbooks.json")
    remediation_store = RemediationStore(settings.data_dir / "remediations.json")
    job_store = JobStore(settings.data_dir / "jobs.json")
    feedback_store = FeedbackStore(settings.data_dir / "feedback.json")
    policy_store = PolicyDocumentStore(settings.data_dir / "policy_documents.json")
    judge_fine_tune_store = JudgeFineTuneStateStore(settings.data_dir / "judge_fine_tune_states.json")

    def healthcheck() -> ConnectorHealth:
        details = {
            "mode": "json",
            "path": str(settings.data_dir),
        }
        if fallback_reason:
            details["fallback_reason"] = fallback_reason
        return ConnectorHealth(
            name="storage",
            enabled=True,
            configured=True,
            healthy=True,
            details=details,
        )

    return StoreBundle(
        incident_store=incident_store,
        run_store=run_store,
        benchmark_store=benchmark_store,
        ticket_store=ticket_store,
        message_store=message_store,
        memory_store=memory_store,
        runbook_store=runbook_store,
        remediation_store=remediation_store,
        job_store=job_store,
        feedback_store=feedback_store,
        policy_store=policy_store,
        judge_fine_tune_store=judge_fine_tune_store,
        healthcheck=healthcheck,
    )


def _redact_dsn(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    prefix, remainder = dsn.split("://", 1)
    credentials, host = remainder.split("@", 1)
    if ":" in credentials:
        username, _password = credentials.split(":", 1)
        return f"{prefix}://{username}:***@{host}"
    return f"{prefix}://***@{host}"
