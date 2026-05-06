# Terraform deployment for AI Incident Commander

This directory provisions the AWS foundation for running AI Incident Commander on Amazon EKS. It creates the network, Kubernetes control plane, worker nodes, private PostgreSQL database, ECR repositories, IAM roles, and Secrets Manager bundle that the application needs.

## What this stack creates

1. A VPC with two public subnets and two private subnets across two Availability Zones
2. One Amazon EKS `1.29` cluster with a single managed node group of `t3.medium` instances
3. One private Amazon RDS for PostgreSQL `15` instance on `db.t3.micro`
4. Two Amazon ECR repositories for the backend and frontend containers
5. IAM roles for the EKS control plane, worker nodes, and the backend service account via IRSA
6. One AWS Secrets Manager secret containing the backend environment bundle

## Prerequisites

Before you run `terraform apply`, make sure these pieces exist:

1. An AWS account with permission to create VPC, EKS, IAM, ECR, RDS, and Secrets Manager resources
2. Terraform `1.6+`
3. `kubectl`, `aws` CLI, and Docker installed locally if you plan to test the deployment outside GitHub Actions
4. A remote Terraform state bucket and lock table
5. An IAM role for GitHub Actions OIDC federation so the workflow can assume AWS credentials without long lived keys
6. The following cluster add-ons installed after the cluster exists:
   `AWS Load Balancer Controller`
   `Secrets Store CSI Driver`
   `AWS provider for the Secrets Store CSI Driver`
   `metrics-server`
7. Secret sync enabled for the Secrets Store CSI Driver so the backend deployment can consume the mirrored Kubernetes Secret via `envFrom`

The deployment manifests in `k8s/` now also ship an in-cluster ADOT collector deployment. The backend exports OTLP spans to `http://adot-collector.ai-incident-commander.svc.cluster.local:4318/v1/traces`, and the collector forwards them to AWS X-Ray through the `awsxray` exporter.

## Bootstrap steps

### 1. Create the remote Terraform state backend

The workflow expects an S3 bucket and a DynamoDB table for state locking.

Example:

```bash
aws s3api create-bucket --bucket <tf-state-bucket> --region us-east-1
aws dynamodb create-table \
  --table-name <tf-lock-table> \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### 2. Provide Terraform variables

At minimum, set these variables:

```bash
export TF_VAR_aws_region=us-east-1
export TF_VAR_cluster_name=ai-incident-commander
export TF_VAR_environment=prod
export TF_VAR_db_password='<strong-password>'
export TF_VAR_openai_api_key='<openai-key>'
```

Optional integrations such as Slack, Jira, Prometheus, and GitHub release correlation can be passed through their matching `TF_VAR_...` values.

### 3. Initialize and apply

```bash
terraform -chdir=terraform init \
  -backend-config="bucket=<tf-state-bucket>" \
  -backend-config="key=prod/ai-incident-commander/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=<tf-lock-table>"

terraform -chdir=terraform apply
```

### 4. Install required cluster add-ons

The Kubernetes manifests assume these controllers are present:

1. AWS Load Balancer Controller for ALB-backed ingress
2. Secrets Store CSI Driver plus the AWS provider so pods can materialize the Secrets Manager bundle
3. metrics-server so the backend HPA can scale on CPU

## Image and deployment flow

1. Terraform creates the ECR repositories and the EKS cluster, plus an IRSA role for the ADOT collector to write traces to X-Ray
2. GitHub Actions builds the backend and frontend images and pushes them to ECR with the commit SHA as the tag
3. The workflow renders the files in `k8s/` with the actual image URIs, the IRSA role ARN, and the secret ARN
4. `kubectl apply` updates the live deployment

## Clean destroy steps to avoid AWS charges

When you are done with the environment, tear it down in this order:

1. Delete the application layer:

```bash
kubectl delete namespace ai-incident-commander --ignore-not-found
```

2. Destroy the Terraform-managed infrastructure:

```bash
terraform -chdir=terraform destroy
```

3. Remove the remote Terraform state backend only if it was created solely for this project:
   delete the S3 state bucket
   delete the DynamoDB lock table

4. If you want immediate removal of the Secrets Manager secret instead of the normal recovery window, force delete it manually:

```bash
aws secretsmanager delete-secret \
  --secret-id /prod/ai-incident-commander/app-env \
  --force-delete-without-recovery
```

## Estimated monthly cost

These are ballpark `us-east-1` on-demand estimates as of `May 5, 2026`. They are intentionally conservative and do not include traffic spikes, NAT data processing, or CloudWatch log growth.

| Component | Assumption | Estimated monthly cost |
| --- | --- | --- |
| EKS control plane | 1 cluster at `$0.10/hour` | `$73` |
| Worker nodes | 2 x `t3.medium` nodes at roughly `$0.0416/hour` each | `$60.74` |
| NAT gateway | 1 NAT gateway at `$0.045/hour` | `$32.85` plus data processing |
| RDS PostgreSQL | `db.t3.micro` at roughly `$0.017/hour` | `$12.41` |
| RDS storage | 20 GiB `gp3` | about `$2 to $3` |
| Application Load Balancer | 1 ALB at roughly `$0.0225/hour` | `$16.43` plus LCU usage |
| Secrets Manager | 1 secret | about `$0.40` |
| ECR storage | 2 private repos, a few GB of images | about `$0.20 to $1` |

Baseline total: roughly `$198 to $205/month` before egress, NAT data processing, CloudWatch log retention, or scale-out events.

## Notes

1. The Terraform in this directory uses one NAT gateway to keep the baseline cost lower. If you want full AZ-level egress redundancy, add one NAT gateway per private subnet and budget for the extra cost.
2. The backend uses PostgreSQL for durable incident, run, benchmark, and job state. That matters for failure recovery when pods restart.
3. The frontend image should always be built with `NEXT_PUBLIC_API_BASE_URL=/api` in this EKS deployment model.
