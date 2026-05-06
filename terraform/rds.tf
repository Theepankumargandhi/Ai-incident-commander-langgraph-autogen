resource "aws_db_subnet_group" "postgres" {
  name       = "${var.cluster_name}-postgres-subnets"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "rds" {
  name        = "${var.cluster_name}-rds-sg"
  description = "Allow PostgreSQL access only from the EKS worker nodes."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from EKS workers"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "postgres" {
  identifier                   = "${var.cluster_name}-${var.environment}-postgres"
  engine                       = "postgres"
  engine_version               = "15"
  instance_class               = "db.t3.micro"
  allocated_storage            = 20
  max_allocated_storage        = 100
  storage_type                 = "gp3"
  db_name                      = local.db_name
  username                     = local.db_username
  password                     = var.db_password
  db_subnet_group_name         = aws_db_subnet_group.postgres.name
  vpc_security_group_ids       = [aws_security_group.rds.id]
  publicly_accessible          = false
  multi_az                     = false
  skip_final_snapshot          = true
  deletion_protection          = false
  backup_retention_period      = 7
  auto_minor_version_upgrade   = true
  apply_immediately            = true
  performance_insights_enabled = false
}
