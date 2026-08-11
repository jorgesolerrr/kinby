---
title: "Research: Claude Agent SDK (Python) as a long-running service"
labels: [wayfinder:research]
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

The project is Claude-first on the Claude Agent SDK (Python). Establish the facts the context/skills and routines tickets wait on: (1) running the SDK as a persistent service rather than a CLI session — session lifecycle, resuming/forking sessions, concurrent sessions from one process; (2) what the SDK gives for free vs what we build: context compaction, subagents, hooks, permission gates, MCP client support, custom tools; (3) Agent Skills support — SKILL.md discovery/loading semantics, whether progressive disclosure is built in; (4) how scheduled/headless invocations work (a routine firing at 9am with no user present) — entry points, streaming output, cost controls; (5) Docker packaging — image size, sandboxing options, known issues running the SDK in containers; (6) rate limits/auth model for a 24/7 personal deployment (API key vs subscription auth).

## Resolution

Full report: [research/claude-agent-sdk-service.md](../../research/claude-agent-sdk-service.md) (claims cited to code.claude.com docs, the GitHub repo/issues, PyPI). Key findings:

1. **Architecture**: Python SDK (v0.2.135, Py 3.10+) spawns a bundled *native* Claude Code binary per session over stdio — no Node.js; Linux wheels ~93 MB, so Docker packaging is trivial (`python:3.12-slim` + pip). Budget ~1 GiB RAM / 1 CPU per concurrent agent.
2. **Persistent service**: `ClaudeSDKClient` holds a session open across turns; `resume`/`fork_session`/`continue_conversation` cover restarts; N concurrent sessions = N subprocesses in one asyncio process (officially supported). Transcripts are local JSONL under `~/.claude/projects/` — on single-host Compose a volume mount suffices; a `SessionStore` adapter exists for external stores.
3. **Free vs build**: auto-compaction (+ PreCompact hook), subagents (programmatic + filesystem), Python hooks (PreToolUse/PostToolUse/Stop/…), `can_use_tool` gates, all MCP transports plus in-process `@tool` servers, structured Pydantic output, `max_turns`/`max_budget_usd`, OTEL. We build only: the scheduler, the service shell, notifications, and non-transcript persistence.
4. **Skills**: filesystem SKILL.md with built-in progressive disclosure (metadata at startup, body on invoke) via `setting_sources`. Caveat: SKILL.md `allowed-tools` frontmatter is ignored by the SDK.
5. **Headless routines**: plain `query()` with `permission_mode="dontAsk"` + explicit tool allowlists is the sanctioned pattern; no built-in wall-clock timeout (wrap in `asyncio.wait_for`); retry watchdog env var recommended for unattended runs.

**Threats to the Claude-first bet (none blocking)**: (a) subscription/OAuth auth is ToS-gray and weekly caps make 24/7 use on Pro/Max fragile — design **API-key-first with budget caps**; (b) the CLI subprocess is a black box: memory growth, version-coupled behavior, root-in-Docker gotchas (`bypassPermissions` refuses to run as root; a stray `DEBUG` env var once broke containers — issue #347); (c) the Python SDK trails the TypeScript one (no SessionStart/SessionEnd hooks, no Workflow tool).
