output "cluster_endpoint" {
  description = "EKS API endpoint."
  value       = aws_eks_cluster.main.endpoint
}

output "backend_ecr_repository_url" {
  description = "ECR repository URL for the backend image."
  value       = aws_ecr_repository.backend.repository_url
}

output "frontend_ecr_repository_url" {
  description = "ECR repository URL for the frontend image."
  value       = aws_ecr_repository.frontend.repository_url
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint."
  value       = aws_db_instance.postgres.address
}

output "kubeconfig_update_command" {
  description = "Command to update local kubeconfig for the cluster."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.main.name}"
}

output "app_secret_arn" {
  description = "Secrets Manager ARN consumed by the backend deployment."
  value       = aws_secretsmanager_secret.app_env.arn
}

output "app_irsa_role_arn" {
  description = "IAM role ARN used by the backend service account."
  value       = aws_iam_role.app_irsa.arn
}

output "adot_collector_irsa_role_arn" {
  description = "IAM role ARN used by the ADOT collector service account."
  value       = aws_iam_role.adot_collector_irsa.arn
}
