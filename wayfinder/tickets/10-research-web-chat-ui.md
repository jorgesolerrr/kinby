---
title: "Research: web chat interface options for a Python agent backend"
labels: [wayfinder:research]
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

Web chat is the decided first interface (also the test surface). Survey the realistic options for a Python, Claude-Agent-SDK backend: (1) batteries-included chat UIs (Chainlit, Gradio, Streamlit chat, Open WebUI as a frontend) — streaming support, approval-button flows (needed for approval-first routines), notification/async-message support (a routine posting results while the user is away), customizability ceilings, maintenance health; (2) protocol-first options (AG-UI, assistant-ui, Vercel AI SDK UI against a FastAPI backend) — cost of owning a small frontend; (3) what Lindy-class products' web UIs actually need beyond chat: an approvals inbox, routine management screens, memory browser — which options can grow those; (4) websocket/SSE patterns for pushing agent-initiated messages. Recommend 2-3 candidates with trade-offs; the decision ticket (possibly with a /prototype) picks.

## Resolution

Full report: [research/web-chat-ui-options.md](../../research/web-chat-ui-options.md). Ranked recommendation:

1. **Own a small React frontend: assistant-ui + FastAPI/SSE** — assistant-ui (MIT, ~11.5k stars, active) treats streaming, markdown, and inline tool-call approval as first-class; backend speaks the Vercel AI SDK data-stream protocol (plain SSE, official Python/FastAPI helpers). Agent-initiated push done right = an outbox table + one long-lived SSE events stream — "away" delivery is a persistence problem, not a transport problem. Approvals inbox / routines / memory browser are just extra routes; the web UI becomes the first client of the channel abstraction. Cost: ~1–2 weeks to chat parity, own trivial single-user auth.
2. **Chainlit** — fastest v1 (streaming, blocking `AskActionMessage` approval buttons, built-in auth, pure Python, under a day), but push only reaches live sessions, the shell can't grow beyond chat, and it's community-maintained since May 2025 (stewardship risk). Viable as a planned-throwaway week-1 test surface if the backend keeps a UI-agnostic event boundary.
3. **Open WebUI** — not recommended: agent sits behind an OpenAI-compatible request/response hole, no mid-chat approval primitive for external backends, branding clause in license.
- Gradio/Streamlit ruled out (no server-initiated push, weak approval ergonomics). AG-UI protocol: too young to adopt, but its event taxonomy (streaming, tool calls, HITL interrupts, state sync) is worth imitating internally.
- Product evidence (Lindy task-view approvals, Khoj automations + knowledge browser, LibreChat admin/memory) confirms the UI must grow ~5 surfaces beyond chat — which drove the ranking.
