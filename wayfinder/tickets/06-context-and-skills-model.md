---
title: Decide the context-management and skills model
labels: [wayfinder:grilling]
status: open
assignee:
blocked-by: [05-research-claude-agent-sdk.md, 04-memory-architecture.md]
---

## Question

Decide how the agent keeps its context lean and its capabilities extensible: (1) what is pinned in every context (persona, memory digest, routine awareness) vs fetched on demand from the memory layer; (2) compaction strategy for long-lived conversations (SDK-native vs custom, what survives a compaction); (3) skills format — adopt Anthropic Agent Skills (SKILL.md folders, progressive disclosure by description) as-is, extend, or diverge; how users author and share skills in a self-hosted deployment; (4) sub-agent policy — when work is delegated to isolated contexts and what comes back; (5) how memory retrieval is triggered (agent-initiated tool calls vs automatic pre-fetch per message). Output: context/skills spec section for the blueprint. Blocked on both the SDK facts and the memory retrieval contract.
