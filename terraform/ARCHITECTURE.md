# EKS deployment architecture notes

## Why EKS over ECS for this project

AI Incident Commander already has a clear platform split between a stateful backend, a separate frontend, horizontal scaling, ingress routing, pod-level identity, and controller-based extensions such as autoscaling and secret injection. EKS fits that model well because the project is already close to a Kubernetes-native internal platform rather than a single stateless web app.

ECS would work for the backend and frontend containers, but it becomes less natural once you want:

1. a HorizontalPodAutoscaler tied to backend CPU
2. ALB ingress resources managed alongside the app
3. pod-scoped IAM through IRSA
4. runtime secret materialization from Secrets Manager
5. a path to adding controllers such as metrics-server, cluster autoscaler, or future operator-style components

For this repo, EKS is the better fit because it matches the app's operator-console nature and its growing internal-platform complexity.

It also fits the observability plan better. The app now ships an in-cluster ADOT collector deployment that accepts OTLP spans and exports them to AWS X-Ray, which is a very natural Kubernetes pattern and would be more awkward to wire cleanly with a simpler task-based runtime.

## How IRSA works here

IRSA stands for IAM Roles for Service Accounts. Instead of giving the whole node broad AWS permissions, the backend pod gets its own Kubernetes service account named `backend-service-account`, and that service account is annotated with an AWS IAM role ARN.

The flow is:

1. Terraform creates an IAM OIDC provider for the EKS cluster
2. Terraform creates an IAM role whose trust policy only allows `system:serviceaccount:ai-incident-commander:backend-service-account`
3. The backend deployment uses that service account
4. When the pod requests AWS credentials, EKS exchanges the service account token for temporary AWS credentials scoped to that IAM role

That means the backend pod can read the application secret bundle without borrowing the node group's credentials.

The same pattern is used for the ADOT collector. It runs under its own `adot-collector` service account and assumes a separate IAM role that only needs `AWSXrayWriteOnlyAccess`.

## How secrets flow from Secrets Manager into pods

This stack stores the backend environment as one JSON document in AWS Secrets Manager. The backend pod mounts a `SecretProviderClass` through the Secrets Store CSI Driver with the AWS provider.

The flow is:

1. Terraform writes the full backend environment bundle into Secrets Manager
2. The backend pod starts with the IRSA-enabled service account
3. The Secrets Store CSI Driver uses that pod identity to call Secrets Manager
4. The driver pulls the JSON document, maps each field into a mounted secret object, and syncs it into a Kubernetes Secret named `backend-app-env`
5. The backend container loads that Kubernetes Secret with `envFrom`

This gives you AWS as the source of truth for secrets while still keeping the application startup simple.

For tracing, the backend reads `ENABLE_TRACING`, `TRACING_SERVICE_NAME`, and `OTLP_ENDPOINT` from the same secret bundle. In EKS, `OTLP_ENDPOINT` defaults to the in-cluster ADOT collector service, and that collector exports the spans to AWS X-Ray.

## What happens to running incidents if a pod crashes

The answer depends on how the work was started.

### Synchronous incident processing

If a client calls the backend directly and the pod crashes in the middle of that HTTP request, the request fails and the caller has to retry. Any run data that was already persisted to PostgreSQL remains available, but transient in-memory work inside that request is lost.

### Background job processing

Background benchmark and incident jobs are more resilient because the code persists `JobRecord` entries and requeues jobs that were still marked `queued` or `running` when the process restarts. That means a crashed pod can lose the live worker process, but once another backend replica starts and the job manager comes up again, those persisted jobs are placed back on the queue.

### Durable state

Incidents, runs, benchmarks, memory, feedback, remediation receipts, and jobs are all intended to live in PostgreSQL in this deployment. So the cluster can lose a backend pod without losing the already committed incident history.

## How the HPA decides to scale

The backend HorizontalPodAutoscaler uses the Kubernetes `autoscaling/v2` API and watches average CPU utilization across the backend pods.

The rule in `k8s/hpa.yaml` is:

1. minimum replicas: `2`
2. maximum replicas: `5`
3. target CPU utilization: `70%`

If the average backend CPU rises above 70 percent for long enough, Kubernetes asks for more replicas. If it stays below the target, Kubernetes can scale back down, but never below two replicas.

That is only half of the scaling story. If the HPA wants more pods than the current nodes can fit, the node group also needs spare room or a cluster autoscaler deployment. Terraform attaches the required autoscaling IAM permissions to the node role so the cluster can be extended with the Kubernetes cluster-autoscaler controller later.
