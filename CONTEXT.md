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

### Transcript store

The canonical record of every conversation with the agent. The knowledge graph is derived from canonical sources like this one, never the reverse.

### Reasoning trace

An append-only log of the agent's reasoning steps for a task, linkable to both short- and long-term memory. The third of the three memories.

### Ingestion pipeline

The path content travels into the knowledge graph: the user drops or uploads an item, or an integration delivers one, and the pipeline turns it into graph knowledge.

### Backfill

A user-initiated, explicitly scoped ingestion of historical content (a folder, a date range, a label) — as opposed to the default incremental ingestion of new items as they arrive.

### Instance

One kinby deployment: a directory (and, when deployed, a container) that owns its behavior configuration, memory and transcripts. One instance serves one user with one persona. Every instance has the same shape; a repo-scoped coding agent is an instance whose workspace is that repo, not a different kind of instance.

### Instance discovery

The ordered lookup that selects an existing **instance** when a command does not name one directly. The matching rule is part of the result so kinby can tell the user why it selected that instance.

### Manifest

The portable description of an instance's identity and configuration. It contains no secrets or runtime state.

### Workspace

The directory holding the user's *own* work that an instance acts on — a repository, a notes folder. Lives under the instance (cloned there in a container, linked there on a local install) and is never written to by kinby on its own behalf: the instance's behavior stays in the instance, though it may *read* the workspace's **conventions** as an additional behavior source.

### Conventions

The workspace's own instruction files and skill directories an **instance** may read as extra behavior sources. Named explicitly in the **manifest**. The instance never loads tools from the workspace.

### Thread

One conversation with its own durable history. Survives across sessions; can be resumed later. What memory distills from and what evals replay.

### Session

One run of the agent loop against a thread, from start to exit (a process, a REPL open–close). Ephemeral; the unit a server wraps. A session contains one or more **turns**.

### Turn

One user-to-agent cycle within a thread: from a user message until the agent yields control back. The natural unit of token attribution, checkpoint bracketing, compaction boundaries, and eval cases.

### Turn interruption

A user's request to stop the active turn before it completes.

### Token usage

The input and output tokens attributed to a turn, with totals rolled up for its thread.
A usage range includes completed turns whose event timestamp falls on or between its optional bounds.

### Turn runner

The part of the runtime that produces the agent's response and the turn's events.

### Approval

A user decision a live turn waits on before it continues. Requested as an event; answered through the contract. A parked turn stays live until the user answers or the turn is interrupted.

### Contract

The typed set of commands and subscriptions every client uses to drive a session. The CLI is its client today; a server can use the same boundary later. Clients import contracts, never core.

### Event

One sequence-numbered record emitted while a turn runs. The event stream is what clients subscribe to and what the **transcript store** persists; replaying it reproduces a thread.

### Scope

A named permission a **contract** command requires of its caller. Holding a connection is not permission to call everything.

### Plugin

Anything an **instance** loads beyond the core: a **tool** or a **skill**, from the instance directory or from an installed package. The workspace never supplies plugins.

### Tool

A native Python capability the model can call during a **turn**. Every tool declares whether it writes (changes files, state, or the outside world); the **gate** reads that flag. Tools present at the start of a turn are the tools for that turn.

### Skill

A markdown instruction set the model reads on demand. Skills are listed to the model by name and description; the body is fetched only when the model asks for it.

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
