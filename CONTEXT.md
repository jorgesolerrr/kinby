# Context — kinby

Glossary of the ubiquitous language. Terms only — no implementation detail.

## Terms

### kinby

The project's name: an open-source, self-hosted personal AI teammate. An invented word — *kin* + *by*, kin at your side. Always lowercase; the same single word names the GitHub repo, the PyPI package, the Python import, and the CLI command. Refers to the *software project*, never to an individual running instance's persona.

### Persona name

The per-instance display name a user gives *their* running agent ("call it whatever you like"). A configuration field, chosen by each self-hoster; deliberately distinct from **kinby**, which names the project itself.

### Short-term memory

The recent past: the current and previous sessions, recalled by recency. One of the three memories, alongside **long-term memory** and **reasoning traces**.

### Long-term memory

Durable knowledge the agent keeps about its user: the **profile** plus the **knowledge graph**.

### Profile

The human-legible record of the user's preferences, persona settings, and standing instructions. The only memory that is always present in the agent's context; directly editable by the user.

### Knowledge graph

The life wiki: entities, episodes, and time-stamped facts about the user's life — drawn from documents, emails, and other content the user grants the agent, plus facts distilled from conversation. Queried by the agent on demand (never auto-injected); answers "search my life" and "what happened on X day". Retires the earlier term *memory graph*.

### Episode

One distilled record in the **knowledge graph** of a single **turn**: what happened, what was decided, what should have gone differently, and the tools and path the agent used. Written by the **recap** and lets later work reuse or improve that path.

### Fact

An atomic, timestamped statement in the **knowledge graph**, recorded when it is learned. Recency decides which fact is current: the latest fact about a subject wins.

### Transcript store

The canonical record of every conversation with the agent. The knowledge graph is derived from canonical sources like this one, never the reverse.

### Reasoning trace

The third memory records the agent's reasoning and actions for a task. The **transcript store** holds its canonical raw form, while an **episode** holds its distilled form in long-term memory.

### Memory facade

The single interface through which kinby recalls, opens, remembers, and forgets. Core callers and **memory tools** share one implementation and stay independent from the **feeds** behind it.

### Feed

One source of memory behind the **memory facade**, such as the **profile**, the **knowledge graph**, or a RAG index over the **transcript store**. Each feed must pass its evals before kinby adds the next one.

### Eval

An offline run of fixed cases that measures an **instance** or one of its parts against reference answers under a named model. Paid for and run on demand; never part of ordinary use.

### Feed gate

The correctness and memory-token thresholds a **feed** must pass before kinby builds the next one.

### Memory tool

A core **tool** that exposes the **memory facade** to the model. Search and open are read tools, while remember and forget are write tools governed by the **gate**.

### Ingestion pipeline

The path content travels into the knowledge graph: the user drops or uploads an item, or an integration delivers one, and the pipeline turns it into graph knowledge.

### Backfill

A user-initiated, explicitly scoped ingestion of historical content (a folder, a date range, a label) — as opposed to the default incremental ingestion of new items as they arrive.

### Tombstone

The suppression marker a forget leaves behind. The **knowledge graph** is derived from canonical sources, so deletion alone would let a fact resurrect on re-ingestion; the **ingestion pipeline** never re-derives a tombstoned fact. The **transcript store** itself is never rewritten.

### Instance

One kinby deployment: a directory (and, when deployed, a container) that owns its behavior configuration, memory and transcripts. One instance serves one user with one persona. Every instance has the same shape; a repo-scoped coding agent is an instance whose workspace is that repo, not a different kind of instance.

### Instance discovery

The ordered lookup that selects an existing **instance** when a command does not name one directly. The matching rule is part of the result so kinby can tell the user why it selected that instance.

### Instance boot

The transition from a loaded **instance** to its live **instance runtime**.

### Instance runtime

The live state and background work of one booted **instance**, owned and stopped together. It can stop after a running **routine** finishes or interrupt that routine immediately.

### Serve mode

An **instance runtime** without a REPL, kept alive so the **scheduler** can fire **routines**.

### Manifest

The portable description of an instance's identity and configuration. It contains no secrets or runtime state.

### Workspace

The directory holding the user's *own* work that an instance acts on — a repository, a notes folder. Lives under the instance (cloned there in a container, linked there on a local install) and is never written to by kinby on its own behalf: the instance's behavior stays in the instance, though it may *read* the workspace's **conventions** as an additional behavior source.

### Conventions

The workspace's own instruction files and skill directories an **instance** may read as extra behavior sources. Named explicitly in the **manifest**. The instance never loads tools from the workspace.

### Workspace snapshot

The recorded state of every file in the **workspace** at one **turn** boundary, ignored files excluded. Every turn has one from before it started and one from when it closed, so the difference between them is exactly what the turn changed.
_Avoid_: checkpoint (that is the **graph checkpoint**), commit

### Workspace diff

The file changes and unified patch between two **workspace snapshots**. A turn's workspace diff compares its starting snapshot with its closing snapshot, so it contains only that turn's changes.
_Avoid_: git diff (the workspace may not be a Git repository), changes (when the snapshot comparison matters)

### Workspace revert

A user's request to put the workspace back to the **workspace snapshot** taken before a chosen **turn**, discarding that turn's changes and every later turn's. Recorded on the thread like a turn, with its own snapshots, so a revert can itself be reverted.
_Avoid_: rollback, undo

### Turn target

A **turn** or recorded **workspace revert** selected for a **workspace diff** or **workspace revert**.

### Thread

One conversation with its own durable history. Survives across sessions; can be resumed later. What memory distills from and what **instance statistics** are derived from.

### Session

One run of the agent loop against a thread, from start to exit (a process, a REPL open–close). Ephemeral; the unit a server wraps. A session contains one or more **turns**. Not a model call: a turn makes one or more model calls, and a model-assisted **recap** makes one more after it.

### Turn

One cycle of agent work within a **thread**, started by the user or a **wake**, ending when it completes, fails, or is interrupted. The natural unit of token attribution, checkpoint bracketing, compaction boundaries, and eval cases. A turn is also the unit of work: what the user calls a task is a turn, and each turn earns at most one **episode**.
_Avoid_: task

### Budget

A ceiling an **instance** sets on one **turn** (steps, tokens, seconds) or on a UTC day (cost). Reaching it closes the turn as failed. Absent means unlimited; a **routine** may lower a budget, never raise it.

### Turn interruption

A user's request to stop the active turn before it completes.

### Turn rating

The user's good or bad verdict on one **turn**, with an optional reason. A later rating becomes the current verdict while earlier ratings remain part of the thread's history.

### Feedback policy

The **instance** setting that chooses whether to ask for a **turn rating** after every completed **turn** or never ask.

### Token usage

The input and output tokens attributed to a turn, with totals rolled up for its thread. Input tokens include the cached tokens the provider reports, recorded as a split so a lost prompt cache is visible.
`usage.get` includes completed turns whose closing timestamp falls within its **time range**.

### Price map

The table of input, output, cache read, and cache write prices per million tokens, keyed by exact `provider:model` names.

### Daily cost

The priced spend attributed to turns closed during one UTC day.

### Time range

Optional, inclusive `since` and `until` bounds applied to a timestamp. `usage.get` and `stats.get` both apply it to a turn's closing timestamp.

### Turn metrics

The derived record of one closed **turn**. It describes the outcome, duration, tokens, tool and memory calls, approvals, and current **turn rating**.

### Instance statistics

Per-turn measures of one **instance** aggregated by UTC day or week. Derived from the **transcript store** through **turn metrics**, `kinby stats` recomputes the totals and writes `stats.json`.

### Model call

One request to a model during a **turn**, recorded as an event with its tokens and duration. A turn's **token usage** is the sum of its model calls; the closing event carries that sum.

### Model call mismatch

A **turn** whose recorded **model calls** do not sum to the **token usage** on its closing event.

### Navigation

The read-only **tool** calls and model reasoning a **turn** spends locating what it needs in the **workspace**. A turn's navigation records all read calls and their duration. It also records the read calls before the first write, distinct paths, repeat opens, tokens before the first write, and write calls. The write count identifies turns that acted. Memory tools and the skill tool are not navigation. Its trend for one workspace shows whether memory is teaching the agent the workspace.
_Avoid_: exploration, workspace search

### Prompt version

A hash of the rendered **system prompt** without the **environment block**, recorded when a turn starts. Groups turns by the prompt the user wrote.

### Eval arm

One way of preparing the same **eval** case for comparison. The memory eval has a graph arm, which uses the case's **knowledge graph**, and a stuffing arm, which puts the case's raw transcript in the **profile** and removes the graph.

### Turn runner

The part of the runtime that produces the agent's response and the turn's events.

### Recap

The retrospective work that runs after a **turn** ends. It records the tool path and may use a model to distill the turn into an **episode**; it never delays the next turn or writes **facts**.

### Recap policy

The **instance** setting that chooses a model-assisted **recap** after every turn or a trace-only recap.

### Recap prompt

The instance's own instructions to the **recap** model: what a retrospective means for this agent. Kinby supplies the frame and a default lens; the instance may replace the lens. A **behavior prompt** for the recap.

### Graph checkpoint

The turn runner's durable working state for a thread. It lets a parked approval resume after a process restart and carries completed thread state across turns. The **transcript store** remains the canonical conversation record.

### Approval

A user decision a live turn waits on before it continues. Requested as an event; answered through the contract. A parked turn stays live until the user answers or the turn is interrupted.

### Approval decision

The normalized answer to an **approval**: approve or deny. The answer `yes` approves; every other answer denies.

### Gate

The check every **tool** call passes through before it runs. It reads the tool's write flag and the instance's permission policy, and answers allow, ask (raise an **approval**), or deny. The policy is the instance's ceiling; a **thread** may narrow it, never widen it.

### Gate rule

The identifier for the policy condition that produced a **gate decision**, such as `mode.ask.write` or `bash.deny[0]`.


### Gate decision

The **gate**'s final verdict on one **tool** call, recorded as an event: allow or deny, the rule that decided it, and whether policy or the user decided. An **approval** the user refuses is a deny decided by the user.

### Sandbox

The isolation boundary around an **instance**. When deployed, the container is the hard wall: the worst a runaway command can destroy is the container itself. Everywhere, the **gate** is the soft wall. A new instance means a new sandbox.

### Permission mode

A named preset of **gate** rules a **thread** runs under: read-only, ask, auto, or full-access. The instance sets the default for new threads and the ceiling; a thread may pin any mode up to the ceiling, never above it.

### Denylist

The instance's list of command patterns the **gate** refuses or escalates regardless of **permission mode**. A tripwire, not a wall: it catches obvious disasters, while the **sandbox** provides the actual isolation.

### Contract

The typed set of commands and subscriptions every client uses to drive a session. The CLI is its client today; a server can use the same boundary later. Clients import contracts, never core.

### Event

One sequence-numbered record in a **thread**'s durable history. Events record turn activity and durable thread state. The event stream is what clients subscribe to and what the **transcript store** persists; replaying it reproduces a thread.

### Scope

A named permission a **contract** command requires of its caller. Holding a connection is not permission to call everything.

### Plugin

Anything an **instance** loads beyond the core: a **tool** or a **skill**, from the instance directory or from an installed package. The workspace never supplies plugins.

### Tool

A native Python capability the model can call during a **turn**. Every tool declares whether it writes (changes files, state, or the outside world); the **gate** reads that flag. Tools present at the start of a turn are the tools for that turn.

### Tool package

An installed **plugin** that supplies one or more tools to an **instance**.

### Default tools

The **tool package** kinby supplies to every instance unless its **manifest** disables it: read, write, edit, grep, glob and bash.

### Tool registry

The instance-owned collection of tools available to turns. It keeps the last valid set when a tool file cannot load.

### Tool snapshot

The fixed, name-sorted set of tools for one **turn**. The model and tool calls use the same snapshot for the whole turn.

### Skill

A markdown instruction set the model reads on demand. Skills are listed to the model by name and description; the body is fetched only when the model asks for it.

### Skill tool

The core, read-only **tool** that returns an available **skill** body by name. It is present on every **turn**.

### Instance tool

A core write **tool** through which the agent changes its own **instance**, such as a **routine** or a **skill**, under the **gate** like any other write. Workspace file tools never reach the instance.
_Avoid_: self-modification tool, config tool

### Behavior prompt

The instance's own instructions to the model (`SYSTEM.md`). One of the sources assembled into the system prompt, alongside the **profile**, workspace **conventions**, the skill list, and the harness-owned environment block.

### System prompt

The single system message assembled for each **turn** from an ordered set of **prompt sections**.

### Prompt section

One named, attributable part of the **system prompt**. Missing file-backed sections are omitted.

### Preamble

The constant, harness-owned **prompt section** that introduces the teammate and the kinby software.

### Skills catalogue

The **prompt section** that lists each available **skill** by name and description. The model reads a skill's body on demand.

### Environment block

The last **prompt section**, containing the instance id, optional **persona name**, **workspace** path, main model, and date.

### Wake

The start of a **turn** by anything other than the user typing. Every wake carries an **origin**, which the thread records and the prompt renders as a cue.

### Origin

What started a **turn**: the user, a **routine**, or a **signal**.

### Routine

A **wake** an **instance** owns: a prompt, and optionally the instance's own **code step** that runs first. Started by a schedule, by a **delivery**, or by hand. Lives as a directory in the instance; its file is the source of truth.
_Avoid_: cron job, automation, job

### Code step

A **routine**'s own deterministic code, a **tool** never offered to the model, that runs before the model and may report "nothing new", completing the **turn** with **no work**.

### Coding client

A command-line agent that implements or reviews repository changes for a coding **instance** under a separate model subscription.
_Avoid_: model, subagent

### Delegated pipeline

The issue-to-PR process that assigns implementation and review to **coding clients**, then gives the coding **instance** a **pipeline report**.

### Factory

The coding **instance** process that turns **eligible issues** into **agent PRs** through a **delegated pipeline**.

### Review round

One independent review of the current change against both the repository's standards and its ticket, followed by fixes when the review has hard findings.

### Agent PR

A pull request that a **delegated pipeline** opens for one **eligible issue**. Its branch name starts with `agent/`.
_Avoid_: automated PR, bot PR

### Stack

A parent issue's ordered series of **agent PRs**, each based on the PR below it. A top-level issue forms a stack of one.
_Avoid_: issue tree, branch chain

### Eligible issue

An open issue marked `ready-for-agent` that has no **agent PR** and whose open blockers already have an agent PR in the same **stack**.

### Pipeline report

The structured result of one **delegated pipeline** run. It identifies the issue and outcome, the PR when opened, review findings, checks, client usage, durations, and any failure.

### No work

A completed **turn** whose **code step** found nothing for the model to do. Neither the main model nor the **recap** model runs.

### Signal

An inbound stimulus from outside the **instance**, such as a webhook call or a received email, that wakes the agent without a schedule. Never called an event: that word names a thread history record.
_Avoid_: event, trigger, webhook (as the concept)

### Receiver

The part of **serve mode** that listens for **signals**, authenticates each call against the **routine** it names, and records the accepted ones as **deliveries**. A rejected call leaves no record.

### Delivery

One authenticated inbound call the **receiver** accepted for a **routine**. Recorded the moment it arrives, before any work starts, so it survives a restart. The **scheduler** starts its **turn** when the instance is free. Two deliveries with the same provider id are one delivery.

### Delivery receipt

The **scheduler** result returned when the **receiver** records a **delivery**. It carries the accepted thread, turn and event sequence, plus whether the provider id matched an existing delivery.

### Scheduler

The instance's coordinator of **routine** wakes, whether due by schedule or waiting as a **delivery**.

### Firing

One attempt to start a **routine** turn, whether scheduled, caught up after downtime, requested manually, or started by a **delivery**.

### Routine payload

The body text and inferred content type read from a file passed to `kinby routine run --payload`.

### Armed routine

An enabled scheduled **routine** with its next firing time selected.

### Routine notice

A durable message about a **routine**'s first failure in a streak or its automatic disablement.

### Failure policy

The rule that counts consecutive failed **firings**, reports the first failure, and disables a **routine** at ten failures. Successful work resets the count; no work, interruption, and daily-budget refusal leave it unchanged.

### Cron schedule

A five-field recurrence rule for a **routine**, interpreted in the instance's time zone.
