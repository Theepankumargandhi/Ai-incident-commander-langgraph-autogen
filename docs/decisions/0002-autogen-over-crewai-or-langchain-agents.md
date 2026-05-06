# ADR 0002: AutoGen Over CrewAI or LangChain Agents

## Status
Accepted

## Date
2026-05-05

## Context
This platform needs a true multi-agent investigation layer. The system does not rely on a single agent with a few tools. It uses specialized roles such as planning, root-cause investigation, runbook interpretation, release correlation, multimodal reasoning, knowledge-graph reasoning, criticism, remediation review, feedback learning, postmortem writing, and final command synthesis.

Because the project already uses LangGraph as the outer workflow runtime, the inner reasoning layer needed to be strongly agent-centric rather than another orchestration framework. We wanted explicit collaboration among agents, not just sequential task execution dressed up as teamwork.

## Decision
We use AutoGen for the investigation team instead of CrewAI or LangChain's higher-level agent abstractions.

AutoGen is the best fit for this project because it models agent-to-agent collaboration clearly, supports role specialization well, and makes the investigation layer feel like a real reasoning team rather than a single agent with tool wrappers.

## Alternatives Considered

### CrewAI
CrewAI has a friendly task-role model and is productive for lighter multi-agent workflows.

We did not choose it because this project needs deeper message-oriented specialization, richer review patterns, and a more explicit distinction between workflow orchestration and agent collaboration. CrewAI would have been workable, but it would have pulled the design toward task delegation more than ongoing investigation dialogue.

### LangChain Agents
LangChain agents are useful when the main need is tool calling around a single decision loop, and the broader LangChain ecosystem is already familiar to many engineers.

We did not choose them for this layer because the platform already uses LangGraph for stateful orchestration. Reusing LangChain agents for the inner team would have blurred the line between workflow runtime and reasoning runtime. AutoGen gives us a cleaner mental split: LangGraph controls the incident process, AutoGen handles the collaborative investigation.

## Consequences

### Positive
- The investigation layer feels intentionally multi-agent rather than nominally multi-agent.
- Specialized roles are easier to justify and trace.
- The separation between orchestration and reasoning is cleaner.
- The architecture has stronger GenAI depth for both demos and interviews.

### Negative
- AutoGen adds another framework to learn and maintain.
- More agents means more prompts, more tuning, and more evaluation surface area.

## Resulting Architecture Impact
AutoGen is used as the reasoning engine inside the LangGraph-controlled workflow. This allows the project to keep workflow durability and operational control in one layer while preserving high-weightage multi-agent reasoning in another.
