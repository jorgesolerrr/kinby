# Base toolsets: Hermes Agent and OpenClaw

Wayfinder ticket: jorgesolerrr/kinby #918, feeding #161. Date: 2026-10-25.

Question: how do Hermes Agent and OpenClaw give their agent a base toolset for acting in the world? For web search, web fetch, browser automation, MCP servers and code execution (including a persistent environment for the agent's own scripts and packages): which tools the model sees, how each is gated in interactive and unattended runs, where secrets and sign-ins live, whether it ships on by default, and how it is isolated. Then: what kinby could borrow.

This builds on [Research: self-extending personal agents](https://github.com/jorgesolerrr/kinby/blob/research/self-extending-agents/docs/research/self-extending-agents.md) (#518), which covered skills, schedules, sub-agents, plugins and agent-created agents. That note found neither project lets the agent author native tools. This one does not repeat it.

Sources are the projects' own repositories, read at fixed commits:

- Hermes Agent: [`NousResearch/hermes-agent@1f91da2`][h-root], docs under `website/docs/`.
- OpenClaw: [`openclaw/openclaw@98dcd36`][o-root], docs under `docs/`.

Almost every claim rests on the projects' own docs. One Hermes claim was checked against code and is marked so. A claim with no primary source is marked **unverified**.

## Short answer

1. **Both ship web search and web fetch on by default, as two separate tools.** Hermes calls them `web_search` and `web_extract`, OpenClaw `web_search` and `web_fetch`. Search goes through a pluggable provider with a key in the env file or config. Fetch blocks private addresses and re-checks every redirect. They differ on keyless search: Hermes falls back to a ring of free vendor tiers when no key is set, while OpenClaw never sends a query to a key-free provider unless the user picks one.
2. **Each has one browser tool over CDP, with a different shape.** Hermes's `browser_exec` runs model-written Python against Chromium and is offered only to sessions that also have the terminal. OpenClaw's `browser` takes an action enum (`snapshot`, `act`, `navigate`, …) and the model clicks by snapshot refs. Both default to a throwaway, agent-only profile. Signed-in browsing is an opt-in persistent profile that the human signs into by hand. Hermes adds a vault that fills passwords into the page without the model seeing them, and it refuses card fills in headless runs.
3. **MCP is config-defined in both, with per-server tool filters.** Hermes adds a reviewed catalog, a `trust: untrusted` tier where every call without `readOnlyHint` asks, and per-chat spend limits that also apply in cron. OpenClaw runs MCP tools through its normal tool policy. Per-call approval is documented only for Codex runs and plugin hooks.
4. **Only Hermes has a persistent local Python environment.** Its Docker terminal backend is one long-lived container whose `/workspace` and `/root` survive restarts. `execute_code` keeps a Python kernel per session with secrets scrubbed from its environment, and scripts call Hermes tools over RPC. OpenClaw's `code_execution` is remote Python on xAI's servers. Local code goes through `exec`, in an optional sandbox that by default has no network and a read-only root.
5. **Unattended runs fail closed in both.** Hermes has `cron_mode`, `unattended_mode` and `single_query_mode`, all `deny` by default. OpenClaw sends an automation's approval to a connected approval client, denies it when none is connected, and turns **Always allow** into a standing grant bound to that exact job, command, directory and environment. Editing the job revokes the grant.

## Hermes Agent

### What the model sees

Tools come in toolsets, enabled per platform with `hermes tools` ([H:toolsets-reference]). The default `hermes-cli` toolset, which cron and every messaging platform reuse, includes `web`, `terminal`, `file`, `browser`, `code_execution`, `delegation`, `cronjob` and more. `hermes-webhook` is the exception: webhook runs get only `web_search`, `web_extract`, `vision_analyze` and `clarify` (same page, "Platform Toolsets").

| Capability | Tools | Shape |
| --- | --- | --- |
| Web search | `web_search` | query and limit, ranked results ([H:web-search]) |
| Web fetch | `web_extract` | one or more URLs to markdown, with a `char_limit` ([H:web-search]) |
| Browser | `browser_exec`, plus `browser_cdp`, `browser_dialog`, `browser_vault` | `browser_exec(code, timeout_s, session)` runs Python with helpers such as `new_tab`, `js`, `fill_input`, `capture_screenshot` ([H:browser]) |
| MCP | `mcp_<server>_<tool>` per server tool | one dynamic `mcp-<server>` toolset per server ([H:mcp], "How Hermes registers MCP tools") |
| Code | `terminal`, `process`, `execute_code` | shell commands; a Python script that imports `hermes_tools` ([H:tools], [H:code-execution]) |

Search and extract share one backend choice, with Firecrawl as the default. Tavily, Exa, Parallel, Perplexity, SearXNG, Brave and DDGS are the alternatives, and search and extract can use different providers. With no key at all, requests rotate across the free tiers of Exa, Parallel, Firecrawl and Keenable. `web.keyless_fallback: false` turns that off ([H:web-search], "Backends"). `web_extract` returns up to 15,000 characters. Longer pages come back as head and tail, with the full text saved to disk and a `read_file` hint (same page, "How `web_extract` handles long pages").

`browser_exec` is offered only to sessions that also have the `terminal` toolset, because it executes model-written Python on the host ([H:browser], "How `browser_exec` works"). `browser` is a separate toolset from `web`, so disabling browsing leaves search in place ([H:toolsets-reference]).

### Gating

- **URL tools.** An SSRF guard covers web, browser and vision fetches. It blocks private, loopback, link-local, CGNAT and cloud-metadata addresses, fails closed on DNS errors, and re-validates every redirect. `security.allow_private_urls` is the opt-out. `security.website_blocklist` adds a domain denylist that all URL tools enforce. ([H:security], "Website Access Policy", "SSRF Protection")
- **Shell.** Dangerous patterns ask under `approvals.mode` (`smart` uses an auxiliary LLM, `manual`, `off`). A hardline blocklist and user `approvals.deny` globs apply even in YOLO mode ([H:security], "Dangerous Command Approval"). The prompts are skipped on isolated backends. The docs list Docker among them. The code skips them for Docker only when no host path is bind-mounted, because "`rm -rf /workspace` then reaches host files" ([`tools/approval.py`][h-approval], checked against code).
- **`execute_code`.** A script can call `subprocess` directly, past the shell patterns, so gateway and messaging sessions approve the whole script before it runs. A local CLI turn the user typed gets no whole-script prompt, and only its `terminal()` calls ask ([H:security], "`execute_code` Scripts").
- **MCP.** `trust: untrusted` makes every call to a tool without `readOnlyHint: true` ask, with Allow Once, Session or Always. Unrecognized trust values count as untrusted. The default is `full`. ([H:mcp-config])
- **Unattended runs.** `cron_mode`, `single_query_mode` and `unattended_mode` decide a dangerous command or script at once, all `deny` by default ([H:security], "Approval Modes"). A cron job can narrow its tools with `enabled_toolsets` ([H:cron], "Toolsets available to cron jobs"). A per-chat MCP `spend_limit` still asks past the limit on a `trust: full` server, and in cron, where nobody can answer, the call is refused ([H:mcp], "Tool costs and spend limits").

### Secrets and sign-ins

- **Keys** live in `~/.hermes/.env`. `execute_code` children drop every variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `PASSWD` or `AUTH`. Local `terminal` drops Hermes's own infrastructure keys. Docker gets no host variables. A skill that declares `required_environment_variables` opens a passthrough for exactly those names once it loads. `terminal.env_passthrough` does the same from config. ([H:security], "Environment Variable Passthrough")
- **MCP stdio servers** get only `PATH`, `HOME`, `USER`, `LANG` and similar, plus their own `env` block. OAuth tokens are cached at `~/.hermes/mcp-tokens/<server>.json` with mode `0600`. When a refresh token dies in the background, the server parks until the user re-authorizes. ([H:security], "MCP Credential Handling"; [H:mcp], "OAuth-authenticated HTTP servers", "Reloading")
- **Browser sign-in, three ways.** `browser.user_data_dir` keeps one persistent profile across sessions. `browser.use_real_profile` copies the user's everyday Chrome profile into a managed snapshot, re-syncs its cookies on each new session, and deletes the snapshot when the setting is turned off. Headed mode keeps the browser open between turns so the user can step in for a sign-in or CAPTCHA. ([H:browser], "Local Chromium", "Real profile browsing")
- **The credential vault.** `browser_vault` fills saved logins, cards, addresses and TOTP codes into the page "without the secrets entering the conversation". A fill is refused unless the page origin exactly matches the saved item. A first sign-in asks for the password in a masked prompt. Every card fill asks first, and headless sessions are refused, so "a prompt injection that reaches a checkout page can ask, but it cannot spend". Items are encrypted under `~/.hermes/vault/`, and 1Password and Bitwarden CLIs are picked up when installed. ([H:credential-vault])
- **Unattended browsing.** A cron job can drive a site the user is signed into through the real-profile snapshot. A login form or fresh 2FA challenge then needs vault credentials saved in advance. Without them, the run reports the login page instead of hanging. ([H:browser], "Scheduled and unattended runs")

### On by default or not

Web search, fetch, browser, terminal and `execute_code` are all in `hermes-cli`. The browser needs Chromium, which the installers fetch by default ([H:browser]). MCP servers are off until configured. The curated catalog ships "disabled by default", installs from manifests merged by PR review, and probes the server to offer a tool checklist at install time ([H:mcp], "Catalog", "Tool selection at install time"). `manage_connections` lets the agent start that install from chat, through a setup card the user completes ([H:toolsets-reference]). Paying Nous subscribers can route search, extract and a cloud browser through Nous's Tool Gateway with no vendor keys ([H:tool-gateway]).

### Isolation and persistence

- **Terminal backends.** `local` (the default), `docker`, `ssh`, `modal`, `daytona`, `vercel_sandbox` ([H:tools], "Terminal Backends").
- **Docker** is "one persistent container, shared across the whole process". Terminal, file and `execute_code` calls all `docker exec` into it, so `pip install foo` stays installed. With `container_persistent: true` (the default), `/workspace` and `/root` are bind-mounted from `~/.hermes/sandboxes/docker/<task_id>/` and survive Hermes restarts. The container drops all capabilities except three, sets `no-new-privileges`, and limits PIDs to 256. ([H:tools], "Docker Backend"; [H:security], "Container Isolation")
- **`execute_code`** keeps one Python kernel per session, so variables, imports and loaded data carry over between calls. The kernel dies on timeout, `reset: true`, 30 minutes idle, or eviction past four live kernels. Defaults: a 300-second timeout, 50 KB of stdout, 50 tool calls per run. It runs in the project directory with the active virtualenv, or in an isolated staging directory with `mode: strict`. ([H:code-execution])
- **Packages.** When Hermes itself runs in Docker, the guidance is to run npm and PyPI tools through `npx` or `uvx` and keep their config under `/opt/data`. System packages go into a derived image, and heavy services into a sidecar container. The runtime user cannot `apt install`. ([H:docker], "Installing more tools in the container")
- **Browser.** Local Chromium by default, one per session, headless, closed after 120 seconds idle. Cloud providers (Browserbase, Browser Use, Firecrawl) are a config switch, and a local sidecar handles private URLs ([H:browser], "Cloud browsers", "Hybrid routing").

## OpenClaw

### What the model sees

The visible tools are the active tool profile (`minimal`, `coding`, `messaging`, `full`), then allow and deny lists, provider restrictions, sandbox state and channel policy ([O:tool-policy]; [O:tools-index], "Configure access and approvals"). Local onboarding sets `full`. `coding` includes `group:web` and `group:runtime` (same page).

| Capability | Tools | Shape |
| --- | --- | --- |
| Web search | `web_search` (plus `x_search`) | `query`, `count`, `country`, `freshness`, date bounds; a normalized result across providers ([O:web]) |
| Web fetch | `web_fetch` | `url`, `extractMode`, `maxChars`; a plain GET plus Readability, with no JavaScript ([O:web-fetch]) |
| Browser | `browser` | one tool with actions `status/start/tabs/open/snapshot/screenshot/navigate/act/text/…`, plus `profile` and `target` (`sandbox`, `host`, `node`) ([O:browser-tools]) |
| MCP | server tools under the `bundle-mcp` plugin id | filtered by `toolFilter.include/exclude` ([O:mcp]; [O:tool-policy]) |
| Code | `exec`, `process`; `code_execution` (xAI); Code Mode's JavaScript `exec` | shell with `host`, `background`, `timeoutSeconds`, `elevated` ([O:exec]; [O:code-execution]; [O:code-mode]) |
| Secrets | `secrets` | `request`, `list`, `delete`; the agent never sees a value ([O:secrets-tool]) |

Search providers include Brave, Exa, Firecrawl, Gemini, Grok, Kimi, Perplexity, SearXNG, Tavily and others. With no provider set, auto-detection picks the first one with a credential. Key-free providers such as DuckDuckGo and Parallel Free "never win auto-detection". OpenAI Responses models use OpenAI's hosted search when no provider is pinned. ([O:web], "Auto-detection")

`web_fetch` is "enabled by default, no configuration needed". By default it returns 20,000 characters and stops downloading at 750 KB, and Firecrawl is an optional fallback when Readability fails ([O:web-fetch]). OpenClaw treats fetched page text and browser page text as untrusted external content ([O:browser-tools]). It wraps that content in boundary markers and strips chat-template tokens from it before the model sees it ([O:prompt-injection], "External content and untrusted-input wrapping").

`code_execution` runs Python on xAI's servers, "has no access to local files, your shell, your repo", and keeps no state between calls ([O:code-execution]). Code Mode is a separate, experimental feature: the model writes JavaScript that calls a hidden tool catalog, under Node `vm` (not a security boundary) or QuickJS ([O:code-mode]).

### Gating

- **`web_fetch`** blocks private and internal hosts and re-checks redirects. `ssrfPolicy.allowedHostnames` makes narrow exceptions, and `dangerouslyAllowPrivateNetwork` opens the private network ([O:web-fetch], "Config").
- **`exec`** is governed by `tools.exec.mode`: `deny`, `allowlist`, `ask`, `auto` (an LLM reviewer allows, denies or escalates) or `full`. Gateway and node hosts default to `full`, meaning no prompts. A sandbox host defaults to `deny` unless configured. `askFallback` decides when no approval UI is reachable and defaults to `deny`. ([O:exec-approvals], "Policy knobs"; [O:exec], "Config") Session permission modes (`read-only`, `guarded`, `workspace`, `full`) set the session's file boundary and its exec reviewer ([O:permission-modes]).
- **Unattended runs.** A cron run's exec approval goes only to connected approval clients (the Control UI, the mobile apps, API clients), never to chat channels. With none connected, "the request is denied immediately". **Always allow** on such an approval mints a **standing grant** bound to "that exact agent, automation, job configuration, and operation (command text, working directory, and requested environment)". Editing the job, a one-byte change to the command, revocation or expiry ends it. Grants live until revoked unless `tools.exec.grantExpiryDays` is set, and are listed with use counts in Settings → Approvals. ([O:exec-approvals], "Standing grants for automations")
- **MCP.** Server tools "go through the same tool-profile and tool-policy controls as everything else" ([O:mcp]). Per-call approval is documented for Codex-harness runs (Allow Always mints a per-agent grant for one server and tool) and for plugins that request approval in a `before_tool_call` hook ([O:mcp], "Approvals"; [O:plugin-permissions]). No doc describes per-call approval for MCP tools in OpenClaw's own embedded runtime (**unverified**).

### Secrets and sign-ins

- **Keys** live in config (SecretRefs are supported), in `~/.openclaw/.env`, or in a shared secret store ([O:web], "Storing API keys"). The store keeps entries unencrypted in `state/openclaw.sqlite` with mode `0600` ([O:secret-store]).
- **The `secrets` tool** lets the agent ask for a missing credential by name, with a reason and proposed allowed hosts. The human types the value into a masked prompt in the Control UI or an app. "There is deliberately no action that writes a value the agent supplies." Chat channels get only a link, because the value must never be typed into chat. ([O:secrets-tool])
- **The egress proxy** is off by default. When it is on, a stored `secret` entry reaches an exec subprocess as an opaque sentinel in its environment, and a loopback proxy swaps in the real value only in HTTPS requests to that entry's exact allowed hosts. Each process gets its own grant, revoked on exit. Sandbox, node and native-harness execution do not get these values. ([O:secret-store], "Secret egress proxy"; [O:secrets-tool], "Using a stored credential")
- **Browser profiles.** `openclaw` is a managed, isolated profile and the default. `user` attaches to the real signed-in Chrome through Chrome DevTools MCP, which needs someone present to approve the attach prompt. `chrome` drives the real Chrome through an extension and works with nobody at the desk. ([O:browser-profiles]) The login guidance is to sign in manually in the managed profile in headed mode, then let the agent reuse it. "Automated logins often trigger anti-bot defenses". The agent may fill owner-supplied credentials for an owner-authorized task. ([O:browser-login])

### On by default or not

`web_fetch` is on by default. `web_search` needs a configured provider. The browser comes from a plugin with a bundled `browser-automation` skill ([O:browser]). `exec` is in `group:runtime`, which the `coding` and `full` profiles include ([O:tool-policy]). MCP servers are added by the user in Settings, the CLI or config. The composer can enable a server for one session or for all of them, and requires administrator access ([O:mcp], "Add a server from the composer"). A `plugins` tool can install and enable plugins from a conversation ([O:tools-index]); the prior note covers its trust policy.

### Isolation and persistence

- **Sandboxing is off by default** (`agents.defaults.sandbox.mode: off`). When on, `exec`, file tools and `process` run inside it. The Gateway stays on the host. ([O:sandboxing]; [O:what-gets-sandboxed])
- **Scope** is one container per agent (the default), per session, or shared ([O:sandbox-modes]).
- **Docker defaults.** `network: "none"`, read-only root, all capabilities dropped, `no-new-privileges`, image `openclaw-sandbox:trixie-slim` with `python3`, `git`, `curl`, `jq` and `ripgrep` ([O:sandbox-docker]; [O:sandbox-images]). `setupCommand` runs once at container creation. With the defaults, package installs fail: no egress, read-only root ([O:sandbox-setup]). The docs' config example prunes idle sandboxes after 24 hours and any sandbox after 7 days ([O:sandbox-config]). Whether those are the defaults is **unverified**.
- **Sandbox browser.** It is a separate container on a dedicated Docker network, with password-protected noVNC so a human can watch or take over. Host control from a sandboxed session needs `allowHostControl: true`. ([O:sandbox-docker], "Sandboxed browser")
- No doc describes a persistent Python environment or kernel that the agent keeps (**unverified** that none exists). The persistent places are the agent workspace and the sandbox container until it is pruned or recreated.

## Side by side

| | Hermes Agent | OpenClaw |
| --- | --- | --- |
| Search | `web_search`; Firecrawl default; keyless vendor ring as fallback | `web_search`; first configured provider; keyless only when picked |
| Fetch | `web_extract`, 15k chars, overflow to a file | `web_fetch`, 20k chars, Readability, untrusted-content wrapping |
| Browser tool | `browser_exec`: Python over CDP, needs `terminal` | `browser`: action enum with snapshot refs |
| Signed-in browser | persistent `user_data_dir`, real-profile snapshot, vault fills | managed `openclaw` profile signed in by hand; `user`/`chrome` attach |
| Credentials for the browser | origin-bound vault; card fills ask; headless refused | owner-supplied form fills; `secrets` tool for API keys |
| MCP gating | `trust: untrusted` asks on non-read-only tools; spend limits | tool policy; per-call approval for Codex and plugin hooks |
| Local Python | `execute_code` persistent kernel, tool RPC, env scrubbed | none; `code_execution` is remote xAI |
| Persistent environment | Docker backend: one container, `/workspace` and `/root` persisted | sandbox container per agent until pruned; no egress by default |
| Unattended shell | `cron_mode: deny` | approval client or deny; standing grants per exact job and command |
| Secrets in subprocesses | stripped by name; declared passthrough | protected entries as sentinels behind a host-bound egress proxy |

## What kinby could borrow

kinby's constraints: every tool call passes the gate, which sets per-tool rules under mode presets; a thread's mode never exceeds the instance's ceiling; bash asks below full-access; and the bash denylist is "a tripwire, not a wall", because isolation comes from the container ([ADR 0014](../adr/0014-gate-policy-is-per-tool-rules-under-mode-presets.md)). Shipped deny patterns always apply ([ADR 0069](../adr/0069-shipped-bash-deny-patterns-always-apply.md)). The hub keeps a library of pinned skills and factories ([ADR 0091](../adr/0091-the-hub-keeps-a-library-and-a-pick-is-a-pinned-copy.md)). Each instance is one Docker container from `python:3.14-slim` with `/instance` as its volume ([`Dockerfile`](../../Dockerfile)).

1. **Ship `web_fetch` and `web_search` as two read-only default tools.** Both projects do this. Fetch needs no key and can run in-process: a GET, Readability or similar, a character cap with overflow to a file the agent reads with `read`, an SSRF guard that re-checks every redirect, and wrapping that marks page text as untrusted. Search needs a provider. Pick one in instance config, with the key in `.env`. SearXNG suits a self-hosted user who wants no vendor. Neither tool writes, so the gate can allow both in every preset. Follow OpenClaw rather than Hermes on keyless search: never send queries to a free tier the user did not choose.
2. **Run the browser in the instance and keep its profile on the volume.** Both default to a throwaway profile and offer a persistent one as opt-in. For an unattended, signed-in job, the shape that works in a container is a Chromium profile directory under `/instance`, signed into once by the user through a headed view. OpenClaw's sandbox browser exposes noVNC for this, and kinby's web app is the natural place for it. Later scheduled runs reuse the cookies. When a session expires mid-routine, report the login page and stop, as Hermes does, rather than trying to sign in. Choose the tool shape deliberately: Hermes's Python-over-CDP tool is code execution and is offered only with the terminal, while OpenClaw's action-enum tool can be gated on its own. Under ADR 0014, the action enum lets reads (`snapshot`, `text`) be allowed while `act` asks.
3. **Keep passwords out of the model, and bind them to an origin.** Hermes's vault is the model to copy if kinby ever fills credentials: the agent names a handle, the fill happens over CDP, the page origin must match exactly, and the tool result says only how many fields were filled. Payment fills ask every time and are refused when nobody can answer. Until that exists, signing in by hand is the safer default, and both projects document it.
4. **Map MCP's `readOnlyHint` onto the gate's write flag.** Hermes's `trust: untrusted` is ADR 0014's ask rule in MCP terms: a tool that declares itself read-only runs, and any other call asks. Treat every MCP tool as `write` unless the server says `readOnlyHint: true`, and take the per-server include/exclude filters from both projects. A server definition with a pinned source fits the hub library (ADR 0091) the way Hermes's reviewed catalog manifests do. Pass a stdio server only the env vars it declares, as Hermes does.
5. **Give each instance a virtualenv on its volume.** Hermes's Docker backend shows what persistence needs: one long-lived container per agent, with home and workspace on a mount, so a `pip install` stays. kinby already has the container and `/instance`. A `uv` venv under `/instance` makes agent-installed packages survive image updates, and system packages stay a recipe or image concern, as both projects advise. Hermes skips its command prompts in Docker only when no host path is mounted. kinby mounts `/instance`, so by Hermes's own rule the gate should keep asking on bash.
6. **Close unattended runs by default, and let approvals become exact standing grants.** Both deny when nobody can answer. OpenClaw's standing grant goes further: "Always allow" on a routine's approval is bound to that routine's definition and the exact command, directory and environment, and editing the routine revokes it. This lets a routine that needs one gated command run unattended without raising the routine's whole mode. OpenClaw lists every grant with a revoke action. kinby's `tool.call` and gate-decision events ([ADR 0026](../adr/0026-model-calls-and-gate-decisions-are-events.md)) already record the approvals such a grant would come from.
7. **Scrub secrets from code the agent runs, and pass through only declared names.** Hermes drops `*KEY*`, `*TOKEN*` and similar names from `execute_code` children, and a skill's frontmatter opens a passthrough for the names it declares. That fits kinby's skills and `.env`. OpenClaw's egress proxy is stronger: subprocesses see only a sentinel, swapped for the real value only in HTTPS to bound hosts. It is also a TLS-intercepting proxy with its own CA, which is more machinery than kinby needs now.
8. **Agent-authored tools stay ahead of both.** Neither project loads a tool the agent wrote. Hermes's nearest thing is an `execute_code` script calling tools over RPC. kinby already loads `tools/*.py` per turn behind the gate, so these two projects offer no model for that item of #161. The prior note's conclusion stands.

[h-root]: https://github.com/NousResearch/hermes-agent/tree/1f91da2a277093c6909542d8c725c0c52fae4628
[h-approval]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/tools/approval.py#L1041-L1047
[H:tools]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/tools.md
[H:toolsets-reference]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/reference/toolsets-reference.md
[H:web-search]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/web-search.md
[H:browser]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/browser.md
[H:credential-vault]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/credential-vault.md
[H:code-execution]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/code-execution.md
[H:mcp]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/mcp.md
[H:mcp-config]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/reference/mcp-config-reference.md
[H:security]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/security.md
[H:cron]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/cron.md
[H:docker]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/docker.md
[H:tool-gateway]: https://github.com/NousResearch/hermes-agent/blob/1f91da2a277093c6909542d8c725c0c52fae4628/website/docs/user-guide/features/tool-gateway.md
[o-root]: https://github.com/openclaw/openclaw/tree/98dcd3674b065d1eda14aa8fc86b016d0551004f
[O:tools-index]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/index.md
[O:tool-policy]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/config-tools/tool-policy.md
[O:web]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/web.md
[O:web-fetch]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/web-fetch.md
[O:prompt-injection]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/security/prompt-injection.md
[O:browser]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/browser.md
[O:browser-tools]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/browser/agent-tools.md
[O:browser-profiles]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/browser/profiles.md
[O:browser-login]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/browser-login.md
[O:mcp]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/mcp.md
[O:plugin-permissions]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/plugins/plugin-permission-requests.md
[O:exec]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/exec.md
[O:exec-approvals]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/exec-approvals.md
[O:permission-modes]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/permission-modes.md
[O:code-execution]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/code-execution.md
[O:code-mode]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/code-mode.md
[O:secrets-tool]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/tools/secrets.md
[O:secret-store]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/secrets/secret-store-and-egress.md
[O:sandboxing]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/sandboxing.md
[O:what-gets-sandboxed]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/sandboxing/what-gets-sandboxed.md
[O:sandbox-modes]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/sandboxing/modes-scope-and-backend.md
[O:sandbox-docker]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/sandboxing/docker-backend.md
[O:sandbox-images]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/sandboxing/images-and-setup.md
[O:sandbox-setup]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/sandboxing/setup-command.md
[O:sandbox-config]: https://github.com/openclaw/openclaw/blob/98dcd3674b065d1eda14aa8fc86b016d0551004f/docs/gateway/config-agents/sandbox.md
