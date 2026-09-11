# Front-end stack for the kinby web app

Wayfinder ticket: jorgesolerrr/kinby #214, child of the map "kinby next: hub, packages, web app, usage map" (#211). Date: 2026-09-11.
Question: for a TypeScript single-page app in `web/`, React with Vite, served as static files by the aiohttp server that `kinby serve` already runs, which setup, client pattern, state model, test tooling, lint and type check, check entry point, and hosting layout does kinby adopt?

Sources: the docs, changelogs, and GitHub source of Vite 8.3, create-vite 9.2, React 19.3, TypeScript 7.0, oxlint 1.82, oxfmt 0.67, Biome 2.5, typescript-eslint 8.70, Vitest 5.0, Testing Library 16.3, Playwright 1.63, aiohttp 3.14 (branch `3.14`), hatchling, Caddy 2, MDN and the WHATWG WebSocket and HTML specs, Pydantic 2.13, and the `pingdotgg/t3code` repository on `main`. Versions were read from the npm registry on the date above. Four background agents read the sources; every claim below names its page or file. On top of the reading, `Event.model_json_schema()` was run against the checkout to see what the TypeScript types would be generated from.

## Short answer

Scaffold `web/` with `npm create vite@latest`'s `react-ts` template (Vite 8 on Rolldown, React 19, oxlint), move TypeScript to 7 so `tsc -b` is the native compiler and oxlint's type-aware rules run, format with oxfmt, test the projector and the connection as plain TypeScript under Vitest with a few Testing Library component tests in jsdom, and keep Playwright as a `just e2e` recipe rather than a pre-commit check. Vite builds straight into `src/kinby/web/dist`, hatchling lists that directory as an artifact, and a Node 24 stage in the Dockerfile builds it before `pip install .`. aiohttp serves it from the same `Application` the receiver already owns: `web.static("/assets", ...)`, a `/{tail:.*}` GET that returns `index.html` with `Cache-Control: no-cache`, and an `on_response_prepare` hook that marks `/assets/` immutable. The WebSocket is one more GET route, `heartbeat=30`, with the handler task reading frames and one `asyncio.Task` per subscription writing them. The client is a hand-written connection class of about a hundred lines: full-jitter backoff capped at 16 seconds, reset after 30 seconds up, resubscribe with `after_sequence` from the store, drop replays by sequence, reject in-flight unary calls on drop and never replay them. State is a pure `apply(thread, event)` projector behind `useSyncExternalStore`; no Zustand, no TanStack Query, no virtualization until a flow needs them. One `justfile` at the root runs the four Python commands plus `npm run check`, and the container image moves from Debian's Node 20 to Node 24 so the coder can run the web checks too.

## The seam this has to fit

`kinby serve` is `_serve_instance` in `src/kinby/cli/main.py`. It boots the runtime and, when the manifest has a `serve` block, starts a `Receiver` (`src/kinby/core/receiver.py`), which builds one `web.Application` with `GET /health` and `POST /signals/{routine}`, runs it through `web.AppRunner` and `web.TCPSite` on the running loop, and awaits `runner.cleanup()` in the `finally`. The reasons for aiohttp are in `docs/research/http-server-options.md`; the socket and the bundle are two more routes on that application.

The contract is `src/kinby/contracts`. `Method` and `Subscription` are frozen dataclasses tying a name to a Pydantic command and result, and `Dispatcher.dispatch(method, payload, scopes)` returns a `ContractModel`, an `ErrorEnvelope` on any failure, while `Dispatcher.subscribe(...)` is an async generator that yields items and, on failure, one `ErrorEnvelope` (`src/kinby/core/dispatcher.py`). `thread.subscribe` takes `{thread_id, after_sequence: int = 0}`; `EventLog.subscribe` first yields every stored event with `sequence > after_sequence`, then live ones from a queue (`src/kinby/core/events.py`). `sequence` is per thread, 1-based, `len(stored(thread_id)) + 1` under a lock, so it is contiguous. Every `Event` carries `sequence`, `thread_id`, `turn_id`, `timestamp`, and a `payload` discriminated on `type`. The CLI's `ContractClient` (`src/kinby/cli/client.py`) is the in-process client the web client mirrors: it parses the generic result into the type the method promises, once.

The CLI already projects the stream: `_render_turn` in `src/kinby/cli/repl.py` matches on `MessageDelta`, `ToolCall`, `ToolGated`, `ToolResult`, `Warning`, and the three closing payloads through `is_turn_closing`. The web client's projector is that `match`, in TypeScript, producing state instead of stdout.

Packaging and deployment: the Dockerfile is `python:3.14-slim`, installs Debian's `nodejs` and `npm` for Claude Code and Codex, copies `src` and runs `pip install .` with hatchling. There is no CI workflow; checks run on the developer machine and in the coder container, whose workspace is a clone of this repository. The public box puts Caddy in front with a bare `reverse_proxy coder:8787` (`docker/Caddyfile`, ADR 0030). A JSON Schema pattern already exists: `uv run python -m kinby.instance.schema` writes `docs/schema/kinby.schema.json` from the manifest model and `tests/test_schema.py` asserts the checked-in file is current.

## Scaffold, versions, and Node

`npm create vite@latest web -- --template react-ts` with create-vite 9.2.1 produces `index.html`, `vite.config.ts` (just `defineConfig({ plugins: [react()] })`), `tsconfig.json` referencing `tsconfig.app.json` and `tsconfig.node.json`, `.oxlintrc.json`, `src/main.tsx`, `src/App.tsx`, `src/vite-env.d.ts`, and `public/`. Scripts: `dev: vite`, `build: tsc -b && vite build`, `lint: oxlint`, `preview: vite preview`. Pins today: `vite ^8.3.0`, `react ^19.2.8`, `@vitejs/plugin-react ^6.1.1`, `oxlint ^1.81.0`, `typescript ~6.0.2` (https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts). Since create-vite 9.1.0 (2026-06-23) the React templates ship oxlint; `--eslint` swaps in ESLint with typescript-eslint, `eslint-plugin-react-hooks`, and `eslint-plugin-react-refresh` (https://github.com/vitejs/vite/blob/main/packages/create-vite/CHANGELOG.md).

Vite 8 (2026-03-12) replaced Rollup and esbuild with Rolldown; `build.rollupOptions` is deprecated in favour of `build.rolldownOptions` (https://vite.dev/blog/announcing-vite8, https://vite.dev/config/build-options). It requires Node 20.19+ or 22.12+ (https://vite.dev/guide/). `tsconfig.app.json` sets `moduleResolution: bundler`, `verbatimModuleSyntax`, `erasableSyntaxOnly`, `noEmit`, `jsx: react-jsx`, `noUnusedLocals`, `noUnusedParameters`, and relies on TypeScript 6's new default `strict: true` (https://github.com/vitejs/vite/blob/main/packages/create-vite/template-react-ts/tsconfig.app.json, https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/).

TypeScript 7.0 shipped on 2026-07-08 and `npm install -D typescript` now gives the native Go compiler as `tsc`, 8 to 12 times faster on full builds, with `--build` and project references supported. It has no programmatic API until 7.1, so tools that embed the checker stay on `@typescript/typescript6` (https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/). The template still pins `~6.0.2`; kinby should start on 7, because type-aware oxlint needs it (below) and nothing in the template needs the 6 API.

Node: v24 is Active LTS, v22 is Maintenance, v20 reached end of life on 2026-03-24 (https://nodejs.org/en/about/previous-releases). Debian 13 ships `nodejs 20.19.2` (https://packages.debian.org/trixie/nodejs), which satisfies Vite 8 and oxlint but not Vitest 5 (`^22.12.0 || ^24.0.0`) and is outside Playwright's documented 22/24/26 support. The image therefore needs Node 24 installed on its own, both for the build stage and so the coder can run `npm run check` in its workspace. Package manager: npm, with `package-lock.json` and `npm ci`, which errors when the lock and `package.json` disagree and never writes to `package.json` (https://docs.npmjs.com/cli/v11/commands/npm-ci). Vite documents npm, pnpm, yarn, and bun equally; one package under `web/` gets nothing from pnpm's workspace features, and Corepack is no longer distributed with Node 25+ (https://github.com/nodejs/corepack#readme).

## Production build and how the bundle reaches the server

`vite build` writes `dist/index.html` unhashed plus `dist/assets/[name]-[hash].js|css` and hashed imported assets; files in `public/` are copied unhashed to the outDir root (https://vite.dev/guide/assets, `packages/vite/src/node/build.ts`). Vite's own cache guidance, under "Load Error Handling": "make sure to set `Cache-Control: no-cache` on the HTML file, otherwise the old assets will be still referenced" (https://vite.dev/guide/build). Hashed files can be immutable; `index.html` cannot be cached. Vite also documents `vite:preloadError` for the moment a stale tab imports a chunk a deploy deleted; T3 Code reloads once on it (below).

Where the output lands decides how it ships. Set `build.outDir` to `../src/kinby/web/dist` and `build.emptyOutDir: true` (the default is `true` only when outDir is inside root, otherwise Vite warns instead of emptying; https://vite.dev/config/build-options). `kinby.web` is a regular package with an `__init__.py`, and `dist/` is gitignored. A dev checkout and an installed wheel then look the same to the server: `importlib.resources.files("kinby.web") / "dist"` is a real `pathlib.Path` for an unzipped install, and `as_file()` covers the zipped case (https://docs.python.org/3.14/library/importlib.resources.html). Hatchling respects `.gitignore`, so the directory has to be listed as an artifact, which is the documented route for "files that are ignored by your VCS, such as those that might be created by build hooks" (https://hatch.pypa.io/latest/config/build/):

```toml
[tool.hatch.build.targets.wheel]
artifacts = ["src/kinby/web/dist/**"]
```

Two ways to run the build. A hatchling custom build hook (`hatch_build.py`, `BuildHookInterface.initialize`) could shell out to `npm run build` so `pip install .` is self-contained (https://hatch.pypa.io/latest/plugins/build-hook/custom/), at the price of Node inside every Python build environment and a build that depends on the network. A Docker multi-stage build keeps it plain: a `node:24-slim` stage runs `npm ci && npm run build` in `web/`, the Python stage does `COPY --from=web /web/../src/kinby/web/dist src/kinby/web/dist` before `pip install .`. On the developer machine, `just build-web` (below) fills the same directory. The hook only pays off if kinby publishes wheels from a bare `uv build`, which it does not today.

The dev loop does not go through the bundle. `vite` serves on 5173 and proxies the socket to `kinby serve`: `server.proxy: { "/ws": { target: "ws://127.0.0.1:8787", ws: true } }` (https://vite.dev/config/server-options#server-proxy). The client derives the socket URL from `window.location.origin` (below), so no `VITE_` variable is needed in either mode.

## Serving the bundle from aiohttp with the single-page fallback

`UrlDispatcher.add_static(prefix, path, *, name=None, expect_handler=None, chunk_size=256*1024, show_index=False, follow_symlinks=False, append_version=False)` registers a `StaticResource` that matches by prefix, resolves the file in the default executor, refuses anything outside the directory with 404 and directories with 403, and returns a `FileResponse` (https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.UrlDispatcher.add_static; `aiohttp/web_urldispatcher.py`, `StaticResource`). `FileResponse` sets `ETag` (`mtime_ns-size` in hex), `Last-Modified`, `Content-Length`, `Accept-Ranges`, guesses `Content-Type`, answers `If-None-Match` and `If-Modified-Since` with 304, `Range` with 206, uses `loop.sendfile` when available, and looks for `.br` then `.gz` siblings when `Accept-Encoding` allows (`aiohttp/web_fileresponse.py`, `ENCODING_EXTENSIONS`, `_get_file_path_stat_encoding`). It sets no `Cache-Control` anywhere.

The docs say "Use `add_static` for development only. In production, static content should be processed by web servers like nginx or apache," citing performance and "several past security vulnerabilities in aiohttp only affected applications using `add_static`" (web_reference, same anchor). For kinby the alternative is Caddy's `file_server` with `try_files {path} /index.html` (https://caddyserver.com/docs/caddyfile/patterns), which would mean a second deploy artifact and a `kinby serve` that cannot show its own UI without a proxy. One user, one process, hashed immutable assets: aiohttp hosting is the right trade, and Caddy stays a TLS terminator that "also supports WebSocket connections, performing the HTTP upgrade request then transitioning the connection to a bidirectional tunnel" with no extra directive (https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

Route resolution makes the fallback safe regardless of registration order. Resources are indexed by their canonical path with any `{var}` part stripped back to the previous `/`, and `resolve()` walks the request path backwards, so `/assets/app.js` tries `/assets` before `/`, and only resources under the same key are tried in registration order (`aiohttp/web_urldispatcher.py`, `UrlDispatcher.resolve`, `_get_resource_index_key`). A `/{tail:.*}` GET is indexed under `/`, so `/health`, `/ws`, `/signals/x`, and `/assets/...` never reach it, and a missing `/assets/x.js` is a 404 from the static resource, not `index.html`. The whole hosting slice is:

```python
async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

async def immutable_assets(request: web.Request, response: web.StreamResponse) -> None:
    if request.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

application.router.add_get("/ws", socket)
application.router.add_static("/assets", dist / "assets")
application.router.add_get("/{tail:.*}", index)
application.on_response_prepare.append(immutable_assets)
```

`on_response_prepare` is the documented hook for adding headers to every response, and `FileResponse.prepare` fires it for 200, 206, and 304 alike (https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.Application.on_response_prepare). Anything Vite emits at the dist root besides `index.html` (a favicon from `public/`) would receive `index.html` from the fallback; serve those few names explicitly or check `(dist / tail).is_file()` behind a `relative_to` guard. A later `web.get("/", ...)` would be a `PlainResource` under `/` and has to be registered before the catch-all.

## The WebSocket route on the same application

`WebSocketResponse(*, timeout=10.0, receive_timeout=None, autoclose=True, autoping=True, heartbeat=None, protocols=(), compress=True, max_msg_size=4194304, writer_limit=65536)`; the handler is a GET route that awaits `ws.prepare(request)`, iterates `async for msg in ws`, and returns `ws` (https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.WebSocketResponse, https://docs.aiohttp.org/en/stable/web_quickstart.html#websockets). `autoping` answers the client's pings and swallows pongs, but "server does not send PING requests"; `heartbeat=N` sends a ping every N seconds, waits N/2 for the pong, and on a miss closes with 1006 and hands the reader a `WSMsgType.ERROR` (`aiohttp/web_ws.py`). Browsers answer pings without any JavaScript, so `heartbeat=30` is the dead-client detector and costs no client code. `receive_timeout` is an idle policy, not liveness, and stays `None`.

Concurrency is spelled out: "Reading from the WebSocket (`await ws.receive()`) must only be done inside the request handler task; however, writing (`ws.send_str(...)`) to the WebSocket, closing (`await ws.close()`) and canceling the handler task may be delegated to other tasks" (https://docs.aiohttp.org/en/stable/web_advanced.html#reading-from-the-same-task-in-websockets). `receive()` raises `RuntimeError("Concurrent call to receive() is not allowed")` on a second reader. The writer holds its own `asyncio.Lock` and writes uncompressed frames without an await before the transport write, so several tasks may call `send_json` without an application lock; each may block on drain past `writer_limit`, and `send_*` raises `ClientConnectionResetError` once the socket is closing (`aiohttp/_websocket/writer.py`). That fixes the bridge: the handler task reads frames and dispatches; each `thread.subscribe` becomes an `asyncio.Task` that iterates `dispatcher.subscribe(...)` and writes; unary calls run as tasks too so a slow `usage.get` never blocks reading; on close, cancel every task. Shutdown follows the docs' recipe: a `WeakSet` under a `web.AppKey`, closed from `on_shutdown` with `WSCloseCode.GOING_AWAY`, which `AppRunner.cleanup()` runs after stopping the sites and before waiting `shutdown_timeout` (https://docs.aiohttp.org/en/stable/web_advanced.html#websocket-shutdown).

aiohttp never reads `Origin` (no match in `web_ws.py`). RFC 6455 section 10.2 exists for exactly this and OWASP's sheet says to validate it on every handshake against an allowlist (https://www.rfc-editor.org/rfc/rfc6455#section-10.2, https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html). The app is same-origin, so the check is `Origin` host equals `request.host`, answered with `HTTPForbidden` before `prepare()`. Behind Caddy the browser sends `https://` while `request.scheme` is `http` and "Forwarded and X-Forwarded-Proto are not used anymore" (web_reference, `BaseRequest.scheme`), so compare hosts, not full origins. Whether a missing `Origin` (a non-browser client) is allowed is a decision for the server slice, together with the single-user token the map parks under web app authentication.

Wire frames. JSON-RPC 2.0 gives the unary shape, `{id, method, params}` answered by exactly one of `result` or `error`, and defines no streaming (https://www.jsonrpc.org/specification). kinby borrows the shape and adds three frames for subscriptions, without the `jsonrpc` field:

```
-> {"id": 7, "method": "thread.turn.start", "params": {...}}
<- {"id": 7, "result": {...}} | {"id": 7, "error": {"code", "message", "retryable"}}
-> {"id": 8, "method": "thread.subscribe", "params": {"thread_id": ..., "after_sequence": 41}}
<- {"id": 8, "event": {...}}  (repeated)
<- {"id": 8, "error": {...}}  (the ErrorEnvelope the dispatcher yields; ends the stream)
-> {"id": 8, "cancel": true}
```

`id` is an integer the client mints per connection. `error` is the `ErrorEnvelope` as is, so the client sees the same `code` values the CLI does. The server writes `{"id", "event": event.model_dump(mode="json")}`.

One gap the chat flow should notice: `EventLog.subscribe` gives no marker when replay ends and live begins, so the client cannot show "syncing" versus "live". T3 Code's stream carries a `synchronized` item for this. A one-line marker after the replay loop in `subscribe`, surfaced as a frame, is cheap; whether it belongs in the contract is for the chat spec.

## The client: one connection, resumable subscriptions

What the browser gives: `new WebSocket(url)` makes one attempt that "ultimately ends with either an `open` event or a `close` event"; `readyState` is 0 to 3; `send()` in CLOSING or CLOSED silently discards; `close` carries `code`, `reason`, `wasClean`, with 1001 on navigation and 1006 when no close frame arrived; `error` is a bare `Event` because the spec forbids leaking failure details; nothing reconnects (https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/WebSocket, https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent/code, https://websockets.spec.whatwg.org/). From an https page the URL must be `wss://`, which `window.location.origin` with `http` swapped for `ws` handles in both deployments (https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API/Writing_WebSocket_client_applications).

Libraries. `reconnecting-websocket` 4.4.0 was published in 2020 and last committed the same year; its growth factor is 1.3 with a single random draw of the minimum delay, not per-attempt jitter, and it buffers messages sent while closed and flushes them on open by default (https://github.com/pladaria/reconnecting-websocket). `partysocket` 1.3.0 (2026-06-23) is its maintained fork with the same options plus URL providers (https://github.com/cloudflare/partykit/tree/main/packages/partysocket). Buffering is the wrong default here: a `thread.turn.start` queued during an outage and flushed later starts a turn the user may have given up on. The mechanism kinby needs is small enough to own.

The connection class, in outline:

- URL: `${location.origin.replace(/^http/, "ws")}/ws`.
- Backoff: full jitter, `sleep = random(0, min(16000, 1000 * 2 ** attempt))`, which AWS's analysis shows does the least total work of the jitter strategies (https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/); attempt resets to 0 after 30 seconds connected, T3 Code's `BACKOFF_RESET_AFTER_MS`. Pause while `navigator.onLine === false` and retry at once on `online` and on `visibilitychange` to visible; `onLine === true` is a hint, not proof (https://developer.mozilla.org/en-US/docs/Web/API/Navigator/onLine, https://developer.mozilla.org/en-US/docs/Web/API/Document/visibilitychange_event).
- Unary calls: `Map<id, {resolve, reject}>`, an `AbortSignal` per call (`AbortSignal.any([caller, AbortSignal.timeout(15000)])`, https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal), and on `close` every pending entry rejects with `{code: "INTERNAL", message, retryable: true}` in `ErrorEnvelope` shape. Nothing is re-sent: `thread.turn.start` has no idempotency key, and re-issuing it can start two turns. A call issued while disconnected rejects immediately rather than queueing.
- Subscriptions: `Map<id, {method, params: () => object, onEvent, onError}>`. `params` is a closure that reads `after_sequence` from the store at the moment of (re)subscribing, so every reconnect resumes from the last event the client holds. This is the SSE `Last-Event-ID` design in the WHATWG spec, where the client owns the cursor and the server replays from it (https://html.spec.whatwg.org/multipage/server-sent-events.html#the-last-event-id-header), and what `EventLog.subscribe` was written for.
- Dedupe and gaps: drop any event with `sequence <= lastSequence`; treat `sequence > lastSequence + 1` as a bug, log it, and resubscribe from `lastSequence`. The integer, per-thread, contiguous sequence is what makes the check possible; SSE ids are opaque.
- Liveness: the server's heartbeat catches dead clients; a client whose server vanished may sit on a half-open socket until TCP gives up. T3 Code probes on foreground with a cheap call and a 15-second timeout. The same probe with `thread.list` on `visibilitychange` to visible is enough; add it when the first stuck tab shows up, not before.
- Exposed state: `{phase: "connecting" | "connected" | "backoff" | "offline", retryAt?: number}` for a status dot.

Every piece is testable without a browser by injecting a `WebSocket`-shaped factory.

## State for the chat stream

The socket is an external store by definition, and React's own hook for one is `useSyncExternalStore(subscribe, getSnapshot)`, with two rules: `getSnapshot` must return the same value while nothing changed, or React loops, and `subscribe` must be a stable function (https://react.dev/reference/react/useSyncExternalStore). A `ThreadStore` class holding `Map<threadId, ThreadState>`, a `Set` of listeners, and `apply(threadId, event)` that replaces the entry with `project(state, event)` satisfies both: untouched threads keep their reference, and `useThread(threadId)` is `useSyncExternalStore(store.subscribe, () => store.get(threadId))`.

`project(state: ThreadState, event: Event): ThreadState` is the reducer the REPL's `_render_turn` already is. `message.delta` appends to the last assistant message's text instead of adding an element per delta; `tool.call` and `tool.result` become items on the current turn; `approval.requested` sets `pendingApproval` until the user's `thread.approval.respond` and the following event clear it; the closing payloads set the turn's outcome and totals. `useReducer`'s contract is the same pure `(state, action) => state` (https://react.dev/reference/react/useReducer), and the difference is only where the state lives; outside the tree it survives navigation between flows and is reachable from the socket callback without refs. Automatic batching means the deltas that arrive in one tick produce one render (https://react.dev/blog/2022/03/29/react-v18).

What is not needed at first. Zustand 5 is `useSyncExternalStore` plus selectors and `set` (`src/react.ts` in https://github.com/pmndrs/zustand); the store above is fifty lines and the projector is the real code. TanStack Query positions itself for async server state reached through fetch-like APIs, and the maintainer's own socket guidance is to invalidate or `setQueryData` on messages with `staleTime: Infinity` (https://tkdodo.eu/blog/using-web-sockets-with-react-query); for a handful of unary reads on the dashboard a `useCall` hook is enough, and the 745 KB package can come back if the dashboard's reads multiply. TanStack Virtual's docs frame it for lists of thousands of rows (https://tanstack.com/virtual/latest/docs/introduction); a thread of a few hundred items with variable-height markdown is the harder case for it and below the size where it pays. React Compiler 1.0 is stable and installs as a Babel plugin through `@vitejs/plugin-react` (https://react.dev/blog/2025/10/07/react-compiler-1); plugin-react 6 otherwise transforms with Oxc, so the compiler brings Babel back into the build. Leave it off until a profile shows re-render cost. `useOptimistic` requires an Action (https://react.dev/reference/react/useOptimistic); the simpler optimistic send is T3's: append the user's message locally with a client-minted id when `thread.turn.start` is called and reconcile on the `turn.started` event.

## Contract types on the TypeScript side

The client should not hand-write what Pydantic already knows. `model_json_schema()` emits Draft 2020-12 (https://docs.pydantic.dev/latest/concepts/json_schema/), and run against the checkout `Event` gives `additionalProperties: false` on every model (`ContractModel` forbids extras), the payload as `oneOf` with a `discriminator.mapping`, each variant's `type` as `{"const": "message.delta", "type": "string", "default": "message.delta"}`, and `format: uuid` and `format: date-time` on the ids and timestamp. One wrinkle: because `type` has a default it is absent from `required`, in validation and serialization mode alike (Pydantic 2.13.4), and a generator would emit it as optional, which breaks narrowing on `payload.type`. The schema writer adds every property with a `const` to `required`; five lines, and true, since the server always writes it.

Generators. `json-schema-to-typescript` 16.0.0 turns `oneOf` plus `const` into a discriminated union and honours `additionalProperties: false`, types only (https://github.com/bcherny/json-schema-to-typescript). Zod 4 added `z.fromJSONSchema()` in 4.2.0 and marks it experimental with no list of supported keywords (https://zod.dev/json-schema); it would give runtime validation of server events, which the client does not need, since the events come from kinby's own Pydantic models over a same-origin socket. Ajv validates the schema at runtime but produces no types and rejects `discriminator.mapping` (https://ajv.js.org/json-schema.html). `openapi-typescript` needs a paths object a socket contract does not have.

So: `uv run python -m kinby.contracts.schema` writes `web/src/contract/schema.json` from the `Method` and `Subscription` tables, exactly as `kinby.instance.schema` does for the manifest, with the same "checked-in file is current" test; `json2ts` generates `web/src/contract/types.ts`, also checked in, so `npm run check` never needs Python. A `just contract` recipe runs both. The one hand-written file is `methods.ts`, which pairs each name with its command and result types the way `methods.py` does, so `call(THREAD_TURN_START, command)` is typed end to end.

## Lint, format, type check

Type check: `tsc -b` on TypeScript 7, which the template already runs as the first half of `build`.

Lint: typescript-eslint 8.70 supports TypeScript `>=4.8.4 <6.1.0`, so it cannot lint a TypeScript 7 project, and its typed rules need a full TypeScript build before linting starts (https://typescript-eslint.io/users/dependency-versions/, https://typescript-eslint.io/getting-started/typed-linting/). oxlint 1.82 is what the template ships; with `oxlint-tsgolint` and `"options": {"typeAware": true}` it runs 59 of typescript-eslint's 61 type-aware rules on the native compiler, `no-floating-promises` among them, and that rule is the one a socket client most needs (https://oxc.rs/docs/guide/usage/linter/type-aware.html). The template's README says to turn it on for production apps. Biome 2.5 is the alternative one-binary tool, closest in spirit to ruff; its type-aware rules use inference without the compiler and catch about 75% of the floating-promise cases typescript-eslint does (https://biomejs.dev/blog/biome-v2/).

Format: oxfmt 0.67 is in beta since 2026-02-24, passes all of Prettier's JavaScript and TypeScript conformance tests, and Vite's own repository formats with it (https://oxc.rs/blog/2026-02-24-oxfmt-beta.html, create-vite 9.2.0 changelog). Prettier 3.9 is the stable baseline it reproduces. Biome would format too. Since oxfmt's output is Prettier's, switching to Prettier or Biome later costs a config file and no reformat.

The choice is one toolchain: `tsc` (TypeScript 7), oxlint type-aware on the same compiler, oxfmt. `web/package.json` gets `"check": "tsc -b && oxlint --type-aware && oxfmt && vitest run"`, mirroring the Python side where `ruff format .` writes and the rest verifies.

## Tests, and what runs before a commit

Vitest 5.0 (2026-09-03) reads the `test` field of `vite.config.ts`, defaults `environment` to `node`, and `vitest run` is the single-run mode; it requires Node 22.12+ (https://vitest.dev/blog/vitest-5, https://vitest.dev/config/environment). Most of this app's tests need no DOM: the projector is `(state, event) => state`, the connection takes a fake socket, and the contract types compile or do not. Component tests use `@testing-library/react` 16.3 (React 19 since 16.1), `@testing-library/user-event` 14.6, and `@testing-library/jest-dom` 6.9 through `import "@testing-library/jest-dom/vitest"` in a setup file, under `environment: "jsdom"` per file with `// @vitest-environment jsdom` (https://github.com/testing-library/react-testing-library/releases, https://github.com/testing-library/jest-dom). T3 Code goes further and splits component logic into `*.logic.ts` files with their own tests, forbidding component tests that render to static markup and assert props; the same split keeps kinby's jsdom tests few.

Vitest Browser Mode is stable since Vitest 4 with `@vitest/browser-playwright` as a provider; the docs position it to augment, not replace, end-to-end runners (https://vitest.dev/guide/browser/). Not now.

Playwright 1.63's `webServer` starts a command, waits for a URL, and `reuseExistingServer: !process.env.CI` skips the start when one is up; browsers install with `npx playwright install --with-deps chromium`, which needs root on Linux (https://playwright.dev/docs/test-webserver, https://playwright.dev/docs/browsers). Its docs never position it as a pre-commit check, and a run needs a built bundle and a running `kinby serve`. It earns a `just e2e` recipe that builds the web app, starts `kinby serve` against a scratch instance, and drives the chat-with-approvals flow; run it before merging a flow ticket. Before every commit: `tsc -b`, oxlint, oxfmt, `vitest run`, the same four things the Python side runs in kind.

## One check entry for Python and web

Today the entry is four commands in `AGENTS.md`. The candidates for one entry:

- `just` (https://just.systems/man/en/): a `justfile` whose recipes run each line in a new shell and stop at the first failure; `[working-directory('web')]` per recipe; installs with `uv tool install rust-just` (it is on PyPI), `winget`, `brew`, or `cargo`; on Windows it uses `sh` from Git for Windows when present. One binary to install everywhere kinby's checks run.
- GNU make: tabs, and on Windows it falls back to `COMSPEC` when no `sh.exe` is on `PATH` (https://www.gnu.org/software/make/manual/html_node/Choosing-the-Shell.html).
- A Python script under `uv run python scripts/check.py` calling `subprocess`: no new tool, but thirty lines of code that only run commands.
- The `pre-commit` framework with `repo: local` hooks: a hook manager on top of the tools, and hooks that skip when the user passes `--no-verify`.
- npm workspaces or Turborepo: for repositories with several JavaScript packages (https://docs.npmjs.com/cli/v11/using-npm/workspaces, https://turborepo.dev/docs); `web/` is one.

`just` wins on readability: the recipe is the documentation, and `AGENTS.md` shrinks to `just check`.

```just
check: check-py check-web

check-py:
    uv run ruff check .
    uv run ruff format .
    uv run ty check
    uv run pytest

[working-directory('web')]
check-web:
    npm run check

[working-directory('web')]
build-web:
    npm ci
    npm run build

contract:
    uv run python -m kinby.contracts.schema
    npm --prefix web run contract
```

The coder runs "its own checks" in its workspace clone (Dockerfile comment), so the image needs `just` and Node 24 in the runtime stage as well as the build stage. If CI arrives later, `astral-sh/setup-uv` plus `actions/setup-node@v7` with `node-version: 24`, `cache: npm`, and `cache-dependency-path: web/package-lock.json` is the whole setup (https://github.com/actions/setup-node).

## What transfers from T3 Code

Read on `main` on 2026-09-11. T3 Code's client lives in `packages/client-runtime` (shared with mobile) and `apps/web`: React 19.2 with the React Compiler, Vite through Vite+ (`vp`), TanStack Router file routes, Tailwind 4, shadcn on `@base-ui/react`, Effect Atom for server state, Zustand for UI-only state, `@legendapp/list` for virtualization, oxlint and oxfmt through Vite+, Vitest with `@effect/vitest`, no Playwright. Its `AGENTS.md` tells agents not to run repo-wide checks ("CI owns the full suite"), and its only commit hook is the formatter. Neither fits kinby, where the coder runs the full check before opening a PR.

Re-expressed for kinby:

- Same-origin socket URL, `/ws` path, `VITE_WS_URL` only as an override (`apps/web/src/environments/primary/target.ts`). kinby needs no override.
- A retry ladder `[3, 4, 8, 16]` seconds with no jitter, reset after 30 seconds up, and an immediate retry on foreground and `online` (`packages/client-runtime/src/connection/supervisor.ts`). kinby keeps the reset and the wake-ups and adds full jitter.
- A `phase` plus `retryAt` object feeding a status dot (`components/ConnectionStatusDot.tsx`).
- Per-thread `lastSequence` resent as `afterSequence` inside the resubscribe callback, events at or below it dropped, and a `synchronized` marker flipping "syncing" to "live" (`packages/client-runtime/src/state/threads.ts`). The marker is the contract gap noted above.
- "Reconnection does not automatically replay mutations, whose retry and idempotency rules belong to the operation" (`docs/internals/connection-runtime.md`). Same rule here.
- A pure `applyThreadDetailEvent(thread, event)` that appends streaming deltas to the message with the same id and keeps untouched references stable (`packages/client-runtime/src/state/threadReducer.ts`). kinby's projector.
- Optimistic user message keyed by a client-minted id, removed when the server's event with that id arrives (`components/ChatView.tsx`). kinby reconciles on `turn.started`.
- Approvals rendered in the composer, not the timeline, with a fixed option set (`ComposerPendingApprovalPanel.tsx`). kinby's `Approval decision` is approve or deny, so two buttons.
- Logic in `*.logic.ts` next to components, tested without rendering.
- A one-time reload on `vite:preloadError` after a deploy invalidates chunks (`lib/chunkReloadGuard.ts`), which is Vite's own documented failure mode.
- A slow-request timer that raises a toast after 15 seconds (`rpc/requestLatencyState.ts`); useful when a turn is parked on the instance's single-turn lock and `thread.turn.start` answers `INSTANCE_BUSY`, which the REPL today polls once a second.

Not transferred: Effect and Effect Atom, the IndexedDB thread cache, paged history, the virtualized list, TanStack Router's codegen, Tailwind and shadcn (a UI kit is the chat flow's decision, not this ticket's), the pairing-token auth dance, Vite+.

## Recommendation

`web/` from create-vite's `react-ts` template on Vite 8 and React 19, TypeScript 7 with `tsc -b`, oxlint type-aware and oxfmt, npm with a lockfile, Node 24. Vite builds into `src/kinby/web/dist`, hatchling ships it as an artifact, a `node:24-slim` stage builds it in the Dockerfile, and the runtime image moves to Node 24 so the coder can run `npm run check`. aiohttp serves it from the receiver's `Application`: `add_static("/assets")`, a `/{tail:.*}` GET returning `index.html` with `no-cache`, immutable `Cache-Control` for `/assets/` through `on_response_prepare`, and `GET /ws` with `heartbeat=30`, an `Origin` host check, the handler task reading and one task per subscription writing, closed from `on_shutdown`. Frames borrow JSON-RPC's `{id, method, params}` and `{id, result | error}` and add `{id, event}` and `{id, cancel}`. The client is a hand-written connection with full-jitter backoff capped at 16 seconds, reset after 30 seconds up, `after_sequence` read from the store on every resubscribe, replays dropped by sequence, in-flight calls rejected on drop and never replayed. State is a pure projector behind `useSyncExternalStore`; Zustand, TanStack Query, virtualization, and the React Compiler wait for a flow that needs them. Types come from the contract's JSON Schema through `json-schema-to-typescript`, checked in and guarded by a test like the manifest schema's. `vitest run` with jsdom for the few component tests is a pre-commit check; Playwright is `just e2e` before a flow merges. `just check` runs the four Python commands and `npm run check`.

Two things to settle before the build tickets: the serve-mode part that hosts the web app and the socket needs a name in `CONTEXT.md` (the `Receiver` is defined as the signals listener, and this is more than that), and the chat flow spec decides whether `thread.subscribe` gains a marker that separates replay from live.

## Sources

- https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts and https://github.com/vitejs/vite/blob/main/packages/create-vite/CHANGELOG.md
- https://vite.dev/guide/, https://vite.dev/guide/build, https://vite.dev/guide/assets, https://vite.dev/config/build-options, https://vite.dev/config/server-options#server-proxy, https://vite.dev/blog/announcing-vite8
- https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/, https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/
- https://nodejs.org/en/about/previous-releases, https://packages.debian.org/trixie/nodejs, https://github.com/nodejs/corepack#readme, https://docs.npmjs.com/cli/v11/commands/npm-ci
- https://hatch.pypa.io/latest/config/build/, https://hatch.pypa.io/latest/plugins/build-hook/custom/, https://docs.python.org/3.14/library/importlib.resources.html
- https://docs.aiohttp.org/en/stable/web_reference.html (add_static, FileResponse, WebSocketResponse, on_response_prepare, BaseRequest.scheme), https://docs.aiohttp.org/en/stable/web_advanced.html (static files, WebSocket shutdown, reading from the same task, graceful shutdown), https://docs.aiohttp.org/en/stable/web_quickstart.html#websockets, https://docs.aiohttp.org/en/stable/faq.html
- https://github.com/aio-libs/aiohttp/tree/3.14/aiohttp (`web_urldispatcher.py`, `web_fileresponse.py`, `web_ws.py`, `_websocket/writer.py`, `web_runner.py`)
- https://caddyserver.com/docs/caddyfile/directives/reverse_proxy, https://caddyserver.com/docs/caddyfile/patterns, https://caddyserver.com/docs/caddyfile/directives/file_server
- https://www.rfc-editor.org/rfc/rfc6455, https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html, https://www.jsonrpc.org/specification
- https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/WebSocket, https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/send, https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent/code, https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API/Writing_WebSocket_client_applications, https://developer.mozilla.org/en-US/docs/Web/API/Navigator/onLine, https://developer.mozilla.org/en-US/docs/Web/API/Document/visibilitychange_event, https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal, https://websockets.spec.whatwg.org/, https://html.spec.whatwg.org/multipage/server-sent-events.html
- https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/, https://github.com/pladaria/reconnecting-websocket, https://github.com/cloudflare/partykit/tree/main/packages/partysocket
- https://react.dev/reference/react/useSyncExternalStore, https://react.dev/reference/react/useReducer, https://react.dev/reference/react/useOptimistic, https://react.dev/blog/2022/03/29/react-v18, https://react.dev/blog/2025/10/07/react-compiler-1, https://github.com/pmndrs/zustand, https://tkdodo.eu/blog/using-web-sockets-with-react-query, https://tanstack.com/virtual/latest/docs/introduction
- https://docs.pydantic.dev/latest/concepts/json_schema/, https://github.com/bcherny/json-schema-to-typescript, https://zod.dev/json-schema, https://ajv.js.org/json-schema.html, https://openapi-ts.dev/introduction
- https://typescript-eslint.io/users/dependency-versions/, https://typescript-eslint.io/getting-started/typed-linting/, https://oxc.rs/docs/guide/usage/linter/type-aware.html, https://oxc.rs/blog/2026-02-24-oxfmt-beta.html, https://biomejs.dev/blog/biome-v2/, https://biomejs.dev/reference/cli/
- https://vitest.dev/blog/vitest-5, https://vitest.dev/config/environment, https://vitest.dev/guide/browser/, https://github.com/testing-library/react-testing-library/releases, https://github.com/testing-library/jest-dom, https://testing-library.com/docs/user-event/install, https://playwright.dev/docs/test-webserver, https://playwright.dev/docs/browsers, https://playwright.dev/docs/ci-intro
- https://just.systems/man/en/, https://www.gnu.org/software/make/manual/html_node/Choosing-the-Shell.html, https://docs.astral.sh/uv/concepts/projects/run/, https://pre-commit.com/, https://docs.npmjs.com/cli/v11/using-npm/workspaces, https://turborepo.dev/docs, https://github.com/actions/setup-node, https://github.com/astral-sh/setup-uv
- https://github.com/pingdotgg/t3code on `main` (`package.json`, `pnpm-workspace.yaml`, `vite.config.ts`, `AGENTS.md`, `docs/internals/connection-runtime.md`, `apps/web/vite.config.ts`, `apps/web/src/environments/primary/target.ts`, `apps/web/src/components/ChatView.tsx`, `apps/web/src/components/chat/ComposerPendingApprovalPanel.tsx`, `apps/web/src/components/ConnectionStatusDot.tsx`, `apps/web/src/lib/chunkReloadGuard.ts`, `apps/web/src/rpc/requestLatencyState.ts`, `packages/client-runtime/src/connection/supervisor.ts`, `packages/client-runtime/src/rpc/session.ts`, `packages/client-runtime/src/rpc/client.ts`, `packages/client-runtime/src/state/threads.ts`, `packages/client-runtime/src/state/threadReducer.ts`)
- The checkout: `src/kinby/cli/main.py`, `src/kinby/core/receiver.py`, `src/kinby/core/dispatcher.py`, `src/kinby/core/events.py`, `src/kinby/cli/client.py`, `src/kinby/cli/repl.py`, `src/kinby/contracts/`, `src/kinby/instance/schema.py`, `tests/test_schema.py`, `Dockerfile`, `docker/Caddyfile`, `docs/adr/0030-the-coder-runs-on-a-public-box-behind-caddy.md`; `Event.model_json_schema()` run under Pydantic 2.13.4
