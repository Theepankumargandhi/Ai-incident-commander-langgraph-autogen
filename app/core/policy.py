from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.core.llm_json import OpenAIJsonClient
from app.core.storage import CollectionStore
from app.core.tracing import traced_span
from app.models import (
    Incident,
    IncidentSeverity,
    PolicyDecision,
    PolicyDocument,
    PolicyVerdict,
    ToolAction,
    utc_now,
)

logger = logging.getLogger(__name__)

DEFAULT_POLICY_DOCUMENT = """Incident remediation policy

1. Direct shell command execution must be blocked.
2. In production, disruptive actions such as restart_service and rollback_release require human approval.
3. In production, controlled scaling actions such as scale_service may be allowed when they are reversible and proportional.
4. If the incident severity is low, do not allow high-risk or critical-risk remediation unless there is a strong documented exception.
5. In non-production environments, restart_service may be allowed when it is limited to the affected service and remains reversible.
6. When evidence is weak, prefer REQUIRE_APPROVAL over ALLOW for potentially disruptive actions.
7. Return a concise reasoning trail grounded in the policy document and the incident context.
"""


class PolicyEngine:
    """Evaluate remediation actions with a policy document and LLM first, then fall back to hardcoded rules."""

    def __init__(
        self,
        settings: Settings | None = None,
        policy_store: CollectionStore[PolicyDocument] | None = None,
    ) -> None:
        self.settings = settings
        self.policy_store = policy_store
        self.llm_client = OpenAIJsonClient(settings) if settings is not None else None
        self._memory_document: PolicyDocument | None = None

    async def evaluate_action(self, incident: Incident, action: ToolAction) -> PolicyDecision:
        with traced_span(
            "policy.action.evaluate",
            {
                "incident_id": incident.id,
                "action_id": action.id,
                "action_name": action.name,
                "incident.service": incident.service,
                "incident.environment": incident.environment,
            },
        ):
            document = self.get_policy_document()
            if self.llm_client is not None and self.llm_client.is_available():
                try:
                    decision = await self._evaluate_with_llm(incident, action, document)
                    if decision is not None:
                        return decision
                except Exception:
                    logger.warning(
                        "LLM policy evaluation failed for incident %s action %s; using hardcoded fallback.",
                        incident.id,
                        action.name,
                        exc_info=True,
                    )
            return self._evaluate_action_fallback(incident, action)

    def get_policy_document(self) -> PolicyDocument:
        if self.policy_store is not None:
            stored = self.policy_store.get("active")
            if stored is not None:
                return stored

        if self._memory_document is None:
            self._memory_document = PolicyDocument(content=DEFAULT_POLICY_DOCUMENT)
            if self.policy_store is not None:
                self.policy_store.save(self._memory_document)
        return self._memory_document

    def update_policy_document(
        self,
        *,
        content: str,
        name: str | None = None,
        updated_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PolicyDocument:
        current = self.get_policy_document()
        updated = current.model_copy(
            update={
                "name": name or current.name,
                "content": content.strip(),
                "version": current.version + 1,
                "updated_at": utc_now(),
                "updated_by": updated_by,
                "metadata": current.metadata if metadata is None else metadata,
            }
        )
        self._memory_document = updated
        if self.policy_store is not None:
            self.policy_store.save(updated)
        return updated

    async def _evaluate_with_llm(
        self,
        incident: Incident,
        action: ToolAction,
        document: PolicyDocument,
    ) -> PolicyDecision | None:
        assert self.llm_client is not None
        model = (self.settings.judge_model if self.settings is not None else None) or (
            self.settings.openai_model if self.settings is not None else None
        )
        if not model:
            return None

        payload = await self.llm_client.complete_json(
            model=model,
            system_prompt=(
                "You are a policy evaluator for AI-assisted DevOps remediation. "
                "Read the policy document, incident context, and proposed action. "
                "Return JSON with keys verdict, reasons, and policy_rule. "
                "verdict must be one of ALLOW, BLOCK, or REQUIRE_APPROVAL. "
                "Keep the reasoning concise and specific to the policy text."
            ),
            user_prompt=self._build_user_prompt(incident, action, document),
            temperature=0.0,
            usage_source="policy_evaluation",
        )

        verdict = self._parse_verdict(payload.get("verdict"))
        if verdict is None:
            return None

        reasons = payload.get("reasons")
        if isinstance(reasons, str):
            reason_list = [reasons.strip()] if reasons.strip() else []
        elif isinstance(reasons, list):
            reason_list = [str(item).strip() for item in reasons if str(item).strip()]
        else:
            reason_list = []
        if not reason_list:
            reasoning = payload.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                reason_list = [reasoning.strip()]

        return PolicyDecision(
            action_id=action.id,
            verdict=verdict,
            approved=verdict == PolicyVerdict.allowed,
            requires_human_approval=verdict == PolicyVerdict.approval_required,
            reasons=reason_list or ["Policy evaluation completed without additional reasoning."],
            policy_rule=str(payload.get("policy_rule", f"llm-policy-v{document.version}")).strip() or f"llm-policy-v{document.version}",
        )

    @staticmethod
    def _build_user_prompt(incident: Incident, action: ToolAction, document: PolicyDocument) -> str:
        incident_context = {
            "id": incident.id,
            "title": incident.title,
            "service": incident.service,
            "environment": incident.environment,
            "severity": incident.severity.value,
            "status": incident.status.value,
            "description": incident.description,
            "source": incident.source,
            "metrics": incident.metrics,
            "labels": incident.labels,
            "context_summary": {
                "release_hint": incident.context.get("observability", {}).get("release_hint"),
                "log_count": len(incident.context.get("observability", {}).get("logs", [])),
                "trace_count": len(incident.context.get("observability", {}).get("traces", [])),
                "artifacts_attached": len(incident.artifacts),
            },
        }
        action_context = {
            "id": action.id,
            "name": action.name,
            "description": action.description,
            "command": action.command,
            "risk_level": action.risk_level.value,
            "metadata": action.metadata,
        }
        return (
            f"Policy document name: {document.name}\n"
            f"Policy document version: {document.version}\n"
            f"Policy document:\n{document.content}\n\n"
            f"Incident context:\n{incident_context}\n\n"
            f"Proposed action:\n{action_context}\n\n"
            "Decide whether the action should be ALLOW, BLOCK, or REQUIRE_APPROVAL."
        )

    @staticmethod
    def _parse_verdict(value: Any) -> PolicyVerdict | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        if normalized == "ALLOW":
            return PolicyVerdict.allowed
        if normalized == "BLOCK":
            return PolicyVerdict.blocked
        if normalized in {"REQUIRE_APPROVAL", "APPROVAL_REQUIRED"}:
            return PolicyVerdict.approval_required
        return None

    def _evaluate_action_fallback(self, incident: Incident, action: ToolAction) -> PolicyDecision:
        reasons: list[str] = []

        if action.name == "run_shell_command":
            reasons.append("Direct shell execution is blocked by policy.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.blocked,
                approved=False,
                requires_human_approval=False,
                reasons=reasons,
                policy_rule="block-shell-command",
            )

        if incident.environment.lower() == "production" and action.name in {"rollback_release", "restart_service"}:
            reasons.append("Production restarts and rollbacks require a human approval checkpoint.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.approval_required,
                approved=False,
                requires_human_approval=True,
                reasons=reasons,
                policy_rule="approval-prod-disruptive-action",
            )

        if action.name == "scale_service" and incident.environment.lower() == "production":
            reasons.append("Controlled scale actions are allowed for production incidents.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.allowed,
                approved=True,
                requires_human_approval=False,
                reasons=reasons,
                policy_rule="allow-prod-safe-scale",
            )

        if incident.severity == IncidentSeverity.low and action.risk_level in {
            IncidentSeverity.high,
            IncidentSeverity.critical,
        }:
            reasons.append("High-risk remediation is disproportionate for a low-severity incident.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.blocked,
                approved=False,
                requires_human_approval=False,
                reasons=reasons,
                policy_rule="block-disproportionate-risk",
            )

        if incident.environment.lower() != "production" and action.name == "restart_service":
            reasons.append("Non-production service restarts are allowed.")
            return PolicyDecision(
                action_id=action.id,
                verdict=PolicyVerdict.allowed,
                approved=True,
                requires_human_approval=False,
                reasons=reasons,
                policy_rule="allow-nonprod-restart",
            )

        reasons.append("Action is allowed under current policy.")
        return PolicyDecision(
            action_id=action.id,
            verdict=PolicyVerdict.allowed,
            approved=True,
            requires_human_approval=False,
            reasons=reasons,
            policy_rule="default-allow",
        )
