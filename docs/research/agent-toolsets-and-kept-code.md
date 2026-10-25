# Agent toolsets and agent-kept code: Claude Code, OpenHands, Goose, Letta, Manus

Wayfinder ticket: jorgesolerrr/kinby #919. Date: 2026-10-25. Feeds #161.
Question: how do other agents give the model web search, web fetch, a browser, MCP servers and code execution, and how do they let the agent keep and grow its own code?

Pins and sources:

- **Claude Code / Agent SDK**: live docs at code.claude.com and platform.claude.com, read 2026-10-25. The pages carry no date, but cite CLI versions up to v2.1.305. No repo commit checked.
- **OpenHands**: SDK `OpenHands/software-agent-sdk` @ `ce5716a3`, app `OpenHands/OpenHands` @ `044ec59e` (v1.33.0), legacy V0 at tag `0.62.0`. SDK below means `https://github.com/OpenHands/software-agent-sdk/blob/ce5716a3c0a955a85bb0cc4dcfaa94d5fefcda50/`.
- **Goose**: `block/goose` now redirects to `aaif-goose/goose`, pinned at `e45707d2` (2026-10-25). Docs at goose-docs.ai, source in `documentation/docs/`. G below means `https://github.com/aaif-goose/goose/blob/e45707d2/`.
- **Letta**: the Python V1 server is archived (last tag `0.16.8`). Current work is the TypeScript "Letta Harness" (formerly Letta Code), `letta-ai/letta-code` @ `1fb6ea4d`.
- **Manus**: Manus blog posts (2025-07-18 and 2026-01-14), Manus docs and help centre. OpenManus (`FoundationAgents/OpenManus` @ `3309bf4e`) stands in for open code.

Hermes Agent and OpenClaw are covered by a sibling ticket.

## Short answer

1. **Nobody gives the model a large native toolset any more.** The core is shell plus file tools everywhere. Web search, fetch and browsing are either one server-side tool (Claude Code's `WebSearch`/`WebFetch`), a skill that teaches the shell (Goose), a sub-agent with MCP servers (OpenHands) or an MCP server (Goose, OpenManus). Goose deleted its own `web_scrape` and `automation_script` tools in 2026-08 because "plain shell/curl does this in one step".
2. **MCP is the universal extension point, and it is user-configured, not agent-configured.** Every system reads a config file or settings the user owns. Claude Code treats `.mcp.json` and its settings as protected paths. Goose's `manage_extensions` can only enable servers already in the user's config. Secrets enter as env vars or headers, never through the model.
3. **The persistent Python REPL is going away.** OpenHands V1 dropped `execute_ipython_cell` for a persistent tmux terminal. Letta's `run_code` is stateless. OpenManus runs each `python_execute` in a fresh process. Persistence lives in the file system: a mounted workspace (OpenHands), a VM that sleeps and wakes (Manus), or an opt-in always-on "Cloud Computer" (Manus).
4. **Agent-kept code is skills almost everywhere.** All five load Agent Skills (`SKILL.md` + `scripts/`) with progressive disclosure, and the agent may write them. Only Letta lets the agent author first-class tools, as "mods": trusted TS/JS files it writes and reloads. Claude Code gets close through in-process SDK MCP servers, but those are written by the host developer, not the agent.
5. **Browser automation with sign-ins is either the user's real browser or saved storage state.** Claude Code and Manus drive the user's own Chrome through an extension (their logins, per-session authorization). OpenHands' browser starts from a fresh profile and gives the agent `browser_get_storage`/`browser_set_storage` over Playwright storage state. Goose and OpenManus leave it to a Playwright or browser-use MCP server.

## Comparison

| | Claude Code / SDK | OpenHands (SDK V1) | Goose | Letta (Harness) | Manus / OpenManus |
|---|---|---|---|---|---|
| Core tools | Bash, Read, Write, Edit, Glob, Grep, Agent, Skill, ToolSearch, Task*, Monitor | terminal (tmux), file_editor, task_tracker, browser set, think, finish | developer: `shell`, `write`, `edit`, `tree`, `read_image` | Bash, Read, Write, Edit, ApplyPatch, Glob, Grep, Memory, Skill, Task* | OpenManus: `python_execute`, `str_replace_editor`, `ask_human`, `terminate` + MCP |
| Web search | `WebSearch`, server-side, titles+URLs, domain filters | `web-researcher` sub-agent with Tavily MCP | built-in `web-search` skill: `uvx ddgs`, Tavily or SearXNG via shell | none native found (UNVERIFIED); V1 had Exa `web_search` | OpenManus `web_search` (Google/Baidu/DDG/Bing fallback) |
| Web fetch | `WebFetch`: fetch, markdown, summary by a second model call | `fetch` MCP in the sub-agent | same skill: `curl \| uvx html2text` | none native found (UNVERIFIED); V1 had `fetch_webpage` | `crawl4ai` tool |
| Browser | none headless; Claude in Chrome drives user's browser | browser-use over CDP, fresh profile, storage get/set tools | none built in; Playwright / Chrome DevTools MCP | bundled `browser-use` skill (CDP or Playwright) | Manus: cloud browser or Browser Operator extension; OpenManus: browser-use MCP |
| Code exec state | shell; API code execution container reusable by id for 30 days | persistent terminal within a conversation; no IPython in V1 | shell | shell; V1 `run_code` was stateless E2B | Manus VM per task, sleeps/wakes, recycled after 7/21 days |
| Gating | modes + allow/ask/deny rules + PreToolUse hooks + OS sandbox | per-call `security_risk` field + analyzer + confirmation policy | modes (auto default) + per-tool levels + optional LLM judge | modes + allow/deny patterns + per-tool approval | OpenManus: `ask_human` only |
| MCP config | `.mcp.json` / `~/.claude.json`, project servers need trust | `mcp_config` dict, `filter_tools_regex` | `config.yaml` extensions, OSV malware check on npx/uvx | `letta mcp` CLI from shell, session-scoped in SDK | Manus: remote HTTPS only, set in Settings |
| Agent-kept code | skills (live reload), memory files | workspace files and skills | skills, recipes, `.goosehints`, memory files | skills in git-backed MemFS, **mods** | files in VM; Cloud Computer for long-lived |
| Agent-authored first-class tools | no (skill or gated config edit) | no | no | **yes, mods** | no |

## Claude Code and the Claude Agent SDK

### Tools

- The tool list ([tools reference](https://code.claude.com/docs/en/tools-reference)) includes Agent, AskUserQuestion, Bash, Read, Write, Edit, Glob, Grep, NotebookEdit, LSP, Monitor, WebFetch, WebSearch, Skill, ToolSearch, Task*, Cron*, plan-mode tools and MCP resource tools. Write, Edit, Bash, WebFetch, WebSearch and Skill need permission; Read and Grep do not. Custom tools come in through MCP only; skills run through the one `Skill` tool and add no tools.
- **WebSearch** uses Anthropic's server-side search ([tools reference](https://code.claude.com/docs/en/tools-reference)). It returns titles and URLs, not page text, runs up to 8 backend searches per call, takes `allowed_domains` or `blocked_domains`, and its backend is not configurable. The docs point to MCP for another provider.
- **WebFetch** fetches a page, converts it to Markdown and answers a prompt "in a separate model call", which the docs call "lossy by design". Up to 100k characters, a 15-minute cache, refuses localhost and dotless hosts, returns cross-host redirects instead of following them. A built-in list of preapproved documentation domains skips the prompt; "don't ask again" writes `WebFetch(domain:x)` into local settings ([tools reference](https://code.claude.com/docs/en/tools-reference)).

### Gating

- Modes: `default`, `acceptEdits`, `plan`, `auto` (a background classifier), `dontAsk` (deny whatever would prompt), `bypassPermissions` ([permission modes](https://code.claude.com/docs/en/permission-modes)). `auto` and `bypassPermissions` take effect only from user or managed settings, never from a project's ([settings](https://code.claude.com/docs/en/settings)).
- Rules go deny, then ask, then allow, first match wins: `Bash(npm run *)`, `WebFetch(domain:example.com)`, `mcp__server__tool` ([permissions](https://code.claude.com/docs/en/permissions)). The docs say Bash rules are text matching and point to the sandbox or a PreToolUse hook for real enforcement. A hook's allow does not override a deny or ask rule.
- **Protected paths** are never auto-approved outside `bypassPermissions`, and `permissions.allow` cannot pre-approve them: `.git`, `.claude` (except worktrees and auto-memory files), `.mcp.json`, `.claude.json`, shell rc files, `.npmrc` and more ([permission modes](https://code.claude.com/docs/en/permission-modes)). So the agent asks before changing its own settings, MCP servers or skills.
- The OS sandbox ([sandboxing](https://code.claude.com/docs/en/sandboxing)) is off by default. It uses Seatbelt on macOS and bubblewrap on Linux, limits writes to the working directory, keeps settings, `.mcp.json` and hooks write-denied, and sends network through a proxy whose host allowlist starts empty.
- Agent SDK ([permissions](https://code.claude.com/docs/en/agent-sdk/permissions)): hooks, then deny, ask, mode, allow, then the `canUseTool` callback. Auto-approved calls never reach `canUseTool`, so a check that must see every call belongs in a PreToolUse hook.

### MCP

- Scopes ([MCP](https://code.claude.com/docs/en/mcp)): local (`~/.claude.json` under the project), project (`.mcp.json`, committed), user. Project servers need interactive approval; in `-p` and SDK runs they load without asking unless blocked.
- Secrets: `${VAR}` and `${VAR:-default}` expand in command, args, env, url and headers. Credential variables such as `ANTHROPIC_API_KEY` expand to empty in a remote server's url and headers. Remote servers use OAuth on 401/403; `-p` and SDK runs cannot finish that flow.
- Tools are named `mcp__<server>__<tool>`. Tool search is on by default for recent models: the model sees names and loads schemas on demand through `ToolSearch`. Output is capped at 25k tokens by default.
- SDK custom tools ([custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)) are `@tool` functions bundled by `create_sdk_mcp_server` and run in-process, named `mcp__{key}__{tool}`. This is how a host adds native tools; the agent does not write them.

### Browser and code execution

- No headless browser. Claude in Chrome ([chrome](https://code.claude.com/docs/en/chrome)) drives the user's real Chrome or Edge through an extension, with the user's logins, pausing on logins and CAPTCHAs. It needs a claude.ai sign-in, not an API key, and a project's settings cannot enable it.
- The API code execution tool ([code execution](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool)) runs a Linux container with 5 GiB RAM and disk, no network and pre-installed libraries only. Passing `container: <id>` reuses one; containers checkpoint after about 5 idle minutes and expire 30 days after creation. `code_execution_20260120` adds REPL state persistence.

### Kept code

- Skills ([skills](https://code.claude.com/docs/en/skills)) follow the Agent Skills standard: user, project, plugin and enterprise locations; descriptions always in context, the body on invocation, `scripts/` "executed, not loaded". Skills live-reload without a restart. Writing one is a protected-path write, so the user approves it.
- Plugins ([plugins](https://code.claude.com/docs/en/plugins)) bundle skills, agents, hooks and MCP servers, installed from a marketplace catalog in a git repo.
- UNVERIFIED: whether the docs recommend a Playwright MCP server. The permissions page uses `mcp__puppeteer__*` only as a rule example.

## OpenHands

The `OpenHands/OpenHands` main branch is now "Agent Canvas", a control centre that runs OpenHands, Claude Code and other agents over ACP ([README @ 044ec59e](https://github.com/OpenHands/OpenHands/blob/044ec59e12e5070406378ba24d568cfbb3a10bf0/README.md)). The agent lives in the software-agent-sdk. CodeActAgent, `execute_ipython_cell` and BrowserGym are V0 only.

### Tools

- `get_default_tools()` returns `TerminalTool`, `FileEditorTool`, `TaskTrackerTool` and `BrowserToolSet`; `FinishTool` and `ThinkTool` attach to every agent; `InvokeSkillTool` attaches when an Agent Skills skill is loaded (SDK `openhands-tools/openhands/tools/preset/default.py`, `openhands-sdk/openhands/sdk/tool/builtins/__init__.py`).
- The terminal is "a persistent terminal session" over tmux, subprocess or PowerShell (SDK `openhands-tools/openhands/tools/terminal/definition.py`).
- V1 has no IPython tool: there is no ipython directory under `openhands-tools/openhands/tools/`. V0 had `execute_ipython_cell` with `%pip` ([V0 ipython.py](https://github.com/OpenHands/OpenHands/blob/0.62.0/openhands/agenthub/codeact_agent/tools/ipython.py)).
- Web search is not a default tool. The built-in `web-researcher` sub-agent gets the browser plus MCP servers `fetch` (`uvx mcp-server-fetch`) and `tavily` (`npx -y tavily-mcp`, `TAVILY_API_KEY: "${TAVILY_API_KEY}"`) (SDK `openhands-tools/openhands/tools/preset/subagents/web_researcher.md`). V0 added Tavily as a default MCP server only when a `tvly-` key was set ([V0 mcp_config.py](https://github.com/OpenHands/OpenHands/blob/0.62.0/openhands/core/config/mcp_config.py)).

### Sandbox and persistence

- The SDK `DockerWorkspace` uses `ghcr.io/openhands/agent-server:latest-python` and removes the container on exit ([docker sandbox](https://docs.openhands.dev/sdk/guides/agent-server/docker-sandbox.md)).
- Agent Canvas runs one container per conversation, stopped after 1200 s idle and restarted on use. `/workspace`, the conversation directory and a per-conversation `HOME` are bind mounts, so files survive container removal ([docker execution](https://docs.openhands.dev/openhands/usage/agent-canvas/backend-setup/docker-execution.md)). Packages installed elsewhere in the container are not described as persisting (UNVERIFIED, probably lost).

### Gating

- With risk prediction on, every non-read-only tool schema gains a `security_risk` field (`LOW`/`MEDIUM`/`HIGH`/`UNKNOWN`) the model fills per call; tools annotated `readOnlyHint` skip it (SDK `openhands-sdk/openhands/sdk/tool/tool.py`, `security/risk.py`).
- Policies are `AlwaysConfirm`, `NeverConfirm` and `ConfirmRisky(threshold=HIGH, confirm_unknown=True)` (SDK `security/confirmation_policy.py`). Analyzers: LLM (default, returns the model's own label), regex patterns, PolicyRail (e.g. fetch piped to exec), an ensemble taking the max, and custom subclasses. The docs' stance is "Confirm, don't block", and `conversation.execute_tool()` bypasses both analyzer and policy ([security](https://docs.openhands.dev/sdk/guides/security.md)).

### MCP and secrets

- `Agent.mcp_config` is a FastMCP-style `{"mcpServers": {...}}` dict with `filter_tools_regex` (SDK `openhands-sdk/openhands/sdk/agent/base.py`). URL servers may use OAuth ([MCP](https://docs.openhands.dev/sdk/guides/mcp.md)). MCP tools keep their raw names, with no server prefix (SDK `openhands-sdk/openhands/sdk/mcp/tool.py`).
- Secrets: the SDK `SecretRegistry` injects secrets referenced in a bash command as env vars and masks their values in output ([secrets](https://docs.openhands.dev/sdk/guides/secrets.md)). App-level custom secrets become env vars for new conversations ([secrets settings](https://docs.openhands.dev/openhands/usage/settings/secrets-settings.md)).

### Browser

- `BrowserToolSet` is built on browser-use over CDP, headless by default, with an `allowed_domains` setting. Each executor gets a fresh `user_data_dir` (`~/.config/browseruse/profiles/<uuid4>`), so there is no persistent profile (SDK `openhands-tools/openhands/tools/browser_use/impl.py`).
- Sign-in state moves through agent-visible tools: `browser_get_storage` and `browser_set_storage`, the latter taking a Playwright-style `storage_state` "to restore a previous session" (SDK `openhands-tools/openhands/tools/browser_use/definition.py`).

### Kept code

- Skills ([skills](https://docs.openhands.dev/overview/skills.md)): `AGENTS.md` always loaded; `SKILL.md` with progressive disclosure; keyword-triggered and path-triggered skills. Project `.agents/skills/`, user `~/.agents/skills/`, a public registry at OpenHands/extensions. Skill scripts "do not grant permissions or install dependencies".
- `.openhands/setup.sh` runs "every time OpenHands begins working with your repository" ([repository customization](https://docs.openhands.dev/openhands/usage/customization/repository.md)).
- No agent-authored tools: tools are Python classes the host registers with `register_tool`. Kept code is workspace files. That a skill the agent wrote loads in the next conversation is UNVERIFIED.

## Goose

### Tools

- Built-in MCP servers are now only `autovisualiser`, `computercontroller`, `memory` and `tutorial` (G `crates/goose-mcp/src/lib.rs`). In-process "platform extensions" enabled by default are `developer`, `analyze`, `apps`, `extensionmanager`, `summon`, `tom`, `skills` and `scheduler`; `todo`, `chatrecall`, `summarize` and `code_execution` are off by default (G `crates/goose/src/agents/platform_extensions/mod.rs`).
- `developer` exposes `write`, `edit`, `shell`, `tree` and `read_image` (G `.../platform_extensions/developer/mod.rs`).
- `computercontroller` lost `web_scrape`, `automation_script` and `cache` on 2026-08-14 ([PR #11198](https://github.com/aaif-goose/goose/pull/11198)). The PR says `automation_script` duplicated `shell` and that shell plus curl does a scrape in one step.
- Web search and fetch are a built-in **skill** (G `crates/goose/src/skills/builtins/web_search.md`): search with `uvx ddgs` (DuckDuckGo, no key), Tavily if `TAVILY_API_KEY` is set, or SearXNG if `SEARXNG_URL` is set; fetch with `curl | uvx html2text` into a temp file, truncated to 15k characters. Its rules: never send cookies, stop at login walls and CAPTCHAs.
- No built-in browser. The docs show how to add Playwright (`npx -y @playwright/mcp@latest`), Chrome DevTools, Puppeteer and Browserbase MCP servers (`documentation/docs/mcp/playwright-mcp.md` and siblings). Persisting sign-ins is left to each server (UNVERIFIED in Goose).

### Gating

- Modes are `auto`, `approve`, `smart_approve` and `chat`; auto is the default ([permissions doc](https://github.com/aaif-goose/goose/blob/e45707d2/documentation/docs/guides/managing-tools/goose-permissions.md)). Per-tool levels `always_allow`, `ask_before`, `never_allow` live in `permission.yaml` (G `crates/goose/src/config/permission.rs`).
- In the inspector (G `crates/goose/src/permission/permission_inspector.rs`), auto returns Allow for every tool, so `never_allow` appears not to apply in auto mode; that reading is not fully confirmed. Smart mode allows MCP `readOnlyHint` tools and asks an LLM judge whether a call is read-only.
- Prompt-injection detection (patterns plus an optional classifier endpoint) is off by default (G `crates/goose/src/security/mod.rs`). "Adversary mode" has an LLM review shell calls against natural-language rules in `~/.config/goose/adversary.md`, and fails open (G `crates/goose/src/security/adversary_inspector.rs`).

### MCP and secrets

- Extension types are `stdio`, `builtin`, `platform` and `streamable_http`; SSE was removed. `available_tools` limits which of an extension's tools are exposed (G `crates/goose/src/agents/extension.rs`). An extension's env silently drops 31 keys such as `PATH`, `LD_PRELOAD`, `PYTHONPATH` and `NODE_OPTIONS`.
- Secrets go to the system keyring, or a plaintext `secrets.yaml` fallback ([config files](https://github.com/aaif-goose/goose/blob/e45707d2/documentation/docs/guides/config-files.md)).
- Before spawning a stdio extension launched by `npx` or `uvx`, Goose checks the package against OSV and refuses on any `MAL-*` advisory; other commands pass (G `crates/goose/src/agents/extension_malware_check.rs`).
- `GOOSE_ALLOWLIST` (a URL to a YAML list of allowed commands) is enforced only in the Desktop UI ([allowlist](https://goose-docs.ai/docs/guides/allowlist); `ui/desktop/src/allowlistFetcher.ts`).
- The model can call `search_available_extensions` and `manage_extensions`, but enable works only for extensions already in the user's config. Those calls need approval in approve and smart modes, not in auto (G `crates/goose/src/agents/platform_extensions/ext_manager.rs`).

### Kept code

- Skills (`SKILL.md`) from `~/.agents/skills/`, `.agents/skills/`, `.goose/skills/` and `.claude/skills/`, loaded on demand ([using skills](https://github.com/aaif-goose/goose/blob/e45707d2/documentation/docs/guides/context-engineering/using-skills.md)).
- Recipes are YAML with instructions, parameters, extensions (which may declare any stdio command), sub-recipes, a JSON response schema and retry checks; a scheduler runs them on a schedule ([recipe reference](https://github.com/aaif-goose/goose/blob/e45707d2/documentation/docs/guides/recipes/recipe-reference.md)).
- The memory extension stores plain files in `.goose/memory/` or `~/.config/goose/memory/`.
- No path for the agent to author and enable an MCP extension. It could edit `config.yaml` through the shell; no review step for that was found (UNVERIFIED).

## Letta

The V1 Python server is archived ([letta README](https://github.com/letta-ai/letta)). Its docs carry `status: legacy` and send readers to skills. V1 is still the clearest case of "tools are source code in a database", so both are covered.

### V1 server (tag 0.16.8)

- Built-ins ([constants.py](https://github.com/letta-ai/letta/blob/0.16.8/letta/constants.py)): memory tools, `conversation_search`, multi-agent messaging, and `run_code`, `web_search`, `fetch_webpage`. `web_search` uses Exa; `run_code` runs in E2B with "no state persistence" between calls ([built-in tools](https://docs.letta.com/v1-sdk/tools/builtin-tools/)).
- A custom tool is a row holding `source_code`, a JSON schema, per-tool `pip_requirements` and `default_requires_approval` ([tool.py](https://github.com/letta-ai/letta/blob/0.16.8/letta/schemas/tool.py)). It runs in a local venv, E2B or Modal sandbox, each with its own pip requirements ([sandbox_config.py](https://github.com/letta-ai/letta/blob/0.16.8/letta/schemas/sandbox_config.py)). Per-agent `secrets` become env vars for tool runs ([agent.py](https://github.com/letta-ai/letta/blob/0.16.8/letta/schemas/agent.py)).
- Tool rules constrain order and count (`run_first`, `exit_loop`, `max_count_per_step`, `requires_approval`) ([tool_rule.py](https://github.com/letta-ai/letta/blob/0.16.8/letta/schemas/tool_rule.py)). An approval pauses the agent with the tool name and arguments; a denial reaches the agent as an error with optional feedback ([human-in-the-loop](https://docs.letta.com/v1-sdk/tools/human-in-the-loop/)).
- MCP servers register on the server and their tools attach to agents by id ([MCP tools](https://docs.letta.com/v1-sdk/tools/mcp-tools/)). That page recommends skills instead, because skills "can be dynamically loaded and unloaded".
- No built-in tool lets the agent create tools. A custom tool could call the injected `client.tools.create` (inference, UNVERIFIED as a documented pattern).

### Letta Harness (letta-code @ 1fb6ea4d)

- Native tools: Bash, Read, Write, Edit, ApplyPatch, Glob, Grep, Memory, Skill, Task*, Monitor, Workflow and more (`src/tools/descriptions/`). No native web search or fetch file was found (UNVERIFIED).
- Skills ([skills](https://docs.letta.com/configuration/skills/)) can be agent-scoped in `$MEMORY_DIR/skills/`, inside the agent's git-backed memory file system, so they travel with the agent. Bundled skills include `browser-use` (CDP or Playwright), `using-mcp-tools`, `creating-skills` and `creating-mods` ([builtin skills](https://github.com/letta-ai/letta-code/tree/main/src/skills/builtin)).
- **Mods** ([mods](https://docs.letta.com/configuration/mods/)) are trusted TS/JS files in `~/.letta/mods/` or `$MEMORY_DIR/mods/`, loaded on `/reload`. A mod can register tools, commands, permission rules and hooks on `tool_start` and `tool_end`. The docs say "Mods are designed to be written and modified by agents." This is the only system here where agent-written code becomes a first-class tool, and it runs trusted, with no sandbox.
- MCP reaches the agent through a `letta mcp` CLI it runs from the shell (`list`, `search`, `schema`, `call`), not as injected tools (`src/skills/builtin/using-mcp-tools/SKILL.md`).
- Permissions ([permissions](https://docs.letta.com/configuration/permissions/)): modes `unrestricted`, `standard`, `acceptEdits`, `strict`, plus allow/deny patterns like `Bash(rm -rf:*)`. Secrets ([secrets](https://docs.letta.com/configuration/secrets/)): the agent writes `$NAME` in a shell command and the harness substitutes the value and scrubs it from output.

## Manus and OpenManus

- **Context engineering** ([Manus blog, 2025-07-18](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)): keep the prompt prefix stable for KV-cache hits; never add or remove tools mid-task, mask their logits instead, with name prefixes (`browser_`, `shell_`) so a group can be masked together; treat the sandbox file system as memory, compressing only in restorable ways (keep the URL, drop the page).
- **Sandbox** ([Manus sandbox, 2026-01-14](https://www.manus.im/blog/manus-sandbox)): one isolated cloud VM per task with root, a browser and tools. It sleeps when idle and is recycled after 7 days (free) or 21 (Pro); artifacts are restored, intermediate code is not. Account data is not reachable from inside the VM.
- **Cloud Computer** ([help, 2026-06-05](https://help.manus.im/en/articles/15392111-what-is-the-cloud-computer)): a separate always-on Ubuntu VM where files, installed tools and running processes persist.
- **Browser Operator** ([docs](https://manus.im/docs/features/browser-operator.md), undated): an extension that drives a dedicated tab in the user's own browser with their logins. The user authorizes each session and can take over by clicking into the tab. The alternative is an isolated cloud browser where the user logs in during the task.
- **MCP** ([custom MCP](https://manus.im/docs/integrations/custom-mcp.md)): remote HTTPS servers only, with API key, bearer or OAuth credentials set in settings. No per-tool approval is documented.
- **OpenManus** ([manus.py](https://github.com/FoundationAgents/OpenManus/blob/main/app/agent/manus.py)): `python_execute`, `str_replace_editor`, `ask_human`, `terminate`, plus MCP tools; the browser arrives as a browser-use MCP server. `python_execute` runs `exec()` in a fresh process with a 5 s timeout, keeping no state and giving no isolation ([python_execute.py](https://github.com/FoundationAgents/OpenManus/blob/main/app/tool/python_execute.py)). The only gate is `ask_human`.
- UNVERIFIED: Manus' reported "fewer than 20 atomic functions, the rest via shell and code" layering. No primary source found.

## What recurs

1. **A small core of shell and file tools, with everything else layered on.** Goose removed tools that the shell already covered; OpenHands moved search into a sub-agent; Letta moved MCP behind a CLI.
2. **Skills are the shared unit of agent-kept code.** All five read `SKILL.md`, most from `.agents/skills/`, with descriptions in context and bodies on demand.
3. **The user owns MCP configuration.** The agent can at most enable what the user listed (Goose) or propose an edit the gate catches (Claude Code).
4. **Secrets stay out of the model's context.** They are expanded into MCP env or headers (Claude Code, OpenHands) or substituted into shell commands and scrubbed from output (OpenHands, Letta).
5. **The model's own claims are not trusted.** OpenHands' default analyzer takes the model's risk label as is, and Goose's smart mode trusts `readOnlyHint`; both offer stronger layers that are opt-in. Claude Code's docs say outright that Bash rules are text matching and the sandbox is the real wall.
6. **Persistence comes from the file system, not a live interpreter.** Mounted workspaces, sleeping VMs and git-backed memory directories outlive the process; REPL state does not.
7. **The tool list is meant to stay stable.** Manus masks tools instead of removing them; Claude Code defers MCP schemas behind `ToolSearch`; Goose advises fewer than 25 tools.

## What kinby could borrow

kinby's gate classifies per tool with allow/ask/deny under mode presets (ADR 0014); its container is the hard wall; instance tools write routines and skills through the gate (ADR 0028); the hub library pins skills to commits (ADR 0091).

- **Web search and fetch as two small native tools, or as a skill over bash.** Copy Claude Code's split. Search returns titles and URLs only, and fetch returns a bounded, converted page and refuses private hosts. Goose's skill route costs no tools and needs `uvx` or `curl` in the image. Native tools fit kinby's gate better. A `web_fetch` can be a read tool with a domain rule, where bash always asks.
- **Browser: drive Playwright in the container, keep sign-ins as storage state.** OpenHands gives the agent `get_storage`/`set_storage` over Playwright storage state; for autonomous jobs a better shape is storage state the user saves once and kinby loads by name, so the agent never handles cookies. Driving the user's own browser, as Claude in Chrome and Manus Browser Operator do, needs a client extension and does not fit a headless instance.
- **MCP: servers in a user-owned instance file, secrets by env reference, and no agent write to that file except through the gate.** Every system lands here. Two cheap additions worth copying: Goose's env blocklist for stdio servers and its OSV check on `npx`/`uvx` packages. Claude Code's `mcp__<server>__<tool>` naming gives `permissions.toml` a natural per-server rule.
- **A persistent Python environment is a mounted directory, not a kernel.** No current system keeps REPL state across turns as its main mechanism. A per-instance venv or `uv` project on the instance volume, used through bash and `uv run`, gives Manus' Cloud Computer persistence without a new tool. Packages the agent installs then survive restarts, and skills' `scripts/` can rely on them.
- **Agent-authored native tools: Letta's mods are the only precedent, and they run trusted.** ADR 0028 deferred this because a tool's `write` flag is self-declared. Nobody else solved that problem. The others route agent-written code through skills, which run under bash and so under the gate. If kinby adds agent-written `tools/*.py`, the precedents suggest the gate should classify them by name in `permissions.toml`, defaulting to ask, rather than trust the flag.
- **Keep the tool list stable within a turn and across turns.** Since tools load per turn from `tools/*.py` (`src/kinby/plugins/registry.py`), every added tool costs a cache write. Manus' masking and Claude Code's deferred schemas are two answers if the list grows.
