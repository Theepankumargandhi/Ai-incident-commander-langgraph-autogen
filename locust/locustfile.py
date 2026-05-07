from __future__ import annotations

import os
import random
from threading import Lock
from uuid import uuid4

from locust import HttpUser, between, events, task


SERVICES = ("checkout-api", "payments-api", "orders-api", "search-api", "billing-api")
ENVIRONMENTS = ("production", "staging")
SEVERITIES = ("low", "medium", "high", "critical")

incident_ids: list[str] = []
run_ids: list[str] = []
pool_lock = Lock()


def _auth_headers() -> dict[str, str]:
    token = os.getenv("LOCUST_BEARER_TOKEN") or os.getenv("API_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _incident_payload() -> dict:
    service = random.choice(SERVICES)
    severity = random.choice(SEVERITIES)
    latency_ms = random.randint(350, 3200)
    error_rate = round(random.uniform(0.005, 0.18), 4)
    return {
        "title": f"{service} synthetic load test incident {uuid4().hex[:8]}",
        "service": service,
        "environment": random.choice(ENVIRONMENTS),
        "severity": severity,
        "description": (
            f"{service} is reporting p95 latency near {latency_ms}ms "
            f"with error rate {error_rate:.2%} during load testing."
        ),
        "source": "locust",
        "metrics": {
            "p95_latency_ms": float(latency_ms),
            "error_rate": error_rate,
            "queue_depth": float(random.randint(10, 950)),
        },
        "labels": ["load-test", severity],
        "context": {"generator": "locust", "scenario": random.choice(["latency", "errors", "backpressure"])},
    }


def _remember_incident(incident_id: str | None) -> None:
    if not incident_id:
        return
    with pool_lock:
        if incident_id not in incident_ids:
            incident_ids.append(incident_id)


def _remember_run(run_id: str | None) -> None:
    if not run_id:
        return
    with pool_lock:
        if run_id not in run_ids:
            run_ids.append(run_id)


def _pick_incident_id() -> str | None:
    with pool_lock:
        return random.choice(incident_ids) if incident_ids else None


def _pick_run_id() -> str | None:
    with pool_lock:
        return random.choice(run_ids) if run_ids else None


class IncidentCommanderUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.headers = _auth_headers()
        if not _pick_incident_id():
            self.create_incident()

    @task(20)
    def create_incident(self) -> None:
        with self.client.post(
            "/incidents",
            json=_incident_payload(),
            headers=self.headers,
            name="POST /incidents",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected 200, got {response.status_code}")
                return
            incident_id = response.json().get("id")
            if not incident_id:
                response.failure("Create incident response did not include id")
                return
            _remember_incident(incident_id)

    @task(30)
    def process_incident(self) -> None:
        incident_id = _pick_incident_id()
        if incident_id is None:
            self.create_incident()
            incident_id = _pick_incident_id()
        if incident_id is None:
            return

        with self.client.post(
            f"/incidents/{incident_id}/process",
            headers={**self.headers, "X-Trace-Id": f"locust-{uuid4().hex}"},
            name="POST /incidents/{id}/process",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected 200, got {response.status_code}")
                return
            run_id = response.json().get("id")
            if not run_id:
                response.failure("Process response did not include run id")
                return
            _remember_run(run_id)

    @task(30)
    def list_incidents(self) -> None:
        with self.client.get(
            "/incidents",
            headers=self.headers,
            name="GET /incidents",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected 200, got {response.status_code}")
                return
            for incident in response.json()[:20]:
                _remember_incident(incident.get("id"))

    @task(20)
    def get_run_cost(self) -> None:
        run_id = _pick_run_id()
        if run_id is None:
            self.process_incident()
            run_id = _pick_run_id()
        if run_id is None:
            return

        with self.client.get(
            f"/runs/{run_id}/cost",
            headers=self.headers,
            name="GET /runs/{id}/cost",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected 200, got {response.status_code}")
                return
            payload = response.json()
            if "estimated_cost_usd" not in payload:
                response.failure("Cost response did not include estimated_cost_usd")


@events.quitting.add_listener
def enforce_load_test_thresholds(environment, **_kwargs) -> None:
    max_error_rate = float(os.getenv("LOCUST_MAX_ERROR_RATE", "0.05"))
    max_p95_ms = float(os.getenv("LOCUST_MAX_P95_MS", "2000"))
    total = environment.stats.total
    error_rate = total.fail_ratio
    p95_ms = total.get_response_time_percentile(0.95) or 0.0
    if error_rate > max_error_rate or p95_ms > max_p95_ms:
        print(
            "Load test thresholds failed: "
            f"error_rate={error_rate:.2%} max={max_error_rate:.2%}, "
            f"p95={p95_ms:.0f}ms max={max_p95_ms:.0f}ms"
        )
        environment.process_exit_code = 1
