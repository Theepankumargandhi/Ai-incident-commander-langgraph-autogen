from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    parts = tuple(item.strip() for item in value.split(",") if item.strip())
    return parts or default


@dataclass(slots=True)
class Settings:
    app_name: str = "AI Incident Commander"
    app_env: str = "development"
    data_dir: Path = Path("data")
    storage_backend: str = "auto"
    postgres_dsn: str | None = None
    postgres_schema: str = "incident_commander"
    enable_background_jobs: bool = True
    background_job_concurrency: int = 1
    background_job_poll_interval_seconds: float = 1.0
    enable_langgraph: bool = True
    enable_autogen: bool = True
    default_prompt_profile: str = "balanced-v1"
    default_model_route: str = "balanced-route"
    model_routing_enabled: bool = True
    auto_execute_safe_actions: bool = False
    openai_api_key: str | None = None
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    judge_model: str | None = None
    judge_fine_tuned_model: str | None = None
    judge_fine_tune_base_model: str = "gpt-4o-mini-2024-07-18"
    synthetic_generation_model: str | None = None
    autogen_planner_model: str | None = None
    autogen_investigator_model: str | None = None
    autogen_critic_model: str | None = None
    autogen_commander_model: str | None = None
    auth_enabled: bool = False
    viewer_api_token: str | None = None
    operator_api_token: str | None = None
    remediation_token: str | None = None
    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_channel: str = "#ops-oncall"
    slack_api_base: str = "https://slack.com/api"
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_project_key: str = "OPS"
    jira_issue_type: str = "Task"
    jira_labels: tuple[str, ...] = ()
    prometheus_base_url: str | None = None
    prometheus_bearer_token: str | None = None
    prometheus_latency_query_template: str = (
        'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service="{service}"}[5m])) by (le))'
    )
    prometheus_error_query_template: str = (
        'sum(rate(http_requests_total{service="{service}",status=~"5.."}[5m])) / sum(rate(http_requests_total{service="{service}"}[5m]))'
    )
    prometheus_uptime_query_template: str = 'avg(up{job="{service}"})'
    enable_release_correlation: bool = False
    github_api_base: str = "https://api.github.com"
    github_repo: str | None = None
    github_token: str | None = None
    github_environment: str = "staging"
    github_branch: str = "main"
    github_workflow_name: str | None = None
    safe_remediation_url: str | None = None
    safe_remediation_enabled: bool = False
    safe_remediation_provider_name: str = "controlled-gateway"
    safe_remediation_allow_production: bool = False
    safe_remediation_allowed_actions: tuple[str, ...] = ("scale_service", "restart_service")
    safe_remediation_allowed_environments: tuple[str, ...] = ("staging", "qa", "dev", "development")
    safe_remediation_allowed_services: tuple[str, ...] = ()
    safe_remediation_min_replicas: int = 1
    safe_remediation_max_replicas: int = 10
    public_demo_mode: bool = True
    log_level: str = "INFO"
    backend_base_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: float = 12.0
    enable_tracing: bool = False
    tracing_service_name: str = "ai-incident-commander"
    otlp_endpoint: str | None = None


def get_settings() -> Settings:
    load_dotenv()
    base_dir = Path(__file__).resolve().parents[1]
    data_dir = base_dir / os.getenv("DATA_DIR", "data")
    data_dir.mkdir(parents=True, exist_ok=True)

    jira_labels_raw = os.getenv("JIRA_LABELS", "ai-incident-commander,agentic-ai")
    jira_labels = tuple(label.strip() for label in jira_labels_raw.split(",") if label.strip())
    remediation_allowed_actions = _as_csv(
        os.getenv("SAFE_REMEDIATION_ALLOWED_ACTIONS"),
        ("scale_service", "restart_service"),
    )
    remediation_allowed_environments = _as_csv(
        os.getenv("SAFE_REMEDIATION_ALLOWED_ENVIRONMENTS"),
        ("staging", "qa", "dev", "development"),
    )
    remediation_allowed_services = _as_csv(
        os.getenv("SAFE_REMEDIATION_ALLOWED_SERVICES"),
        (),
    )

    return Settings(
        app_name=os.getenv("APP_NAME", "AI Incident Commander"),
        app_env=os.getenv("APP_ENV", "development"),
        data_dir=data_dir,
        storage_backend=os.getenv("STORAGE_BACKEND", "auto").strip().lower(),
        postgres_dsn=os.getenv("POSTGRES_DSN"),
        postgres_schema=os.getenv("POSTGRES_SCHEMA", "incident_commander"),
        enable_background_jobs=_as_bool(os.getenv("ENABLE_BACKGROUND_JOBS"), True),
        background_job_concurrency=_as_int(os.getenv("BACKGROUND_JOB_CONCURRENCY"), 1),
        background_job_poll_interval_seconds=_as_float(os.getenv("BACKGROUND_JOB_POLL_INTERVAL_SECONDS"), 1.0),
        enable_langgraph=_as_bool(os.getenv("ENABLE_LANGGRAPH"), True),
        enable_autogen=_as_bool(os.getenv("ENABLE_AUTOGEN"), True),
        default_prompt_profile=os.getenv("DEFAULT_PROMPT_PROFILE", "balanced-v1"),
        default_model_route=os.getenv("DEFAULT_MODEL_ROUTE", "balanced-route"),
        model_routing_enabled=_as_bool(os.getenv("MODEL_ROUTING_ENABLED"), True),
        auto_execute_safe_actions=_as_bool(os.getenv("AUTO_EXECUTE_SAFE_ACTIONS"), False),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        judge_model=os.getenv("JUDGE_MODEL"),
        judge_fine_tuned_model=os.getenv("JUDGE_FINE_TUNED_MODEL"),
        judge_fine_tune_base_model=os.getenv("JUDGE_FINE_TUNE_BASE_MODEL", "gpt-4o-mini-2024-07-18"),
        synthetic_generation_model=os.getenv("SYNTHETIC_GENERATION_MODEL"),
        autogen_planner_model=os.getenv("AUTOGEN_PLANNER_MODEL"),
        autogen_investigator_model=os.getenv("AUTOGEN_INVESTIGATOR_MODEL"),
        autogen_critic_model=os.getenv("AUTOGEN_CRITIC_MODEL"),
        autogen_commander_model=os.getenv("AUTOGEN_COMMANDER_MODEL"),
        auth_enabled=_as_bool(os.getenv("AUTH_ENABLED"), False),
        viewer_api_token=os.getenv("VIEWER_API_TOKEN"),
        operator_api_token=os.getenv("OPERATOR_API_TOKEN"),
        remediation_token=os.getenv("REMEDIATION_TOKEN"),
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN"),
        slack_signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
        slack_channel=os.getenv("SLACK_CHANNEL", "#ops-oncall"),
        slack_api_base=os.getenv("SLACK_API_BASE", "https://slack.com/api"),
        jira_base_url=os.getenv("JIRA_BASE_URL"),
        jira_email=os.getenv("JIRA_EMAIL"),
        jira_api_token=os.getenv("JIRA_API_TOKEN"),
        jira_project_key=os.getenv("JIRA_PROJECT_KEY", "OPS"),
        jira_issue_type=os.getenv("JIRA_ISSUE_TYPE", "Task"),
        jira_labels=jira_labels,
        prometheus_base_url=os.getenv("PROMETHEUS_BASE_URL"),
        prometheus_bearer_token=os.getenv("PROMETHEUS_BEARER_TOKEN"),
        prometheus_latency_query_template=os.getenv(
            "PROMETHEUS_LATENCY_QUERY_TEMPLATE",
            'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service="{service}"}[5m])) by (le))',
        ),
        prometheus_error_query_template=os.getenv(
            "PROMETHEUS_ERROR_QUERY_TEMPLATE",
            'sum(rate(http_requests_total{service="{service}",status=~"5.."}[5m])) / sum(rate(http_requests_total{service="{service}"}[5m]))',
        ),
        prometheus_uptime_query_template=os.getenv(
            "PROMETHEUS_UPTIME_QUERY_TEMPLATE",
            'avg(up{job="{service}"})',
        ),
        enable_release_correlation=_as_bool(os.getenv("ENABLE_RELEASE_CORRELATION"), False),
        github_api_base=os.getenv("GITHUB_API_BASE", "https://api.github.com"),
        github_repo=os.getenv("GITHUB_REPO"),
        github_token=os.getenv("GITHUB_TOKEN"),
        github_environment=os.getenv("GITHUB_ENVIRONMENT", "staging"),
        github_branch=os.getenv("GITHUB_BRANCH", "main"),
        github_workflow_name=os.getenv("GITHUB_WORKFLOW_NAME"),
        safe_remediation_url=os.getenv("SAFE_REMEDIATION_URL"),
        safe_remediation_enabled=_as_bool(os.getenv("SAFE_REMEDIATION_ENABLED"), False),
        safe_remediation_provider_name=os.getenv("SAFE_REMEDIATION_PROVIDER_NAME", "controlled-gateway"),
        safe_remediation_allow_production=_as_bool(os.getenv("SAFE_REMEDIATION_ALLOW_PRODUCTION"), False),
        safe_remediation_allowed_actions=remediation_allowed_actions,
        safe_remediation_allowed_environments=remediation_allowed_environments,
        safe_remediation_allowed_services=remediation_allowed_services,
        safe_remediation_min_replicas=_as_int(os.getenv("SAFE_REMEDIATION_MIN_REPLICAS"), 1),
        safe_remediation_max_replicas=_as_int(os.getenv("SAFE_REMEDIATION_MAX_REPLICAS"), 10),
        public_demo_mode=_as_bool(os.getenv("PUBLIC_DEMO_MODE"), True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        backend_base_url=os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000"),
        request_timeout_seconds=_as_float(os.getenv("REQUEST_TIMEOUT_SECONDS"), 12.0),
        enable_tracing=_as_bool(os.getenv("ENABLE_TRACING"), False),
        tracing_service_name=os.getenv("TRACING_SERVICE_NAME", "ai-incident-commander"),
        otlp_endpoint=os.getenv("OTLP_ENDPOINT"),
    )
