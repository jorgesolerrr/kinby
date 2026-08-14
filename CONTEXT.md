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
