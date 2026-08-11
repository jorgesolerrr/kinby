---
labels: [wayfinder:map]
title: Blueprint for an open-source personal AI teammate
---

# Blueprint for an open-source personal AI teammate

> **Tracker convention (local-markdown fallback):** tickets live in `wayfinder/tickets/*.md`, one file per ticket; the file name (`NN-slug.md`) is the ticket id. Frontmatter holds `status` (open/closed), `assignee` (the claim — empty = unclaimed), `labels` (`wayfinder:<type>`), and `blocked-by` (list of ticket file names — the blocking convention, since markdown has no native dependencies). A ticket is on the **frontier** when `status: open`, `assignee` empty, and every `blocked-by` entry is closed. Resolutions are appended to the ticket body under `## Resolution`, then `status` flips to closed. Research assets live in `research/`.

## Destination

A complete, buildable blueprint for an open-source, self-hosted personal AI teammate (Lindy-style behavior, my design): project **named**, **GitHub repo created** (personal account, Apache-2.0) with the charter/spec docs committed, and every architectural decision **locked** — memory graph design, web-first interface, MCP integration strategy, context/skills model, first-class routines model — so a build effort can start immediately. Decisions, not the build: v0.1 implementation is the next effort, guided by this blueprint.

## Notes

- **Domain:** open-source personal agent framework; single-user, self-hosted, reference deployment = persistent Docker Compose (agent + services) on any always-on box. Docker is also the test bed for agent behavior.
- **Skills to consult per session:** `/grilling` + `/domain-modeling` for decision tickets; `/research` for research tickets; `/prototype` if a ticket turns on look/feel.
- **Settled during charting** (constraints for all tickets, not re-litigated):
  - Language: **Python** (user's background).
  - Model posture: **Claude-first** on the Claude Agent SDK; provider abstraction deferred until a second concrete case exists.
  - Deployment: **single-user self-hosted**, Docker Compose reference target.
  - Memory: **graph-based is the core bet**, user is thinking **Neo4j** (note: Graphiti is Neo4j-backed — compatible). Hybrid (files + graph) split is an open ticket, not settled.
  - Interface order: **web chat first**, then Telegram or Discord (user judged Telegram/WhatsApp integration risky as a starting point; web is also the test surface).
  - Integrations: **architecture only** in this map — mechanism (likely MCP), credential model, priority order **email (Gmail) → GitHub → Linear**. Building connectors is out of scope.
  - Routines: **first-class primitive** — v0.1 mechanism = trigger (cron/event) + prompt + destination; ambition = agent-teachable/proposable routines. Proactivity (teammate behavior) is implemented *as* routines, per Lindy.
  - Autonomy: **per-routine setting, approval-first default** for outward-facing actions (notify/question/review vocabulary).
  - Naming: **invented-word project name** (trademark/search-safe); agent persona name is a per-instance config field.
  - License/home: **Apache-2.0**, user's personal GitHub account.
- **Key asset:** [Landscape research — Lindy + open-source personal agents](../research/landscape-lindy-and-personal-agents.md) (read before any decision ticket; covers Lindy's routines/skills/memory design, OpenHands/Letta/Khoj/LangGraph/Huginn, Graphiti vs mem0 graph-memory evidence, context-engineering practice).

## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [Research: Graphiti/Neo4j graph memory in practice](tickets/03-research-graph-memory.md) — Graphiti fits, but only as an async background consolidation layer (each episode = seconds + cents of LLM calls); Claude extraction supported but second-class; a separate embedding provider is mandatory; Neo4j CE in a 2 GB container suffices; a hybrid file layer must still cover preferences/transcripts/inspection. Unblocks the memory-architecture decision.
- [Research: Claude Agent SDK (Python) as a long-running service](tickets/05-research-claude-agent-sdk.md) — the bet holds: persistent multi-session service is an officially supported pattern, compaction/subagents/hooks/MCP/skills come free, Docker packaging is trivial (no Node), headless runs use `query()` + allowlists. Design API-key-first (subscription auth is ToS-gray for 24/7); we build only scheduler, service shell, notifications, persistence. Unblocks the routines and context/skills decisions.
- [Research: MCP landscape for Gmail, GitHub, Linear + credential management](tickets/08-research-mcp-integrations.md) — GitHub and Linear are solved (official servers, token-as-Bearer headless auth); Gmail is the weak spot (no official send-capable server, no device flow, GCP-project-per-user onboarding → a guided setup wizard is effectively mandatory); poll-first triggers; `.env` + data-volume secrets baseline. Unblocks the integration-architecture decision.
- [Research: web chat interface options for a Python agent backend](tickets/10-research-web-chat-ui.md) — top candidate: own a small React frontend (assistant-ui + FastAPI/SSE, outbox-table push); Chainlit as fastest-throwaway alternative; Open WebUI/Gradio/Streamlit ruled out. Unblocks the web-interface decision.
- [Choose the project name](tickets/01-project-name.md) — **kinby** (invented, *kin* + *by*: kin at your side); PyPI/GitHub/domains verified clean 2026-08-10; runner-up was *sidekin*. Unblocks repo creation.
- [Create the GitHub repository](tickets/02-create-github-repo.md) — live at [github.com/jorgesolerrr/kinby](https://github.com/jorgesolerrr/kinby) (public, Apache-2.0, thesis README); full working directory pushed as first commit, so the map, tickets, and research now live in the repo — commit resolutions as they land.

## Not yet specified

- **Behavior testing / eval harness in Docker** — the user wants agent behavior tested in Docker; what an eval suite for a personal teammate even looks like (scenario replays? routine dry-runs?) sharpens after the SDK, memory, and routines decisions.
- **Teachable routines (the ambition level)** — "I noticed you do this weekly, want me to take it over?": propose/create/edit flow, versioning, trust escalation. Ticketable once the v0.1 routines model is locked.
- **Sleep-time memory consolidation** — a background agent distilling/reorganizing memory during downtime (Letta pattern). Depends on the memory architecture decision.
- **Security model detail** — sandboxing of agent actions, secrets storage in the Compose deployment, blast-radius limits. Sharpens after integration architecture.
- **Persona/identity config** — how the per-instance agent name/personality is expressed. Trivial but unspecifiable until the context model exists.
- **Sub-agent orchestration** — whether/how the agent spawns isolated sub-agents for context economy. Depends on Claude Agent SDK findings.
- **Community/contribution surface** — CONTRIBUTING, plugin/skill sharing story for other self-hosters. After the blueprint takes shape.

## Out of scope

- **Building the v0.1 implementation** — the destination is the blueprint; the build is the next effort.
- **Building individual connectors** (Gmail, GitHub, Linear wiring) — the map decides the integration architecture only.
- **Telegram / Discord / other interfaces** — web chat is the decided first surface; more channels are post-v0.1.
- **Multi-tenancy** — single-user self-hosted is the architecture; serving many users from one deployment is somebody's fork.
- **LLM provider abstraction** — Claude-first now; revisit only when a second concrete provider case exists.
