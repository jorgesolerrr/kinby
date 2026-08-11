# Web chat UI options for a Python (Claude Agent SDK) agent backend

Resolves wayfinder ticket `10-research-web-chat-ui.md`. Researched 2026-08-10 against project docs, GitHub repos, and issue trackers.

**Requirements recap:** streaming chat; mid-conversation approval buttons (approval-first routines); agent-initiated messages arriving while the user is away (real push, not request/response); later growth into an approvals inbox, routines management screens, and a memory browser. Self-hosted, effectively single-user. Web is the first interface and the test surface; Telegram/Discord come later behind a channel abstraction.

---

## 1. Batteries-included Python-native chat UIs

### Chainlit

- **What it is:** Python framework that ships a complete React chat frontend over a FastAPI + Socket.IO backend; you write only Python callbacks (`@cl.on_message`, `@cl.on_chat_start`, `@cl.step`). ~12.4k stars, Apache-2.0. [github.com/Chainlit/chainlit](https://github.com/Chainlit/chainlit)
- **Streaming:** first-class (`msg.stream_token()`); steps/tool-call visualization built in.
- **Approval buttons:** the strongest of the batteries-included group. `cl.Action` buttons attach to any message (`cl.Message(..., actions=[...])`) with `@cl.action_callback` handlers, and `cl.AskActionMessage` *blocks the agent loop until the user clicks* — exactly the approval-first primitive needed. Actions can appear mid-conversation and be removed after use ([docs.chainlit.io/api-reference/action](https://docs.chainlit.io/api-reference/action)).
- **Unprompted/push messages:** works *within a live session* — `await cl.Message(...).send()` can be called from any asyncio task holding the session context, so a routine can post into an open tab. But sessions are WebSocket-bound: there is no built-in "deliver to user who is away" path. Injecting a message from an external process into a session is a known pain point ([issue #2274 "On-demand send and handle user message"](https://github.com/Chainlit/chainlit/issues/2274)); away-delivery means writing to Chainlit's data layer (SQLAlchemy-based chat persistence) so the message appears on chat resume — workable but hand-rolled, and there's no notification badge concept.
- **Auth:** built in for self-hosting: password auth, OAuth (Google/GitHub/etc.), and header auth, JWT-signed via `CHAINLIT_AUTH_SECRET` ([docs.chainlit.io/authentication/overview](https://docs.chainlit.io/authentication/overview)).
- **Customizability ceiling:** theming/custom CSS+JS; `CustomElement` lets you render arbitrary JSX (shadcn+Tailwind environment) inside messages ([docs.chainlit.io/api-reference/elements/custom](https://docs.chainlit.io/api-reference/elements/custom)) — good for rich approval cards. But the shell is chat-only: no sidebar pages for an approvals inbox / routines / memory browser. The escape hatch is replacing the whole frontend with `@chainlit/react-client` against the Chainlit backend ([cookbook/custom-frontend](https://github.com/Chainlit/cookbook/tree/main/custom-frontend)) — at which point you own a React app anyway. Rough edges exist (e.g. [CustomElement not re-rendered after refresh/resume, #2576](https://github.com/Chainlit/chainlit/issues/2576)).
- **Maintenance health (2026):** original team (Chainlit SAS, pivoted to Literal AI) stepped back **May 1, 2025**; now community-maintained by `@Chainlit/chainlit-maintainers` under a formal agreement, with "no warranties on future updates" from the company. Releases have continued through 2026 (2.11.x on [PyPI](https://pypi.org/project/chainlit/)), ~93 open issues. Alive but stewardship risk is real.

### Gradio (`gr.ChatInterface` / `gr.Chatbot`)

- **Streaming:** yes, via generator `yield` ([gradio.app/docs/gradio/chatinterface](https://www.gradio.app/docs/gradio/chatinterface)).
- **Approval buttons:** no in-message action primitive. Built-in chat buttons are like/dislike, retry, edit. Approvals would be simulated with separate `gr.Button` components in a `gr.Blocks` layout or option-style replies — clumsy for per-message mid-conversation approvals.
- **Push:** no server-initiated push into a session. The pattern is polling with [`gr.Timer`](https://www.gradio.app/docs/gradio/timer) re-querying state; event flow starts in the browser. Away-delivery = poll a store on load. Feasible, inelegant.
- **Auth:** basic username/password via `demo.launch(auth=...)`, plus OAuth mainly aimed at Hugging Face Spaces. Adequate for single-user behind a reverse proxy.
- **Ceiling / health:** custom components system exists; layout flexibility is decent but everything lives inside Gradio's Blocks/rerun-event model. Maintenance is excellent (Hugging Face core project, very active). Best fit is demos/internal tools, not a persistent multi-screen agent product.

### Streamlit chat

- **Streaming:** yes (`st.chat_message`, `st.chat_input`, `st.write_stream`).
- **Approval buttons:** possible with `st.button` inside chat messages, but the script-rerun execution model makes stateful mid-conversation approval flows awkward (every click reruns the script; you manage flow state in `st.session_state`).
- **Push:** the weakest point. Streamlit "requires an execution flow that starts in the browser"; server-push is an open feature request ([issue #11665 — WebSocket notify component to avoid polling](https://github.com/streamlit/streamlit/issues/11665)). Workaround is `st.fragment(run_every=...)` polling ([docs](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)), which wastes reruns and has blocking pitfalls ([forum: fragment rerun blocks app](https://discuss.streamlit.io/t/st-fragment-rerun-blocks-app/86360)).
- **Auth:** native OIDC via `st.login()` since 1.42 — any OIDC provider, config in `secrets.toml`; advanced OAuth params not exposed ([docs](https://docs.streamlit.io/develop/concepts/connections/authentication), [issue #11703](https://github.com/streamlit/streamlit/issues/11703)). No simple built-in password auth without OIDC.
- **Ceiling / health:** multipage apps could host routines/memory screens, but the rerun model fights long-lived agent sessions. Snowflake-backed, very actively maintained. Wrong execution model for this product.

### Open WebUI (as a frontend to a custom backend)

- **Integration model:** your backend appears as a model — either an external OpenAI-compatible endpoint or a Python "Pipe" function/Pipelines server inside Open WebUI ([docs.openwebui.com](https://docs.openwebui.com/)). Streaming: yes (OpenAI-style SSE chunks). Fundamentally **request/response**: the UI calls your backend when the user sends a message.
- **Approval buttons:** no native mid-chat approval primitive for external backends; interactivity beyond text requires writing Open WebUI Functions/Actions (community "action buttons" exist but are plugin-level, tied to Open WebUI's internal plugin API, not your external agent loop).
- **Push:** unprompted messages cannot land in a chat thread. The adjacent features are **Channels** (Slack-like rooms, [docs](https://docs.openwebui.com/features/channels/), [issue #8050](https://github.com/open-webui/open-webui/issues/8050)) with incoming webhooks to post messages into a channel, and per-user outbound webhooks for notifications ([docs](https://docs.openwebui.com/features/administration/webhooks/)). A routine could post results to a channel via webhook — but that's a separate surface from the conversation, and multi-agent/channel-agent conversation is still an open discussion ([#14588](https://github.com/open-webui/open-webui/discussions/14588)).
- **Auth:** best-in-class for self-hosting — multi-user, RBAC, OAuth/LDAP, admin panel.
- **Health / license:** extremely active, ~100k+ stars. **License changed at v0.6.6 (April 2025)** from BSD-3 to "Open WebUI License": BSD-3-based plus a branding-retention clause (irrelevant for ≤50-user deployments, but it's no longer OSI-pure) ([discussion #8467](https://github.com/open-webui/open-webui/discussions/8467), [docs.openwebui.com/license](https://docs.openwebui.com/license/), [HN thread](https://news.ycombinator.com/item?id=43901575)).
- **Verdict:** a large product you'd bend, not a component you'd own. Its Ollama/multi-model chat worldview doesn't map to "one agent teammate with approvals and routines"; the customizability ceiling for *your* flows is low because your backend sits behind an OpenAI-shaped hole.

---

## 2. Protocol / own-frontend options

### AG-UI protocol

- Open, event-based protocol (from the CopilotKit team, mid-2025) standardizing agent↔frontend communication: streamed message events, tool-call events, shared state sync (event-sourced diffs), and **human-in-the-loop interrupts** ("pause, approve, edit, retry mid-flow") ([docs.ag-ui.com](https://docs.ag-ui.com/introduction), [github.com/ag-ui-protocol/ag-ui](https://github.com/ag-ui-protocol/ag-ui)).
- Transport-agnostic (SSE or WebSocket). Python SDKs exist via first-party integrations (Pydantic AI, LangGraph, CrewAI, LlamaIndex, Google ADK, Microsoft Agent Framework...). **No first-party Claude Agent SDK integration** as of Aug 2026 (OpenAI Agents SDK is "in progress"); you'd write a thin event mapper from Claude Agent SDK stream events to AG-UI events — straightforward but on you. Main mature client is CopilotKit (React), which pulls in its own ecosystem. Young protocol; useful as a *shape to imitate* (its event taxonomy is exactly your use case) even if not adopted wholesale.

### assistant-ui (React)

- MIT, ~11.5k stars, YC-backed, actively maintained ([github.com/assistant-ui/assistant-ui](https://github.com/assistant-ui/assistant-ui)). Composable shadcn-style primitives (`Thread`, `Message`, `Composer`); streaming, markdown, auto-scroll, attachments built in; **generative UI: render tool calls as React components with inline human approval** — the approval-button requirement is a documented first-class pattern ([docs: human-in-the-loop / tool UI](https://www.assistant-ui.com/docs)).
- Backends: Vercel AI SDK data stream (primary), LangGraph, or a custom runtime — a Python FastAPI backend speaking the AI SDK data-stream protocol plugs in directly. Optional paid "Assistant Cloud" for persistence; not required (you persist threads yourself, which you must do anyway for the memory/routines model).

### Vercel AI SDK UI (`useChat`) over FastAPI/SSE

- AI SDK v5's UI Message Stream protocol is plain **SSE** with documented part types (text deltas, tool calls, data parts) — explicitly designed for custom backends: set header `x-vercel-ai-ui-message-stream: v1` and stream from any server ([ai-sdk.dev/docs/ai-sdk-ui/stream-protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)).
- Python-side helpers exist: official FastAPI template ([vercel.com/templates/next.js/ai-sdk-python-streaming](https://vercel.com/templates/next.js/ai-sdk-python-streaming)), [fastapi-ai-sdk](https://github.com/doganarif/fastapi-ai-sdk), [py-ai-datastream](https://github.com/elementary-data/py-ai-datastream) (writeup: [elementary-data.com blog](https://www.elementary-data.com/post/building-a-python-native-backend-for-ai-chat-streaming)). Mapping Claude Agent SDK `stream_async` events to data-stream parts is a small adapter.
- **Cost of owning the frontend:** a Vite/Next React app: one chat route (assistant-ui gives the chat surface in ~a day), plus auth (single user: a shared-secret cookie or Authelia/Caddy in front — trivial self-hosted), plus your push channel (§3). Realistically ~1–2 weeks to parity with Chainlit's chat, mostly plumbing not UI.
- **What it buys:** no ceiling. Approvals inbox, routines management, memory browser are just additional routes hitting your FastAPI. The web frontend becomes *a channel client of your channel abstraction* rather than a framework your backend lives inside — the same backend events later feed Telegram/Discord adapters. No stewardship risk concentrated in a chat-framework dependency (assistant-ui is replaceable; the protocol boundary is yours).

---

## 3. WebSocket vs SSE for agent-initiated push (self-hosted, single-user)

- **SSE:** one-way server→client over plain HTTP. Inherits your existing auth cookies, TLS, reverse-proxy config, and logging; auto-reconnect with `Last-Event-ID` built into `EventSource`; degrades gracefully through proxies ([germano.dev/sse-websockets](https://germano.dev/sse-websockets/), [websocket.org/comparisons/sse](https://websocket.org/comparisons/sse/)). Historic 6-connections-per-domain limit is gone under HTTP/2.
- **WebSocket:** bidirectional; needed only if the client must send signals *on the same connection* (cancel generation, live steering). Costs: upgrade handshake through proxies, manual heartbeat/reconnect, separate auth path ([svix.com/resources/faq/websocket-vs-sse](https://www.svix.com/resources/faq/websocket-vs-sse/)).
- **Pattern that fits this app:** two SSE uses — (a) per-request stream for the chat turn (the AI SDK protocol above), and (b) one long-lived "events" SSE connection per open tab for agent-initiated messages, approval requests, and badge updates. Client→server actions (send message, approve/deny, cancel) are ordinary POSTs — bidirectionality via HTTP, which is fine at single-user scale ([sniki.dev: Go with SSE for your AI chat app](https://www.sniki.dev/posts/sse-vs-websockets-for-ai-chat/)). Crucially, **"away" delivery is a persistence problem, not a transport problem**: routines write results/approval-requests to the DB; the events stream is just the live notifier; on reconnect the client fetches undelivered items. Any transport choice that skips the outbox table is wrong. WebSockets remain a fine alternative (single user = negligible resource cost) but buy nothing SSE+POST doesn't here.

---

## 4. What Lindy-class products include beyond chat (target shape evidence)

- **Lindy:** human-in-the-loop is a *task-centric* surface, not only chat: agent pauses on a gated action, the pending action shows as a **draft in a task view** with approve/deny, plus email fallback and **sidebar notification badges** for tasks awaiting confirmation ([docs.lindy.ai/testing/human-in-the-loop](https://docs.lindy.ai/testing/human-in-the-loop)). Implication: an approvals inbox decoupled from the live chat thread is core, and approvals need an out-of-band fallback channel.
- **LibreChat:** MIT, ~42k stars, very active ([github.com/danny-avila/LibreChat](https://github.com/danny-avila/LibreChat)). Beyond chat: agents + marketplace, MCP servers/skills/subagents, artifacts, code interpreter, web search, memory (user memory feature), multi-user auth (OAuth2/LDAP), admin panel. Custom backends again enter as OpenAI-compatible endpoints — same "your agent behind a model-shaped hole" limitation as Open WebUI; no unprompted-message path into threads.
- **Khoj:** AGPL-3.0, ~36k stars, active ([github.com/khoj-ai/khoj](https://github.com/khoj-ai/khoj)). A "personal AI" whose web UI includes exactly the growth surfaces in question: **Automations** (scheduled research/routines whose results are *delivered as newsletters/notifications to the inbox* — i.e., away-delivery routed to email, sidestepping web push), custom **Agents**, and a knowledge-base/search view over indexed personal files (a memory browser).
- **Converged shape:** chat + an approvals/tasks inbox with badges + routines (automations) CRUD screens + a memory/knowledge browser + out-of-band notification fallback (email/Telegram). Chat-only frameworks cover one of five surfaces.

---

## Recommendation

**1. Own a small React frontend: assistant-ui + FastAPI, AI SDK data-stream SSE for chat turns, a persistent SSE events channel + outbox table for push.** Best fit for every hard requirement: mid-conversation approvals are a documented assistant-ui pattern; agent-initiated/away messages are native (DB outbox + events stream); the approvals inbox, routines screens, and memory browser are ordinary routes; the web UI becomes the first client of the channel abstraction instead of a framework the backend lives inside. Borrow AG-UI's event taxonomy for the internal event schema without adopting the protocol. Trade-off: highest upfront cost (~1–2 weeks to chat parity), you own auth (trivial single-user: shared secret or reverse-proxy auth) and thread persistence (needed regardless).

**2. Chainlit — fastest credible v1 / test surface.** Streaming, blocking `AskActionMessage` approvals, and self-host auth out of the box in <1 day of Python. Trade-offs: push only into live sessions (away-delivery = hand-rolled data-layer writes, no badges); chat-only shell can't grow the inbox/routines/memory screens (escape hatch is `@chainlit/react-client`, i.e., owning a React app anyway); community-maintained since May 2025 with real stewardship risk. Sensible as a throwaway week-1 harness if the backend keeps a UI-agnostic event boundary so migration to option 1 is cheap.

**3. Open WebUI — not recommended as the frontend.** Superb polish, auth, and maintenance, but your agent sits behind an OpenAI-compatible request/response hole: no mid-chat approval primitive for external backends, unprompted messages only via Channels/webhooks outside the conversation, and the product's multi-model worldview fights the single-teammate design. License now carries a branding clause (harmless at this scale, but a signal). Gradio and Streamlit are ruled out primarily by their no-server-push execution models (polling-only) and weak mid-conversation approval ergonomics.

**Suggested decision:** option 1 as the destination; optionally option 2 for the first two weeks only if a same-day test surface is worth a planned throwaway.
