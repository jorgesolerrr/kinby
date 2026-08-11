# Claude Agent SDK (Python) as a long-running service — research findings

Resolves `wayfinder/tickets/05-research-claude-agent-sdk.md`. Researched 2026-08-10 against primary sources: the official docs (canonical host is now **code.claude.com** — platform.claude.com/docs/en/agent-sdk/* 307-redirects there) and [github.com/anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python). Current PyPI version: **claude-agent-sdk 0.2.135** (released 2026-08-10), Python 3.10+ ([pypi.org/project/claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/)).

**Architecture in one sentence:** the SDK is a Python wrapper that spawns and supervises a bundled native `claude` (Claude Code) CLI subprocess per session and talks to it over stdio; the subprocess owns the shell, the working directory, and JSONL session transcripts on local disk ([hosting guide](https://code.claude.com/docs/en/agent-sdk/hosting)). Every design decision below follows from that.

---

## 1. Running it as a persistent service

Source: [Sessions](https://code.claude.com/docs/en/agent-sdk/sessions), [Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop), [Hosting](https://code.claude.com/docs/en/agent-sdk/hosting), [Python reference](https://code.claude.com/docs/en/agent-sdk/python).

### Two entry points
- **`query(prompt, options)`** — one-shot async iterator. Spawns a subprocess, runs the agentic loop to completion (as many turns as needed), yields messages, exits. On an error result it yields the `ResultMessage` *then raises*, so wrap in `try`.
- **`ClaudeSDKClient`** — stateful client for multi-turn conversations. Use as `async with`, or call `connect()`/`disconnect()` manually. Each `client.query()` automatically continues the same session (no ID juggling). Methods: `query()`, `receive_response()` (iterate until `ResultMessage`), `receive_messages()`, `interrupt()`, `set_permission_mode()`, `set_model()`, `rewind_files()` (needs `enable_file_checkpointing=True`), `get_mcp_status()`, `reconnect_mcp_server()`, `toggle_mcp_server()`, `stop_task()`.

### Session lifecycle, resume, fork
- A session = the accumulated conversation transcript, written automatically to `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` (or `$CLAUDE_CONFIG_DIR/projects/...`). Sessions persist the *conversation*, not the filesystem (use [file checkpointing](https://code.claude.com/docs/en/agent-sdk/file-checkpointing) for file snapshots).
- Capture `session_id` from `ResultMessage.session_id` (present on success *and* error), or from the init `SystemMessage.data`.
- `ClaudeAgentOptions` session fields: `continue_conversation=True` (resume most recent session in cwd — survives process restarts, no ID needed), `resume="<session-id>"` (specific session; since CLI v2.1.223 lookup works cross-directory on the same machine), `fork_session=True` (branch: new ID, original untouched), `session_id` (force a specific UUID).
- Helper functions: `list_sessions()`, `get_session_messages()`, `get_session_info()`, `rename_session()`, `tag_session()` — enough to build a session picker / cleanup logic.
- **Cross-host resume**: transcripts are local files. Either copy the `.jsonl` into `~/.claude/projects/` on the new host, or configure a **`SessionStore` adapter** (`session_store` option) that mirrors transcripts to S3/Redis/Postgres — reference implementations exist ([session storage](https://code.claude.com/docs/en/agent-sdk/session-storage)). Mirroring is best-effort (`mirror_error` system messages on failure); local disk stays authoritative. Docs' honest alternative: "don't rely on session resume — capture the results you need as application state and pass them into a fresh session's prompt."

### Concurrency in one process
- One session = one `claude` subprocess. N concurrent sessions = N subprocesses (multiple `ClaudeSDKClient` instances or parallel `query()` calls in one asyncio loop is the supported pattern; the hosting guide explicitly describes "multiple SDK processes per container"). Give each session its own `cwd` if they need separate filesystems.
- Sizing guidance: **~1 GiB RAM, 5 GiB disk, 1 CPU per agent** as a starting point; `agents per host = (host RAM − overhead) / per-session RAM ceiling` ([hosting](https://code.claude.com/docs/en/agent-sdk/hosting#scaling-and-concurrency)).
- Known limitations table (hosting doc): **no top-level session timeout** (bound with `max_turns`), **memory growth over long sessions** (cap session length or recycle subprocesses), wide parallel-subagent fanouts can hit rate limits, no per-subagent wall-clock deadline (`CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` is only a stall watchdog).

### Streaming
- Default: message-level streaming (`AssistantMessage` per turn, `UserMessage` per tool result, final `ResultMessage`).
- `include_partial_messages=True` adds `StreamEvent` with raw API deltas for live token-level UI ([streaming output](https://code.claude.com/docs/en/agent-sdk/streaming-output)).
- Streaming *input* (ClaudeSDKClient) keeps the session alive after error results, supports `interrupt()`, mid-turn message queueing ([streaming vs single mode](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)).

---

## 2. What the SDK provides for free vs what we build

Everything that powers Claude Code is in the SDK ([overview](https://code.claude.com/docs/en/agent-sdk/overview)).

### Provided
- **Context management/compaction** ([agent loop — context window](https://code.claude.com/docs/en/agent-sdk/agent-loop#the-context-window)): automatic compaction when the context approaches the limit (summarizes older history; emits `SystemMessage` subtype `compact_boundary`); microcompaction offloads bulky tool results to disk before that; prompt caching of stable prefixes is automatic. Customization: summarization instructions in CLAUDE.md (the compactor reads them), `PreCompact` hook (e.g. archive the full transcript), manual `/compact` sent as a prompt string. Persistent rules belong in CLAUDE.md, not the initial prompt, because compaction can drop early instructions.
- **Subagents** ([subagents](https://code.claude.com/docs/en/agent-sdk/subagents)): programmatic (`agents={"name": AgentDefinition(description, prompt, tools, model, skills, maxTurns, background, effort, permissionMode, mcpServers, memory, ...)}`) or filesystem (`.claude/agents/*.md`); programmatic wins on name clash. Context isolation (only the final message returns to parent), parallel execution, background-by-default since CLI v2.1.198, nesting up to 3 levels (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`), resumable via `agentId` + session resume. Built-in `general-purpose` agent always available. Include `"Agent"` in `allowed_tools` to auto-approve delegation.
- **Hooks** ([hooks](https://code.claude.com/docs/en/agent-sdk/hooks)): in-process Python async callbacks registered via `hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[fn])]}`. **Python supports**: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PermissionRequest`, `Notification`. **TypeScript-only** (gap to note): `SessionStart`, `SessionEnd`, `PostCompact`, `StopFailure`, `MessageDisplay`, `TaskCreated/Completed`, `ConfigChange`, `FileChanged`, and more. Hooks can deny/allow/modify tool input (`updatedInput`), replace tool output (`updatedToolOutput`), inject context, show `systemMessage`, or stop the run (`continue_`); `deny > defer > ask > allow` precedence; async fire-and-forget mode (`{"async_": True}`) for logging side effects. Hooks run in your process and don't consume context.
- **Permission gates** ([permissions](https://code.claude.com/docs/en/agent-sdk/permissions), [agent loop — permission mode](https://code.claude.com/docs/en/agent-sdk/agent-loop#permission-mode)): `allowed_tools` (auto-approve, supports scoped rules like `"Bash(npm *)"` and MCP wildcards `mcp__server__*`), `disallowed_tools`, `permission_mode` ∈ `default` | `acceptEdits` | `plan` | `dontAsk` (hard-deny anything unlisted — the right mode for headless routines) | `auto` (model classifier) | `bypassPermissions` (**cannot be used running as root on Unix** — matters for Docker), plus the **`can_use_tool` async callback** (`(tool_name, input, context) -> PermissionResultAllow(updated_input=...) | PermissionResultDeny(message=..., interrupt=...)`) invoked only when evaluation reaches a prompt. Mode switchable mid-session via `set_permission_mode()`.
- **MCP client** ([mcp](https://code.claude.com/docs/en/agent-sdk/mcp)): all transports — **stdio** (`{"command": ..., "args": ..., "env": ...}`), **HTTP** (`{"type": "http", "url": ..., "headers": ...}`), **SSE**, plus config via project `.mcp.json`. Tool names `mcp__<server>__<tool>`; must be allowed explicitly (`acceptEdits` does NOT auto-approve MCP tools). Built-in **tool search** defers MCP tool schemas from context and loads on demand (on by default). Tool results >25k tokens are spilled to a file automatically (`MAX_MCP_OUTPUT_TOKENS`). OAuth for remote MCP servers is *not* interactive — you run the OAuth flow yourself and pass the bearer token in `headers`; servers report `needs-auth` status. Runtime server management: `get_mcp_status()`, `reconnect_mcp_server()`, `toggle_mcp_server()`.
- **Custom in-process tools** ([custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools), [README](https://github.com/anthropics/claude-agent-sdk-python)): `@tool("name", "desc", {"arg": str})` + `create_sdk_mcp_server(name=..., tools=[...])` → pass in `mcp_servers`. Runs in the Python process (no subprocess/IPC), mixes freely with external MCP servers. Custom tools run sequentially unless annotated `readOnlyHint`.
- **Structured output** ([structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)): `output_format={"type": "json_schema", "schema": PydanticModel.model_json_schema()}` (draft-07); validated with retries; result in `ResultMessage.structured_output`; failure subtype `error_max_structured_output_retries`. Works after arbitrary multi-turn tool use — ideal for routines that must emit machine-readable results.
- **Cost controls**: `max_turns`, `max_budget_usd` (covers subagent spend since CLI v2.1.217), `effort` (`low`→`max`), `model`/`fallback_model`, `thinking` budget. `ResultMessage` carries `total_cost_usd`, `usage` (incl. cache tokens), `num_turns` on every result subtype ([cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)).
- **Observability**: OTEL traces/metrics/logs via env vars (`CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_*`); prompt/tool contents excluded by default ([observability](https://code.claude.com/docs/en/agent-sdk/observability)).
- **Also free**: CLAUDE.md memory loading via `setting_sources`, auto memory (`~/.claude/projects/<project>/memory/`, disable with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`), slash commands, plugins, file checkpointing/rewind, session transcripts, built-in tools (Read/Edit/Write/Glob/Grep/Bash/WebSearch/WebFetch/Agent/Skill/AskUserQuestion/TaskCreate...), automatic retries (`CLAUDE_CODE_MAX_RETRIES`, `CLAUDE_CODE_RETRY_WATCHDOG=1` recommended for unattended runs).

### What we build ourselves
- The **scheduler/routine trigger** (cron/APScheduler firing `query()` calls) — the SDK has no scheduling.
- **Durable memory beyond transcripts**: `SessionStore` mirrors transcripts only; CLAUDE.md files, working-directory artifacts, and any knowledge-base need our own persistence (volume mounts in our single-host Docker Compose case — easy).
- **The service shell**: HTTP/WS endpoint or message-bus consumer, session→client mapping, subprocess recycling policy, health checks.
- **Notification/delivery channels** (Telegram/email/etc.) — via hooks, custom tools, or post-processing of results.
- **Approval UX** for `can_use_tool` when the user *is* around (the callback is ours to wire to a phone/chat prompt).
- **Python hook gaps**: no `SessionStart`/`SessionEnd` hooks — emulate around `connect()`/`disconnect()` in our own code.

---

## 3. Agent Skills support

Source: [Agent Skills in the SDK](https://code.claude.com/docs/en/agent-sdk/skills), [Claude Code skills](https://code.claude.com/docs/en/skills), [plugins in the SDK](https://code.claude.com/docs/en/agent-sdk/plugins).

- Skills are **filesystem-only artifacts**: `<dir>/.claude/skills/<name>/SKILL.md` (YAML frontmatter `name`/`description` + Markdown body, optional supporting files). **No programmatic registration API** (unlike subagents).
- **Discovery**: governed by `setting_sources` — `"user"` → `~/.claude/skills/`, `"project"` → `<cwd>/.claude/skills/` and any parent up to the repo root. Default `query()` options load user+project, so skills work out of the box; if you set `setting_sources` explicitly you must include `"user"`/`"project"` or skills silently vanish (top troubleshooting item).
- **Progressive disclosure is built in**: skill *metadata* (name + description) is discovered at startup and injected cheaply; the *full SKILL.md content* loads only when Claude invokes the skill via the `Skill` tool. Model-invoked based on the `description` field matching the request.
- **Filtering**: `skills="all"` | `["pdf", "docx"]` | `[]` on options. Setting `skills` auto-adds the Skill tool to `allowedTools`; it is a context filter, not a sandbox (unlisted skill files are still readable via Read/Bash). Init `SystemMessage` carries a `skills` array to verify loading.
- **Caveat**: the `allowed-tools` frontmatter field in SKILL.md **does not apply through the SDK** — tool restriction must come from the main `allowed_tools`/permission config.
- **Plugins**: package skills + agents + hooks + MCP servers and load by local path via the `plugins` option — an alternative to setting_sources for loading skills from a pinned directory.
- Subagents can **preload** named skills into context at startup via `AgentDefinition.skills`.

Implication for us: our skills library is just a directory of SKILL.md folders mounted into the container at `~/.claude/skills/` or `<workdir>/.claude/skills/` — versionable in git, hot-editable, and identical semantics to Claude Code.

---

## 4. Headless / scheduled invocation (the 9am routine)

- **Entry point**: plain `query()` in a Python coroutine is the sanctioned headless pattern; the [hosting guide's "ephemeral sessions"](https://code.claude.com/docs/en/agent-sdk/hosting#choose-a-session-pattern) shows exactly this (prompt from env, `max_turns=20`, iterate to completion). Our routine runner can be one asyncio task per firing inside the always-on service process — no CLI, no TTY, no user needed. Permission prompts never block if we use `permission_mode="dontAsk"` (deny anything not pre-approved) + explicit `allowed_tools`, or supply a `can_use_tool` that auto-decides.
- **Continuity options per routine**: fresh session (cheapest, pass needed state in the prompt), `resume=` a standing session ID, or `continue_conversation=True` per-cwd. `fork_session=True` lets a routine branch off a main conversation without polluting it.
- **Cost controls**: `max_turns`, `max_budget_usd` (result subtypes `error_max_turns` / `error_max_budget_usd`; resumable with higher limits), `effort="low|medium"` for cheap routines, model pinning (`model="claude-haiku-..."` for trivial ones), `thinking` budget. Check `ResultMessage.total_cost_usd` after every run and aggregate.
- **Structured output** (`output_format` + Pydantic) makes routine results machine-consumable — validated JSON after arbitrary tool use.
- **Unattended reliability knobs** ([Python reference env vars](https://code.claude.com/docs/en/agent-sdk/python)): `API_TIMEOUT_MS`, `CLAUDE_CODE_MAX_RETRIES`, `CLAUDE_CODE_RETRY_WATCHDOG=1` (retry capacity errors indefinitely — docs recommend for unattended runs), `CLAUDE_ENABLE_STREAM_WATCHDOG=1` + `CLAUDE_STREAM_IDLE_TIMEOUT_MS`. There is **no built-in wall-clock timeout** — wrap the routine in `asyncio.wait_for()` ourselves.
- Progress streaming is optional; for background jobs just iterate the messages and log, or persist `AssistantMessage`s for a morning digest.

---

## 5. Docker packaging

Source: [Hosting](https://code.claude.com/docs/en/agent-sdk/hosting), [Quickstart install note](https://code.claude.com/docs/en/agent-sdk/quickstart), [Secure deployment](https://code.claude.com/docs/en/agent-sdk/secure-deployment), [hosting cookbook (Dockerfiles/K8s)](https://github.com/anthropics/claude-cookbooks/tree/main/claude_agent_sdk/hosting).

- **No Node.js required**: the Python SDK's platform wheels **bundle a native Claude Code binary** (Linux glibc 2.17+, macOS, Windows x86-64). A stock `python:3.12-slim` image + `pip install claude-agent-sdk` works. Exception: if pip falls back to the source distribution (e.g. ARM64 Windows; also watch musl/Alpine — glibc wheels won't match, so use Debian-based images), no binary is bundled and you must install Claude Code natively (found on PATH, or via `cli_path`). The bundled CLI is pinned to the SDK version — upgrading the pip package upgrades the agent runtime.
- **Image size**: wheels are **82–93 MB** (Linux x86-64: 93.4 MB) ([PyPI](https://pypi.org/project/claude-agent-sdk/)); expect roughly +250–350 MB over a slim Python base once installed. Trivial for a self-hosted 24/7 box.
- **Resources**: 1 GiB RAM / 5 GiB disk / 1 CPU per concurrent agent as a floor; memory grows with session length — plan to recycle subprocesses. Community guidance suggests ≥4 GB for comfort ([claudecodeguides.com](https://claudecodeguides.com/claude-code-with-docker-containers-guide/)).
- **Network**: outbound HTTPS to `api.anthropic.com` (+ MCP endpoints). Inbound is our own HTTP/WS port; the subprocess never listens on the network. Secure-deployment doc recommends an egress proxy with domain allowlists for hardening — overkill for a personal box but the option exists.
- **Sandboxing**: the container itself is the recommended sandbox ("Reserve `bypassPermissions` for CI, containers, or other isolated environments"). Deeper options (gVisor, Firecracker) and sandbox-as-a-service providers (Modal, E2B, Fly Machines, Daytona...) are documented but unnecessary for single-user Compose. **Gotcha: `bypassPermissions` cannot be used when running as root on Unix** — run the container as a non-root user if we want that mode (or better, use `dontAsk` + explicit allowlists and avoid bypass entirely).
- **State**: mount volumes for `~/.claude` (or set `CLAUDE_CONFIG_DIR`) so transcripts/config/memory survive restarts — this substitutes for `SessionStore` on a single host. `CLAUDE.md`, skills, agents dirs mount the same way.
- **Known container issues**:
  - [claude-agent-sdk-python#347](https://github.com/anthropics/claude-agent-sdk-python/issues/347): SDK broke in Docker when a generic `DEBUG` env var was set (CLI 2.0.43+) — keep the subprocess env clean, don't leak app env vars (use the `env` option deliberately).
  - OAuth/browser login flows fail in headless containers ([claude-code#34917](https://github.com/anthropics/claude-code/issues/34917)) and credential persistence across container runs has been buggy ([claude-code#22066](https://github.com/anthropics/claude-code/issues/22066)) — use env-var auth, never interactive login, in containers.
  - Running as root from `/` makes the CLI scan the whole filesystem (memory spikes/hangs) — always set a real `WORKDIR`/`cwd`.

---

## 6. Auth and rate limits for a 24/7 personal deployment

- **Officially supported for the SDK**: `ANTHROPIC_API_KEY` (Claude Console), or third-party platforms — Bedrock (`CLAUDE_CODE_USE_BEDROCK=1`), Vertex (`CLAUDE_CODE_USE_VERTEX=1`), Claude-on-AWS (`CLAUDE_CODE_USE_ANTHROPIC_AWS=1`), Microsoft Foundry (`CLAUDE_CODE_USE_FOUNDRY=1`) ([quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart)). The SDK does not read `.env` files itself. `ANTHROPIC_BASE_URL` can route through a key-injecting proxy so the key never enters the container.
- **Subscription (claude.ai Pro/Max) auth**: the docs state — in both the overview and quickstart — "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK" ([overview](https://code.claude.com/docs/en/agent-sdk/overview)). This clause targets *third-party developers offering* subscription auth to others; a single-user personal agent using your own subscription is the gray zone. Mechanically it works: `claude setup-token` mints a long-lived `sk-ant-oat01-...` token consumed via `CLAUDE_CODE_OAUTH_TOKEN`, and community images exist specifically for running the SDK in Docker on Pro/Max plans ([cabinlab/claude-code-sdk-docker](https://github.com/cabinlab/claude-code-sdk-docker), [its auth doc](https://github.com/cabinlab/claude-code-sdk-docker/blob/main/docs/AUTHENTICATION.md)). Practical downsides beyond ToS ambiguity: subscription usage limits are 5-hour rolling windows plus weekly caps shared with interactive Claude Code/claude.ai use — a 24/7 agent can exhaust them and then *everything* is throttled; tokens have expired/failed to persist in containers (issues above); Anthropic could tighten enforcement at any time.
- **API key profile**: standard [API rate limits](https://platform.claude.com/docs/en/api/rate-limits) by usage tier (RPM/ITPM/OTPM; tiers rise with spend) — far above a single personal agent's needs except during wide parallel-subagent fanouts (the hosting doc's own warning). Cost is metered per token; the SDK's prompt caching, `effort`, model routing (Haiku for cheap routines), `max_budget_usd`, and per-result `total_cost_usd` are the containment tools. Docs note token cost dominates infra cost "by an order of magnitude"; a long agent session can cost dollars.
- **Recommendation**: build API-key-first (deterministic, ToS-clean, per-routine budget caps), keep `CLAUDE_CODE_OAUTH_TOKEN` as a documented user-supplied alternative the user can opt into on their own subscription, and make the auth source a single env-var switch.

---

## Verdict on the "Claude-first on the Agent SDK" bet

**The bet holds.** The SDK is explicitly positioned and documented for exactly this shape of system (the hosting guide's "long-running sessions" and "hybrid sessions" patterns are our architecture), and the free-stuff list is enormous: agent loop, compaction, subagents, hooks, permission gates, MCP (stdio+HTTP), in-process tools, skills with progressive disclosure, structured outputs, session resume/fork, cost caps, OTEL. What we build is genuinely thin: scheduler, service shell, notifications, and volume-backed persistence.

**Risks / threats to the bet:**
1. **Subscription auth is contractually shaky** for anything beyond strictly-personal use, and subscription weekly caps make 24/7 operation on Pro/Max fragile → design API-key-first (cost is the trade).
2. **Runtime is a black-box subprocess**: session state is CLI-internal JSONL, no top-level timeout, documented memory growth, and behavior shifts with bundled-CLI versions (several "before v2.1.x" caveats in the docs) → pin SDK versions, recycle subprocesses, wrap runs in our own timeouts.
3. **Python SDK trails TypeScript**: no `SessionStart`/`SessionEnd`/`PostCompact` hooks, no `Workflow` tool, no `persistSession:false` — nothing blocking, but check the [Python changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md) before assuming TS-doc features exist in Python.
4. **Docs churn**: canonical docs moved to code.claude.com; some community/blog knowledge is stale.
