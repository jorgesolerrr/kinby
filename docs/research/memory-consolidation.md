# Memory consolidation and deduplication in agent memory

Ticket: jorgesolerrr/kinby #520, child of map #516. Feeds #523 (episodes, facts, and nothing stored twice) and #108 (dreaming). Researched 2026-10-03.

Question: how current agent memory systems avoid storing the same thing twice and consolidate over time. For each system: when consolidation runs, how a duplicate is detected, how episodes become facts, what happens to the source records, how reprocessing is avoided, and what a run costs. Then what fits kinby's markdown graph feed, its tombstones and its feed gate (ADR 0016).

Source code is pinned to the commit or tag read:

- Mem0 `main` at [`abb81c88`](https://github.com/mem0ai/mem0/tree/abb81c88e1f738a8117d8293530fbc31a5ef8fd9), released as v2.2.1 on 2026-09-25.
- Letta at tag [`0.16.8`](https://github.com/letta-ai/letta/tree/0.16.8). Letta's `main` branch now holds only top-level docs files. The server code is in the tags.
- Graphiti `main` at [`3c427640`](https://github.com/getzep/graphiti/tree/3c427640abf909f12f71f963fce15eb514a3c493).
- LangMem `main` at [`48e3c11f`](https://github.com/langchain-ai/langmem/tree/48e3c11f5bb527282c7d5339c6a87a0b35abccfc).

A claim with no primary source is marked **unverified**.

## kinby today

- The graph is markdown nodes in `memory/graph/`. A node is an episode or a fact, with `date`, `description` and `subjects` frontmatter (`src/kinby/memory/graph.py`).
- The recap writes at most one episode per turn. It never reads the graph and never writes facts (`src/kinby/memory/recap.py`, ADR 0017). The only filter is the recap model's `keep` flag.
- Facts come only from the `remember` tool or the user. The latest fact about a subject is current. There are no supersedes edges (ADR 0016).
- Recall needs every query term to appear as a substring of the description or a subject. It returns the newest 20 matches (`_RECALL_LIMIT`), so repeated episodes push older knowledge out of the window.
- Forget appends `tombstone: true`. `_read_node` returns `None` for a tombstoned node, so `GraphStore.nodes()` cannot see tombstones.
- #108 already agreed one rule: dreaming runs a code step first, and with nothing uncovered it returns `None` and skips both models (ADR 0021). A failed run does not advance coverage, and coverage survives a restart.

## Summary table

| System | When it runs | Duplicate detection | Episode to fact | Source records | Reprocessing guard | Cost per run |
|---|---|---|---|---|---|---|
| Mem0 v3 (current OSS) | On every `add()`, inline | MD5 of the fact text, plus model judgement against the top 10 similar memories | One extraction call writes ADD-only facts | Old facts kept. Changes accumulate, and retrieval ranks them | Model told to skip facts in "recently extracted" and "existing" lists | 1 LLM call, plus embeddings |
| Mem0 paper (v2 algorithm) | On every message pair, inline | Top 10 similar by embedding, then the LLM picks ADD/UPDATE/DELETE/NOOP | Extraction call, then update call | UPDATE and DELETE rewrite or remove. Mem0g marks graph edges invalid | None beyond NOOP | 2 LLM calls per add |
| Letta sleep-time | Background agent every N foreground turns (5 by default) | Model judgement while editing memory blocks | The agent rewrites in-context blocks with `rethink` | Messages are kept. Blocks are rewritten in place | `last_processed_message_id` cursor, advanced before the run | One multi-step agent run |
| Zep / Graphiti | On every episode ingested, inline | Exact normalized match, then MinHash/Jaccard ≥ 0.9 for names, then a small-model call | Facts are edges extracted from each episode | Episodes kept non-lossily. Duplicate edges gain the episode id. Contradicted edges get `invalid_at`/`expired_at` | Exact-match fast path | Several LLM calls per episode |
| LangMem | Inline, or debounced in the background (`ReflectionExecutor`) | Model judgement over existing memories passed in | Manager extracts and patches memories | Hard delete when deletes are enabled | Debounce cancels the pending task per thread | 1+ calls (`max_steps`, default 1) |
| A-MEM | Inline, on each new note | Top-k (10) by cosine, then the LLM decides links | Every note stays a note. Neighbours "evolve" | The evolved neighbour replaces the original | None | ~1.2k–2.5k tokens per operation |
| Deep Agents | Cron-triggered consolidation agent | Model judgement | Agent merges facts into a memory file | Memory file rewritten | Lookback window that must equal the cron interval | One agent run |

## Mem0

**The paper's algorithm: ADD/UPDATE/DELETE/NOOP.** The Mem0 paper splits the work into two phases ([arXiv 2504.19413, §2.1](https://arxiv.org/html/2504.19413)):

- **Extraction.** Takes the new message pair, a conversation summary, and the last m = 10 messages, and produces candidate facts. An asynchronous module refreshes the summary "without introducing processing delays".
- **Update.** For each candidate fact, retrieves the top s = 10 similar memories by embedding. The LLM then chooses by function call: "ADD for creation of new memories when no semantically equivalent memory exists; UPDATE for augmentation of existing memories with complementary information; DELETE for removal of memories contradicted by new information; and NOOP".

The graph variant Mem0g does not delete. Its update resolver marks conflicting relationships "as invalid rather than physically removing them to enable temporal reasoning" ([§2.2](https://arxiv.org/html/2504.19413)). The paper reports about 7k tokens per conversation for Mem0 and about 14k for Mem0g, and a p95 total latency of 1.44 s against 17.1 s for full context ([§4.5](https://arxiv.org/html/2504.19413)). The four-way prompt still ships as `DEFAULT_UPDATE_MEMORY_PROMPT` ([prompts.py:176-185](https://github.com/mem0ai/mem0/blob/abb81c88e1f738a8117d8293530fbc31a5ef8fd9/mem0/configs/prompts.py#L176-L185)). The default `add()` path no longer uses it.

**The current algorithm is ADD-only.** Mem0's open-source migration guide replaces the two calls with one: "collapses this into a single call that only adds. The model spends its capacity on understanding the input rather than diffing against existing state." It also says: "When information changes, the new fact is stored alongside the old one. Retrieval handles ranking". Ranking fuses three signals: vector similarity, BM25, and entity overlap. The guide credits the change with LoCoMo 71.4 → 91.6 and LongMemEval 67.8 → 93.4, and with roughly halving extraction latency ([docs.mem0.ai/migration/oss-v2-to-v3](https://docs.mem0.ai/migration/oss-v2-to-v3)). These are vendor-reported numbers.

The code (`_add_to_vector_store`, [main.py:919-1034](https://github.com/mem0ai/mem0/blob/abb81c88e1f738a8117d8293530fbc31a5ef8fd9/mem0/memory/main.py#L919-L1034)) runs in this order:

1. Load the last 10 session messages.
2. Search the top 10 existing memories for the new messages.
3. Replace the existing memories' UUIDs with small integers ("anti-hallucination").
4. Make one JSON-mode LLM call with `ADDITIVE_EXTRACTION_PROMPT`.
5. Batch-embed the results.
6. Drop any fact whose MD5 hash matches an existing memory or another fact in the same batch.

The prompt does the semantic dedupe. "Recently Extracted Memories" is "your primary deduplication reference". An existing memory that is "semantically equivalent ... with no meaningful new context" is skipped. A new event about a known entity is still a separate memory, linked by `linked_memory_ids` ([prompts.py:464-530](https://github.com/mem0ai/mem0/blob/abb81c88e1f738a8117d8293530fbc31a5ef8fd9/mem0/configs/prompts.py#L464-L530)). The same prompt requires relative dates to be resolved against the observation date ("'User went to Paris last week' is useless 6 months later").

Mem0 has no background consolidation pass, and its source messages are only saved for context.

## Letta sleep-time agents

Sleep-time compute comes from Letta's paper. A model "thinks" offline about a context before queries arrive. The paper reports about 5x less test-time compute for the same accuracy, and a 2.5x lower average cost per query when several related queries share the context ([arXiv 2504.13171](https://arxiv.org/abs/2504.13171)).

In the server, the foreground agent loses its core-memory edit tools, and a sleep-time agent manages "both the in-context memory of the primary agent as well as its own" ([Letta blog](https://www.letta.com/blog/sleep-time-compute/)). Mechanics at tag 0.16.8:

- **Trigger.** Each foreground turn bumps a group turns counter. The sleep-time agents run when `turns_counter % sleeptime_agent_frequency == 0`, and the run is skipped when the turn produced no response messages ([sleeptime_multi_agent_v4.py:132-169](https://github.com/letta-ai/letta/blob/0.16.8/letta/groups/sleeptime_multi_agent_v4.py#L132-L169)). The server creates the group with `sleeptime_agent_frequency=5` ([server.py:784](https://github.com/letta-ai/letta/blob/0.16.8/letta/server/server.py#L784)).
- **Input.** The sleep-time agent reads a transcript of every message after `last_processed_message_id`, plus the new response messages, framed as "a conversation that already happened" ([sleeptime_multi_agent_v4.py:216-245](https://github.com/letta-ai/letta/blob/0.16.8/letta/groups/sleeptime_multi_agent_v4.py#L216-L245)).
- **The cursor moves first.** `get_last_processed_message_id_and_update_async` saves the new cursor before the background task is spawned with `safe_create_task`. A sleep-time run that fails after that point leaves its messages behind the cursor, and they are never reprocessed (same file, lines 152-160 and 186-197).
- **Writes.** The system prompt ([sleeptime_v2.py](https://github.com/letta-ai/letta/blob/0.16.8/letta/prompts/system_prompts/sleeptime_v2.py)) has the agent keep blocks "comprehensive, readable, and up to date". It makes narrow edits or uses `rethink` to rewrite a whole block, and it calls the finish tool directly when nothing is worth editing. It also says not to write "today" or "recently".
- **Duplicates.** Dedupe is whatever the model does while rewriting a size-limited block. No key or similarity check exists. Messages stay in the message store, and the blocks are overwritten.

Letta Code ships the same idea as "dreaming": background subagents "review recent conversations, consolidate useful lessons, and update memory", triggered "after a set number of completed agent steps or when the context window is compacted" ([docs.letta.com](https://docs.letta.com/guides/agents/architectures/sleeptime)).

## Zep and Graphiti

The Zep paper describes three subgraphs. The episode subgraph holds raw input "non-lossily", with bidirectional links from episodes to the entities and facts derived from them. Above it sit the semantic entity subgraph and a community subgraph ([arXiv 2501.13956](https://arxiv.org/html/2501.13956)). Facts are bi-temporal: `t_valid`/`t_invalid` on the event timeline, and `t'_created`/`t'_expired` on the ingestion timeline. A contradiction invalidates the old edge and does not delete it. Entity extraction reads the current episode plus the last n = 4 messages. Communities are extended incrementally and fully refreshed only periodically. All of this happens inline, per episode. Graphiti has no separate sleep pass.

How Graphiti detects duplicates, cheapest first:

- **Entity nodes** ([dedup_helpers.py:220-280](https://github.com/getzep/graphiti/blob/3c427640abf909f12f71f963fce15eb514a3c493/graphiti_core/utils/maintenance/dedup_helpers.py#L220-L280)):
  1. An exact normalized-name match with a single candidate merges the node. A match with several candidates goes to the LLM.
  2. Names that are short or low in entropy go to the LLM. The gates are Shannon entropy < 1.5, length < 6, or fewer than 2 tokens ([lines 31-36](https://github.com/getzep/graphiti/blob/3c427640abf909f12f71f963fce15eb514a3c493/graphiti_core/utils/maintenance/dedup_helpers.py#L31-L36)).
  3. Otherwise, MinHash (32 permutations, band size 4) over 3-gram shingles finds candidates through LSH, and a Jaccard score ≥ 0.9 merges.
  4. Whatever is left is batched into one dedupe prompt.
- **Fact edges** ([edge_operations.py](https://github.com/getzep/graphiti/blob/3c427640abf909f12f71f963fce15eb514a3c493/graphiti_core/utils/maintenance/edge_operations.py)):
  1. Within one extraction batch, exact-text duplicates collapse first (lines 344-352).
  2. Hybrid search (RRF) collects two candidate sets: duplicate candidates among edges between the same two nodes, and invalidation candidates from the whole graph (lines 365-415).
  3. When an edge with the same endpoints and the same normalized fact text exists, the new edge reuses it. The LLM is not called, and the episode uuid is appended to the existing edge's `episodes` (lines 683-695).
  4. Otherwise a small-model call with `resolve_edge` returns `duplicate_facts` and `contradicted_facts` ([prompts/dedupe_edges.py](https://github.com/getzep/graphiti/blob/3c427640abf909f12f71f963fce15eb514a3c493/graphiti_core/prompts/dedupe_edges.py)). The prompt insists: "NEVER mark facts as duplicates if they have key differences, particularly around numeric values, dates, or key qualifiers". Its example "Bob ran 5 miles on Tuesday" / "Bob ran 3 miles on Wednesday" is neither a duplicate nor a contradiction.
  5. A duplicate keeps the existing edge and appends the new episode to its `episodes` (line 752).
  6. A contradicted edge gets `invalid_at` set to the new edge's `valid_at`, and `expired_at = now`. When a candidate is newer than the incoming fact, the incoming fact is expired instead (lines 538-570 and 822-838).

The source episode is never removed. A fact accumulates the list of episodes that support it, and that list is the provenance.

## LangMem

`create_memory_manager` runs with `enable_inserts=True`, `enable_updates=True` and `enable_deletes=False` by default, and with `max_steps` defaulting to 1 ([extraction.py:217-266](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/knowledge/extraction.py#L217-L266)). The caller passes `existing` memories. The model then patches, inserts or (when enabled) removes them, and calls `Done` when finished. The instructions ask it to "consolidate and compress redundant memories", to "remove incorrect or redundant memories", and to prefer "dense, complete memories over overlapping ones" ([lines 185-215](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/knowledge/extraction.py#L185-L215)). Duplicate detection is entirely model judgement.

The store-backed manager retrieves candidates with an optional query model (`query_limit=5`). It can run extra `phases`, whose default instruction is "Deduplicate, consolidate, and enrich these memories" ([lines 846-990](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/knowledge/extraction.py#L846-L990)). Removals are hard deletes through `store.adelete` ([line 1134](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/knowledge/extraction.py#L1134)).

Background mode is `ReflectionExecutor.submit(payload, after_seconds=...)`. A new submit for the same thread cancels the pending one, so a burst of turns collapses into a single run ([reflection.py:273-328](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/reflection.py#L273-L328)). LangMem keeps no cursor. The caller decides which messages to pass in.

## A-MEM

A-MEM stores every interaction as a note with content, timestamp, LLM-written keywords, tags, a context description, an embedding, and links ([arXiv 2502.12110](https://arxiv.org/html/2502.12110)). A new note retrieves its top-k neighbours by cosine (k = 10 by default), and the LLM decides which ones to link. "Memory evolution" then has the LLM rewrite each neighbour's context and tags in light of the new note, and the evolved neighbour replaces the original. The paper has no deduplication step. It reports about 1.2k–2.5k tokens and under $0.0003 per operation, with 5.4 s latency on GPT-4o-mini.

## Deep Agents

The Deep Agents memory docs describe background consolidation as "a deep agent that reads recent conversation history, extracts key facts, and merges them into the memory store", run by a cron ([docs.langchain.com/oss/python/deepagents/memory](https://docs.langchain.com/oss/python/deepagents/memory)). Coverage is a time window: "The cron interval must match the lookback window inside the consolidation agent". If the cron runs more often, conversations are reprocessed. If it runs less often, memories fall outside the window and are dropped. The docs also warn that "consolidating much more often than users converse just burns tokens on no-op runs". The earlier kinby note covers the rest ([deepagents-ideas.md](deepagents-ideas.md), item 4).

## Newer work

- **Memory-R1** trains the ADD/UPDATE/DELETE/NOOP manager with outcome-driven RL (PPO and GRPO) from 152 QA pairs, on 3B–14B models ([arXiv 2508.19828](https://arxiv.org/abs/2508.19828)). The four-way decision is learnable. It is still a write-time model call.
- **LightMem** (ICLR 2026) decouples consolidation from inference with an offline "sleep-time update". It reports up to 38x fewer tokens and 30x fewer API calls with GPT ([arXiv 2510.18866](https://arxiv.org/abs/2510.18866)).
- **MindMemOS** (August 2026) adds an offline "dreaming" pass that merges redundant records and resolves conflicts left by incremental writes. The abstract does not say what happens to the sources ([arXiv 2608.12428](https://arxiv.org/abs/2608.12428)).
- **"Control-Plane Placement Shapes Forgetting"** (June 2026) compares 13 configurations. Deterministic primitives handle lexical and temporal cases but fail canonicalization (5% on identifier obfuscation). A read-side LLM fixes canonicalization but scores 0% on intent-aware deletion. Write-side ("mutation-time") hooks reach 91.7–93.2% overall at $0.17 per test run ([arXiv 2606.15903](https://arxiv.org/abs/2606.15903)). Single author. Not reproduced.
- **Claude Code "Auto Dream"** is **unverified**. Secondary reports describe a background subagent that reads recent session transcripts, merges facts into `MEMORY.md` and topic files, deletes contradicted notes, converts relative dates, and trims the index under 200 lines. It reportedly triggers after 24 hours and at least 5 sessions, or on `/dream` ([wmedia.es](https://wmedia.es/en/tips/claude-code-auto-dream-memory-consolidation)). The official memory page, [code.claude.com/docs/en/memory](https://code.claude.com/docs/en/memory), does not mention it as of 2026-10-03.

## Patterns across systems

1. **Write time versus later.** Mem0 v3, Graphiti and A-MEM do all their work inline. Letta, LangMem's executor, Deep Agents and LightMem defer it. Mem0's move from a write-time diff to ADD-only plus ranking is the strongest recent signal that write-time model diffing costs more than it returns ([migration guide](https://docs.mem0.ai/migration/oss-v2-to-v3)).
2. **Cheap checks before the model.** Every system with explicit dedupe runs a deterministic check first: an MD5 hash (Mem0), exact normalized text, or MinHash/Jaccard (Graphiti). The model only sees a short candidate list: 10 in Mem0 and A-MEM, 5 in LangMem.
3. **Sources survive when time matters.** Systems that answer temporal questions (Zep/Graphiti, Mem0g) keep the episodes and invalidate facts instead of deleting them. Systems that rewrite or hard-delete (Letta blocks, LangMem with deletes enabled, A-MEM evolution) lose history.
4. **Provenance is a list on the fact.** Graphiti's `edge.episodes` is the cleanest version: a duplicate costs one appended id instead of a new record.
5. **Coverage is the weak spot.** Letta moves its cursor before the run. Deep Agents uses a window that must equal the cron interval. Mem0 and LangMem leave coverage to the caller. None of them advances coverage only on success.
6. **Dates are made absolute at write time.** Mem0's extraction prompt and Letta's sleep-time prompt both require it.

## What fits kinby

The points below are recommendations for #523 and #108, drawn from the evidence above. They are not decisions.

1. **Keep episodes, and let a fact cite them.** A consolidated fact is a new node whose frontmatter lists its source episodes, for example `sources: [<node ids>]`. This is Graphiti's `episodes` list. Nothing is rewritten in place, so it matches ADR 0016 and the Correction rule. Recall can hide an episode that a live fact cites, which relieves the newest-20 crowding without touching the episode file. If the fact is later forgotten, its tombstone brings the episodes back into recall with no extra step.
2. **Tombstones mean forget and nothing else.** Consolidation should not tombstone its sources. A tombstone is the user's suppression signal (CONTEXT.md, Tombstone). Reusing it for "merged" would make a later forget ambiguous and would hide the episodes for good.
3. **Dedupe in layers, code first.**
   - Layer 1, in code and safe in the recap: an exact normalized-text check on description plus subjects, like Mem0's MD5 and Graphiti's fast path.
   - Layer 2, in code: candidates by subject overlap and date. kinby already stores `subjects`, and ADR 0016 rejected embeddings for v1. Graphiti shows lexical matching (MinHash/Jaccard ≥ 0.9) is enough for names.
   - Layer 3, a model, in dreaming only: judgement over that short list, using Graphiti's rule that differing numbers, dates or qualifiers are never duplicates.
   - The recap stays cheap and keeps not writing facts.
4. **Contradictions need no new machinery.** "The latest fact about a subject wins" is already the non-destructive model that Mem0 v3 and Mem0g/Graphiti converged on. Dreaming writes a newer fact. It does not delete or tombstone the old one.
5. **Coverage is an event, advanced only on success.** Model it on `memory.recapped` (ADR 0017). For example, a `memory.consolidated` event listing the episodes or turns it covered is appended after the writes succeed. The #108 code step compares recapped turns against consolidated ones and returns `None` when nothing is new. This avoids Letta's cursor-before-run loss and Deep Agents' window drift. Since a crash can land between the writes and the event, layer 1 must make a retry idempotent: re-running the same material finds its own facts and writes nothing.
6. **Tombstoned facts must be visible to dedupe.** `GraphStore.nodes()` skips tombstoned nodes, so a dreaming pass built on it would re-derive a forgotten fact. That breaks the Tombstone rule ("never re-derives a tombstoned fact"). The dedupe read has to include tombstoned facts, and a match against one means "do not write".
7. **Repeated episodes become facts through a code-selected trigger.** The code step selects candidates, for example a subject seen in several uncovered episodes, or one that matches an existing fact. The model only writes or declines. This keeps the #108 rule (no material, no model) and bounds each run to the new episodes plus their candidates.
8. **Eval cases for the feed gate.**
   - A topic repeated across several turns recalls as one fact, and an older unrelated node still fits within the 20.
   - A retried consolidation writes no duplicate.
   - A forgotten fact stays forgotten after dreaming.
   - A changed value ("moved from X to Y") returns the newer fact, and the older one stays reachable by date.
   - Memory tokens stay within the 25% gate in `evals/memory/RESULTS.md`.
