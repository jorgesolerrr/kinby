# Memory is one facade over feeds proven in order

Ticket #8 chose one internal facade for recall, open, remember, and forget so core callers do not depend on a feed. Core calls the facade directly, while the model uses core memory tools. No contract methods ship in v1.

## Consequences

- Feeds are built in order: the profile, the light knowledge graph, then an index over the transcript store. Each feed must pass expected-behavior checks before kinby adds the next one.
- The checks compare answer correctness and memory tokens against putting the whole transcript in context. Ticket #10 owns the numeric thresholds.
- The light graph stores human-readable episodes and facts as markdown nodes in `memory/graph/`. Only recomputable indexes live in `.state/`.
- `memory_search` matches frontmatter descriptions and subjects within optional date bounds, returns the newest results first, and `memory_open` returns the node body.
- The latest fact about a subject is current. The graph has no supersedes edges in v1.
- Forget writes a tombstone because deleting a derived fact would let ingestion recreate it. Memory operations never rewrite the transcript store.

## Considered options

- Per-feed interfaces were rejected because they would spread memory behavior through the runtime and make a future server wrap several interfaces.
- A vector or graph database was rejected for v1 because the required temporal questions need dates, subjects, and recency rather than embeddings.
