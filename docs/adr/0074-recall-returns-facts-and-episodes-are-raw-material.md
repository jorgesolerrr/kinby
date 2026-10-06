# Recall returns facts, and episodes are raw material

Long-term memory is the **facts** the agent has learned. **Episodes** are the raw material **dreaming** learns them from. A memory search returns facts only. It returns episodes only when the search is bounded by date, as in "what happened on Tuesday", or when no fact matches, as in "when did I last touch X". A **turn** with **no work** writes no episode. Dreaming consolidates a repeated lesson into one fact that cites its **source episodes**; it never merges, rewrites or tombstones an episode. An episode older than three months that dreaming has **covered** and no live fact cites is **pruned**: its file is deleted.

This amends ADR 0016, where search returned the newest 20 nodes of any kind, and ADR 0021, where a no-work turn wrote a trace episode. On the daily coder instance, 95% of the graph was no-work traces. They held 16 to 20 of the top 20 search slots for 14 of 15 common terms, so a lesson more than two days old could not be recalled.

## Considered options

- **Rank facts ahead of episodes.** Rejected. Even with facts first, a search with few matching facts filled the rest of the 20 with today's episodes. The agent does a task again rather than read how it was done last time, and the new run leaves its own episode.
- **Merge repeated episodes into one summary episode.** Rejected. A merged episode rewrites the evidence, and the fact that cites its sources already serves as the summary.
- **Tombstone consolidated or old episodes.** Rejected. A tombstone means the user forgot something. Reusing it for "merged" or "old" would make a later forget ambiguous.
- **Deduplicate in the recap at write time.** Rejected. Once no-work traces are gone, the recap writes no exact repeats, and a repeated lesson only shows up across several turns, which only dreaming sees.

## Consequences

- A prune deletes the file without leaving a tombstone. The turn keeps its `memory.recapped` marker, so the recap never writes the episode again. The raw turn stays in the thread's history (ADR 0072).
- If dreaming never runs, nothing is covered and nothing is pruned.
- Forgetting a fact removes its citation. Its source episodes become prunable once they are old enough.
