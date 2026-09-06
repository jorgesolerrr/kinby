# Ideas from OpenBot for kinby

Source: https://github.com/CopilotKit/openbot (inspected 2026-09-04, commit `ba3ab6e4`, `main`). MIT, alpha, "a template, not a product". Two background agents read the code and the `docs/` pages; every claim below points at a file and line range in that commit. Detailed working notes with more citations sit in the session scratchpad and were folded into this file.

OpenBot is CopilotKit's multi-tenant "AI coworkers" platform. A Bot is any HTTP endpoint speaking AG-UI; the server wraps it in a CopilotKit runtime, injects the tool schemas, and routes every browser, file, shell and MCP call through one gateway that decides against a CEL policy, writes an audit row, and only then acts. Each Bot gets its own Playwright container with a persistent Chromium profile, created on demand by a Docker-socket supervisor. Threads live in CopilotKit Intelligence, an external SaaS the server refuses to boot without. The stack is TypeScript on Bun, Hono, Drizzle on Postgres, React. Nothing in it is code kinby can import.

The honest fit summary first. OpenBot is ahead of kinby on governance plumbing and on operational rules for routines, and behind on everything the kinby thesis is about. It has no long-term memory of any kind (the README says "threads and memory", the code has a transcript and a files folder, `shared/bot-prompt.ts:35-43`), no checkpointing or interrupts in its LangGraph bot, no code step, no token or cost budget, and a routine turn is built with `tools: []` so it cannot use the computer (`server/src/routines/run-turn.ts:365-379`). So it does not change kinby's direction. What it changes is a handful of details in the routines work already in flight, two prompt sentences, the shell tool's environment, and how the event log treats secrets. And its docs are worth stealing from.

Useful files: `agent-langgraph/src/{index,stream,history}.ts`, `server/src/computer/{gateway,policy,policy-store}.ts`, `server/src/audit.ts`, `server/src/routines/{run-turn,sweep,runner,schedule,store}.ts`, `server/src/plugins/{builtin-routines,selection,catalogue}.ts`, `server/src/copilot.ts:212-322`, `shared/bot-prompt.ts`, `agent-computer/src/{shell,workspace,control}.ts`, `supervisor/src/docker.ts:363-395`, `prompt.txt`, `CHANGELOG.md`, `docs/README.md`.

## What their LangGraph use says about ours

Short answer: nothing that argues against ADR 0007, and one warning.

`agent-langgraph` is on `@langchain/langgraph` 1.4.10 and `@langchain/core` 1.2.8 (`agent-langgraph/package.json:10-17`), the JS counterparts of the 1.x Python packages kinby pins. `buildGraph` runs once per AG-UI run and builds a two-node `StateGraph` over the stock `MessagesAnnotation`: an `answer` node calling `bound.invoke`, a `tools` node running the last message's tool calls with `Promise.all`, a conditional edge that ends the run when there are no calls or when any call is a browser-side "surface tool" (`src/index.ts:325-385`). No checkpointer, no `interrupt`, no `Command`, no `thread_id`. The whole history arrives on the wire every run and is rebuilt into LangChain messages (`src/history.ts:25-127`). Kinby's runner is the same graph shape with a SQLite checkpointer, `interrupt` for approvals, and `durability="exit"`; OpenBot is the thinner version of the same design, not a different one.

The warning. The docstring says "`recursionLimit` bounds a model that would otherwise call tools in a circle" (`index.ts:323`), and the code passes only `{ version: "v2" }` to `streamEvents` (`index.ts:405-408`), so the LangGraph default of 25 applies and nobody noticed. Kinby sets `recursion_limit` from the steps budget in `turn_runner.py`; the tests in #109 that trip the budget are what keeps that true. Keep them.

Two smaller things worth knowing. Their tool loop runs server-side only for tools the server named; anything the browser registered ends the run, which is why they need synthetic `NO_ANSWER_CAME` tool results and a neutral "continue" human message when a history starts on an assistant turn (`history.ts:43-130`, `shared/bot-prompt.ts:182-185`). Kinby parks and resumes from a checkpoint instead, so it never needs this repair; the wording is a good template for the ToolMessage a denied approval should produce. And `model-options.ts:26-56` validates `BOT_REASONING_EFFORT` against the efforts the provider documents, while `index.ts:75-82` switches OpenAI to the Responses API by model name pattern, a small reference for the cross-model work in #93.

## Adopt

Ranked. The first three land in tickets that are already open.

### 1. Frame a routine firing so the model does the work instead of checking the schedule

A live firing recorded `succeeded` and did nothing. The instruction read "Every run, append the current date to the Notion page"; the model read schedule-shaped prose as a request to set up a schedule, called `list_routines`, found one, and reported it was already configured (`server/src/routines/run-turn.ts:216-244`). No wording of the stored instruction fixes that, because the sentence a person writes is the sentence that describes the schedule. So the firing is wrapped at presentation time, never in the stored row: "One of your routines is firing right now, on its schedule, and this is that firing. Carry out the instruction below in this turn: do the work now, then say what happened. Do not create, list or change any routine unless the instruction itself asks you to." (`run-turn.ts:246-254`). Only the new message is framed; history is never re-framed, and a test holds that.

kinby version: ADR 0019 already renders the routine origin as a bracketed cue at prompt time. Add these three sentences to that cue in the prompt module. The third sentence matters once #61 gives the agent a verb to write into `routines/`. The `create_routine` tool description says the other half: write the instruction "as the work of ONE firing, in the imperative, and do not restate the schedule inside it" (`server/src/plugins/builtin-routines.ts:74-97`). That line belongs in the `write-routine` skill.

Bears on: #112, #116, #61.

### 2. The fatigue rule, a frequency floor, and an enabled cap

Two ledgers that are deliberately not the same ledger: queue attempts bound retries of one firing, while `routine_runs` counts consecutive failed firings across days (`server/src/routines/runner.ts:10-23`). The first failure after a success posts one line, "This routine failed: ...". Ten in a row disables the routine and says so, because "a routine that goes quiet without explaining itself is worse than one that fails". In between, nothing is said; the run rows carry it (`runner.ts:149-172`). Skips neither count nor reset the streak (`store.ts:841-882`). Alongside: a 15-minute floor enforced against the cron expression's own cycle from a fixed start, not from now (`schedule.ts:4,72-176`), and 20 enabled routines per person counted under a lock (`store.ts:61,495-529`).

kinby version: the scheduler in #113 already folds the event log per routine. Fold `turn.failed` runs too, and apply the fatigue rule with the same numbers: say it once on the first failure, disable at ten and say so. Disabling means writing `enabled: false` into the routine's frontmatter, since the file is the source of truth. The floor and the cap are budgets in kinby's sense; a `[routines]` key for each, with these defaults, and the skill mentions the floor.

Bears on: #113, #116.

### 3. Manage routines by talking, through gated tools whose description is the contract

There is no routine form. Four tools on a `builtin-routines` transport, `create_routine`, `list_routines`, `update_routine`, `delete_routine`, granted like any MCP tool and classified as writes (`server/src/plugins/builtin-routines.ts:72-200`, `catalogue.ts:282-284`). The description carries the cron examples, the floor, the timezone rule ("pass the person's IANA timezone whenever they speak in local time"), and "it takes no owner and no Bot: both come from the run". The page only lists, toggles and deletes (`routines/routes.ts:38-62`).

kinby version: this is the shape for #61. Write-flagged core tools that edit `routines/<name>/ROUTINE.md` through the gate, so under ask mode the user approves the routine the agent proposes. That is the README's "an agent that proposes routines itself" with no new mechanism.

Bears on: #61, #116.

### 4. Two prompt sentences and a generated holdings block

Standing instructions are wrapped as "The person you are working with has standing instructions that apply in every channel and every task, alongside your role: ..." followed by "Where the two conflict, the role decides what you do and these decide how you do it." Empty text means no section at all (`server/src/copilot.ts:212-222`). `PROVENANCE_GUIDANCE`, unconditional, tells the model to say where an answer came from, to mark answers from its own knowledge, and not to go hunting the web for a source (`shared/bot-prompt.ts:111-146`). And `grantedToolGuidance` generates, per run, "You can reach these systems directly" plus "This deployment also connects to: X. You hold none of their tools", placed before the browser prose on purpose because the browser prose otherwise wins (`server/src/plugins/tools.ts:79-140`, `copilot.ts:278-283`).

kinby version: the profile section gets the precedence sentence, worded for kinby: the behavior prompt decides what the agent does, the profile decides how. The preamble gets the provenance line. The environment block, or a new section after the skills catalogue, lists the turn's tool snapshot by name; it is fixed per turn, so this is one string. Under a narrowed permission mode the same block can say which tools will ask.

Bears on: ADR 0011 (system prompt sections).

### 5. Shell environment allowlist for the default `bash` tool

The computer's `/exec` hands the child an environment of PATH, locale, terminal and proxy variables only, strips userinfo from proxy URLs, adds names listed in `COMPUTER_SHELL_ENV`, denies `LD_PRELOAD` and friends, and sets HOME to the workspace so a command that writes to `~` writes where the Bot's files already are (`agent-computer/src/shell.ts:24-35,63-145,203-233`). Timeout is clamped (default 120 s, max 600 s) and output truncated at 64 KiB.

kinby version: `plugins/defaults/shell.py` inherits the whole process environment today, which in a container includes the model API keys. Build the env the same way, with a `[tools] shell_env` list in `kinby.toml` for names to pass through. Timeout and output cap already match.

Bears on: the sandbox ADR; a new ticket.

### 6. Redact by key name before an event is written, and make a failed action its own event

Audit payloads pass through a recursive redactor keyed on names like `token`, `password`, `authorization`, `tool_arguments`, `tool_result`, `content`, `prompt` before insert (`server/src/audit.ts:5-35,458-483`); the gateway additionally never puts typed text, file contents or command output in a payload (`computer/gateway.ts:1085-1137`). Order inside `govern` is resolve, decide, write the row, throw or act, and a permitted action that fails writes a second `computer.action_failed` row with the same subject (`gateway.ts:523-538,576-607`). Two Postgres triggers make the table append-only (`drizzle/0000_schema.sql:391-403`).

kinby version: the gate already emits its decision with the rule that matched, which is the part of this that matters most and is done. What is missing is redaction: `tool.call` events carry arguments verbatim, so a tool that takes a token puts it in the JSONL. A key-name redactor in `core/events.py` applied to `tool.call` and `tool.result` payloads is small. The append-only property has no trigger for a file; a test that the writer only appends is the equivalent.

Bears on: #12.

### 7. Unknown tools count as writes

The plugin catalogue lists `writeTools` per connector; a tool not on the list, and every tool from a custom MCP server, is classified as a write (`server/src/plugins/catalogue.ts:364-382`, `plugins/store.ts:2825`).

kinby version: today every kinby tool is Python with a write flag, so the question does not arise. When MCP tools arrive, the loader should mark any tool without a declared flag as `write=True`. One line in the ADR that introduces MCP.

Bears on: the MCP work when it opens.

### 8. Never end a turn with nothing visible, and test the stream translator with hand-made events

`streamRun` is a pure function from `streamEvents` v2 to AG-UI events, taking a factory and a `send` callback, so the whole ordering is tested from arrays of fake events (`agent-langgraph/src/stream.ts:48-237`, `tests/stream.test.ts:8-30`). Rules: one message id per stretch of prose; close text before a tool call; pending calls that never ran are emitted after the loop; an empty run gets a visible fallback line rather than a silent finish (`stream.ts:27-28,206-218`).

kinby version: the runner already raises `ModelNoResponse` for the empty case, which is the stricter answer. The pattern to copy is for the web server seam: one function from the runner's event stream to whatever the wire speaks, tested without a model. If that wire is AG-UI, see the reference note below.

Bears on: the server effort.

### 9. A setup prompt, a changelog rule, and a one-question docs index

`prompt.txt` is a prompt to paste into an AI assistant helping someone install from a fresh clone. It states "every claim in it was checked against the code in this repository; if something here disagrees with what you see, trust the repository and say so", lists the three values a person must supply out of ten blank keys, lists what the assistant must not do (run the browser login, echo keys), and lists each startup refusal with what it means (`prompt.txt:1-94`). `CHANGELOG.md` opens with its own rule: a line belongs there when a deployment behaves differently afterwards and not when only the code moved; every entry is a titled paragraph naming the failure that motivated it, and release notes are that section verbatim (`CHANGELOG.md:1-40`, `docs/releasing.md:8-10`). `docs/README.md` is seventeen lines, one per page saying what question it answers, then a rule about what never goes in public docs.

kinby version: all three, as written, when the README grows an install section. The setup prompt is the cheapest of the three and the one an agent-first project should have.

Bears on: README, docs.

### 10. Fail CI when the test count drops

`bun run test:ci` fails if fewer than 400 tests ran (`scripts/test-ci.ts:14,40`), catching a broken preload or a silently skipped directory. A pytest plugin or a `--co -q | wc -l` check in the workflow does the same.

Bears on: CI.

## Reference

Worth knowing, not worth building now.

**AG-UI at the server seam.** OpenBot's whole framework-independence claim rests on AG-UI (`README.md`, "Built on AG-UI"). Speaking it would let a kinby web client use CopilotKit's React components. But OpenBot also shows the cost: browser-registered tools end the run, so the bot needs history repair (`history.ts:85-130`) and the server needs a signed run assertion so a remote endpoint can prove which run it acts for (`server/src/agents/callback-token.ts:116-182`). Kinby's contract (ADR 0004) and event stream are already typed; if AG-UI is ever wanted, put an adapter in front of the dispatcher and keep the contract as the source of truth.

**Take-the-wheel state machine.** `agent-computer/src/control.ts:17-236` is a 250-line state machine, `{holder: bot | human, requested, requestedAt, secretWanted}`, with a ten-minute expiry on the request flag and `assertBotMayAct` refusing while a human holds. The secret path types into a masked box that goes straight to the page; the audit row records only a character count (`gateway.ts:746-769`). If kinby adds a browser tool inside the sandbox, this is the shape, and the approval is the trigger.

**Dry-run over history.** `mode: "dry-run"` records a refusal and forwards anyway (`policy.ts:273,297`), and an endpoint replays past audit rows against a draft rule (`computer/policy-dry-run.ts:158`). Kinby's event log holds every tool call, so "which past bash commands would this denylist pattern have caught" is a cheap `kinby` subcommand when denylists get edited in anger.

**Skill summary as the retrieval index, tool narrowing above twelve tools.** Skills are a four-field record (`slug`, `title`, `summary`, `instructions`) plus a tool list that declares, never grants (`server/src/db/schema/plugins.ts:189-226`). They are not fetched on demand: a `/slug` chip inserts the instructions as a system message ahead of the user turn (`app/src/components/channels/channel-chat.tsx:359-378`). Kinby's catalogue plus a read-on-demand skill tool is the stronger model. But when a Bot holds more than twelve tools, a cheap model call picks skills by summary and offers only their tools, failing open (`server/src/plugins/selection.ts:1-135`). Revisit if kinby's default tool count grows. The shipped `skill-creator` instruction (interview, look before you name, rehearse against one request, save once, `examples/fintech/skills.yaml:96-102`) is a template for `write-routine` in #116.

**Model-written thread title.** Three to six words, written once (`server/src/channels/summary.ts:1-26`, `titler.ts:11-16`). Kinby's `thread list` would read better with it; it costs one small model call per thread.

**Container recipe.** One container per Bot with two named volumes, port published on `127.0.0.1` only, `CapDrop: ["ALL"]`, `no-new-privileges`, optional `runsc`, memory limit (`supervisor/src/docker.ts:363-395`). Kinby has one instance per container and does not need the supervisor, but the flags belong in the reference compose file. Same for the workspace checks: refuse `..`, resolve symlinks on both the anchor and the leaf, bound bytes (`agent-computer/src/workspace.ts:119-186`); `plugins/defaults/files.py` does the first two already.

**Constant-time shared-secret compare.** `shared/agent-authorisation.ts:2-20` for the `x-openbot-agent-token` header. The signal receiver in #107 needs exactly this for webhook auth.

**Credential envelope.** AES-GCM, random 12-byte IV, `{version, iv, ciphertext}` keyed by a 32-byte env value, values never returned by any API (`server/src/credentials.ts:6-10,143-193`). The minimum shape if kinby ever stores third-party tokens; the OS keychain is the better answer on a user's own box.

**Connector docs shape.** Each plugin page is who does what (admin vs person), numbered steps, then troubleshooting keyed on the exact audit row names (`docs/plugins/google-drive.md:8-16,110-120`). Copy the shape for integration docs.

**Refuse to start on a missing named value.** Every required env or package value fails startup with its name (`docs/configuration.md:17-27`, `server/src/config.ts:812-826`). Kinby's manifest already rejects unknown keys; do the same for missing required ones and say which.

## Read with care

Places where the README promises what the code does not do. Useful if kinby ever cites OpenBot as prior art.

- `recursionLimit` is documented and never passed (`agent-langgraph/src/index.ts:323,405-408`).
- "CEL policy, fail closed" is true of the engine, but the shipped default is `allow: ["true"]`, so a fresh clone permits everything (`server/src/computer/policy-store.ts:52-56`). A policy language with no rules is theatre.
- "Durable threads and memory" means an external event stream per thread; nothing reads or writes memory across threads.
- Routines cannot browse, read files or run commands; a headless turn has `tools: []` (`run-turn.ts:365-379`). And nothing fires without a second process, a Kubernetes CronJob or a laptop worker loop (`docs/deployment.md:49-56`, `worker/src/index.ts:1-19`).
- Built-in package agents are OpenAI-only (`copilot.ts:126`, `tenant-package.ts:175`).
- Component and approval calls are decided in the app, not by `govern`, and get no `computer.*` audit row.

## Skip

Multi-tenant and multi-replica machinery, all of it: OAuth providers, roles, people admin, coworker visibility and ownership, tenant packages, channel membership, `allowed_groups`, the `work_items` queue with `for update skip locked` and lease renewal (`server/src/routines/sweep.ts:260-442`), the separate worker and its shared secret, `pg_notify` policy fan-out, the Kubernetes sandbox provider, Helm. Kinby is one user, one instance, one directory, one process.

CEL as a policy language and `cel-js`. Kinby's gate reads a write flag, a permission mode and a denylist; that is enough for one person, and it cannot ship with an "allow everything" default because there is no policy to forget to write.

The signed run assertion (`callback-token.ts`). It exists because a bot at a foreign endpoint must prove which run it acts for. Kinby's gate runs in the same process as the graph.

CopilotKit Intelligence for threads. A hard dependency on a hosted service contradicts "your agent, your data, your keys". The JSONL event log plus SQLite checkpoints already give kinby the same durability.

Bot-to-bot handoff with depth and fan-out caps (`config.ts:314-315`). Cross-instance messaging in kinby goes over the server seam, as the Grok Bot note already decided.

The surface-tool split. Browser-executed tools that end the run are an artifact of the app being the only thing that can reach the computer; every kinby tool runs where the gate runs.
