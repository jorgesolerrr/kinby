---
title: Decide the memory architecture
labels: [wayfinder:grilling]
status: closed
assignee: jorge
blocked-by: [03-research-graph-memory.md]
---

## Question

The core bet of the project. Decide: (a) pure graph (everything in Neo4j/Graphiti) vs hybrid (inspectable plain-file layer for preferences/persona + graph for entities, episodes, multi-hop/temporal recall) — note Lindy shipped plain files and mem0 abandoned graphs, so the graph must earn each layer it claims; (b) what gets written to memory and when (every message? distilled facts? tool results?); (c) the retrieval contract the agent programs against (query API the rest of the system sees, so the storage can evolve behind it); (d) inspectability story — how the user reads and corrects what the agent believes; (e) whether Graphiti is adopted, wrapped, or replaced by a custom Neo4j schema. Output: a memory spec section for the blueprint.

## Resolution

Decided 2026-08-14, grilling session with Jorge. Key asset consulted: [Graphiti/Neo4j research](../../research/graphiti-neo4j-memory.md).

### Conceptual model — three memories, one architecture

Jorge's framing, adopted as the blueprint's vocabulary:

- **Short-term memory** — the recent past (current and past sessions). Served by the transcript store via recency queries.
- **Long-term memory** — durable knowledge: the **profile** (preferences/persona) plus the **knowledge graph** (entities, episodes, temporal facts — a Karpathy-style *life wiki* built from the user's content, not primarily from chat logs).
- **Reasoning traces** — an append-only log of the agent's reasoning steps per task, linkable (edges) to both short- and long-term memories. Named as a layer here; its concrete form is deferred to the fog (sharpens with the context/skills decision).

### (a) Hybrid, not pure graph

Four storage layers:

1. **Profile** — human-legible plain file(s); source of truth for preferences, persona, standing instructions. The *only* memory always injected into context.
2. **Transcript store** — canonical conversation record owned by kinby. The graph is *derived* from canonical stores and rebuildable (ontology or embedder changes = re-ingest, not data loss).
3. **Knowledge graph** — Neo4j CE + Graphiti: entities, episodes, bi-temporal facts from ingested content and conversation-derived facts. `reference_time` = the document/event date, so "what happened on X day" works.
4. **Reasoning-trace log** — append-only, per task, with links into the other layers.

Rationale: Lindy shipped plain files, mem0 abandoned graphs, Graphiti has no profile/transcript abstractions; the graph earns exactly one job — relational/temporal recall over the user's life — and Graphiti's bi-temporal model is the thing files and every alternative (cognee, custom schema, neo4j-graphrag) lack.

### (b) What gets written, and when

- **Content ingestion is the primary stream** (the life-wiki reframe): documents, emails, integration items. v0.1 intake points: **watched inbox folder** on the Docker data volume + **web-UI upload**; integrations are declared as "deliver items to the ingestion pipeline" (wiring stays in the integration-architecture ticket).
- **Chunking by size**: small document = one episode; large = chunked by kinby's pipeline (Graphiti degrades on oversized episodes).
- **Conversation stream is secondary and distilled**: an async background consolidation pass (never inline in the chat loop) distills sessions into episode-sized facts; already-structured facts (tool results, integration events) use `add_triplet`, skipping LLM extraction.
- **Explicit writes are agent tools**: `remember` → verbatim episode into the graph (today's `reference_time`); `update_profile` → profile file edit. Two tools, not one — the routing choice is visible in the tool-call log.
- **Cost posture: incremental by default; backfill is user-initiated, scoped, and preceded by an item-count/cost estimate.** Extraction is a multi-call LLM chain (~5–20 s, cents/episode); un-scoped archive imports are the documented runaway-cost scenario.

### (c) Retrieval contract — agent tools (agentic RAG)

Nothing graph-side is auto-injected; the model decides when to query. kinby-owned tools over kinby's own memory interface:

- `memory_search` — hybrid semantic+fulltext search ("search my life")
- `memory_timeline` — day/date-range recall ("what happened on X day")
- `remember` / `forget` — explicit graph writes
- `update_profile` — explicit profile writes

This tool set is the whole contract the rest of kinby (and the model) sees; storage evolves behind it. Graphiti's bundled MCP server is **not** used — it would bypass the wrap and the hybrid layers.

### (d) Inspectability

Graph-as-index with **rendered entity/timeline pages** in the web UI — a browsable read-only view of what the agent believes (per entity, per day). Corrections flow via chat (`remember`/`forget`); the profile file is directly editable; the tool-call log makes every memory write auditable. Direct graph editing is a post-v0.1 luxury.

### (e) Graphiti: wrapped

Adopted as engine, hidden as implementation detail behind the memory interface (swappable for FalkorDBLite or a custom schema later). Stack: Neo4j 5.26 CE in Compose (2 GB container: 1 GB heap / 512 MB pagecache), `AnthropicClient` extraction (Sonnet-class `model`, Haiku-class `small_model`), versions pinned (Anthropic is Graphiti's second-class-tested path). **Embedder pluggable, default Voyage** (Claude-first spirit), documented local Ollama option (`nomic-embed-text`) for zero-external-keys deployments. `forget` = **hard delete** (user sovereignty on a self-hosted agent); automatic bi-temporal invalidation is reserved for contradicted facts.

### Process note

Jorge's standing objective for this project is *learning AI-agent construction from the ground up*: future sessions should run as **teach sessions** (via a teach skill he will invoke), using the ticket roadmap as the curriculum — build understanding first, artifacts second.
