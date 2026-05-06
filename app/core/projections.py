from __future__ import annotations

from app.config import Settings
from app.models import BenchmarkSummary, Incident, IncidentRun, MemoryEntry, RemediationReceipt

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


class NullProjector:
    def healthcheck(self) -> dict:
        return {"name": "normalized_schema", "enabled": False, "configured": False, "healthy": True, "details": {"mode": "disabled"}}

    def sync_incident(self, incident: Incident) -> None:
        return None

    def sync_run(self, incident: Incident, run: IncidentRun) -> None:
        return None

    def sync_benchmark(self, summary: BenchmarkSummary) -> None:
        return None

    def sync_memory(self, entry: MemoryEntry | None) -> None:
        return None

    def sync_remediation(self, receipt: RemediationReceipt) -> None:
        return None


class NormalizedPostgresProjector:
    def __init__(self, settings: Settings) -> None:
        if psycopg is None or sql is None or Jsonb is None or dict_row is None:
            raise RuntimeError("psycopg is required for normalized Postgres projections.")
        if not settings.postgres_dsn:
            raise RuntimeError("POSTGRES_DSN is required for normalized Postgres projections.")
        self.dsn = settings.postgres_dsn
        self.schema = settings.postgres_schema
        self._ensure_schema()

    def _connect(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS {schema}.incident_records (
                incident_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                service TEXT NOT NULL,
                environment TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS {schema}.run_records (
                run_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt_profile TEXT,
                model_route_id TEXT,
                score DOUBLE PRECISION,
                latency_ms INTEGER,
                cost_usd DOUBLE PRECISION,
                trace_id TEXT,
                job_id TEXT,
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ,
                root_cause TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS {schema}.action_records (
                action_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                name TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_verdict TEXT,
                command TEXT NOT NULL,
                description TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS {schema}.policy_records (
                run_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                approved BOOLEAN NOT NULL,
                requires_human_approval BOOLEAN NOT NULL,
                reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                PRIMARY KEY (run_id, action_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS {schema}.benchmark_records (
                benchmark_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                model_route_id TEXT,
                total_cases INTEGER NOT NULL,
                average_score DOUBLE PRECISION NOT NULL,
                match_rate DOUBLE PRECISION NOT NULL,
                average_latency_ms DOUBLE PRECISION NOT NULL,
                average_cost_usd DOUBLE PRECISION NOT NULL,
                approval_rate DOUBLE PRECISION NOT NULL,
                executed_action_rate DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS {schema}.memory_records (
                memory_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                service TEXT NOT NULL,
                environment TEXT NOT NULL,
                severity TEXT NOT NULL,
                root_cause TEXT NOT NULL,
                outcome TEXT NOT NULL,
                resolution_quality DOUBLE PRECISION NOT NULL,
                approval_required BOOLEAN NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS {schema}.remediation_records (
                remediation_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                action_name TEXT NOT NULL,
                status TEXT NOT NULL,
                service TEXT,
                environment TEXT,
                provider TEXT NOT NULL,
                executed_at TIMESTAMPTZ NOT NULL,
                external_reference TEXT
            )
            """,
        ]
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
                for statement in statements:
                    cursor.execute(sql.SQL(statement).format(schema=sql.Identifier(self.schema)))

    def healthcheck(self) -> dict:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("SELECT COUNT(*) AS count FROM {}.run_records").format(sql.Identifier(self.schema))
                    )
                    row = cursor.fetchone() or {"count": 0}
            return {
                "name": "normalized_schema",
                "enabled": True,
                "configured": True,
                "healthy": True,
                "details": {"mode": "postgres", "schema": self.schema, "run_records": row["count"]},
            }
        except Exception as exc:  # pragma: no cover - runtime dependent
            return {
                "name": "normalized_schema",
                "enabled": True,
                "configured": True,
                "healthy": False,
                "details": {"mode": "postgres", "schema": self.schema, "error": str(exc)},
            }

    def sync_incident(self, incident: Incident) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.incident_records (
                            incident_id, title, service, environment, severity, status, source, updated_at, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (incident_id)
                        DO UPDATE SET
                            title = EXCLUDED.title,
                            service = EXCLUDED.service,
                            environment = EXCLUDED.environment,
                            severity = EXCLUDED.severity,
                            status = EXCLUDED.status,
                            source = EXCLUDED.source,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(sql.Identifier(self.schema)),
                    (
                        incident.id,
                        incident.title,
                        incident.service,
                        incident.environment,
                        incident.severity.value,
                        incident.status.value,
                        incident.source,
                        incident.updated_at,
                        incident.created_at,
                    ),
                )

    def sync_run(self, incident: Incident, run: IncidentRun) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.run_records (
                            run_id, incident_id, status, prompt_profile, model_route_id, score, latency_ms, cost_usd,
                            trace_id, job_id, started_at, finished_at, root_cause
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id)
                        DO UPDATE SET
                            status = EXCLUDED.status,
                            prompt_profile = EXCLUDED.prompt_profile,
                            model_route_id = EXCLUDED.model_route_id,
                            score = EXCLUDED.score,
                            latency_ms = EXCLUDED.latency_ms,
                            cost_usd = EXCLUDED.cost_usd,
                            trace_id = EXCLUDED.trace_id,
                            job_id = EXCLUDED.job_id,
                            finished_at = EXCLUDED.finished_at,
                            root_cause = EXCLUDED.root_cause
                        """
                    ).format(sql.Identifier(self.schema)),
                    (
                        run.id,
                        incident.id,
                        run.status.value,
                        run.investigation.prompt_profile if run.investigation else None,
                        run.investigation.model_route_id if run.investigation else None,
                        run.evaluation.score if run.evaluation else None,
                        run.evaluation.latency_ms if run.evaluation else None,
                        run.evaluation.estimated_cost_usd if run.evaluation else None,
                        run.trace_id,
                        run.job_id,
                        run.started_at,
                        run.finished_at,
                        run.investigation.suspected_root_cause if run.investigation else None,
                    ),
                )

                for action in (run.investigation.recommended_actions if run.investigation else []):
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {}.action_records (
                                action_id, run_id, incident_id, name, risk_level, status, policy_verdict, command, description, metadata
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (action_id)
                            DO UPDATE SET
                                status = EXCLUDED.status,
                                policy_verdict = EXCLUDED.policy_verdict,
                                metadata = EXCLUDED.metadata
                            """
                        ).format(sql.Identifier(self.schema)),
                        (
                            action.id,
                            run.id,
                            incident.id,
                            action.name,
                            action.risk_level.value,
                            action.status.value if hasattr(action.status, "value") else str(action.status),
                            action.policy_verdict.value if getattr(action, "policy_verdict", None) else None,
                            action.command,
                            action.description,
                            Jsonb(action.metadata),
                        ),
                    )

                for decision in run.policy_decisions:
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {}.policy_records (
                                run_id, action_id, verdict, approved, requires_human_approval, reasons
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (run_id, action_id)
                            DO UPDATE SET
                                verdict = EXCLUDED.verdict,
                                approved = EXCLUDED.approved,
                                requires_human_approval = EXCLUDED.requires_human_approval,
                                reasons = EXCLUDED.reasons
                            """
                        ).format(sql.Identifier(self.schema)),
                        (
                            run.id,
                            decision.action_id,
                            decision.verdict.value,
                            decision.approved,
                            decision.requires_human_approval,
                            Jsonb(decision.reasons),
                        ),
                    )

    def sync_benchmark(self, summary: BenchmarkSummary) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.benchmark_records (
                            benchmark_id, profile_id, model_route_id, total_cases, average_score, match_rate,
                            average_latency_ms, average_cost_usd, approval_rate, executed_action_rate, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (benchmark_id)
                        DO UPDATE SET
                            average_score = EXCLUDED.average_score,
                            match_rate = EXCLUDED.match_rate,
                            average_latency_ms = EXCLUDED.average_latency_ms,
                            average_cost_usd = EXCLUDED.average_cost_usd,
                            approval_rate = EXCLUDED.approval_rate,
                            executed_action_rate = EXCLUDED.executed_action_rate
                        """
                    ).format(sql.Identifier(self.schema)),
                    (
                        summary.id,
                        summary.profile_id,
                        summary.model_route_id,
                        summary.total_cases,
                        summary.average_score,
                        summary.root_cause_match_rate,
                        summary.average_latency_ms,
                        summary.average_cost_usd,
                        summary.approval_rate,
                        summary.executed_action_rate,
                        summary.created_at,
                    ),
                )

    def sync_memory(self, entry: MemoryEntry | None) -> None:
        if entry is None:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.memory_records (
                            memory_id, incident_id, service, environment, severity, root_cause, outcome,
                            resolution_quality, approval_required, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (memory_id)
                        DO UPDATE SET
                            outcome = EXCLUDED.outcome,
                            resolution_quality = EXCLUDED.resolution_quality,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(sql.Identifier(self.schema)),
                    (
                        entry.id,
                        entry.incident_id,
                        entry.service,
                        entry.environment,
                        entry.severity.value,
                        entry.root_cause,
                        entry.outcome,
                        entry.resolution_quality,
                        entry.approval_required,
                        entry.updated_at,
                    ),
                )

    def sync_remediation(self, receipt: RemediationReceipt) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.remediation_records (
                            remediation_id, incident_id, action_name, status, service, environment, provider, executed_at, external_reference
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (remediation_id)
                        DO UPDATE SET
                            status = EXCLUDED.status,
                            external_reference = EXCLUDED.external_reference,
                            executed_at = EXCLUDED.executed_at
                        """
                    ).format(sql.Identifier(self.schema)),
                    (
                        receipt.id,
                        receipt.incident_id,
                        receipt.action_name,
                        receipt.status,
                        receipt.service,
                        receipt.environment,
                        receipt.provider,
                        receipt.executed_at,
                        receipt.external_reference,
                    ),
                )
