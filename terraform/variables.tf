variable "aws_region" {
  description = "AWS region for the EKS stack."
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "Base name used for the EKS cluster and related AWS resources."
  type        = string
  default     = "ai-incident-commander"
}

variable "environment" {
  description = "Environment label applied to AWS resources and app config."
  type        = string
  default     = "prod"
}

variable "db_password" {
  description = "Master password for the PostgreSQL database."
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key passed into the backend secret bundle."
  type        = string
  sensitive   = true
  default     = ""
}

variable "judge_fine_tuned_model" {
  description = "Optional fine-tuned judge model ID used by the backend evaluator."
  type        = string
  default     = ""
}

variable "auth_enabled" {
  description = "Enable bearer token auth in the backend."
  type        = bool
  default     = false
}

variable "viewer_api_token" {
  description = "Viewer token for the backend API."
  type        = string
  sensitive   = true
  default     = ""
}

variable "operator_api_token" {
  description = "Operator token for the backend API."
  type        = string
  sensitive   = true
  default     = ""
}

variable "remediation_token" {
  description = "Token used by the controlled remediation endpoint."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_bot_token" {
  description = "Slack bot token used by the notification connector."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_signing_secret" {
  description = "Slack signing secret used to verify interactive requests."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_channel" {
  description = "Slack channel used for incident notifications."
  type        = string
  default     = "#ops-oncall"
}

variable "pagerduty_webhook_secret" {
  description = "Secret used to verify PagerDuty webhook signatures."
  type        = string
  sensitive   = true
  default     = ""
}

variable "opsgenie_webhook_secret" {
  description = "Secret token used to verify OpsGenie webhook requests."
  type        = string
  sensitive   = true
  default     = ""
}

variable "jira_base_url" {
  description = "Jira Cloud base URL."
  type        = string
  default     = ""
}

variable "jira_email" {
  description = "Jira Cloud user email."
  type        = string
  default     = ""
}

variable "jira_api_token" {
  description = "Jira Cloud API token."
  type        = string
  sensitive   = true
  default     = ""
}

variable "jira_labels" {
  description = "Comma-separated Jira labels."
  type        = string
  default     = "ai-incident-commander,agentic-ai"
}

variable "prometheus_base_url" {
  description = "Prometheus base URL for live observability."
  type        = string
  default     = ""
}

variable "prometheus_bearer_token" {
  description = "Prometheus bearer token."
  type        = string
  sensitive   = true
  default     = ""
}

variable "enable_release_correlation" {
  description = "Enable GitHub release metadata lookups."
  type        = bool
  default     = false
}

variable "github_repo" {
  description = "GitHub repository in owner/name format for release correlation."
  type        = string
  default     = ""
}

variable "github_token" {
  description = "GitHub token for release correlation lookups."
  type        = string
  sensitive   = true
  default     = ""
}

variable "github_workflow_name" {
  description = "Optional GitHub workflow name used for release metadata."
  type        = string
  default     = ""
}

variable "enable_tracing" {
  description = "Enable OTLP tracing in the backend."
  type        = bool
  default     = false
}

variable "otlp_endpoint" {
  description = "OTLP collector endpoint."
  type        = string
  default     = ""
}

variable "safe_remediation_allowed_services" {
  description = "Optional service allowlist for the controlled remediation gateway."
  type        = list(string)
  default     = []
}
