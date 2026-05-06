locals {
  namespace                 = "ai-incident-commander"
  backend_repository_name   = "ai-incident-commander-backend"
  frontend_repository_name  = "ai-incident-commander-frontend"
  secret_name               = "/${var.environment}/${var.cluster_name}/app-env"
  db_name                   = "incident_commander"
  db_username               = "incident"
  azs                       = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnet_cidrs       = ["10.0.0.0/20", "10.0.16.0/20"]
  private_subnet_cidrs      = ["10.0.128.0/20", "10.0.144.0/20"]
  backend_base_url          = "http://backend-service.${local.namespace}.svc.cluster.local:8000"
  adot_collector_endpoint   = "http://adot-collector.${local.namespace}.svc.cluster.local:4318/v1/traces"
  safe_remediation_url      = "${local.backend_base_url}/sandbox/remediation/execute"
  postgres_dsn              = format("postgresql://%s:%s@%s:%d/%s", local.db_username, var.db_password, aws_db_instance.postgres.address, aws_db_instance.postgres.port, local.db_name)
  safe_remediation_services = join(",", var.safe_remediation_allowed_services)
  otlp_endpoint             = trimspace(var.otlp_endpoint) != "" ? var.otlp_endpoint : local.adot_collector_endpoint

  tags = {
    Project     = "ai-incident-commander"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  secret_env = {
    APP_NAME                              = "AI Incident Commander"
    APP_ENV                               = var.environment
    DATA_DIR                              = "data"
    STORAGE_BACKEND                       = "postgres"
    POSTGRES_DSN                          = local.postgres_dsn
    POSTGRES_SCHEMA                       = "incident_commander"
    ENABLE_BACKGROUND_JOBS                = "true"
    BACKGROUND_JOB_CONCURRENCY            = "1"
    BACKGROUND_JOB_POLL_INTERVAL_SECONDS  = "1.0"
    ENABLE_LANGGRAPH                      = "true"
    ENABLE_AUTOGEN                        = "true"
    DEFAULT_PROMPT_PROFILE                = "balanced-v1"
    DEFAULT_MODEL_ROUTE                   = "balanced-route"
    MODEL_ROUTING_ENABLED                 = "true"
    AUTO_EXECUTE_SAFE_ACTIONS             = "false"
    OPENAI_API_KEY                        = var.openai_api_key
    OPENAI_API_BASE                       = "https://api.openai.com/v1"
    OPENAI_MODEL                          = "gpt-4.1-mini"
    JUDGE_MODEL                           = ""
    SYNTHETIC_GENERATION_MODEL            = ""
    AUTOGEN_PLANNER_MODEL                 = ""
    AUTOGEN_INVESTIGATOR_MODEL            = ""
    AUTOGEN_CRITIC_MODEL                  = ""
    AUTOGEN_COMMANDER_MODEL               = ""
    AUTH_ENABLED                          = tostring(var.auth_enabled)
    VIEWER_API_TOKEN                      = var.viewer_api_token
    OPERATOR_API_TOKEN                    = var.operator_api_token
    REMEDIATION_TOKEN                     = var.remediation_token
    SLACK_BOT_TOKEN                       = var.slack_bot_token
    SLACK_CHANNEL                         = var.slack_channel
    SLACK_API_BASE                        = "https://slack.com/api"
    JIRA_BASE_URL                         = var.jira_base_url
    JIRA_EMAIL                            = var.jira_email
    JIRA_API_TOKEN                        = var.jira_api_token
    JIRA_PROJECT_KEY                      = "OPS"
    JIRA_ISSUE_TYPE                       = "Task"
    JIRA_LABELS                           = var.jira_labels
    PROMETHEUS_BASE_URL                   = var.prometheus_base_url
    PROMETHEUS_BEARER_TOKEN               = var.prometheus_bearer_token
    PROMETHEUS_LATENCY_QUERY_TEMPLATE     = "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service=\"{service}\"}[5m])) by (le))"
    PROMETHEUS_ERROR_QUERY_TEMPLATE       = "sum(rate(http_requests_total{service=\"{service}\",status=~\"5..\"}[5m])) / sum(rate(http_requests_total{service=\"{service}\"}[5m]))"
    PROMETHEUS_UPTIME_QUERY_TEMPLATE      = "avg(up{job=\"{service}\"})"
    ENABLE_RELEASE_CORRELATION            = tostring(var.enable_release_correlation)
    GITHUB_API_BASE                       = "https://api.github.com"
    GITHUB_REPO                           = var.github_repo
    GITHUB_TOKEN                          = var.github_token
    GITHUB_ENVIRONMENT                    = "staging"
    GITHUB_BRANCH                         = "main"
    GITHUB_WORKFLOW_NAME                  = var.github_workflow_name
    SAFE_REMEDIATION_URL                  = local.safe_remediation_url
    SAFE_REMEDIATION_ENABLED              = "true"
    SAFE_REMEDIATION_PROVIDER_NAME        = "controlled-gateway"
    SAFE_REMEDIATION_ALLOW_PRODUCTION     = "false"
    SAFE_REMEDIATION_ALLOWED_ACTIONS      = "scale_service,restart_service"
    SAFE_REMEDIATION_ALLOWED_ENVIRONMENTS = "staging,qa,dev,development"
    SAFE_REMEDIATION_ALLOWED_SERVICES     = local.safe_remediation_services
    SAFE_REMEDIATION_MIN_REPLICAS         = "1"
    SAFE_REMEDIATION_MAX_REPLICAS         = "10"
    PUBLIC_DEMO_MODE                      = "false"
    LOG_LEVEL                             = "INFO"
    BACKEND_BASE_URL                      = local.backend_base_url
    REQUEST_TIMEOUT_SECONDS               = "12"
    ENABLE_TRACING                        = tostring(var.enable_tracing)
    TRACING_SERVICE_NAME                  = "ai-incident-commander"
    OTLP_ENDPOINT                         = local.otlp_endpoint
  }
}
