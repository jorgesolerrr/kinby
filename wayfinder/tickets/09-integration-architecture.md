---
title: Decide the integration architecture
labels: [wayfinder:grilling]
status: open
assignee:
blocked-by: [08-research-mcp-integrations.md]
---

## Question

Lock the mechanism by which the agent connects to the user's services (building connectors stays out of scope): (1) confirm MCP-server-per-integration as the architecture, or adjust per research findings; (2) the credential/auth model for headless self-hosted Docker — onboarding flow when a user first connects Gmail/GitHub/Linear, storage, refresh; (3) event triggers for routines — push where possible vs polling, and the abstraction that hides the difference from the routine object; (4) the permission boundary — how per-routine autonomy (approval-first) maps onto MCP tool calls (which tools are "outward-facing"); (5) the extension contract — what a community contributor must provide to add integration N+1. Output: integration spec section for the blueprint.
