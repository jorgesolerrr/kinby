# Code mode and standard MCP servers for kinby

Ticket: [jorgesolerrr/kinby #1215](https://github.com/jorgesolerrr/kinby/issues/1215) (OAuth sign-in for remote MCP servers). Date: 2026-11-17.
Questions: (1) should kinby offer MCP tools through a small search-and-execute gateway, where the model writes code that calls several tools? (2) What is the standard way to declare an outside MCP server, sign in to it, and filter its tools, so that what users already run works in kinby?

Related notes: [hot-plug-tools.md](hot-plug-tools.md) covers how a tool-list change breaks the prompt cache, and [deepagents-ideas.md](deepagents-ideas.md) covers evicting large tool results to a file. Neither is repeated here.

## Summary

**Both ideas already have an owner.** The MCP project now publishes [Client Best Practices](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices), which names both patterns: *progressive discovery* (a `search_tools` meta-tool, with definitions loaded only when needed) and *programmatic tool calling / code mode* (the host turns tool schemas into typed functions, the model writes a script, a sandbox runs it, and the host brokers every call). Anthropic, Cloudflare, OpenAI, Pydantic, goose and Docker ship versions of them. No SEP makes either part of the protocol: they are client behaviour, and the protocol does not change.

**Recommendation for idea 1 (code mode).** Follow the MCP Client Best Practices shape and copy Pydantic AI's `CodeMode` as the Python reference:

- The model sees a few stable meta-tools: search the picked servers' tools, read one tool's signature, and run code. The `tools` array stays the same through a thread, so the prompt cache holds.
- The code is Python and runs in [Monty](https://github.com/pydantic/monty) (`pydantic-monty`), in the turn's own process. Monty has no filesystem, environment or network, and reaches the host only through the functions kinby passes in.
- Every call the code makes goes back through kinby's MCP client and through the **gate**, one call at a time, as the MCP guidance requires. Credentials stay in the turn runner and never enter the sandbox.
- It replaces nothing. A thread with a few tools keeps classic tool calls. Code mode is for many servers, fan-out, and large results. The sources agree it is a net loss for short sequential work.

**Recommendation for idea 2 (standard servers).** There is nothing to invent:

- The **library** accepts and exports the de-facto `mcpServers` JSON entry (`command`/`args`/`env` or `type`/`url`/`headers`, `${VAR}` interpolation). kinby's include filter and trust choice sit beside it.
- Remote sign-in follows the [MCP authorization spec, revision 2026-07-28](https://modelcontextprotocol.io/specification/latest/basic/authorization), through the MCP Python SDK kinby already depends on. Its `OAuthClientProvider` does discovery, PKCE, CIMD or dynamic registration, and refresh. kinby writes only a `TokenStorage` and the place the browser redirect lands.
- The official [MCP Registry](https://registry.modelcontextprotocol.io) and its `server.json` are the search source for adding a server.
- The gap users will hit first is `npx`. Most published snippets launch Node servers, and ADR 0111 left Node out of the image.

## What kinby has now

- **An MCP server is a library kind** ([ADR 0111](../adr/0111-mcp-servers-are-a-library-kind-whose-calls-are-writes.md), CONTEXT "MCP server"). The hub keeps one JSON definition per server under its library directory (`src/kinby/hub/library.py`). An instance picks it, and the pick records a copy in the instance's lock (`McpServerChoice`, `LockedMcpServer`).
- **Definition** (`McpServer` in `src/kinby/contracts/models.py`). The `transport` is either `url` (a Streamable HTTP URL plus `headers`, a map from header name to the `.env` variable that holds its value) or `uvx` (a package and args, run inside the instance's container). It also holds `env`, the variable names a `uvx` server gets and the only ones, and `include`, the tool names a turn is offered. `npx` is out because the image has no Node.
- **At turn start** (`src/kinby/plugins/mcp.py`, `mcp_connection.py`), the turn connects to every picked server within 60 seconds, lists all tools with pagination, and keeps those in `include`. Each becomes a kinby **tool** named `<server>__<tool>`. Header and env values come from the instance's `.env` with `interpolate=False`. A server that fails is left out and reported as an **instance notice** (`McpServerFailed`).
- **The gate.** Every MCP tool call is a write unless the user trusts the server's `readOnlyHint` on the pick (`trust_hints`).
- **No OAuth.** Nothing obtains or renews a token. This is the gap in #1215: Cockpit's server accepts OAuth only.
- **No search and no code mode.** Every included tool's schema goes into every model call.

## A. Code mode

### A1. Cloudflare Code Mode

Cloudflare's [first post](https://blog.cloudflare.com/code-mode/) (2025-09-26) describes client-side code mode in their Agents SDK:

- The SDK fetches each connected MCP server's schema and generates a TypeScript API with doc comments from it. The model gets one tool that runs TypeScript, and the tools appear as async functions on a `codemode` object. At the time, "the entire API is loaded" into context.
- The code runs in a fresh V8 isolate per snippet, created through the Worker Loader API (`env.LOADER.get(id, callback)`) and thrown away afterwards. The post says isolates "can start in a handful of milliseconds using only a few megabytes of memory".
- "The global `fetch()` and `connect()` functions throw errors." The sandbox's only way out is the generated API. Each call is an RPC back to the agent loop.
- Each MCP server appears in the sandbox as a **binding**, an already-authorized client object. Calls go to the supervisor, which holds the tokens and attaches them. Their argument is that the code cannot leak a key it never sees.
- The post gives no token or latency numbers. Its stated reason is "LLMs have seen a lot of code. They have not seen a lot of 'tool calls'."

Their [second post](https://blog.cloudflare.com/code-mode-mcp/) (2026-02-20) moves code mode to the server side. The Cloudflare API MCP server exposes only `search()` and `execute()`, and each takes a `code` string:

- `search` runs model-written JavaScript against the full OpenAPI spec with `$ref`s resolved, so "the full OpenAPI spec never enters the model context".
- The two fixed tool definitions take about 1,000 tokens. The post estimates 1.17 million tokens for the same API as plain MCP tools, a 99.9% cut measured with tiktoken.
- Auth is OAuth 2.1 through the Workers OAuth Provider. The isolate has no filesystem and no environment variables.
- The post names the trade-off itself: client-side code mode means "the agent must ship with secure sandbox access". It names goose and Anthropic's programmatic tool calling as client-side examples.

Source: [`packages/codemode`](https://github.com/cloudflare/agents/tree/main/packages/codemode) in `cloudflare/agents`, documented as `@cloudflare/codemode` ([docs](https://developers.cloudflare.com/agents/model-context-protocol/codemode/)):

- `codeMcpServer({ server, executor })` wraps an MCP server as one `code` tool whose description holds generated TypeScript declarations.
- `openApiMcpServer(...)` exposes `search` and `execute` over an OpenAPI document.
- The `Executor` interface is the seam: `execute(code, providers) -> { result, error?, logs? }`. The source comment says to "implement it to run LLM-generated code in any sandbox (Workers, QuickJS, Node VM, etc.)" ([`executor.ts`](https://github.com/cloudflare/agents/blob/main/packages/codemode/src/executor.ts), [`executor-types.ts`](https://github.com/cloudflare/agents/blob/main/packages/codemode/src/executor-types.ts)). Shipped executors are `DynamicWorkerExecutor` and an iframe executor.
- Documented limits: experimental; "neither function provides durable per-operation approval", so authorization must live in the upstream handlers; generated code can see whatever the results hold, so secrets must stay out of them; final responses are cut at about 6,000 estimated tokens.

### A2. Anthropic

**"Code execution with MCP"** ([engineering post](https://www.anthropic.com/engineering/code-execution-with-mcp), 2025-11-04) is a pattern, not an API:

- MCP servers appear as a file tree, such as `servers/google-drive/getDocument.ts`, where each file wraps `callMCPTool`. The agent lists `./servers/` and reads only the files it needs. An optional `search_tools` takes a detail level: name only, name and description, or the full schema.
- Reported: "This reduces the token usage from 150,000 tokens to 2,000 tokens", a 98.7% saving. A 2-hour meeting transcript passed through the model costs about 50,000 extra tokens.
- Intermediate data stays in the execution environment unless it is logged. The harness can tokenize PII (`[EMAIL_1]`) and swap the real value back in on the next tool call.
- State persists as files, and working code can be saved as a reusable function with a `SKILL.md`.
- Caveat, verbatim: it "requires a secure execution environment with appropriate sandboxing", plus resource limits and monitoring, overhead that direct tool calls avoid.

**Tool search tool** ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)):

- It is a server tool, `tool_search_tool_regex_20251119` (Python `re.search` patterns, at most 200 chars) or `tool_search_tool_bm25_20251119` (natural language, at most 500 chars).
- You still send every definition. Tools marked `defer_loading: true` stay out of the context until a search returns them as `tool_reference` blocks (5 by default), which the API expands into full definitions.
- The limit is 10,000 deferred tools per request. At least one tool must not be deferred, and a deferred tool cannot carry `cache_control`.
- Deferred tools are excluded from the system-prompt prefix, so discovery does not break the cache.
- A **custom client-side search** is supported: your own tool returns `tool_reference` blocks in a normal `tool_result`.
- MCP connector servers set `defer_loading` on the `mcp_toolset` `default_config` or per tool.
- The docs' guidance: use it with 10 or more tools or more than 10k tokens of definitions. They say selection accuracy "degrades once you exceed 30–50 available tools", and a five-server setup takes about 55k tokens.

**Advanced tool use** ([engineering post](https://www.anthropic.com/engineering/advanced-tool-use), 2025-11-24):

- Tool search: about 77K → 8.7K tokens before work begins, described as an "85% reduction". MCP evals went from 49% to 74% on Opus 4 and from 79.5% to 88.1% on Opus 4.5.
- Programmatic tool calling: average usage fell from 43,588 to 27,297 tokens (37%). Knowledge retrieval went from 25.6% to 28.5%, and GIA from 46.5% to 51.2%.
- Tool use examples: complex parameter handling went from 72% to 90%.

**Programmatic tool calling** ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)):

- A tool with `allowed_callers: ["code_execution_20260120"]` becomes an async Python function inside Anthropic's code execution container. Each function takes one dict and returns the string of your `tool_result`.
- When the code calls one, the response stops with `stop_reason: "tool_use"`, a `container` id, and a `tool_use` block whose `caller` names the code run. You answer with a message that holds only `tool_result` blocks, and the code resumes.
- Programmatic results do not enter the model's context or count as tokens. Only the final output does.
- Published numbers: about 38% fewer billed input tokens on a 75-tool benchmark with no accuracy change; 20–40% savings typical for 10–49 tools; and on τ²-bench, "scores unchanged and cost roughly 8 percent more". On BrowseComp and DeepSearchQA, +11% at 24% fewer input tokens.
- Weak fit, per the docs: "strictly sequential workflows", "a small number of tool calls with small responses", and "tools that require immediate user feedback between calls".
- Limits that matter for kinby:
  - A pending call "times out after about 4 minutes".
  - `allowed_callers` is "not a hard API-level block" and must not be used as a security boundary.
  - MCP connector tools cannot be called programmatically, and `strict: true` tools are not supported.
  - It is not eligible for zero data retention, and container data is kept up to 30 days.
- The docs also describe the self-hosted variant: the same pattern in your own sandbox with "no network egress", which needs "a protocol for running tool calls outside the sandbox".

**MCP connector** ([docs](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector), beta `mcp-client-2026-09-15`): the Messages API connects to remote MCP servers itself. You pass `mcp_servers` and a matching `mcp_toolset` with allowlist or denylist configs. It handles remote servers only, and the caller must supply the OAuth token.

### A3. Other first-party implementations

- **MCP Client Best Practices** ([modelcontextprotocol.io](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices), added in [PR #2582](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2582)). This is the cross-vendor statement:
  - Switch to progressive discovery when definitions pass a threshold, "for example, 1%-5%" of the context window. Search can be keyword, embedding, a small model, or a mix. The layers are catalog → inspect → execute, with "multiple detail levels".
  - On caching: "route every call through a single stable `call_tool({name, args})` meta-tool so the array never changes", and treat disconnecting a server as a conversation-boundary operation.
  - For code mode: no network in the sandbox; "Authorization tokens and credentials are held by the host"; "Approving the script does not grant blanket approval for every tool call it makes at runtime"; turn `isError: true` into a thrown exception; set timeouts and memory limits.
  - It lists sandboxes: Deno or `isolated-vm`, Monty (Python, "experimental"), pctx, and Wasmtime.
- **Pydantic AI `CodeMode`** ([docs](https://pydantic.dev/docs/ai/harness/code-mode/)). The closest match to kinby's stack (Python, Monty):
  - One `run_code` tool. Tools are rendered as `async def` stubs from their JSON schemas, and the catalog goes into the instructions with `on_change='append'` so the cache survives.
  - Deferred tools stay out until a local `search_tools` or the provider's native search finds them.
  - Defaults: 30 s per snippet (time waiting on tools excluded), a 256 MiB heap, `max_tool_calls` 100, and 3 retries. Syntax and runtime errors go back to the model as retries.
  - Tools that need approval raise an error inside `run_code` unless a `HandleDeferredToolCalls` capability resolves them inline.
- **goose** ([Code Mode guide](https://github.com/aaif-goose/goose/blob/main/documentation/docs/guides/managing-tools/code-mode.md)):
  - Three meta-tools: `list_functions`, `get_function_details` and `execute_javascript`. The JavaScript runs in a fresh [Boa](https://boajs.dev/) context per call, with no filesystem, shell, network or environment.
  - "Tool calls retain the extension lease, permission flow, cancellation, and mutation handling." Only text results reach the script.
  - The docs recommend it for "5+ extensions" and classic calls for "1-3".
  - A goose maintainer's [blog post](https://github.com/aaif-goose/goose/blob/main/documentation/blog/2026-02-06-8-things-you-didnt-know-about-code-mode/index.md) reports one task at 23,339 vs 33,648 tokens (30% fewer). That is a single run.
- **Docker MCP Gateway** ([dynamic servers spec](https://github.com/docker/mcp-gateway/blob/main/docs/feature-specs/dynamic_servers.md), [`dynamic_mcps.go`](https://github.com/docker/mcp-gateway/blob/main/pkg/gateway/dynamic_mcps.go)):
  - Behind the `dynamic-tools` flag it adds `mcp-find`, `mcp-add`, `mcp-config-set`, `mcp-remove`, `mcp-registry-import`, `mcp-exec` and `code-mode`.
  - `code-mode` builds a JavaScript tool over chosen servers and runs it in [goja](https://github.com/dop251/goja) ([`pkg/codemode/exec.go`](https://github.com/docker/mcp-gateway/blob/main/pkg/codemode/exec.go)).
  - A catalog server can be activated only when a policy provider allows it. Passing `--servers` turns dynamic tools off.
- **OpenAI** ([tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)): `{"type": "tool_search"}` with `defer_loading: true` and namespaces. There is a client-executed variant (`execution: "client"`). Only `gpt-5.4` and later support it in the Responses API.

**Spec status.** No accepted SEP adds tool search or code mode to the protocol. [SEP-1821 Dynamic Tool Discovery](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1821) was closed in 2026-06 when SEPs moved to pull requests, and its PR [#1822](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/1822) closed unmerged. [SEP-1300 tool groups and tags](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1300) and [SEP-2084 primitive grouping](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2084) were rejected. What did land in 2026-07-28 helps a client-side catalog: servers SHOULD return `tools/list` "in a deterministic order", list results carry `ttlMs` and `cacheScope` cache hints, and `inputSchema` and `outputSchema` accept any JSON Schema 2020-12 ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog.md)). I found no working group on code mode; **unverified** beyond the issue search.

### A4. Sandboxes for a Python, single-user, self-hosted kinby

| Option | Used by | What the code can reach | Fit for kinby |
| - | - | - | - |
| [Monty](https://github.com/pydantic/monty) (`pydantic-monty`, Rust, MIT) | Pydantic AI `CodeMode` | "Filesystem, environment variables and network do not exist inside the sandbox", only host functions passed in. New sandbox from a pool in under 1 ms. | Best fit. Python 3.14 subset, runs in the turn process, a `uv` dependency. It is a subset: no third-party imports, about 30 stdlib modules, no `async with` or `async for`, no `enum` or `contextlib` ([limitations](https://pydantic.dev/docs/monty/limitations/)). The MCP docs call it "experimental". |
| Anthropic managed container (programmatic tool calling) | Claude API | Anthropic's container; your tools come back as `tool_use` pauses | Anthropic only, while kinby also runs OpenAI. The 4-minute pending-call timeout breaks a **gate** approval that waits for the user. Not eligible for zero data retention. |
| V8 isolate: Worker Loader, Deno, `isolated-vm` | Cloudflare, MCP docs | Only bindings or stubs; fetch and connect throw | Worker Loader needs workerd. Deno or a Node addon means adding JavaScript and a runtime to a Python image. |
| Embedded JS: Boa (Rust), goja (Go) | goose, Docker gateway | `tools.call` only | Same pattern, but the hosts are Rust and Go, with no Python binding of note (**unverified**). |
| Wasmtime | MCP docs | Capabilities granted | General, but more plumbing than Monty for the same result. |
| The instance's own container plus bash | Anthropic's `servers/` file tree; kinby **instance code** today | Everything the container holds, including `.env` and the network | No new dependency. But the code then holds the secrets and the network, which is what code mode is meant to remove, and every call would bypass the per-call gate. |

The security model is the same everywhere: the code has no network or secrets; the host holds the MCP clients and credentials; each brokered call is authorized as if the model had made it directly; the host sets time and memory limits and truncates output ([MCP best practices](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices#security-considerations)). Two further rules fit kinby:

- **Tool results are untrusted input.** Programmatic tool calling warns about "code injection risks if the output is interpreted or run as code" ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling#validate-tool-results-before-use)). The MCP guidance says "output truncation alone does not prevent exfiltration".
- **kinby's gate already fits.** The gate decides one tool call at a time. Code mode only changes who makes the call. kinby's broker would call the gate with each brokered call's real tool name and write flag, so `routine grant`s and `trust_hints` keep working.

### A5. Costs and failure modes reported

- **Overhead on small or sequential work.** Anthropic measured +8% cost on τ²-bench, where turns make one or two sequential calls ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling#token-efficiency)). The weak-fit list is sequential reasoning, few small calls, and calls that need user feedback in between. goose recommends classic calls for one to three extensions.
- **The model must write correct code.** Pydantic AI retries syntax, type and runtime errors up to 3 times, and resets the REPL on a timeout, oversized output or a memory error. Cloudflare and goose cap output.
- **Partial side effects.** An uncaught error ends the script after some writes have happened. The MCP guidance makes the model "responsible for reporting any partial side effects already committed".
- **Missing output schemas.** Without `outputSchema` the stubs return `Any`. Pydantic AI warns with `CodeModeReturnSchemaWarning`, and the MCP guidance suggests a generic type or a small-model extractor.
- **Approvals inside a script.** Cloudflare's SDK has no durable per-operation approval. Pydantic AI errors unless deferred calls are handled. Anthropic's pending call times out after about 4 minutes. goose keeps its permission flow. kinby's gate can ask the user mid-script, so the script must be able to wait on an **approval**. Monty supports pausing on a host call ([snapshots](https://pydantic.dev/docs/monty/concepts/snapshots/)), but a gate pause of hours has not been tried in kinby (**unverified**).
- **Search misses.** The model has to find a tool before it can call it. Anthropic's advice: name tools with a consistent service prefix, put search keywords early in descriptions, and keep the 3–5 most used tools non-deferred. Cloudflare notes that a search function "must be maintained and evaluated".
- **Text only.** goose passes only text results to the script. Anthropic's programmatic results must be strings.

## B. How others handle MCP servers

### B1. The MCP specification (revision 2026-07-28)

**Transports** ([overview](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports.md)): stdio (newline-delimited JSON-RPC to a subprocess, logs on stderr) and Streamable HTTP (each message is a POST to one endpoint; the reply is JSON or an SSE stream for that request). This revision made the protocol stateless ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog.md)):

- `initialize` and `Mcp-Session-Id` are gone. Every request carries its version and capabilities in `_meta`, and the new `server/discover` RPC tells a client what a server supports.
- `subscriptions/listen` replaces the GET stream. Servers ask for input through "multi round-trip requests" instead of server-initiated requests.
- HTTP+SSE, Roots, Sampling and Logging are deprecated.

Whether MCP Python SDK 2.3, which kinby pins, speaks 2026-07-28 or the earlier revision is **unverified**. The spec defines a fallback for older peers ([versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning.md)).

**Authorization** ([spec](https://modelcontextprotocol.io/specification/latest/basic/authorization)) applies to HTTP only. A stdio server "SHOULD NOT follow this specification, and instead retrieve credentials from the environment".

- The server is an OAuth 2.1 resource server and "MUST implement" Protected Resource Metadata (RFC 9728). A 401 carries `WWW-Authenticate: Bearer resource_metadata=..., scope=...`.
- The client finds the authorization server through RFC 8414 or OIDC discovery and "MUST support both". It uses PKCE, sends the `resource` parameter (RFC 8707) on both the authorization and token requests, and validates `iss` (RFC 9207).
- Tokens go only in `Authorization: Bearer`, never in the query string.
- Refresh tokens are optional for the authorization server. A client that wants one SHOULD list `refresh_token` in its grant types and MAY add `offline_access`.
- A 403 with `insufficient_scope` triggers a step-up flow with the union of scopes.

**Client registration** ([spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration.md)), in priority order:

1. Pre-registered client information.
2. Client ID Metadata Documents (CIMD), when the authorization server advertises `client_id_metadata_document_supported`. The `client_id` is an HTTPS URL with a path that serves a JSON document holding at least `client_id`, `client_name` and `redirect_uris`.
3. Dynamic Client Registration (RFC 7591), now **deprecated** but kept for older servers. The client MUST send `application_type` (`"native"` for localhost-hosted apps).
4. Asking the user for client details.

Credentials obtained this way MUST be keyed by the authorization server's `issuer` and re-registered if it changes. Cockpit, the case in #1215, publishes a DCR endpoint and allows public clients, so step 3 covers it today.

**The MCP Python SDK already implements the client side.** `mcp.client.auth.OAuthClientProvider(server_url, client_metadata, storage, redirect_handler, callback_handler, client_metadata_url=...)` is an httpx auth flow. It handles protected-resource discovery, authorization-server metadata, CIMD (`client_metadata_url`) or DCR, PKCE, the token exchange and refresh. Storage is a `TokenStorage` Protocol with `get_tokens`, `set_tokens`, `get_client_info` and `set_client_info` (`mcp/client/auth/oauth2.py` in the installed `mcp` 2.3.0, [repo](https://github.com/modelcontextprotocol/python-sdk)). kinby already passes an httpx client to `streamable_http_client` (`mcp_connection.py`), so this is where the auth plugs in.

**Official MCP Registry** ([repo](https://github.com/modelcontextprotocol/registry)): in preview since 2025-09-08, with the v0.1 API frozen since 2025-10-24. Namespaces are verified through GitHub, DNS or HTTP. It lists servers and hosts no code.

- Live API: `GET https://registry.modelcontextprotocol.io/v0.1/servers?search=linear` returns `server.json` records with `_meta` status. I checked this on 2026-11-17.
- [`server.json`](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md) has `name` (reverse DNS), `description` and `version`.
- `packages[]` holds `registryType` (`npm`, `pypi`, `oci`, `nuget`, `mcpb`, `cargo`), `identifier`, `runtimeHint` (`npx`, `uvx`, …), `transport` and `environmentVariables[]` with `isRequired` and `isSecret`.
- `remotes[]` holds `type` (`streamable-http` or `sse`), a `url` with `{variables}`, and `headers[]` with `isSecret`.

It maps almost one to one onto kinby's `McpServer`: a `pypi` package with `runtimeHint: uvx` is the `uvx` transport, and a remote is the `url` transport. Its `isSecret` flags say which variables are **pending setup**.

### B2. Client config formats

| Client | File(s) | Root key | Local server | Remote server | Variables | OAuth | Server on/off | Tool filter |
| - | - | - | - | - | - | - | - | - |
| [Claude Code](https://code.claude.com/docs/en/mcp) | `.mcp.json` (project), `~/.claude.json` (user, local) | `mcpServers` | `type: "stdio"`, `command`, `args`, `env` | `type: "http"`, `url`, `headers`, `headersHelper`, `oauth{clientId, callbackPort, scopes, authServerMetadataUrl}` | `${VAR}`, `${VAR:-default}` in command, args, env, url, headers | `/mcp` or `claude mcp login`; tokens in the keychain, refreshed automatically; a configured `Authorization` header disables OAuth | `disabledMcpServers`, `enabledMcpjsonServers`, `disabledMcpjsonServers` | permission rules on `mcp__<server>__<tool>`; tool search on by default (`ENABLE_TOOL_SEARCH`, `auto` threshold 10% of context); `alwaysLoad` per server |
| [Cursor](https://cursor.com/docs/context/mcp) | `.cursor/mcp.json`, `~/.cursor/mcp.json` | `mcpServers` | `command`, `args`, `env`, `envFile` | `url`, `headers`, `auth{CLIENT_ID, CLIENT_SECRET, scopes}` | `${env:NAME}`, `${userHome}`, `${workspaceFolder}` | DCR, or the static `auth` block; fixed redirect URLs | toggle in Customize | per-tool approval; enterprise allowlists |
| [VS Code / Copilot](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) | portable `~/.copilot/mcp-config.json` and `.mcp.json`; editor `.vscode/mcp.json` | `mcpServers` (portable), `servers` + `inputs` (editor) | `type: "stdio"`, `command`, `args`, `env`, `cwd`, `envFile` (editor only) | `type: "http"`, `url`, `headers`; `oauthClientId` (portable) or `oauth{clientId}` | `${NAME}`, `${NAME:-default}` (portable); `${env:NAME}`, `${input:id}` with `password: true` (editor) | browser sign-in; secrets in VS Code secret storage | Disable or Uninstall per server | `tools: ["*"]` (portable); per-server `sandbox` (macOS and Linux) |
| [Codex CLI](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) | `~/.codex/config.toml`, `.codex/config.toml` (trusted projects) | `[mcp_servers.<name>]` | `command`, `args`, `env`, `env_vars`, `cwd` | `url`, `bearer_token_env_var`, `http_headers`, `env_http_headers`, `http_headers_helper`, `oauth{client_id, callback_port, client_secret}`, `scopes` | values named by env var, not interpolated | `codex mcp login <name>` with `--oauth-client-registration cimd\|dcr` | `enabled`, `required` | `enabled_tools`, `disabled_tools`, `tools.<tool>.approval_mode`, `tools.<tool>.output_token_limit` |
| [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/) | `settings.json` | `mcpServers` | `command`, `args`, `env`, `cwd` | `httpUrl` (Streamable HTTP), `url` (SSE), `headers`, `oauth{clientId, clientSecret, scopes, …}`, `authProviderType` | `$VAR`, `${VAR}` (undefined → empty); host secrets matching `*TOKEN*`, `*KEY*` and the like are redacted from the env | 401 → discovery → DCR; tokens in `~/.gemini/mcp-oauth-tokens.json`; `/mcp auth` | `mcp.allowed`, `mcp.excluded` | `includeTools`, `excludeTools` (exclude wins); `trust` skips confirmations |
| [goose](https://github.com/aaif-goose/goose/blob/main/crates/goose-agent/src/extension.rs) | `~/.config/goose/config.yaml` | `extensions` | `type: stdio`, `cmd`, `args`, `envs`, `env_keys` | `type: streamable_http`, `uri`, `headers`, `client_id`, `client_secret_key`, `scopes` | `$VAR` / `${VAR}` in `client_id` | CIMD or DCR automatically, `client_id` overrides; secret values stay out of the config ([docs](https://goose-docs.ai/docs/getting-started/using-extensions)) | `enabled` | `available_tools` |
| kinby today | hub library `<name>.json` plus the instance lock | one definition per file | `transport.type: "uvx"`, `package`, `args`, `env` (names only) | `transport.type: "url"`, `url`, `headers{header: VAR}` | the header→variable map only, from the instance `.env` | none | the pick | `include` |

What the formats agree on:

- The `mcpServers` map of name → entry.
- `command`/`args`/`env` for a local server, and `url`/`headers` for a remote one, with `type` naming the transport. Claude Code warns that an entry with a `url` and no `type` is read as stdio.
- `${VAR}` interpolation, with `${VAR:-default}` in Claude Code and VS Code.
- A per-server allow or deny list of tool names.
- Browser OAuth run by the client, with tokens kept outside the config file and a static `client_id` as the override.

Codex (TOML) and goose (YAML) use other field names for the same things.

### B3. Self-hosted assistants

- **LibreChat** ([`mcpServers`](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers)):
  - Uses the same `mcpServers` shape in `librechat.yaml`, adding `oauth{authorization_url, token_url, client_id, …}`, `requiresOAuth` and `customUserVars` (per-user values the user types once, marked `sensitive`).
  - Placeholders are `${ENV}` and `{{LIBRECHAT_USER_ID}}`. When the server supports DCR, the `oauth` block can be omitted.
  - Stored clients and tokens are "bound to the server URL and the OAuth client and endpoint settings". Refresh is automatic when the server issues refresh tokens, and the user signs in again otherwise.
  - Servers created in the UI may use only `streamable-http` or `sse`. A `stdio` server must be in the YAML file.
- **Open WebUI** ([MCP](https://docs.openwebui.com/features/extensibility/mcp.md)):
  - "Native MCP support in Open WebUI is **Streamable HTTP only**"; stdio goes through the `mcpo` proxy.
  - Auth modes: None, Bearer, Session, OAuth (the SSO token), OAuth 2.1 (DCR), and OAuth 2.1 (Static).
  - Tokens are stored per user, encrypted with `WEBUI_SECRET_KEY`, and refreshed automatically. A refresh the provider rejects deletes the sign-in.
  - Its warning matches kinby's routine problem: do not make OAuth 2.1 tools default-on, because "the browser consent redirect cannot happen mid-chat-request". It also has a per-server function-name filter and tool search with "Always Loaded Tools".
- **OpenHands** ([MCP settings](https://docs.openhands.dev/openhands/usage/settings/mcp-settings)):
  - Users add servers in the UI as SHTTP, SSE or STDIO, with auth set to None, Bearer, Header or OAuth.
  - Settings live in `~/.openhands/settings.json` with credentials encrypted, and OAuth tokens in `~/.fastmcp/oauth-mcp-client-cache/`.
  - Its docs note that OAuth servers "need user interaction, so they don't suit headless automation". It toggles whole servers, not single tools.

Across all three, sign-in happens once, interactively, outside the chat. After that, refresh is automatic, and a failed refresh sends the user back to sign in.

### B4. Gateways and aggregators

- **Docker MCP Gateway** ([docs](https://docs.docker.com/ai/mcp-catalog-and-toolkit/mcp-gateway/), [repo](https://github.com/docker/mcp-gateway)): one MCP endpoint in front of many servers, each run in a container with restricted privileges. It injects credentials and selects servers by profile. It has dynamic discovery and code mode behind a feature flag (A3).
- **MetaMCP** ([repo](https://github.com/metatool-ai/metamcp)): groups servers into namespaces with on/off switches for servers and single tools. It can override tool names, descriptions and annotations (such as `readOnlyHint`) and serves the result as one SSE or Streamable HTTP endpoint with API-key or OAuth auth. Tool-selection search is listed as "coming soon".
- **mcp-proxy** ([repo](https://github.com/sparfenyuk/mcp-proxy)): "A bridge between Streamable HTTP and stdio MCP transports". Open WebUI's `mcpo` turns MCP into OpenAPI.

Among these, Docker is the one that ships search and execute (`mcp-find`, `mcp-add`, `code-mode`). For kinby, a gateway would sit between the instance and its servers. kinby's per-turn client already does that job, and a gateway would add a container and a second policy layer without solving sign-in.

## How this maps onto kinby

**Idea 2 first. It unblocks #1215, and code mode depends on it.**

1. **Definitions in the standard shape.** The library entry keeps what kinby has, renamed toward `mcpServers`: `type: "stdio" | "http"`, `command`/`args`/`env` or `url`/`headers`, and `${VAR}` interpolation over the instance's `.env` or a **hub secret**. `include` and `trust_hints` stay as kinby fields beside it (Codex's `enabled_tools`, Gemini's `includeTools` and VS Code's `tools` are the same thing). Pasting a Claude Code or Cursor entry should then work as is, except for `npx`.
2. **Adding from the Registry.** The web app and the **home instance** search `GET /v0.1/servers?search=` and turn a `server.json` into a definition. Secrets marked `isSecret` become pending setup.
3. **OAuth through the SDK.** A `TokenStorage` keeps the client registration and the tokens; where it lives is the #1215 question. The library entry needs no OAuth field: the spec finds it from the 401, as Gemini and goose do. An optional static `client_id` covers servers without CIMD or DCR. The sign-in itself is the hard part: it needs a browser and a redirect that reaches whoever holds the verifier. Every self-hosted project above does it once, interactively, in its web app, and refuses to start it mid-turn.

**Idea 1 after that, in two steps that can ship apart.**

1. **Progressive discovery.** Above a threshold (Claude Code uses 10% of context, the MCP docs say 1–5%), a turn offers a `search_tools` / `describe_tool` pair over the picked servers' included tools, plus a stable `call_tool({name, args})`, instead of every schema. That keeps the `tools` array fixed, as the MCP guidance and [hot-plug-tools.md](hot-plug-tools.md) recommend. Native provider search (Anthropic `defer_loading`, OpenAI `tool_search`) is an optimization per provider, not the base, because kinby runs both providers.
2. **Execute.** Add a `run_code` tool backed by Monty. Picked MCP tools, and later kinby's own read tools, appear as async Python stubs generated from `inputSchema` and `outputSchema`. Each stub calls kinby's broker, which runs the gate and then `client.call_tool`. Copy Pydantic AI's defaults as a starting point and let evals move them: 30 s per snippet excluding tool time, 256 MiB, 100 calls per run, output truncated before it returns.

## Open questions for Jorge

1. **Where does the OAuth sign-in run, and where do tokens live?** These are the #1215 grilling questions. The sources add one fact: everyone runs it once, in the UI, and nobody starts it from a running turn or a routine. Does a sign-in become pending setup on the pick, finished in the web app?
2. **CIMD needs a public HTTPS URL for kinby's client metadata document.** A self-hosted hub may have none. Do we publish one document for the kinby project (for example on the project's site, with loopback `redirect_uris`), use DCR only (deprecated, but what Cockpit supports), or both, in the spec's priority order?
3. **Node.** Most `mcpServers` snippets in the wild are `npx`. Is "whatever users have works" worth adding Node to the image, which ADR 0111 deferred, or do we point users to remote or `uvx` equivalents?
4. **Does a brokered call that asks the user pause the whole script?** Monty can snapshot on a host call, but a gate approval can take hours. Or does code mode refuse any tool the gate would ask about and send the model back to direct calls (Pydantic AI's default)?
5. **What does `run_code` see besides MCP tools?** kinby's own read tools (memory search, web fetch), or MCP tools only at first?
6. **Who chooses the mode?** Automatic above a token threshold (Claude Code, MCP docs), a per-instance switch on the **toolset**, or both?
7. **Python or TypeScript for model-written code?** Cloudflare argues models write TypeScript well, while Anthropic's programmatic tool calling, Pydantic AI and Monty use Python. Python keeps kinby to one language; I found no head-to-head numbers (**unverified**).
8. **ADRs.** OAuth token storage, the `mcpServers`-shaped definition, and code mode are three separate decisions, one ADR each.
