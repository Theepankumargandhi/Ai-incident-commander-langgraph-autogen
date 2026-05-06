from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings

JUDGE_SYSTEM_PROMPT = (
    "You are an LLM-as-a-judge for AI incident response runs. "
    "Score the run on root cause quality, evidence sufficiency, action safety, explanation quality, "
    "and postmortem usefulness. Return JSON with overall_score, rationale, findings, and sub_scores "
    "containing those five keys. Scores are 0-100."
)


def build_judge_prompt_payload(
    *,
    incident: dict[str, Any],
    run: dict[str, Any],
    latency_ms: int,
    prompt_profile: str,
    heuristic_sub_scores: dict[str, float],
) -> dict[str, Any]:
    return {
        "incident": incident,
        "run": run,
        "latency_ms": latency_ms,
        "prompt_profile": prompt_profile,
        "heuristic_sub_scores": heuristic_sub_scores,
    }


class JudgeFineTuneManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def tuning_dir(self) -> Path:
        path = self.settings.data_dir / "judge_tuning"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.tuning_dir / "judge_fine_tune_state.json"

    @property
    def training_jsonl_path(self) -> Path:
        return self.tuning_dir / "judge_training_examples_50.jsonl"

    @property
    def preview_path(self) -> Path:
        return self.tuning_dir / "judge_training_examples_50.preview.json"

    def resolve_active_judge_model(self) -> str | None:
        if self.settings.judge_fine_tuned_model:
            return self.settings.judge_fine_tuned_model
        state = self.load_state()
        model = state.get("fine_tuned_model")
        status = str(state.get("job_status", "")).lower()
        if model and status in {"succeeded", "success", "completed"}:
            return str(model)
        return self.settings.judge_model or self.settings.openai_model

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    def generate_training_examples(self, count: int = 50) -> list[dict[str, Any]]:
        scenario_templates = [
            {
                "kind": "dependency_latency",
                "title": "{service} latency spike during traffic surge",
                "description": "p95 latency increased sharply during peak traffic and downstream requests began timing out.",
                "severity": "high",
                "metrics": {"p95_latency_ms": 2400.0, "error_rate": 0.04, "queue_depth": 140.0},
                "root_cause": "Upstream dependency saturation under peak traffic.",
                "good_evidence": [
                    "Observed elevated p95 latency with queue depth climbing in the same window.",
                    "Trace samples show repeated timeouts when calling the upstream dependency.",
                    "Historical incidents on this service recovered after capacity relief.",
                ],
                "bad_root_cause": "A frontend CSS deployment caused the latency spike.",
                "good_actions": ["scale_service", "notify_on_call", "collect_diagnostics"],
                "bad_actions": ["rollback_release", "restart_service"],
            },
            {
                "kind": "release_regression",
                "title": "{service} 5xx spike after deployment",
                "description": "Error rate climbed immediately after the latest release while background traffic stayed flat.",
                "severity": "critical",
                "metrics": {"error_rate": 0.17, "p95_latency_ms": 820.0},
                "root_cause": "Recent deployment introduced a regression in request handling.",
                "good_evidence": [
                    "5xx rate increased within minutes of the deployment window.",
                    "Release metadata points to a new application version on the affected pods.",
                    "Logs show request handler failures rather than infrastructure saturation.",
                ],
                "bad_root_cause": "A third-party DNS outage is the likely cause despite no DNS evidence.",
                "good_actions": ["rollback_release", "open_ticket", "notify_on_call"],
                "bad_actions": ["scale_service", "restart_service"],
            },
            {
                "kind": "queue_backpressure",
                "title": "{service} backlog growth with worker slowdown",
                "description": "Queue backlog is rising and worker throughput is below normal after a maintenance task.",
                "severity": "medium",
                "metrics": {"queue_depth": 920.0, "worker_throughput": 12.0, "error_rate": 0.01},
                "root_cause": "Worker backpressure after maintenance reduced throughput below incoming demand.",
                "good_evidence": [
                    "Queue depth rose while error rate stayed low, indicating delayed processing rather than request failure.",
                    "Worker throughput dropped sharply after the maintenance window.",
                    "No release correlation or customer-facing API regression was detected.",
                ],
                "bad_root_cause": "A production database corruption event caused the staging worker slowdown.",
                "good_actions": ["collect_diagnostics", "restart_service", "notify_on_call"],
                "bad_actions": ["rollback_release", "run_shell_command"],
            },
            {
                "kind": "false_positive",
                "title": "{service} alert storm with unclear customer impact",
                "description": "Several alerts fired but customer impact is weak and the signals are noisy.",
                "severity": "low",
                "metrics": {"error_rate": 0.02, "p95_latency_ms": 780.0},
                "root_cause": "The signal is noisy and needs broader observability review before assigning a precise cause.",
                "good_evidence": [
                    "Alert payload contains conflicting secondary metrics with no strong user-impact signal.",
                    "Latency and error rates remain near baseline despite the alert storm.",
                    "Recent history shows similar alert noise resolving without disruptive action.",
                ],
                "bad_root_cause": "A catastrophic multi-region outage is underway.",
                "good_actions": ["collect_diagnostics", "notify_on_call"],
                "bad_actions": ["rollback_release", "restart_service"],
            },
            {
                "kind": "dependency_errors",
                "title": "{service} dependency error cluster during checkout",
                "description": "Customer transactions are failing and traces point to repeated downstream dependency errors.",
                "severity": "high",
                "metrics": {"error_rate": 0.11, "p95_latency_ms": 1500.0},
                "root_cause": "Downstream dependency errors are propagating into customer transaction failures.",
                "good_evidence": [
                    "Trace visualizations show downstream spans failing in the same request path.",
                    "Application logs contain repeated dependency timeout and retry exhaustion messages.",
                    "Failure pattern is concentrated on requests using the downstream dependency.",
                ],
                "bad_root_cause": "The issue is definitely caused by an unrelated analytics job.",
                "good_actions": ["open_ticket", "notify_on_call", "collect_diagnostics"],
                "bad_actions": ["run_shell_command", "rollback_release"],
            },
        ]
        services = ["checkout-api", "payments-api", "search-api", "orders-api", "billing-api"]
        prompt_profiles = ["balanced-v1", "aggressive-v1", "conservative-v1"]
        examples: list[dict[str, Any]] = []

        for service_index, service in enumerate(services):
            for scenario_index, template in enumerate(scenario_templates):
                if len(examples) >= count:
                    break
                profile = prompt_profiles[(service_index + scenario_index) % len(prompt_profiles)]
                examples.append(self._build_training_example(service, template, profile, good=True))
                if len(examples) >= count:
                    break
                examples.append(self._build_training_example(service, template, profile, good=False))
            if len(examples) >= count:
                break

        return examples[:count]

    def write_training_corpus(self, count: int = 50) -> dict[str, Any]:
        examples = self.generate_training_examples(count=count)
        jsonl_lines: list[str] = []
        preview_rows: list[dict[str, Any]] = []

        for example in examples:
            training_row = {
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(example["prompt_payload"], indent=2, default=str)},
                    {"role": "assistant", "content": json.dumps(example["target_response"], default=str)},
                ]
            }
            jsonl_lines.append(json.dumps(training_row, default=str))
            preview_rows.append(example)

        self.training_jsonl_path.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")
        self.preview_path.write_text(json.dumps(preview_rows, indent=2), encoding="utf-8")

        state = self.load_state()
        state.update(
            {
                "dataset_path": str(self.training_jsonl_path),
                "preview_path": str(self.preview_path),
                "example_count": len(examples),
                "base_model": self.settings.judge_fine_tune_base_model,
            }
        )
        self.save_state(state)
        return state

    async def start_fine_tune(self, *, count: int = 50, suffix: str = "incident-judge") -> dict[str, Any]:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required to create a fine-tuning job.")

        self.write_training_corpus(count=count)
        training_file_id = await self._upload_training_file(self.training_jsonl_path)
        job = await self._create_fine_tuning_job(training_file_id, suffix=suffix)
        state = self.load_state()
        state.update(
            {
                "training_file_id": training_file_id,
                "job_id": job.get("id"),
                "job_status": job.get("status"),
                "base_model": job.get("model", self.settings.judge_fine_tune_base_model),
                "fine_tuned_model": job.get("fine_tuned_model"),
                "job": job,
            }
        )
        self.save_state(state)
        return state

    async def refresh_fine_tune_status(self) -> dict[str, Any]:
        state = self.load_state()
        job_id = state.get("job_id")
        if not job_id:
            raise RuntimeError("No fine-tuning job has been created yet.")
        job = await self._retrieve_fine_tuning_job(str(job_id))
        state.update(
            {
                "job_status": job.get("status"),
                "fine_tuned_model": job.get("fine_tuned_model") or state.get("fine_tuned_model"),
                "job": job,
            }
        )
        self.save_state(state)
        return state

    async def _upload_training_file(self, file_path: Path) -> str:
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        endpoint = f"{self.settings.openai_api_base.rstrip('/')}/files"
        async with httpx.AsyncClient(timeout=max(60.0, self.settings.request_timeout_seconds)) as client:
            with file_path.open("rb") as file_handle:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    data={"purpose": "fine-tune"},
                    files={"file": (file_path.name, file_handle, "application/jsonl")},
                )
            response.raise_for_status()
            payload = response.json()
        return str(payload["id"])

    async def _create_fine_tuning_job(self, training_file_id: str, *, suffix: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.settings.openai_api_base.rstrip('/')}/fine_tuning/jobs"
        body = {
            "model": self.settings.judge_fine_tune_base_model,
            "training_file": training_file_id,
            "suffix": suffix[:18],
            "metadata": {
                "project": "ai-incident-commander",
                "use_case": "incident-judge",
                "example_count": "50",
            },
        }
        async with httpx.AsyncClient(timeout=max(60.0, self.settings.request_timeout_seconds)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            return response.json()

    async def _retrieve_fine_tuning_job(self, job_id: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        endpoint = f"{self.settings.openai_api_base.rstrip('/')}/fine_tuning/jobs/{job_id}"
        async with httpx.AsyncClient(timeout=max(30.0, self.settings.request_timeout_seconds)) as client:
            response = await client.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()

    def _build_training_example(
        self,
        service: str,
        template: dict[str, Any],
        prompt_profile: str,
        *,
        good: bool,
    ) -> dict[str, Any]:
        incident_id = str(uuid4())
        run_id = str(uuid4())
        latency_ms = 1800 if good else 2900
        root_cause = template["root_cause"] if good else template["bad_root_cause"]
        evidence = template["good_evidence"] if good else [template["good_evidence"][0], "Evidence is thin and partly contradictory."]
        actions = template["good_actions"] if good else template["bad_actions"]
        severity = template["severity"]
        policy_decisions = []
        for action_name in actions:
            if action_name == "run_shell_command":
                policy_decisions.append({"action_id": str(uuid4()), "verdict": "blocked", "approved": False, "requires_human_approval": False, "reasons": ["Direct shell execution is blocked."]})
            elif action_name in {"rollback_release", "restart_service"} and severity in {"high", "critical"}:
                policy_decisions.append({"action_id": str(uuid4()), "verdict": "approval_required", "approved": False, "requires_human_approval": True, "reasons": ["Disruptive action requires approval in this context."]})
            else:
                policy_decisions.append({"action_id": str(uuid4()), "verdict": "allowed", "approved": True, "requires_human_approval": False, "reasons": ["Action is reversible and aligned with the evidence."]})

        incident_payload = {
            "id": incident_id,
            "title": template["title"].format(service=service),
            "service": service,
            "environment": "production" if template["kind"] != "queue_backpressure" else "staging",
            "severity": severity,
            "description": template["description"],
            "source": "synthetic",
            "metrics": template["metrics"],
            "labels": [template["kind"], "judge-training", "good-analysis" if good else "bad-analysis"],
            "context": {
                "synthetic": True,
                "expected_root_cause": template["root_cause"],
            },
        }
        run_payload = {
            "id": run_id,
            "incident_id": incident_id,
            "status": "completed",
            "investigation": {
                "summary": f"{service} investigation concluded that {root_cause}",
                "suspected_root_cause": root_cause,
                "confidence": 0.86 if good else 0.93,
                "evidence": evidence,
                "recommended_actions": [
                    {
                        "name": action_name,
                        "description": f"Recommended action {action_name}",
                        "command": action_name.replace("_", " "),
                        "risk_level": "high" if action_name in {"rollback_release", "run_shell_command"} else "medium",
                        "status": "executed" if good and action_name in template["good_actions"][:1] else "proposed",
                    }
                    for action_name in actions
                ],
                "audience_summaries": {
                    "operator": f"Operator summary for {service}.",
                    "executive": f"Executive summary for {service}.",
                    "engineering": f"Engineering summary for {service}.",
                    "postmortem": f"Postmortem summary for {service}.",
                },
                "reflection_notes": ["Critic revised the response before finalization."] if good else [],
            },
            "policy_decisions": policy_decisions,
            "postmortem_summary": (
                f"The incident on {service} was caused by {root_cause}. "
                f"Recommended actions were {' and '.join(actions)}."
            ),
            "memory_hits": [
                {
                    "incident_id": str(uuid4()),
                    "title": f"Historical {service} incident",
                    "summary": template["root_cause"],
                    "score": 0.87 if good else 0.42,
                    "service": service,
                }
            ],
        }
        sub_scores = self._target_sub_scores(good)
        target = {
            "overall_score": 92 if good else 28,
            "rationale": (
                "The analysis stays close to the observable evidence, proposes proportionate actions, and explains the likely cause clearly."
                if good
                else "The analysis overstates confidence, misidentifies the cause, and recommends actions that do not fit the evidence."
            ),
            "findings": (
                [
                    "Root cause aligns with the strongest metrics and traces.",
                    "Remediation plan is proportionate to the observed failure mode.",
                    "Explanation quality is clear enough for operator handoff.",
                ]
                if good
                else [
                    "Root cause does not match the strongest incident signals.",
                    "Evidence is too thin to justify the proposed remediation.",
                    "Action safety is weak for the stated incident severity and context.",
                ]
            ),
            "sub_scores": sub_scores,
        }
        return {
            "quality": "good" if good else "bad",
            "expected_root_cause": template["root_cause"],
            "prompt_payload": build_judge_prompt_payload(
                incident=incident_payload,
                run=run_payload,
                latency_ms=latency_ms,
                prompt_profile=prompt_profile,
                heuristic_sub_scores=sub_scores,
            ),
            "target_response": target,
        }

    @staticmethod
    def _target_sub_scores(good: bool) -> dict[str, float]:
        if good:
            return {
                "root_cause_quality": 94.0,
                "evidence_sufficiency": 90.0,
                "action_safety": 88.0,
                "explanation_quality": 86.0,
                "postmortem_usefulness": 84.0,
            }
        return {
            "root_cause_quality": 22.0,
            "evidence_sufficiency": 31.0,
            "action_safety": 18.0,
            "explanation_quality": 35.0,
            "postmortem_usefulness": 28.0,
        }
