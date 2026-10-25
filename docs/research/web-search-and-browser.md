# Web search, fetch and browser automation for a self-hosted agent

Research for [#920](https://github.com/jorgesolerrr/kinby/issues/920), feeding [#161](https://github.com/jorgesolerrr/kinby/issues/161). Prices and docs seen on 2026-10-25.

## Short answer

- **Search: one `web_search` tool with a swappable backend, Brave Search API by default.** It works with every model provider, costs $5 per 1,000 queries with $5 of free credit a month, and allows 50 queries per second. Tavily and Exa are drop-in alternatives behind the same tool. SearXNG is the zero-key option for users who want no third party, at the price of upstream engines suspending it. Provider-native search (Anthropic, OpenAI, both $10 per 1,000) only works with that provider's models, so it can be an optional fast path but not the tool.
- **Fetch: a plain `web_fetch` tool in the instance, httpx plus trafilatura, returning Markdown.** It costs nothing, needs no key, and adds no browser. It does not run JavaScript. When a page comes back empty, the agent switches to the browser rather than to a paid reader.
- **Browser: Playwright in the instance, driven through accessibility-tree snapshots, as an opt-in capability.** Storage state (a JSON file under the instance) keeps sign-ins between runs. Headless Chromium adds about 0.8 GB to the image and a few hundred MB of memory per live browser, so it should not ship in the default instance image. A human takes over by having the routine pause and hand them a headed or remote view; a hosted browser (Browserbase) is the paid route when the box cannot show one.

## Context that shapes the answer

- Each instance runs in its own container built from `python:3.14-slim` ([`Dockerfile`](../../Dockerfile)), and tools are Python plugins ([`src/kinby/plugins/tools.py`](../../src/kinby/plugins/tools.py)). Nothing web-facing exists in `src/kinby` today.
- Models come from several providers, so a tool that only works with one provider's model cannot be the default.
- Routines run unattended. A browser job must fail cleanly or ask a human, never hang on a CAPTCHA.
- One user runs the hub on a small box, so per-instance memory and image size matter more than throughput.

## 1. Web search

| Option | Price (seen 2026-10-25) | Free tier | Limits | Notes |
|---|---|---|---|---|
| [Brave Search API](https://brave.com/search/api/) | $5 / 1,000 requests | $5 credit a month; card required | 50 queries/s | Own independent index. Storing results or training on them needs a plan with storage rights. |
| [Tavily](https://www.tavily.com/pricing) | $0.008 / credit pay-as-you-go; plans from $30/mo for 4,000 credits | 1,000 credits a month, no card | Not published | [Credits](https://docs.tavily.com/documentation/api-credits): basic search 1, advanced 2, extract 1 per 5 URLs. Returns content snippets built for LLMs, plus an extract endpoint. |
| [Exa](https://exa.ai/pricing) | $4 to $7 / 1,000 searches (lite/fast); $1 / 1,000 pages of contents | $10 credit a month, no card | Not published | Neural search; deep search $12 to $15 / 1,000. |
| [SearXNG](https://docs.searxng.org/admin/installation-docker.html) self-hosted | Free software; costs a container | Not applicable | Set by upstream engines | Metasearch over Google, Bing, DuckDuckGo and others. JSON output stays off until you add `json` to `search.formats` ([settings](https://docs.searxng.org/admin/settings/settings_search.html)). |
| [Anthropic web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool) | $10 / 1,000 searches plus result tokens | None | Org rate limit | Server tool: runs inside the Claude API call, Claude models only. Always cites; domain allow/block lists; `max_uses`. |
| [OpenAI web search](https://developers.openai.com/api/docs/pricing) | $10 / 1,000 calls plus search content tokens | None | Not published | Server tool on the Responses API, OpenAI models only. |

Findings:

- **Provider-native search ties the tool to the model.** Anthropic's search is a [server tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool): the API runs it and returns encrypted result blocks that must be sent back unchanged on later turns. Results cannot be handed to a different provider's model. It is also the most expensive per query of the options here, and results count as input tokens on every later turn.
- **Brave is the cheapest keyed API with a published rate limit**, and its own index means it is not a reseller of Google or Bing. Its free credit covers about 1,000 queries a month, enough for one person.
- **SearXNG's cost is reliability, not money.** SearXNG suspends an engine after it errors: a day after a CAPTCHA, an hour after HTTP 429, 15 days after a Cloudflare CAPTCHA, 7 days after a Google reCAPTCHA ([settings](https://docs.searxng.org/admin/settings/settings_search.html)). A single home IP that searches often from routines will hit these. The recommended Compose setup also runs a Valkey container for the limiter ([docs](https://docs.searxng.org/admin/installation-docker.html)). Its resource footprint is not documented (**unverified**).
- **Result quality for agents: unverified.** No independent benchmark was run for this note. Tavily and Exa market themselves as built for LLMs and return page content with results, which saves a fetch; Brave returns snippets and needs a fetch for full text. A small eval in kinby's own harness would settle it.

Recommendation: a `web_search(query, max_results)` tool whose backend is chosen in settings, with one key in `.env`. Ship Brave first, since it has the clearest pricing and limits. Tavily, Exa and SearXNG are small adapters behind the same tool; SearXNG is the documented path for users who want no third party. Leave provider-native search out of v1. Add it later as an option for Claude or OpenAI models if the token cost of a separate fetch turns out to matter.

## 2. Web fetch

| Option | Runs | JavaScript | Cost | Notes |
|---|---|---|---|---|
| [trafilatura](https://trafilatura.readthedocs.io/en/latest/) | In-process Python | No | Free, Apache 2.0 | Main-text extraction with metadata; outputs Markdown, TXT, JSON and more. Claims to beat other open-source extractors on its own benchmark. |
| readability (Mozilla's algorithm, Python ports) | In-process | No | Free | Not checked against primary sources here (**unverified** which port is maintained). |
| [Jina Reader](https://jina.ai/reader/) (`r.jina.ai/<url>`) | Jina's servers | Yes, default engine uses a headless browser | Free at 20 RPM without key; tokens billed with a key (10M free) | Sends every URL to a third party. Its ReaderLM models are CC-BY-NC. |
| [Anthropic web fetch](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool) | Anthropic's servers | **No** | No charge beyond tokens | Claude models only. Fetches only URLs already in the conversation; refuses URLs carrying credentials; handles PDFs. |
| Tavily extract | Tavily's servers | Advanced mode (**unverified** that it renders JS) | 1 credit per 5 URLs | Comes free if Tavily is the search backend. |

Findings:

- **Even Anthropic's own fetch does not run JavaScript.** Its docs point to a browser for pages that need one ([web fetch](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool)). A fetch tool without JavaScript is what the providers ship too.
- **Anthropic's fetch has a guard worth copying.** It only fetches URLs that appeared earlier in the conversation and refuses URLs with credentials in them, to limit exfiltration through a URL the model writes ([URL validation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool#url-validation)). kinby's gate (ADR 0014) is where an equivalent check would live.
- **A fetch costs tokens in proportion to the page.** Anthropic's figures: about 2,500 tokens for a 10 kB page, 25,000 for a 100 kB docs page. A `max_chars` cap on the tool keeps routines bounded.

Recommendation: `web_fetch(url, max_chars)` in-process: httpx GET, trafilatura to Markdown, PDFs through a text extractor, a size cap, and a clear "no readable text, page may need JavaScript" result. When that result comes back, the agent uses the browser tool if the instance has it. Skip Jina Reader as a default: it sends every URL a user reads to a third party, which cuts against self-hosting.

## 3. Browser automation

### How the model acts

| Option | What the model sees | Language | Notes |
|---|---|---|---|
| Playwright called from kinby tools | Whatever the tool returns; [`aria_snapshot()`](https://playwright.dev/python/docs/aria-snapshots) gives a YAML accessibility tree | Python | Same library the e2e skill already uses. |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Accessibility tree; screenshots and x/y clicks only with `--caps=vision` | Node | Its README recommends the CLI for coding agents because the MCP puts large tool schemas and accessibility trees in context. |
| [playwright-cli](https://github.com/microsoft/playwright-cli) | Snapshot written to a file; the agent reads or `find`s what it needs | Node | Has `state-save`/`state-load`, and `show`: a dashboard where a human clicks into the viewport to take over input and presses Escape to hand back. |
| [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp) | Accessibility snapshot with uids; screenshots for visual checks | Node | 60 tools, many for debugging and performance ([tool reference](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/tool-reference.md)). Aimed at coding agents inspecting their own pages. |
| [browser-use](https://github.com/browser-use/browser-use) | Page state plus screenshots by default (`use_vision=True`, [parameters](https://docs.browser-use.com/customize/agent/all-parameters)) | Python ≥ 3.11, MIT | Runs its own agent loop with its own LLM wrappers (OpenAI, Anthropic, Google, Ollama). |
| [Stagehand](https://github.com/browserbase/stagehand) | Natural-language `act`/`extract`/`observe` resolved by a model | TS, Python, Go; MIT | Runs locally or on Browserbase; action caching is a Browserbase feature. |

Findings:

- **Accessibility snapshots are the cheap default, screenshots the fallback.** Playwright MCP and Chrome DevTools MCP both act on the accessibility tree and keep screenshots for vision-only cases. A snapshot is still large: in a measurement for this note, `aria_snapshot()` of the English Wikipedia "Web browser" article was 74,707 characters (roughly 19k tokens at 4 chars/token). The tool has to cap or scope snapshots (a locator, a depth, a search) to keep long routines affordable. Per-step token cost for screenshots was not measured (**unverified**).
- **browser-use and Stagehand bring their own agent loop.** Each step makes its own LLM call through its own model wrapper. That duplicates kinby's runtime, its model settings and its gate, and it hides each action from the permission check. Calling Playwright directly keeps every click a kinby tool call that the gate and the run record see.
- **MCP servers need Node in the instance.** The three MCP/CLI options are Node packages, and #161 lists MCP as out of scope for now. Playwright for Python needs no Node.
- **Reliability on long unattended jobs: unverified.** No source gives comparable failure rates. The design choices that help are the same everywhere: a step budget, a per-step timeout (browser-use defaults to 180 s), and a clean failure when a step finds a CAPTCHA or a sign-in page.

### Cost in the container (measured 2026-10-25)

Measured in `python:3.14-slim` with `pip install playwright` (1.64.1) and `playwright install --with-deps --only-shell chromium`:

- The `playwright` package: 141 MB. The headless shell: 273 MB. Its system libraries bring the container's added layer to **817 MB** uncompressed on top of the 119 MB base.
- One browser with one Wikipedia page open: **684 MB** summed RSS across all processes. That sum counts shared pages more than once, so real use is lower; treat it as an upper bound.
- Playwright's docs ask for `--ipc=host` (or a bigger `/dev/shm`) or Chromium "can run out of memory and crash", and `--init` against zombie processes ([Docker docs](https://playwright.dev/python/docs/docker)). As root the Chromium sandbox is off; for untrusted sites they recommend a non-root user and a seccomp profile. Both are settings the hub must apply when it starts an instance with a browser.
- `--only-shell` installs just the headless shell, which is enough when the browser always runs headless ([browsers](https://playwright.dev/python/docs/browsers)). A headed browser for human takeover needs full Chromium (281 MB on macOS per the same page) plus a display.

### Signed-in sessions

- Playwright's [storage state](https://playwright.dev/python/docs/auth) saves cookies, localStorage, IndexedDB and passkeys to a JSON file and loads it into a new context. It does not save sessionStorage. Its docs warn the file can impersonate the user and must stay out of repositories. In kinby it would live in the instance directory, beside secrets, never in the workspace.
- Browserbase [Contexts](https://docs.browserbase.com/features/contexts) persist the whole user data directory, sessionStorage included, encrypted at rest, kept until deleted.
- Sessions expire. Every browser routine must detect a signed-out page and ask for a fresh sign-in rather than retry.

### Human takeover for a CAPTCHA or sign-in

- Local: run the browser headed and show it. playwright-cli's `show` dashboard lets a human click into the viewport and press Escape to hand control back. Inside a headless container on a small box this needs a virtual display and a VNC or CDP-screencast view served through the hub (**unverified**: not built or measured here).
- Hosted: Browserbase's [live view](https://docs.browserbase.com/features/session-live-view) gives an iframe URL where a person clicks, types and scrolls in the running session. Pricing ([seen 2026-10-25](https://www.browserbase.com/pricing)): free tier 1 browser hour a month, 15-minute sessions, no CAPTCHA solving; Developer $20/month for 100 hours, then $0.12 per hour, with automatic CAPTCHA solving. Browser Use Cloud advertises browsers from $0.02/hour with a live view URL and CAPTCHA solving ([repo](https://github.com/browser-use/browser-use)); its live view's interactivity was not checked (**unverified**).
- Either way the routine's job is the same: notice the wall, pause, notify the user with the takeover link, and resume or fail after a timeout. The pause-and-notify part is kinby's, whatever browser runs underneath.

Recommendation: a small set of kinby browser tools over Playwright for Python (`open`, `snapshot` with a scope and a size cap, `click`/`fill` by snapshot ref, `screenshot`, `save_session`), installed only in instances that opt in, through a separate image stage or a package recipe, since it adds about 0.8 GB to every instance image. Storage state per site lives in the instance directory. A CAPTCHA or sign-in pauses the run and notifies the user. Start with a local headed takeover for when the user is at the box; add a Browserbase backend (same tools, remote CDP endpoint, live view link in the notification) for unattended routines that hit walls. Do not adopt browser-use or Stagehand: they would run a second agent loop around the gate.

## Open questions for #161

- Does the browser ship as a separate instance image, or does a package recipe install it on top (the Dockerfile already says a package's recipe adds what it needs)?
- Should `web_fetch` refuse URLs the model wrote itself, as Anthropic's fetch does, or leave that to the gate's approval?
- How does the hub show a takeover view: a VNC page, a CDP screencast, or only a hosted live view?
