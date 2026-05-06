from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.models import ConnectorHealth

logger = logging.getLogger(__name__)


class GitHubReleaseMetadataClient:
    """Retrieve recent deployment metadata from GitHub Actions for staging correlation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.enable_release_correlation
            and self.settings.github_repo
            and self.settings.github_token
        )

    def healthcheck(self) -> ConnectorHealth:
        base_details: dict = {
            "repo": self.settings.github_repo,
            "environment": self.settings.github_environment,
            "workflow_name": self.settings.github_workflow_name,
        }
        if not self.configured:
            return ConnectorHealth(
                name="github_release",
                enabled=self.settings.enable_release_correlation,
                configured=False,
                healthy=True,
                details={**base_details, "mode": "disabled"},
            )
        url = f"{self.settings.github_api_base.rstrip('/')}/rate_limit"
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.settings.github_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            if response.status_code < 300:
                return ConnectorHealth(
                    name="github_release",
                    enabled=True,
                    configured=True,
                    healthy=True,
                    details={**base_details, "mode": "live"},
                )
            return ConnectorHealth(
                name="github_release",
                enabled=True,
                configured=True,
                healthy=False,
                details={**base_details, "mode": "live", "error": f"HTTP {response.status_code}"},
            )
        except Exception as exc:
            return ConnectorHealth(
                name="github_release",
                enabled=True,
                configured=True,
                healthy=False,
                details={**base_details, "mode": "live", "error": str(exc)},
            )

    def latest_release(self) -> dict | None:
        if not self.configured:
            return None

        try:
            response = httpx.get(
                f"{self.settings.github_api_base.rstrip('/')}/repos/{self.settings.github_repo}/actions/runs",
                headers={
                    "Authorization": f"Bearer {self.settings.github_token}",
                    "Accept": "application/vnd.github+json",
                },
                params={
                    "branch": self.settings.github_branch,
                    "status": "completed",
                    "per_page": 20,
                },
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            workflow_runs = response.json().get("workflow_runs", [])
            for run in workflow_runs:
                workflow_name = run.get("name") or ""
                if self.settings.github_workflow_name and workflow_name != self.settings.github_workflow_name:
                    continue
                if run.get("conclusion") != "success":
                    continue
                return {
                    "provider": "github_actions",
                    "workflow_name": workflow_name,
                    "run_id": run.get("id"),
                    "run_number": run.get("run_number"),
                    "head_sha": run.get("head_sha"),
                    "head_branch": run.get("head_branch"),
                    "updated_at": run.get("updated_at"),
                    "html_url": run.get("html_url"),
                    "environment": self.settings.github_environment,
                }
        except Exception as exc:  # pragma: no cover - network dependent
            logger.exception("GitHub release metadata lookup failed.")
            return {
                "provider": "github_actions",
                "error": str(exc),
            }
        return None
