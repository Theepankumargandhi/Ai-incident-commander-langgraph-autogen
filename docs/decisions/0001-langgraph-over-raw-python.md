# ADR 0001: LangGraph Over Raw Python Orchestration

## Status
Accepted

## Date
2026-05-05

## Context
This project is not a short-lived chat loop. It runs incident investigations that gather observability context, consult memory, coordinate a multi-agent investigation team, evaluate candidate actions, wait for approval when needed, execute safe remediations, generate postmortems, and record benchmarking data. The workflow also carries `incident_id`, `run_id`, trace context, cost usage, policy decisions, and replay events across many steps.

We could have implemented the orchestration in raw Python with a sequence of service calls and custom state passing. That approach would work for a smaller prototype, but it becomes brittle once the system needs durable stage boundaries, replayable runs, approval pauses, consistent tracing, explicit transitions, and clean debugging for long-running incident workflows.

## Decision
We use LangGraph as the outer orchestration runtime instead of raw Python orchestration.

LangGraph is responsible for the workflow skeleton: stage ordering, shared state, transition clarity, approval gates, retries, replayability, and the lifecycle of a run. The AutoGen team operates inside that structure for the actual multi-agent reasoning, but the workflow control itself belongs to LangGraph.

## Alternatives Considered

### Raw Python Orchestration
Raw Python was the simplest initial option. It would have reduced dependencies and allowed direct control over every function call.

We rejected it because the project now behaves like a workflow platform, not a simple script. As the number of stages, approval branches, analytics hooks, and recovery paths grew, custom orchestration would have made the code harder to reason about and much easier to break.

## Consequences

### Positive
- The workflow is explicit and easier to explain in code reviews, interviews, and incident demos.
- Stage boundaries are clean, which improves tracing, replay, benchmarking, and debugging.
- Approval and execution gates are easier to model than in ad hoc Python control flow.
- The project can evolve without turning the orchestration layer into a pile of conditionals.

### Negative
- LangGraph adds framework overhead and another abstraction that contributors must understand.
- Some simple flows take more structure to express than they would in plain Python.

## Resulting Architecture Impact
LangGraph remains the workflow control plane for the platform. The codebase uses it to define the incident lifecycle, while specialized agent reasoning stays inside the AutoGen team. This keeps orchestration concerns separate from reasoning concerns.
