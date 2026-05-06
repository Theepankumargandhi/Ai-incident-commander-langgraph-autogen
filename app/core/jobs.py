from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from app.core.tracing import generate_trace_id, set_trace_id
from app.models import BenchmarkRequest, JobKind, JobRecord, JobStatus, utc_now

if TYPE_CHECKING:
    from app.services.incident_service import IncidentService


class BackgroundJobManager:
    def __init__(self, service: IncidentService, concurrency: int = 1) -> None:
        self.service = service
        self.concurrency = max(1, concurrency)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.running = False

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._requeue_pending_jobs()
        self.tasks = [asyncio.create_task(self._worker(index)) for index in range(self.concurrency)]

    async def stop(self) -> None:
        self.running = False
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()

    def enqueue_incident(self, incident_id: str, prompt_profile_id: str | None, model_route_id: str | None, requested_by: str) -> JobRecord:
        job = JobRecord(
            kind=JobKind.process_incident,
            trace_id=generate_trace_id(),
            subject_id=incident_id,
            payload={
                "incident_id": incident_id,
                "prompt_profile_id": prompt_profile_id,
                "model_route_id": model_route_id,
                "requested_by": requested_by,
            },
        )
        self.service.job_store.save(job)
        self.queue.put_nowait(job.id)
        return job

    def enqueue_benchmark(self, request: BenchmarkRequest, requested_by: str) -> JobRecord:
        job = JobRecord(
            kind=JobKind.run_benchmark,
            trace_id=generate_trace_id(),
            payload={
                "benchmark_request": request.model_dump(mode="json"),
                "requested_by": requested_by,
            },
        )
        self.service.job_store.save(job)
        self.queue.put_nowait(job.id)
        return job

    def list_jobs(self) -> list[JobRecord]:
        return sorted(self.service.job_store.list(), key=lambda job: job.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.service.job_store.get(job_id)

    def _requeue_pending_jobs(self) -> None:
        for job in self.service.job_store.list():
            if job.status in {JobStatus.queued, JobStatus.running}:
                job.status = JobStatus.queued
                self.service.job_store.save(job)
                self.queue.put_nowait(job.id)

    async def _worker(self, worker_index: int) -> None:
        while self.running:
            job_id = await self.queue.get()
            job = self.service.job_store.get(job_id)
            if job is None:
                self.queue.task_done()
                continue
            try:
                await self._execute_job(job, worker_index)
            finally:
                self.queue.task_done()

    async def _execute_job(self, job: JobRecord, worker_index: int) -> None:
        job.status = JobStatus.running
        job.started_at = job.started_at or job.created_at
        self.service.job_store.save(job)
        set_trace_id(job.trace_id)

        try:
            if job.kind == JobKind.process_incident:
                result = await self.service.process_incident(
                    job.payload["incident_id"],
                    job.payload.get("prompt_profile_id"),
                    model_route_id=job.payload.get("model_route_id"),
                    job_id=job.id,
                    trace_id=job.trace_id,
                )
                job.result = {"run_id": result.id, "worker_index": worker_index}
            elif job.kind == JobKind.run_benchmark:
                request = BenchmarkRequest.model_validate(job.payload["benchmark_request"])
                summaries = await self.service.run_benchmark(
                    request,
                    job_id=job.id,
                    trace_id=job.trace_id,
                )
                job.result = {
                    "benchmark_ids": [summary.id for summary in summaries],
                    "worker_index": worker_index,
                }
            else:  # pragma: no cover - defensive
                raise ValueError(f"Unsupported job kind {job.kind}")
            job.status = JobStatus.completed
        except Exception as exc:  # pragma: no cover - runtime dependent
            job.status = JobStatus.failed
            job.error = str(exc)
        finally:
            job.finished_at = utc_now()
            self.service.job_store.save(job)
