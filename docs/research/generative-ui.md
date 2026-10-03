# Generative UI for agents: the state of the art

Wayfinder ticket: jorgesolerrr/kinby #519, part of map #516. Date: 2026-10-03.
Question: how do agents generate UI for their users today, and what does that mean for per-instance views that routines keep current in kinby's shadcn/ui web app?

For each system: free-form code or a declared catalogue, how data binds and updates without regenerating the UI, sandboxing, persistence, and how the user asks for changes. Claims link to the source that owns them. A claim with no primary source is marked **unverified**.

## Short answer

1. The field has split into three tiers, a split CopilotKit names directly ([Generative UI spectrum](https://www.copilotkit.ai/generative-ui-spectrum)):
   - **Controlled.** The app ships fixed components and the agent picks one and fills its props (Vercel AI SDK tool parts, CopilotKit `useComponent`).
   - **Declarative.** The agent composes a JSON spec from a catalogue the app declares (Google A2UI, Vercel json-render).
   - **Open-ended.** The agent or a server ships HTML/JS that runs in a sandboxed iframe (MCP Apps, OpenAI Apps SDK, Claude artifacts, Gemini dynamic view).
2. Every system that updates a UI without regenerating it keeps **structure and data apart**. Data changes travel as small messages: A2UI `updateDataModel` at a JSON Pointer path, AG-UI `STATE_DELTA` as JSON Patch, AI SDK data parts reconciled by `id`, MCP Apps tool results pushed into a stateless template.
3. Persistence is weak almost everywhere. MCP Apps defers it. OpenAI says widget state is ephemeral and must not hold business data. Claude artifacts add a separate key-value store. A view that lives for months and that a routine updates every morning is not a solved product shape anywhere. kinby would have to own that part itself.
4. For kinby, the declarative tier fits best. A view is a spec plus a JSON data model, both stored as files in the instance. The spec composes a small catalogue of shadcn components that kinby owns. Routines and turns change the data through a tool, and the user changes the spec by asking the agent. Its shape should follow A2UI (flat component list, JSON Pointer bindings, separate data model) so that adopting A2UI later is a mapping and not a rewrite.

## 1. Google A2UI

- **What it is.** A protocol for "how can AI agents safely send rich UIs across trust boundaries". v0.9.1 is current and v1.0 is a release candidate ([a2ui.org](https://a2ui.org/)).
- **Catalogue, not code.** "Agents can only use pre-approved components from your catalog" ([a2ui.org](https://a2ui.org/)). Each surface names a `catalogId`. A basic catalogue ships standard components and functions, and custom catalogues use the same JSON Schema structure ([v0.9.1 spec](https://a2ui.org/specification/v0.9.1-a2ui/)).
- **Messages.** The server sends four: `createSurface` (`surfaceId`, `catalogId`), `updateComponents`, `updateDataModel` (a value at a JSON Pointer `path`, or the whole model when the path is omitted) and `deleteSurface` ([v0.9.1 spec](https://a2ui.org/specification/v0.9.1-a2ui/)).
- **Structure.** Components form a flat adjacency list: each has an `id`, a `component` type and props, and children are referenced by id. One component must have the id `root` ([v0.9.1 spec](https://a2ui.org/specification/v0.9.1-a2ui/)).
- **Data binding.** Props are literals, JSON Pointer paths (absolute `/user/name`, or relative inside a list scope) or function calls. A list binds children to an array with `{"path": "/employees", "componentId": "template_id"}` and repeats the template once per item ([v0.9.1 spec](https://a2ui.org/specification/v0.9.1-a2ui/)). Updating data therefore re-renders the bound parts and does not touch the component tree.
- **Events back.** A component's `action` is either a server `event` (a `name` plus `context`) or a local `functionCall`. The client sends `action` messages (name, surfaceId, sourceComponentId, timestamp, context) and `error` messages. Validation functions (`required`, `regex`, `email` and others) run on the client and disable a button when a check fails ([v0.9.1 spec](https://a2ui.org/specification/v0.9.1-a2ui/)).
- **v1.0.** Adds typed bidirectional function calls (`callRendererFunction`, `callAgentFunction` and their responses), lets `createSurface` carry the initial tree and data model in one message, and removes theme properties "to defer visual styling entirely" to the client. With `sendDataModel: true` the renderer attaches the surface's whole data model to every message it sends to the agent that created it ([v1.0 spec](https://a2ui.org/specification/v1.0-a2ui/)).
- **Transport and renderers.** The transport must be reliable, ordered and framed. A2A, AG-UI, MCP, SSE and WebSocket are named ([v0.9.1 spec](https://a2ui.org/specification/v0.9.1-a2ui/)). Renderers exist for Lit, Angular, Flutter, React and Markdown ([a2ui.org](https://a2ui.org/)).
- **Persistence and user edits.** The spec says nothing about storing a surface, and nothing about a user editing the layout. A surface lives as long as the session that streams it.

## 2. MCP Apps (and MCP-UI)

- **What it is.** An official MCP extension (SEP-1865) whose stable spec is dated 2026-01-26. It renders in Claude, ChatGPT, VS Code, Goose, Postman and other hosts ([ext-apps README](https://github.com/modelcontextprotocol/ext-apps)). MCP-UI "is now standardized into MCP Apps" and now ships the client SDK (`AppRenderer`) plus an adapter for legacy hosts ([mcpui.dev](https://mcpui.dev/)).
- **Free-form code.** A tool points at a template through `_meta.ui.resourceUri` (`ui://...`), and the resource's mimeType "MUST be `text/html;profile=mcp-app`" ([spec 2026-01-26](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)).
- **Data binding.** The template is fixed and the data arrives as notifications over a postMessage JSON-RPC bridge. After `ui/initialize`, the host sends `ui/notifications/tool-input`, optionally `tool-input-partial` while the model is still streaming, and `ui/notifications/tool-result` with `content` and `structuredContent`. The app can call `tools/call` to fetch fresh data. A tool marked `visibility: ["app"]` is hidden from the model and callable only by the UI ([spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)).
- **Sandbox.** The template runs in a sandboxed iframe. Web hosts use a double iframe through a sandbox proxy on a different origin. CSP domains are declared per resource (`connectDomains`, `resourceDomains`, `frameDomains`, `baseUriDomains`), and the host "MUST block connections to undeclared domains". Camera, microphone, geolocation and clipboard write must each be requested ([spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)).
- **Fitting the host.** The host supplies theme CSS variables (`--color-text-primary`, `--font-sans` and others) and display modes `inline`, `fullscreen` and `pip` ([spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)).
- **Talking to the model.** `ui/update-model-context` hands data to future turns, and `ui/message` posts a user message, which may need the user's consent ([spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)).
- **Persistence.** Deferred to a future version: the templates are stateless and the data is passed in ([spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)). The user cannot edit the UI. The server author owns it.

## 3. OpenAI Apps SDK

- **Built on MCP Apps.** It now layers on MCP Apps: "Use the MCP Apps field or method whenever the shared specification covers the capability." `window.openai` remains for ChatGPT-only extras (`toolInput`, `toolOutput`, `widgetState`, `setWidgetState`, `callTool`, `sendFollowUpMessage`, `requestCheckout`) ([ChatGPT UI guide](https://developers.openai.com/apps-sdk/build/chatgpt-ui)).
- **State.** OpenAI names three layers: business data (owned by the server and long-lived), UI state (ephemeral and scoped to one rendered instance) and cross-session state (durable storage the developer runs). "Widget state belongs to one rendered UI instance. Do not use it as the source of truth for business data" ([ChatGPT UI guide](https://developers.openai.com/apps-sdk/build/chatgpt-ui)).
- **Sandbox and display.** The sandbox is an iframe with CSP allowlists, which plugin review checks against the UI's behavior. The display modes are inline card, inline carousel, fullscreen and picture-in-picture ([ChatGPT UI guide](https://developers.openai.com/apps-sdk/build/chatgpt-ui)).

## 4. Vercel AI SDK

- **Controlled tier.** A tool call becomes a typed message part `tool-${toolName}`. The app maps each part to its own React component, which receives `part.output` as props ([generative UI](https://ai-sdk.dev/docs/ai-sdk-ui/generative-user-interfaces)). The states are `input-streaming`, `input-available`, `output-available`, `output-error`, `output-denied`, `approval-requested` and `approval-responded` ([tool usage](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-tool-usage)).
- **Updating in place.** Custom `data-<name>` parts are reconciled by id: "When you write to a data part with the same ID, the client automatically reconciles and updates that part." Transient parts reach only `onData` and stay out of history ([streaming data](https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data)).
- **Persistence.** It persists `UIMessage`s, so the UI lives inside the chat transcript ([generative UI](https://ai-sdk.dev/docs/ai-sdk-ui/generative-user-interfaces)).
- **RSC `streamUI`.** Marked "currently experimental. We recommend using AI SDK UI for production" ([RSC overview](https://ai-sdk.dev/docs/ai-sdk-rsc/overview)).
- **json-render (Vercel Labs).** The declarative tier. The catalogue is defined with Zod schemas and the spec is flat: a root plus an elements map. Dynamic props use `$state`, `$cond` and `$computed`, a built-in `setState` action handles reactivity, and `SpecStreamCompiler` renders the spec while it streams. It has renderers for React, Vue, Svelte, React Native and others ([json-render](https://github.com/vercel-labs/json-render)).

## 5. CopilotKit and AG-UI

- **AG-UI.** An event protocol between agent and user, and the transport, not a UI format. Its event categories are lifecycle, text message, tool call, state, activity, reasoning, subagent and special (raw, custom) ([events](https://docs.ag-ui.com/concepts/events)). State syncs both ways: a `STATE_SNAPSHOT` at start or after a reconnect, then `STATE_DELTA` as JSON Patch (RFC 6902) that the frontend applies in order ([state](https://docs.ag-ui.com/concepts/state)).
- **CopilotKit.** Uses the controlled / declarative / open-ended taxonomy above. In the controlled tier, `useComponent` registers a React component as a tool the agent can invoke ([generative UI docs](https://docs.copilotkit.ai/generative-ui)). In the declarative tier it maps to A2UI, a "typed catalog of components and instances" built on AG-UI ([spectrum](https://www.copilotkit.ai/generative-ui-spectrum)).
- **Persistence.** UI is still tied to the chat or the agent's shared state. Neither source describes a durable dashboard.

## 6. Claude artifacts

- **Free-form content.** Anything from documents and diagrams to SVG, HTML and React components. Claude makes an artifact when the content is "significant and self-contained, typically over 15 lines" ([help center](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)).
- **Changes.** The user asks in the conversation, highlights text and picks "Edit with Claude", or edits template-based artifacts (Docs, Slides, Design) directly. Each edit regenerates or patches the code ([help center](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)).
- **Data and persistence.** An artifact can call Claude itself. On paid plans it gets persistent storage, personal or shared, limited to 20 MB of text ([help center](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)). Data lives in that store, separate from the code, which matches point 2 of the short answer. Artifacts run sandboxed and can be published and remixed ([help center](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)).

## 7. Newer: Gemini generative UI

- **Fully generated per prompt.** Google ships "dynamic view", where Gemini "designs and codes a fully customized interactive response for each prompt", and "visual layout" in the Gemini app and in Search AI Mode. The paper is "Generative UI: LLMs are Effective UI Generators" ([Google Research blog](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)).
- **Persistence.** These experiences are one-off answers, not living views.

## Comparison

| System | Tier | Data updates without a rebuild | Sandbox | Persistence | User changes |
|---|---|---|---|---|---|
| A2UI | Declarative catalogue | `updateDataModel` at a JSON Pointer | None needed (data only) | Not specified | Agent sends new components |
| MCP Apps | Open-ended HTML | Tool results into a fixed template | Double iframe + CSP | Deferred | None (server-authored) |
| OpenAI Apps SDK | Open-ended HTML | Same as MCP Apps | iframe + CSP + review | Widget state is ephemeral | None |
| AI SDK tool/data parts | Controlled | Data parts reconciled by id | None (app code) | In chat messages | None |
| json-render | Declarative catalogue | `$state` bindings, `setState` | None needed | App's job | Regenerate the spec |
| AG-UI / CopilotKit | Transport + all three | `STATE_DELTA` JSON Patch | Per tier | Agent state | Via chat |
| Claude artifacts | Open-ended code | Separate storage | Sandboxed | Storage, 20 MB text | Ask Claude, inline edit |

## Options for kinby

kinby's need differs from every system above. A view belongs to an **instance** rather than a message, outlives any **thread**, is written by **routines** with no user present, and is changed when the user asks the agent. "Artifact" is taken (image artifact), so **view** is the working term. It needs an entry in `CONTEXT.md` before code.

**Option A: fixed view kinds (controlled).** kinby ships a few React view kinds (board, table, list, checklist), and a tool writes typed data into them. This is the cheapest and safest, and it looks native. The weakness is customization: "add a salary column" or "group by company" works only when the kind already supports it, and a new kind means a web app release.

**Option B: declarative spec over a kinby catalogue (recommended).** A view is two files in the instance: a spec (a flat component list with ids, a `root`, props bound by JSON Pointer, list templates) and a JSON data model.
- **Catalogue.** A small set of shadcn components that kinby owns: Card, Table, Badge, a Kanban column, Stat, Chart (recharts is already a dependency), Checklist, Link.
- **Updates.** Routines and turns change the data through a tool that applies JSON Patch or a set at a pointer. The user asks the agent to change the spec, and the same tool validates it against the catalogue schema.
- **Transport.** The contract carries a view's spec and data as events or a subscription. Data changes go as patches, the AG-UI snapshot/delta pattern, which fits kinby's sequenced events and `head_sequence` replay ([ADR 0048](../adr/0048-the-contract-crosses-the-network-in-typed-frames-one-socket-per-instance.md)).
- **Safety.** No code runs in the browser, so no sandbox is needed. A prompt-injected job posting can only put text into the data.
- **Path to A2UI.** Following A2UI's shape keeps adoption cheap: if A2UI v1.0 settles and gains a React/shadcn catalogue, kinby's spec maps onto `createSurface` / `updateComponents` / `updateDataModel`. Adopting A2UI wholesale today means taking on a v1.0 release candidate and its churn. Whether any A2UI renderer targets shadcn is **unverified**.

**Option C: open-ended HTML in a sandbox (MCP Apps / artifacts style).** The agent writes HTML/JS per view, served from a separate origin in an iframe, with data pushed through a postMessage bridge.
- **For.** Maximum freedom, and MCP Apps is a real standard, so a view could in principle also come from an MCP server.
- **Against.** It needs a second origin on the hub, CSP and theme variables, and it will not look like the shadcn app. Routines that read untrusted input (job postings, email) would be writing code that runs in the user's browser. The user's edits regenerate code, which is fragile across months of routine updates.
- **Fit.** Better as a later escape hatch, for example a catalogue component that hosts an MCP App, than as the base.

**What all three share.** Keep the data model as the routine's write target and the spec as the user's customization target, and store both as instance files so they survive restarts, migrations and replacement like any other instance state. No surveyed system offers that durable, routine-fed shape, so it is kinby's own work in every option.
