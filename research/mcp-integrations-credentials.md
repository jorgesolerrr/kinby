# MCP integrations for Gmail / GitHub / Linear + credential management (self-hosted, headless Docker)

Research for wayfinder ticket 08. Researched 2026-08-10 against vendor docs, official repos, and deployment reports. Context: open-source personal AI teammate, Python + Claude Agent SDK, Docker Compose on a home box with **no public IP**, integration priority Gmail → GitHub → Linear.

---

## 1. Server landscape per integration

### Gmail — no GA official server; the space is the weakest of the three

| Option | Type | Auth | Send? | Health |
|---|---|---|---|---|
| Google official Gmail MCP (Developer Preview) | Remote hosted `https://gmailmcp.googleapis.com/mcp/v1` | OAuth 2.0 (bring your own web-app client) | **No — draft only** | Preview, gated |
| [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) | Local (stdio or streamable HTTP), Python/FastMCP, `uvx workspace-mcp` | OAuth 2.0/2.1, bring your own GCP client | **Yes** | Active, 3k+ stars, 2.6k commits, PyPI |
| [GongRzhe/Gmail-MCP-Server](https://github.com/GongRzhe/Gmail-MCP-Server) | Local stdio, TypeScript | OAuth 2.0, tokens in `~/.gmail-mcp/credentials.json` | Yes | **Archived 2026-03-03** (1.2k stars, read-only) |
| [baryhuang/mcp-headless-gmail](https://github.com/baryhuang/mcp-headless-gmail) | Local stdio, Docker-first | Tokens passed **as tool arguments** (no local credential setup) | Yes (get/send only) | Niche, narrow coverage |

- **Google's official server** ([config guide](https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server)) exists as of mid-2026 but is part of the gated Google Workspace Developer Preview Program. Tools: `create_draft`, `get_thread`, `label_message`, `label_thread`, `list_drafts`, `list_labels`, `search_threads`, `unlabel_message`, `unlabel_thread`. Scopes `gmail.readonly` + `gmail.compose`. **Deliberately no send tool** — a human must send drafts from Gmail. You still must create your own OAuth web client with the MCP host's redirect URI (e.g. `https://claude.ai/api/mcp/auth_callback`), which does not fit a headless Agent SDK deployment. Good future watch item, not usable today for an autonomous agent that sends mail.
- **The long-time community default (GongRzhe) is archived** — a real maintenance-health signal for this whole niche. Its feature set (send, draft, read, search, labels, filters, batch ops, attachments) and its Docker/custom-callback-URL auth pattern remain the reference design.
- **Best pick today: `workspace-mcp`** — actively maintained, Python (matches the stack), ~15 Gmail tools (search, send, draft, labels, filters, attachments) plus 11 other Google services if wanted, stdio and streamable-HTTP transports, encrypted disk-backed token cache with a stateless mode for containers, single-user and multi-user modes. Downside: it's a big server (120+ tools) — restrict enabled services/toolsets to Gmail-only to avoid context bloat (see §5).
- A defensible alternative is **skipping MCP for Gmail** and writing a thin in-process SDK MCP server over `google-api-python-client` — the Gmail surface the agent needs (search/read/draft/send/label) is small, and it removes a third-party dependency from the most sensitive integration. Comparison discussion: [Gmail MCP vs Gmail API for agents](https://www.scalekit.com/blog/gmail-mcp-vs-api).

### GitHub — solved; official server is excellent

[github/github-mcp-server](https://github.com/github/github-mcp-server) (Go, very actively maintained, 1000+ commits):

- **Remote hosted** at `https://api.githubcopilot.com/mcp/` — zero local processes. Auth: one-click OAuth for interactive clients, **or a PAT as a Bearer header**, which is exactly the headless pattern and is the literal example in the [Agent SDK MCP docs](https://code.claude.com/docs/en/agent-sdk/mcp).
- **Local**: `ghcr.io/github/github-mcp-server` Docker image (stdio) with `GITHUB_PERSONAL_ACCESS_TOKEN`; a Jan-2026 release also added an HTTP server mode taking the token from the `Authorization` header per request (works with GHES). [OAuth login docs](https://github.com/github/github-mcp-server/blob/main/docs/oauth-login.md) exist for local OAuth but need a browser + fixed localhost callback port — irrelevant for our box; use a PAT.
- **Toolsets**: `--toolsets` / `GITHUB_TOOLSETS` (default `context,repos,issues,pull_requests,users`), a `--read-only` mode, and dynamic toolset discovery / tool-search — the best-in-class answer to tool-count bloat. Jan 2026 added automatic OAuth scope filtering. ([2026 deep dive](https://kansei-link.com/en/insights/github-mcp-deep-dive-2026), [GitHub setup docs](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server))
- **Auth recommendation**: fine-grained PAT scoped to the repos the agent needs (solo use); GitHub App only if this ever becomes multi-user/org. Consensus in 2026 writeups: don't start new deployments on classic PATs.

**Verdict: use the remote server + fine-grained PAT in an `Authorization` header.** No process to run, no OAuth dance, GitHub maintains it.

### Linear — solved; official hosted server, and it accepts an API key

[Linear's official MCP server](https://linear.app/docs/mcp): hosted at `https://mcp.linear.app/mcp` (streamable HTTP; `https://mcp.linear.app/sse` is a deprecated fallback; `https://mcp.linear.app/mcp/readonly` for read-only).

- **Auth**: primary is OAuth 2.1 with dynamic client registration (browser-based — bad for headless), **but it also accepts `Authorization: Bearer <token>` with a Linear personal API key**, created in Linear Settings → API. That makes headless trivial. Enterprise Okta-managed auth also exists.
- **Tools**: find/create/update issues, projects, comments, teams, users, labels, statuses; Feb-2026 update added initiatives, initiative updates, project milestones, project updates, project labels. Maintained by Linear itself.
- Community stdio servers (e.g. [tacticlaunch/mcp-linear](https://mcpservers.org/servers/tacticlaunch/mcp-linear)) that wrap the GraphQL API with `LINEAR_API_KEY` are now redundant given the official server takes an API key directly.

**Verdict: official hosted server + personal API key header.** Nothing to run locally.

---

## 2. OAuth in a headless self-hosted Docker deployment

The only integration where this bites is Gmail (GitHub = PAT, Linear = API key).

### What does NOT work
- **Google's OAuth device flow is not available for Gmail.** The [limited-input-device flow](https://developers.google.com/identity/protocols/oauth2/limited-input-device) allows only `openid/email/profile`, `drive.appdata`, `drive.file`, and two YouTube scopes. No Gmail scopes, full stop. (GitHub's OAuth *does* support [device flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow) if ever needed, but PATs make it moot.)
- **The Claude Agent SDK will not run an OAuth flow for you.** Per the [SDK MCP docs](https://code.claude.com/docs/en/agent-sdk/mcp): "The SDK doesn't open a browser or run an interactive OAuth flow. When a configured server returns an authorization challenge and no stored token is available, the agent run continues without that server's tools, and the server reports status `needs-auth`" (poll `get_mcp_status()` in Python to detect it). You must obtain tokens in your own application and pass them via `headers`. `claude mcp login` pre-auth exists for the CLI but doesn't fit an unattended Docker service.

### What actually works (patterns in the wild)
1. **One-time browser flow elsewhere, then copy the refresh token** — the dominant pattern. Run the [server-side authorization flow](https://developers.google.com/workspace/gmail/api/auth/web-server) with `access_type=offline` once on a laptop (`google-auth-oauthlib` `InstalledAppFlow.run_local_server()`), then place the resulting token JSON (refresh token + client id/secret) on the server via a mounted volume. Client libraries auto-refresh access tokens forever after; no user interaction again.
2. **SSH port-forward variant**: run the flow *on* the box with `ssh -L 8080:localhost:8080 box` so the `http://localhost:8080` redirect completes in the laptop's browser. Slightly slicker, same result.
3. **Reverse-proxy callback** (GongRzhe's cloud pattern): a stable public callback URL through a proxy/tunnel. Overkill for single-user.
4. **Token-as-context** ([mcp-headless-gmail](https://github.com/baryhuang/mcp-headless-gmail)): the MCP server holds no credentials; the orchestrating app injects access/refresh tokens per call. Clean separation, but moves refresh bookkeeping into our code.

For an OSS project, ship pattern 1 as a **guided one-time setup CLI** (print auth URL, catch redirect locally or accept pasted code, write token file into the Compose volume).

### Refresh handling and the traps
- Refresh tokens from a **published/Production** consent screen last indefinitely; expiry triggers are: **7-day expiry while consent screen is in "Testing"** (the #1 self-hosted gotcha — [Google audience docs](https://support.google.com/cloud/answer/15549945), [Nango invalid_grant guide](https://nango.dev/blog/google-oauth-invalid-grant-token-has-been-expired-or-revoked)), 6 months unused, password change (some scopes), user revocation, >50 outstanding refresh tokens per client/account. Setup docs must tell users to **click "Publish app"** (staying unverified is fine for personal use — you click through the "unverified app" warning once).
- Because each self-hosting user creates **their own GCP project + OAuth client**, the project never needs Google's restricted-scope verification/CASA audit — that burden only exists for a shared/hosted OAuth client. This is exactly why every self-hosted Gmail tool (n8n, Home Assistant, etc.) makes users do the ~15-minute GCP console dance. That console dance *is* the real onboarding friction.
- **Storage**: token files live in a bind-mounted or named volume (`~/.gmail-mcp/` pattern, or workspace-mcp's encrypted disk-backed cache); they mutate at runtime so they cannot be env vars.

---

## 3. Event-driven triggers without a public IP

| Source | Push mechanism | Works w/o public IP? | Polling fallback |
|---|---|---|---|
| Gmail | `users.watch` → Cloud Pub/Sub | **Yes — via Pub/Sub *pull*/StreamingPull** (outbound-only) | `history.list` / `messages.list` every N min |
| GitHub | Webhooks (need public HTTPS) | Not directly; `gh webhook forward` or tunnel | Events/Notifications API with ETag + `X-Poll-Interval` |
| Linear | Webhooks (need public HTTPS, admin) | No | GraphQL polling on `updatedAt` filters |

- **Gmail is the pleasant surprise**: [push notifications](https://developers.google.com/workspace/gmail/api/guides/push) publish `{emailAddress, historyId}` to a Pub/Sub topic; with a **pull subscription (StreamingPull)** the home box gets near-real-time delivery over an outbound gRPC connection — no inbound port, no tunnel. Setup: create topic, grant `gmail-api-push@system.gserviceaccount.com` the Publisher role, create pull subscription, call `users.watch`, and **renew the watch daily (it expires after 7 days)**; ack messages after processing. ([Unipile 2026 guide](https://www.unipile.com/gmail-api-push-notifications/)) The cost is more GCP setup for users.
- **GitHub**: webhooks need a public URL. Alternatives: (a) **polling** the [Events API](https://docs.github.com/en/rest/activity/events) / Notifications API with conditional requests — ETag-304 responses don't burn rate limit, server tells you cadence via `X-Poll-Interval` (~60s); (b) [`gh webhook forward`](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/using-the-github-cli-to-forward-webhooks-for-testing) — GA, forwards real repo/org webhooks to localhost over an outbound connection; officially positioned for testing, but it is a working no-tunnel path; (c) [smee.io](https://smee.io/) proxy — explicitly not for production (unauthenticated channels).
- **Linear**: [webhooks](https://linear.app/developers/webhooks) require an admin-configured public HTTPS non-localhost URL with HMAC-SHA256 signature verification. Without a tunnel: poll the [GraphQL API](https://linear.app/developers/graphql) with the API key.
- **Tunnels** (for users who *want* real webhooks): **Cloudflare Tunnel** — free, stable hostname, production-grade, but requires a Cloudflare account + a domain moved to Cloudflare DNS (highest one-time friction); **Tailscale Funnel** — easiest if already on a tailnet, public HTTPS on a `ts.net` hostname; **ngrok free** — instant but 1 GB/mo, 3 endpoints, browser interstitial, non-stable URL without paid plan. ([2026 comparison](https://instatunnel.my/blog/comparing-the-big-three-a-comprehensive-analysis-of-ngrok-cloudflare-tunnel-and-tailscale-for-modern-development-teams), [freeCodeCamp roundup](https://www.freecodecamp.org/news/top-ngrok-alternatives-tunneling-tools/))
- **Recommendation for an OSS project**: **poll-first by default** (works for 100% of users, zero extra accounts), with Gmail Pub/Sub-pull and tunnel-based webhooks as documented opt-in upgrades. Requiring GCP Pub/Sub + a tunnel in the happy path would gut onboarding.

---

## 4. Secrets storage for Docker Compose self-hosting

What comparable projects actually do, in ascending rigor:

1. **`.env` + `env_file:` + gitignore, with a committed `.env.example`** — the de-facto standard (n8n, most self-hosted stacks). Threat model: anyone with Docker socket access can read env via `docker inspect`; on a single-user home box this is the accepted "good enough" baseline. ([n8n self-hosting writeups](https://dev.to/lyraalishaikh/self-hosting-n8n-in-2026-why-and-how-to-reclaim-your-automation-3o50), [threat-model discussion](https://blog.stackademic.com/secrets-management-in-docker-compose-env-sops-bitwarden-and-the-good-enough-threat-model-2bbc6d8e1064))
2. **Compose file-based `secrets:`** (works without Swarm): secrets mounted at `/run/secrets/<name>`, kept out of `docker inspect` env output. Requires the app (or image) to support `*_FILE`-style config — n8n and postgres images do; worth supporting in our own app (`GMAIL_TOKEN_FILE` etc.).
3. **SOPS + age for git-tracked config**: encrypt `.env`/token files in the repo, decrypt at deploy with `sops exec-env` / `exec-file` so plaintext stays in memory. age beats GPG for homelabs (single key file, no daemon/keyring/trust model). Increasingly common in homelab writeups ([Will Pike](https://pikemd.com/notes/sops-age-docker-compose/), [cmmx.de](https://blog.cmmx.de/2025/08/27/secure-your-environment-files-with-git-sops-and-age/)). Offer as a documented option for users who version their deployment, not a requirement.
4. **Runtime-mutable OAuth token files** are a separate class from static secrets: they must live in a **named/bind-mounted volume** with tight permissions (this is what Gmail MCP servers already do). Never bake them into images; back the volume up.

Pragmatic posture: static secrets (PATs, API keys, OAuth client id/secret) in `.env`; mutable tokens in a data volume; support `*_FILE` vars; document SOPS/age as the upgrade path. Compensate at the authorization layer: fine-grained PAT, Linear readonly endpoint where applicable, minimal Gmail scopes.

---

## 5. Composing multiple MCP servers under the Claude Agent SDK

Source: [Agent SDK MCP docs](https://code.claude.com/docs/en/agent-sdk/mcp), [tool search docs](https://code.claude.com/docs/en/agent-sdk/tool-search).

- **Config**: `ClaudeAgentOptions(mcp_servers={...})` dict — stdio (`command`/`args`/`env`), `"type": "http"` / `"sse"` (`url` + `headers`), or in-process **SDK MCP servers** defined in Python (`create_sdk_mcp_server`). Alternatively `.mcp.json` at project root via the `project` setting source. Header values support `${VAR}` env expansion in JSON configs — the natural join point with `.env` secrets.
- **Naming/collisions**: tools are namespaced `mcp__<server-key>__<tool>` — the dict key prevents cross-server collisions by construction. Permission with `allowed_tools=["mcp__linear__*", "mcp__github__list_issues", ...]`; wildcards per server. Prefer `allowed_tools` over `bypassPermissions`.
- **Startup cost**: stdio servers (and HTTP servers without a cached tool list) **block the first turn until connected**, default `MCP_TIMEOUT` 30 s; remote servers with a cached tool list don't delay (connect on first call); in-process SDK servers never delay. `npx`/`uvx`-launched stdio servers cold-start slowly (package fetch) — pre-install or bake into the Docker image. Each stdio server is a child process per agent session, so a spawn-agent-per-task design pays repeated startup; a long-lived `ClaudeSDKClient` session amortizes it.
- **Context bloat**: 10+ servers of tool definitions can eat 10–25k+ tokens before the first user turn. **MCP tool search is now enabled by default in the SDK/Claude Code layer** — definitions are deferred and loaded on demand; `alwaysLoad: true` exempts a server (and makes startup wait for it). Finer per-tool `defer_loading` control at the raw-API level (`advanced-tool-use-2025-11-20` beta) is still being plumbed through the SDKs ([python #525](https://github.com/anthropics/claude-agent-sdk-python/issues/525), [typescript #281](https://github.com/anthropics/claude-agent-sdk-typescript/issues/281)). Complementary mitigations: GitHub server `--toolsets` to shrink what's even registered; enable only Gmail in workspace-mcp; note `allowed_tools` restricts *use*, not context — deferral is what saves tokens.
- **Failure handling**: `system`/`init` message carries per-server status (`connected` / `pending` / `failed` / `needs-auth` / `disabled`); `pending` is not failure; poll `get_mcp_status()` later. Tool results >25k tokens are spilled to a file automatically (`MAX_MCP_OUTPUT_TOKENS`).
- **Verdict**: server-per-integration composes fine at our scale (3–5 servers). Recommended shape: **GitHub and Linear as remote HTTP servers with static tokens in headers (zero local processes), Gmail as the one local server** (workspace-mcp over stdio/HTTP, or an in-process SDK server for maximum control).

---

## Bottom line

- GitHub and Linear are essentially solved: official, maintained, remote-hosted servers that accept a static token in a header — perfect for headless Docker, nothing to run locally.
- Gmail is the hard integration on **both** axes: no GA official server (and the preview one can't send), the leading community server archived, the best current option is a large multi-service community server; and auth requires per-user GCP project setup, a one-time browser OAuth flow (device flow unavailable for Gmail scopes), and publishing the consent screen to Production to dodge 7-day token expiry.
- Triggers: default to polling everywhere; Gmail uniquely offers real push without a public IP via Pub/Sub *pull*; tunnels (Cloudflare Tunnel best, Tailscale Funnel easiest-if-tailnet, ngrok most limited free) stay opt-in.
- Secrets: `.env` + volume-mounted token files is the honest community baseline; support `*_FILE` vars and document SOPS+age as the upgrade path.
