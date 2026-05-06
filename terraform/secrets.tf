resource "aws_secretsmanager_secret" "app_env" {
  name                    = local.secret_name
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app_env" {
  secret_id     = aws_secretsmanager_secret.app_env.id
  secret_string = jsonencode(local.secret_env)
}
