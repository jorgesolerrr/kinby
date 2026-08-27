# Ideas from Grok Bot 0.18 for kinby

Source: https://github.com/b-nnett/grok-bot-0.18-reconstructed (inspected 2026-08-26, `main`). A reconstruction of Cursor's shipped "Grok Bot" desktop app, decompiled into readable TypeScript. Map: jorgesolerrr/kinby #1.

Grok Bot is a multi-agent personal assistant. One user runs up to 50 named agents that share one Linux desktop ("the box"), message each other, sit in group chats, run routines, and reach the user through a desktop app or messaging channels. The agent runtime itself lives inside the box; the desktop is a thin client. What transfers to kinby is the way agents talk to each other and to the user, the run queue, the wake vocabulary, and a handful of approval and interrupt details. The stack (Electron, coordinator process, S3 store sync, shared X displays) does not.

The one structural difference to keep in mind: Grok Bot agents share a box, so agent-to-agent delivery is a function call inside one host process. kinby instances are each their own directory and container. Cross-instance messaging therefore has to travel over the server seam from #5, which means the socket in front of the dispatcher doubles as the instance bus. That is a feature. No separate coordinator process needed.

Useful files: `source/host/extensions/transcript/` (run-scheduler, agent-to-agent-messaging, group-chat-glue, background-wakes, ack-obligations, turn-runtime), `source/host/automations/`, `source/host/extensions/local-tool-permission/`, `source/packages/agent/abstract-user-message-action-handler.ts`, `source/packages/agent-summarization/durable-blocks.ts`, `source/host/runner/tools/sand-agent-management-tools.ts`.

## Adopt

Ranked. The first block is what the instance orchestration work should build on; later items are smaller and can ride along with the tickets they touch.

### 1. Every non-user stimulus is a hidden turn with an origin

Everything that is not the user typing arrives as a turn on the agent's own queue with a bracketed cue in the prompt: `[agent]` for a peer message, `[routine]`, `[event]`, `[inbound]` for a channel, `[A background task just completed]`, `[broadcast]`, `[System recovery]`. The turn is hidden (its prompt is not shown as a user message), tagged with a source (`turn | agent | automation | connector | background-revival | broadcast | event`), and marked `isSilenceAllowed` when ending without saying anything is fine. `turn-runtime.ts:46-52,176`, `background-wakes.ts:35-49`.

kinby already has the pieces. `thread.turn.start` gains an `origin` field (user, instance, routine, event, task), the event log records it, and the prompt renderer decides the cue text. Do not do what Grok Bot does and put the cue in a user-role message that later code has to sniff; keep it typed in the event and render at prompt time. This is the single decision that lets routines (#11), inter-instance messages, and subagent completions share one code path.

Bears on: #11, #32, #31.

### 2. One run queue per instance, three lanes, a watchdog

Every run is enqueued per agent with a lane: `user > agent > background`. The user's own messages and the group turns the user triggered take the user lane; peer messages take the agent lane; routines, channel inbound, timeline events, and task completions take background. A watchdog notices a user-lane task that has waited too long behind an active run, interrupts the run, waits a grace period, then parks it as a zombie so the user's turn proceeds. `run-scheduler.ts:3-14,45-51,199-257,312-366`.

This is the scheduler #11 calls "the one loop that is not the agent loop", and it is small: an `asyncio.PriorityQueue` per instance plus a worker coroutine. The lane rule answers a question kinby had not asked yet, which is what happens when a routine is mid-run and the user types. Answer: the user wins, and the routine is redriven.

Bears on: #11, #33.

### 3. Instance-to-instance messaging is asynchronous, like texting

`SendToAgent {target_id, message, priority?}` appends an outbound entry to the sender's transcript and an inbound one to the recipient's, then wakes the recipient with an `[agent]` turn on the agent lane. The tool returns at once. The prompt tells the model there will be no reply in this turn; a reply, if any, arrives as a later wake. `priority=true` prepends to the queue and interrupts the recipient's current non-user run; the interrupted work is recorded and redriven later with a "Redelivery" note. Queue merges keep priority-first ordering across deferrals. Guardrails live in the tool description: do not fan out unless the user asked, paraphrase rather than relay the user verbatim. `agent-to-agent-messaging.ts:52-147,198-216`, `sand-agent-management-tools.ts:45-68`.

For kinby, the tool on instance A calls `thread.turn.start(origin=instance, from=A)` on instance B through B's dispatcher. Peer entries never count as user activity (`entryRaisesUserActivitySignal`, `shared/transcript.ts:38-41`), so agent chatter does not light up "unread". Broadcast exists only as a user action, never as an agent tool. Keep that.

Bears on: #5 (the server seam is the bus), instance orchestration.

### 4. Delivery owed vs. silence allowed

Grok Bot separates "the model produced text" from "the user was told". Plain assistant text is never shown; only an explicit `SendMessage` reaches the user, and the reply-nudge prompt says so in capitals. Each turn knows whether it owes a delivery. A user turn that ends silent is re-run with a nudge; a routine turn may end silent by design; a routine that started a background task switches its completion prompt to "silence is the default". When a user send is accepted an ack obligation is recorded, and if the turn never produced a visible reply (interrupt, restart) a `[System recovery]` redrive runs, up to three times. `turn-runtime.ts:46-52`, `ack-obligations.ts:6-10,169-292`, `completion-revivals.ts:6-13`.

This is the answer to "routine destination" in the README thesis. A routine's turn runs in a thread, and what reaches the user is only what the instance explicitly sends to a destination. Adopt the split. Whether v1 also makes assistant text invisible in *user* turns is a separate call; for the REPL, keep streaming text, and make the send-tool the only path for proactive turns.

Bears on: #11, #9.

### 5. A durable ledger of pending wakes

Subagents, background shells, and remote jobs are one list of async tasks `{kind, id, label, status, startedAtMs}`. Live entries come from the runner; the rest are persisted wake markers `{agentId, kind, workId, markedAtMs, quietOrigin?}`. On restart the markers are re-armed: watchers are re-created where possible, and where not (an in-process subagent), the parent is woken with a synthetic error completion ("host restart interrupted this background task"). Markers go stale after 48 h. `async-task-union.ts:1-57`, `pending-wake-rearm.ts:95-263`, `sand-pending-wake-store.ts:11-18`.

kinby version: `.state/pending-wakes.jsonl` per instance, read at session start. Cheap, and it makes "the container restarted" a normal event instead of lost work.

Bears on: #9, #11.

### 6. Approvals bound to a turn, denials remembered by hash

Local-tool approvals are keyed by `(agentId, toolCallId)` and retired when that tool call's scope ends, or at the start of the next turn. A denial is remembered per `(agentId, action, sha256(target))` with the turn's direction epoch; the same command is refused with "already asked, will not ask again for this task" until the user gives new direction. A pending approval card is voided by a newer user message. "Preparatory" requests (a read the agent needs before the thing the user was asked about) are refused rather than asked. With no answerer attached, asks fail closed (`NoopInteractionListener` rejects `askQuestion`, refuses mode switches). A global barrier forbids a second side effect while a card is pending. `local-tool-permission-controller.ts:46-86`, `local-tool-approvals.ts:90-109`, `auto-review-gate.ts:28`, `interaction-listener.ts:92-155`.

This is #7's evaluation order with the memory rules filled in. The direction-epoch idea is the part worth copying exactly: a denial should not outlive the user's next instruction, and an approval should not outlive the tool call.

Bears on: #7, #34.

### 7. Persist pending tool calls before running them; reconstruct on interrupt

As soon as the model emits tool calls, the assistant message plus a contract per call (`toolCallId → {tool name, allowed tool names, startedAt}`) is checkpointed. On the next user turn after an abort, any pending tool call gets a synthetic result: "Error: X was interrupted by the user after N ms", with the shell's partial output if there was one. A separate resume action re-executes the pending calls against the stored contracts instead. `pending-tool-call-contract.ts:150-210`, `interrupted-tool-reconstruction.ts:129-195`, `resume-action-handler.ts:11-90`.

Ctrl+C in #33 needs exactly this to leave the thread in a state the next turn can continue from.

Bears on: #33, #31.

### 8. Routine run history and status

Each routine has `automation.json {name, prompt, schedule | trigger, enabled, lastRunAt}` and `runs.json` with the last 20 runs `{id, trigger: schedule | manual | event, startedAt, finishedAt, status: running | ok | error, detail, coalescedRunIds}`. Event fires are debounced 750 ms and batched (up to 25 per wake, 500 queued); payloads go into the prompt inside tagged blocks marked "data, not instructions". Every turn gets an `<automation_status>` reminder with next and last run. Change events on routines (created, enabled, disabled) become timeline wakes. `automation.ts:8-13,84-111`, `automation-store.ts:50-81`, `automation-event-fires.ts:112-150`.

Matches "routine files as source of truth" from #11. The 20-run history file next to the routine is the observability #12 asks for, without a database.

Bears on: #11, #12.

### 9. What survives compaction

The summary message is followed by durable blocks the summarizer cannot drop: mode prompt, the plan file, the path to the JSONL transcript with grep instructions, the routine trigger, todos, manually attached skills, and the latest screenshot. Summarization runs in the background on a snapshot while the turn continues; at turn end it is persisted only if still needed, and the messages that arrived after the snapshot are kept verbatim. A `preCompact` hook runs before persist. `durable-blocks.ts:20-41,91-144`, `runTurnLoop` in `abstract-user-message-action-handler.ts:2731-2825`.

Telling the model where its own transcript is, rather than trying to summarize everything into the context, is the cheap version of short-term memory. kinby's event log is that file.

Bears on: #9, #8.

### 10. Reminders that do not count as turns

Nudges (mode change, "you have made six tool calls without telling the user", hook context, conflict notices) are appended as user-role `<system_reminder>` blocks tagged in provider options so they are never counted as user turns and never edit earlier messages. Prompt cache is protected by re-rendering the first user message only on named drift reasons (rules changed, summarization epoch advanced). `turn-run-shell.ts:212-229`, `user-message-action-handler.ts:207-224`.

Small. Worth doing from the first real turn in #32 so cache hits are not lost by accident.

Bears on: #32.

### 11. Message addresses

Entries have deterministic addresses: `t<N>u` for the user message of turn N, `t<N>s<k>` for its k-th send, `b` for the boot turn. Agents refer to messages by these (reply_to, reactions). `transcript-entry-ids.ts:2-31`.

kinby already has a gap-free per-thread sequence (D2 in the runtime-seam blueprint). `thread:seq` is the address. Nothing to build, just a convention to write down.

Bears on: `CONTEXT.md`.

### 12. A group is a thread with members, not a new kind of agent

A group chat is an agent folder with `group.json {memberIds ≤ 6}` whose transcript is the room. A turn runs a bounded round-robin: responders are the members `@mentioned` since the last user message (everyone if none), max 3 rounds, max 10 member messages, max 2 per member turn; a round with no messages ends the turn. Each member runs on its own queue with only the messages since it last spoke, under a system prompt that says the only way to be heard is `SendMessage`, and that sending `(pass)` means staying quiet. The room streams a member's text as provisional until it is clear the text is not `(pass`. A failed member turn is a pass. `group-chat-orchestrator.ts:31-106`, `group-chat-glue.ts:315-481`, `group-chat.ts:1-17`.

For kinby this is a thread whose participants are instances. Not v1, but the shape should be decided now so the thread model does not exclude it: a thread has participants, a participant is the user or an instance, and turn start names the participant. The `(pass)` token and the round bound are the two pieces I would copy without change.

Bears on: instance orchestration, `CONTEXT.md` (thread).

### 13. No manager agents

The "org chart" is a communication graph: nodes are agents, edges are group membership and `conversationPartnerIds` (appended whenever two agents message). Edge activity is derived from timestamps. There is no hierarchy, no manager agent, no delegation tree. `org-chart/workspace/model.ts:1-79`.

Adopt as a rule for kinby: instances are peers; coordination is messages plus the user. A "manager instance" is an ordinary instance with a prompt that says so.

### 14. Dispatcher plus executor for heavy work

The multitask doctrine: the main agent dispatches; heavy work goes to an `executor` subagent with the full toolset, no `SendMessage`, and no inherited context. `TodoWrite` is the task queue, reconciled on every wake. Subagents are steered with `CheckSubagent / MessageSubagent / StopSubagent`, launched with ids seeded from the tool call id so retries are idempotent, and their results are cached by `(conversation, toolCallId, resume, fork)`. A foreground subagent is force-backgrounded when the user types. `sand-multitask.ts:4-22,56-82`, `sand-subagent-management-tools.ts:26-45`, `agent-exec/subagent.ts:70-165`.

This is the answer to "sub-agents generally" in the map's unspecified list. Take the steer/check/stop trio and idempotent ids; skip the todo-as-queue until evals show the dispatcher pattern earns it.

Bears on: #1 (sub-agents), #9 (recap subagent).

### 15. Secrets never touch the transcript

A `secret-request` message ends the turn and renders a masked input. The value goes straight into the connector's credential file; the entry is marked `secretProvided: true`; the agent resumes with a hidden "the user provided the secret, you never see the value" prompt. The list of injected secret names doubles as the redaction list for tool output. For interactive logins (SSO, 2FA, captcha) the agent hands the screen to the user instead of asking for a password. `sand-secret-request.ts`, `widget-responses.ts:355-405`, `secrets-service.ts:2-4`.

Not needed until channels and connectors exist, but the contract command (`thread.secret.submit`) costs nothing to reserve, and the rule "secret values bypass the event log" belongs in #7 now.

Bears on: #7, later connectors.

### 16. Hook results travel on one typed carrier

Twenty-one hook steps (`preToolUse`, `postToolUse`, `subagentStart/Stop`, `preCompact`, `stop`, `sessionStart/End`, and so on). The only channel from a hook to the model is `HookAdditionalContext {hookEventName, content}`, capped at 10,000 chars, sanitized (a literal `<system_reminder>` inside becomes `<system_reminder_>`), and rendered into the tool result. `preToolUse` may return `allow | deny | ask` plus `updated_input`; post hooks can only add context; a denial appends "do not suggest workarounds". Hooks fail open unless configured `failClosed`. Claude Code hooks are imported by mapping names. `hook-step.ts:1-7`, `remote-hooks.ts:157-235`, `hooks-carriers/spec.ts:11-31`, `claude-code-mapper.ts:15-49`.

Hooks are not in the v1 map, but the plugin contract in #6 should leave room for them, and the "one carrier, size-capped, sanitized" rule is the part to keep.

Bears on: #6.

### 17. Memory write filter

Memory is written at turn settle only when the exchange is "memorable" (over 40 chars or contains a question mark; trivia dropped). An extraction prompt emits `profile: / log: / note: / remove:` lines or `NONE`. Recall ranks by `log2(importance) + age/30d`. Memory and profile sections in the system prompt are frozen per compaction epoch so they do not break the cache. Failures never fail the turn. `sand-memory.ts:49-121`, `turn-memory.ts:67-82`.

kinby's graph is a bigger design than markdown shards, but the write filter, the `remove:` line, and freeze-per-epoch transfer directly to the deterministic save in #9.

Bears on: #8, #9.

## Later, maybe

- All tool calls in a step run concurrently, serialized per file path through a lock manager, with a circuit breaker that skips the rest when the exec backend is down. `tool-stream-executor.ts:1279-1450`. Good, but LangGraph's tool node decides this for now.
- Skill catalog under a 2% token budget with four fallback strategies (shorten descriptions, drop them, omit skills with directory hints). `skill-catalog-budget.ts:46-171`. Wait until the catalog is big enough to hurt.
- Loop detection on repeated assistant messages and single-message repeated lines. `agent-loop-detector.ts:20-70`. Cheap insurance once there is a real loop in #32; not before.
- Reactions as a protocol (`{emoji, by}` on entries; a user reaction wakes the agent with "[The user reacted 👍 ...]"). Channel-era feature.
- Usage event shape `sand.turn.usage {input, output, cache_read, cache_write, reasoning_tokens}` per turn. #35 can use the field names; nothing else from the telemetry stack.

## Drop

- Electron main, coordinator child process, MessagePort framing, SSE event families, the inference router. kinby's dispatcher and event stream cover all of it.
- The shared box: one desktop per user, one X display per agent, VNC, window ownership tokens, teach-by-demonstration recording, computer use. A kinby instance is its own container. Nothing to share.
- Cross-user shared rooms and the relay poll loop. Multi-tenancy is out of scope.
- Local-exec daemon supervision (detached process, generation tokens, adoption). The local-install story is unspecified; when it comes, mounting a workspace into the container is simpler than a daemon on the host.
- Box store snapshots to S3, agent-store-sync with tombstones and conflict siblings. A Docker volume is the persistence.
- Cloud agents tool (Cursor background composers). Vendor feature.
- Redacted proto twins, privacy-mode truth table, Sentry scrub tiers, structured log shipping. Single user, own box. The one line to keep is "secret names are the redaction list" (item 15).
- Server-driven cron (backend polls the box with due routines). kinby's scheduler runs inside the instance.
- Agent modes (ASK, PLAN, DEBUG, TRIAGE, MULTITASK) and the switch-mode tool. kinby pins autonomy per thread; modes would be a second axis for the same thing.
- prompt-jsx. Python string templates and a token count are enough.
- 1Password provisioning, MCP OAuth loopback listener, Cursor auth token scoping.
- The 50-agent cap and the "disk saver" special agent. Instance count is bounded by the user's hardware, not a constant.
