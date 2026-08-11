---
title: "Research: Graphiti/Neo4j graph memory in practice"
labels: [wayfinder:research]
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

The landscape research identified Graphiti (Neo4j-backed, bi-temporal) as the strongest published graph-memory design, and the user is already thinking Neo4j. Go one level deeper, to the facts the memory-architecture decision waits on: (1) Graphiti as a library — API shape for ingest ("episodes") and retrieval, LLM calls it makes internally and whether Claude models are supported for extraction, embedding requirements; (2) its Neo4j footprint — schema it creates, Community Edition sufficiency, Docker resource needs; (3) what it does NOT cover (preference/profile store? conversation transcripts?) — i.e., what a hybrid file-or-block layer must still provide; (4) alternatives if Graphiti disappoints: cognee, plain Neo4j + custom schema, neo4j-graphrag-python; (5) real-world reports of Graphiti in personal-agent use (issues, latency, cost per episode).

## Resolution

Full report: [research/graphiti-neo4j-memory.md](../../research/graphiti-neo4j-memory.md). Key findings:

1. **API**: async Python; ingest via `add_episode(name, episode_body, source, reference_time, group_id)` with Pydantic-defined custom entity/edge ontology; retrieval via `search()` (hybrid embedding+BM25 with graph-distance rerank, no LLM calls, ~200–300 ms) and `_search()` recipes. `add_triplet()` bypasses LLM extraction for known facts.
2. **Claude supported but second-class**: `AnthropicClient` exists (`graphiti-core[anthropic]`, Sonnet + Haiku roles) but is the less-tested path — and **a separate embedding provider is mandatory** (OpenAI default, or Gemini/Voyage/local Ollama), since Anthropic has no embedding API. Embedding choice is baked into the graph.
3. **Neo4j footprint is light**: 3 node labels (Episodic/Entity/Community), 3 relationship types (bi-temporal props on RELATES_TO); Neo4j 5.26+ **Community Edition suffices, no plugins**; a 2 GB Docker container is comfortable. FalkorDBLite is an embedded zero-ops fallback.
4. **Gaps a hybrid layer must fill**: session/transcript model, editable preference/profile store (Lindy-style plain files as source of truth), document storage, context-block assembly, memory-inspection UX, backups — exactly what Zep's paid platform adds on top of Graphiti.
5. **Alternatives**: cognee (lighter, corpus knowledge, no bi-temporal/incremental memory); plain Neo4j + custom schema (Claude-native but rebuilds dedup/invalidation/hybrid retrieval); neo4j-graphrag-python (first-party, but batch GraphRAG with no memory-over-time semantics).
6. **Health & cost**: healthy project (29.8k stars, monthly releases). Ingestion is a multi-call LLM chain per episode: ~5–20+ s, cents per episode, 600k+ tokens observed for one long conversation.

**Most decision-relevant fact**: `add_episode` costs seconds and cents in LLM calls — Graphiti only works as an **async background consolidation layer fed distilled turns** (a natural fit for the sleep-time consolidation idea in the fog), never as the inline per-message memory path; and the stack needs a second (embedding) provider alongside Claude.
