# ADR 0004: Why 12 Agents Instead of Fewer

## Status
Accepted

## Date
2026-05-05

## Context
One of the core goals of this project is high GenAI weightage with clear role specialization. A smaller agent team would have been easier to implement, but it would also collapse important reasoning responsibilities into broad, blurry roles that are harder to evaluate, harder to trace, and less convincing in production or interview settings.

The platform now performs more than simple root-cause analysis. It reasons across observability context, historical memory, release signals, service dependencies, multimodal artifacts, governance constraints, human feedback, and post-incident communication. That breadth justifies explicit specialists rather than a single overloaded investigator.

## Decision
We keep 12 specialized AutoGen agents instead of merging them into a smaller team.

The current roles are:
- `PlannerAgent`
- `InvestigatorAgent`
- `RunbookAgent`
- `ReleaseCorrelationAgent`
- `CausalAnalystAgent`
- `MultimodalReasoningAgent`
- `ServiceGraphAgent`
- `CriticAgent`
- `RemediationReviewerAgent`
- `FeedbackLearningAgent`
- `PostmortemWriterAgent`
- `CommanderAgent`

## Why These Roles Stay Separate

### PlannerAgent
The planner defines the investigation path and sequencing. It should not be merged with the investigator because planning and evidence interpretation fail in different ways and should be traceable separately.

### InvestigatorAgent
The investigator forms the main technical hypothesis from incident evidence. It should stay separate from the planner and commander so that hypothesis quality can be reviewed independently from coordination and final synthesis.

### RunbookAgent
The runbook agent translates operational procedures and remediation guidance. It should not be merged into the investigator because runbook interpretation is a governance and operations problem, not only a diagnosis problem.

### ReleaseCorrelationAgent
The release-correlation agent tests whether deployments or release changes explain the incident. It stays separate because release reasoning is a distinct failure mode from service-level root-cause analysis.

### CausalAnalystAgent
The causal analyst reasons about dependency propagation and likely causal chains. This is different from basic incident summarization and deserves a separate agent so causal reasoning can be evaluated directly.

### MultimodalReasoningAgent
The multimodal agent interprets attached artifacts such as screenshots and diagrams when available. It should not be folded into the investigator because artifact interpretation often uses different evidence and different failure patterns.

### ServiceGraphAgent
The service-graph agent reasons over the service dependency graph and blast radius. It remains separate because graph reasoning is structurally different from free-text log or metric reasoning.

### CriticAgent
The critic challenges the current analysis and looks for weak evidence, gaps, or overconfidence. It stays separate because self-critique is most useful when it is not merged into the same role that produced the original hypothesis.

### RemediationReviewerAgent
The remediation reviewer focuses on action safety, operational risk, and downstream impact. That role should not be collapsed into the critic because the project needs a dedicated reviewer for action governance, not only reasoning quality.

### FeedbackLearningAgent
The feedback learning agent pulls lessons from operator approvals, overrides, and corrections. It stays separate because learning from human intervention is different from real-time diagnosis.

### PostmortemWriterAgent
The postmortem writer converts the run into a clear retrospective artifact. This should not be the commander’s job because incident communication and incident command are related but not identical responsibilities.

### CommanderAgent
The commander is responsible for the final operational synthesis. It stays separate because the project needs a single agent that consolidates inputs into an actionable decision without also owning every upstream reasoning task.

## Consequences

### Positive
- Stronger specialization and clearer traces for debugging and evaluation.
- Better interview story because each agent has a concrete responsibility.
- Easier to benchmark which reasoning surface is weak when a run underperforms.
- Higher GenAI depth than a shallow multi-agent design.

### Negative
- More prompts, more calls, and more evaluation complexity.
- Higher orchestration and maintenance cost than a smaller team.

## Resulting Architecture Impact
The 12-agent design is intentional. It keeps the system modular, makes evaluation more honest, and aligns with the project's goal of being a serious agentic incident-response platform rather than a lightly themed single-agent workflow.
