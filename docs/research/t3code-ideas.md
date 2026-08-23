# Ideas from T3 Code for kinby

Source: https://github.com/pingdotgg/t3code (inspected 2026-08-23, `main`). Map: jorgesolerrr/kinby #1.

T3 Code is a Node server that wraps agent CLIs (Codex, Claude Code, Cursor, Grok, OpenCode) and exposes them to web, desktop and mobile clients over one WebSocket. It is a harness *around* agents, not an agent. What transfers to kinby is the server boundary, the state model and the vocabulary, not the loop.

Useful files: `packages/contracts/src/rpc.ts`, `packages/contracts/src/orchestration.ts` (subscription resume around lines 540–630), `docs/internals/overview.md`, `docs/internals/glossary.md`, `docs/internals/work-artifacts.md`, `AGENTS.md`.

## 1. One typed RPC boundary over a single WebSocket

`rpc.ts` declares every method as a schema triple, `payload / success / error`, plus `stream: true` for server-push methods. No REST surface. One socket carries unary calls (`projects.readFile`, `server.getSettings`) and subscriptions (`orchestration.subscribeThread`, `terminal.attach`).

Why it fits kinby: a session is one run of the loop against a thread, inherently streaming (deltas, tool calls, approvals). RPC-over-WS makes streaming a method kind instead of SSE bolted onto REST. The contract is the only thing shared between server and clients; in Python that is a `kinby.contracts` package of Pydantic models, method names like `thread.turn.start`, served by a small dispatcher over `websockets` or FastAPI WS.

Authorization is per method: `RPC_REQUIRED_SCOPE` maps each method to a scope (`orchestration:read`, `orchestration:operate`, `terminal:operate`, `review:write`, `access:read/write`, `relay:read/write`). Holding a socket is not permission to call everything.

Bears on: #5 (runtime shape / session seam).

## 2. Event-sourced orchestration with a pure decider

Clients send **commands** (`thread.turn.start`), a pure **decider** turns command plus current state into **events** (`thread.turn-start-requested`), events are appended in one SQL transaction together with the projection, and a **projector** derives the read model. Commands are verbs, events are past tense (`thread.archive` / `thread.archived`). Each command carries an id; a durable receipt makes retries idempotent.

For kinby the transcript store already is "the canonical record... the knowledge graph is derived from canonical sources, never the reverse". Storing it as an append-only event stream with a monotonic `sequence` gives:

- resumable subscriptions: `subscribeThread({threadId, afterSequence})` replays the gap, then goes live; the client dedupes by sequence;
- literal replay for evals ("what evals replay" in the Thread definition);
- idempotent retries.

The LangGraph checkpointer stays as the loop's working state; the event log is the durable truth above it.

Bears on: #5, #9 (transcript as the source memory distils from), #12 (what an instance records).

## 3. Thread / turn / session

T3's **turn** is one user-to-agent cycle, ending when the session leaves `running`; checkpoint work settling later does not move the turn end. kinby has thread and session but no turn. A turn is the natural unit for token attribution, checkpoint bracketing, compaction boundaries (`safe_cut` is "do not cut mid-turn") and eval cases.

Bears on: `CONTEXT.md`, #10, #12.

## 4. Workspace checkpoints as hidden git refs

Every turn is bracketed by a checkpoint stored as a hidden Git ref through a `VcsCheckpointOps` driver contract, so "diff this turn" and "revert this turn" are exact and free in storage. kinby's workspace is "never written to by kinby on its own behalf"; refs under `refs/kinby/checkpoints/<thread>/<turn>` respect that (no working-tree or branch writes) while giving undo.

Bears on: #7 (what the gate can roll back), #9.

## 5. Permission modes per thread, mapped per provider

Four runtime modes: `approval-required`, `auto-accept-edits`, `auto`, `full-access`, set per thread; approvals surface inline as `thread.approval.respond` commands answered over RPC. The mode is a thread attribute; the tool executor interprets it. A server pauses the session on an approval request and a client answers, instead of the tool blocking on stdin.

Bears on: #7 (the (sandbox_mode, approval_policy) bundle, ask-with-no-answerer).

## 6. Drainable workers and receipts in tests

`DrainableWorker` pairs a queue with a transactional outstanding count; `drain()` awaits "queue empty and current item finished". Typed **receipts** (`checkpoint.baseline.captured`, `turn.processing.quiesced`) exist only on the test layer; production publish is a no-op. Tests wait on drains and receipts, never on sleeps. Python shape: `asyncio.Queue` plus a counter with a `drain()` coroutine.

Bears on: #9 (restart-safe background memory writer), #10.

## 7. Usage accounting read from transcripts

`usage.ts` aggregates `(day, hour?, provider, model)` buckets from on-disk transcripts, not live counters, with `costSource: providerReported | modelPriced | unpriced` and input split into disjoint `uncached / cached / cacheCreation`; `reasoningTokens` is a subset of `outputTokens`. A contract version number lets old environments render partially.

Bears on: #12, #11 (USD/day budget needs a priced source).

## 8. Repo conventions

- `docs/` split by audience: `user/` (product voice), `internals/` (maintainers), `operations/` (runbooks); a glossary where every term links to the defining file.
- `work-artifacts.md`: plans live in GitHub issues, never in the tree; docs are present tense and updated with the code; a merged PR is the implementation record.
- `t3.json` per project with a published JSON Schema URL; the analogue is a schema for `kinby.toml`.
- `AGENTS.md` "hit every surface" checklist (entry points, clients, providers, contracts, reverse states such as "snooze needs unsnooze", connection modes, docs) and "three ways to hurt yourself" (never kill by pattern, never touch the live install, never bake in origins).

Bears on: `CLAUDE.md` / `AGENTS.md` rewrite, #20 (container contract doc).

## Not worth copying

- Effect, Effect RPC, Atom: TypeScript-specific machinery; the pattern transfers, the libraries do not.
- The provider-driver layer over several CLIs: kinby is the agent; the model provider string is the equivalent seam.
- T3 Connect relay, SSH environments, Electron shell.
- Buffered assistant delivery with a 24k-char spill: a client-performance hack.
