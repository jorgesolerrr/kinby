---
title: "Research: MCP landscape for Gmail, GitHub, Linear + credential management"
labels: [wayfinder:research]
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

Integration architecture (decided direction: MCP; priority email/Gmail → GitHub → Linear) needs ground truth: (1) for each of Gmail, GitHub, Linear — the best available MCP servers (official? maintained? local vs remote), their auth flows (OAuth device flow? PAT? app passwords), and tool coverage (can the Gmail one send/draft/label, not just read?); (2) how OAuth is realistically handled in a headless self-hosted Docker deployment — token acquisition, refresh, storage; (3) event-driven triggers — do any of these expose push (Gmail pub/sub, GitHub webhooks, Linear webhooks) usable by a self-hosted box without a public IP (polling fallbacks, tunneling options); (4) secrets storage patterns for Docker Compose deployments (env files, mounted volumes, SOPS, etc.); (5) whether an MCP-server-per-integration architecture composes sanely in the Claude Agent SDK (many servers, tool-name collisions, startup cost).

## Resolution

Full report: [research/mcp-integrations-credentials.md](../../research/mcp-integrations-credentials.md). Key findings:

1. **GitHub — solved.** Official `github/github-mcp-server`: remote hosted (`api.githubcopilot.com/mcp/`) accepting a fine-grained PAT as Bearer header (the exact example in the Agent SDK docs), or local Docker stdio. Toolsets + read-only mode control scope and context bloat. Very actively maintained.
2. **Linear — solved.** Official hosted server (`mcp.linear.app/mcp`, streamable HTTP, readonly variant). OAuth 2.1 primary, but it **accepts a Linear personal API key as Bearer** — headless trivial.
3. **Gmail — the weak spot.** No GA official server; Google's preview server is gated and **cannot send** (draft-only); the long-time community default (GongRzhe) was archived March 2026. Best pick: `taylorwilsdon/google_workspace_mcp` (Python, active, full send/draft/label/search); a thin custom in-process SDK server over the Gmail API is a defensible alternative.
4. **Headless OAuth:** Google device flow does **not** cover Gmail scopes. Realistic pattern: one-time browser flow on any machine → copy refresh token to a mounted volume; auto-refresh thereafter. Trap: consent screen in "Testing" revokes refresh tokens after 7 days — users must publish to Production. The SDK never runs OAuth itself; tokens supplied via `headers`.
5. **Triggers without a public IP:** poll-first default. Gmail uniquely offers real push with no inbound port (Pub/Sub pull/StreamingPull, watch renewal ≤7 days). GitHub: Events API polling with ETag. Linear: GraphQL polling. Tunnels stay opt-in.
6. **Secrets:** community baseline is `.env` + gitignore + `.env.example`; mutable OAuth tokens in a data volume; Compose `secrets:` and SOPS+age as documented upgrades.
7. **SDK composition:** `mcp_servers` dict composes cleanly; `mcp__<server>__<tool>` namespacing prevents collisions; MCP tool search on by default mitigates context bloat. Recommended shape: **remote HTTP for GitHub/Linear + one local Gmail server**.

**Hardest problem — Gmail, but not the tokens: the compound onboarding friction.** Every self-hosting user must create a GCP project + OAuth client + consent screen, publish to Production, and do a one-time browser dance. A guided setup wizard is effectively mandatory for Gmail onboarding.
