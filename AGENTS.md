## Multi-agent efficiency rules

Use multi-agent work when parallel work is genuinely useful.

### Agent spawning
- Only the root agent may spawn subagents.
- Subagents must not spawn additional subagents.
- When spawning agents, keep `fork_context` false unless the delegated task truly requires the full parent conversation history.
- Prefer giving each subagent a precise self-contained task instead of copying parent context.
- Do not repeatedly spawn replacement agents for the same responsibility.

### Agent coordination
- Prefer reusing an existing agent for follow-up work instead of spawning another agent.
- Do not busy-poll agents. Use long waits when waiting for results.
- Do not create repeated review -> fix -> review loops unless a concrete failing test or defect requires another pass.
- Once enough information has been gathered, proceed with the implementation instead of spawning additional agents for confirmation.

### Small changes
- For small localized fixes, use the root agent directly unless delegation provides a clear benefit.
- Do not spawn agents merely because agent capacity is available.