---
title: Decide the memory architecture
labels: [wayfinder:grilling]
status: open
assignee:
blocked-by: [03-research-graph-memory.md]
---

## Question

The core bet of the project. Decide: (a) pure graph (everything in Neo4j/Graphiti) vs hybrid (inspectable plain-file layer for preferences/persona + graph for entities, episodes, multi-hop/temporal recall) — note Lindy shipped plain files and mem0 abandoned graphs, so the graph must earn each layer it claims; (b) what gets written to memory and when (every message? distilled facts? tool results?); (c) the retrieval contract the agent programs against (query API the rest of the system sees, so the storage can evolve behind it); (d) inspectability story — how the user reads and corrects what the agent believes; (e) whether Graphiti is adopted, wrapped, or replaced by a custom Neo4j schema. Output: a memory spec section for the blueprint.
