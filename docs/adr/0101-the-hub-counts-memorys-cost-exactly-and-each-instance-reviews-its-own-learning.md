# The hub counts memory's cost exactly, and each instance reviews its own learning

Extends [ADR 0063](0063-the-hub-aggregates-usage-live-and-stores-none.md), [ADR 0097](0097-a-factory-run-is-recapped-once-from-a-digest-of-its-coding-sessions.md) and [ADR 0099](0099-dreaming-runs-in-stages-as-routines-whose-body-is-the-lens.md).

Recaps write episodes, dreaming turns them into facts, and reflection proposes skills. Nothing shows whether any of it pays off. The first dreaming pass on real data, on 2026-10-18, was judged by reading its output by hand. The stats that exist count tokens and cost per turn, but:

- Memory tokens are an estimate: the characters of memory tool output divided by four. The profile is not counted.
- Nothing records which facts a turn read. Fact ids appear only inside tool calls and results.
- A factory run has no token or cost total. Its coding client's tokens land on the instance's log with no step or attempt, and its recap's tokens are dropped from stats.

ADR 0097 named the measure: tokens per run and send-backs, on work with memory against work without.

## Decision

Two measures, the counted one first.

**Usage is counted, exactly, and derived on read.**

- **Memory tokens come from the provider's own counts.** Between two model calls in a turn, the input grows by the previous call's output plus the tool results that came back, so a tool result's size is `input(n+1) − input(n) − output(n)`. When one step returns memory and other results together, its delta is split by each result's share of the characters. A turn records memory's size, added once, and its carried tokens, that size times the remaining calls, priced at the cache rate. The profile counts by its size, from the provider's tokenizer once per profile version. A coding session's memory search is counted the same way from its transcript's per-message usage, through the session digest.
- **A recall event records what memory a turn used.** `memory.recalled` names the facts and episodes a search or open returned. `kinby memory search` writes one too when it runs inside a factory run's coding session.
- **Run usage is derived from the hub and the instance together.** A factory run's tokens and cost join its agent steps' turns, its delegated runs and its recap on the run id. Attempts, retries and send-backs come from the hub's step attempts, and duration from the run's creation to its end. `run.delegated` gains the step and attempt, and the run recap's tokens count toward the run. Nothing is stored.
- **Where it shows.** The instance stats page gains a Runs tab with each run's usage and weekly medians. The Memory card on the Quality tab gains memory tokens per turn, and runs that recalled a fact against runs that did not. The hub's Usage page gains a runs column per instance.
- **No claim of cause.** The pages show the weekly trend and the recalled-against-not split next to each other. Whether memory made the difference is the learning review's job.

**Learning is judged by each instance, weekly.**

- **The learning review is a dreaming stage.** It is a routine like the others, with its own lens, run on Sunday after reflection, under a token budget set from its first measured run.
- **A code step picks the candidates, the model judges them.** The candidates are the week's turns and runs that went badly: a failed tool, a retry, a send-back, a bad rating. Each comes with the facts a search matches for it and what it recalled. A week with no candidates makes no model call.
- **Two verdicts, each cited.** A repeated mistake is a matching fact that was not recalled. An ignored lesson is a fact that was recalled and then contradicted. Each verdict cites the turn or run and the fact, as dreaming cites episodes.
- **A report only.** The review writes no memory and makes no proposals. Its dream report is read by the user.
- **Trust is earned.** The user rates the review's turn, and a bad rating names the wrong verdict. Verdicts marked wrong become cases in an `evals/learning` set of fixture weeks. Turning findings into proposals waits until the ratings hold for a few weeks.

## Considered options

- **Keep the character estimate for memory tokens.** Rejected. A trend only needs consistency, but the provider already reports exact counts per call, so the exact number costs nothing more to derive.
- **Run a tokenizer on every memory result.** Rejected. It means a count call or a local tokenizer per result. The delta is exact per step, and only a mixed step's split is approximate.
- **Compare the same area before and after an area fact exists.** Not taken. Runs have no area field, and the work changes from week to week. The recalled-against-not split and the trend need no new field.
- **The home instance judges across the hub.** Not taken. ADR 0096 lets it read every instance, but one instance's verdicts must prove trustworthy before a hub-wide judge earns its cost. A per-instance stage reads the memory dreaming already reads, and its report stays with that memory.
- **Findings become proposals.** Deferred. A repeated mistake whose fact exists is a failure to recall, not missing memory, so a proposal would mostly duplicate reflection's. And it would write memory from a judgement nobody has checked.
- **Score the optimized path.** Rejected. It is too vague to grade, and the counted trend covers cost.

## Consequences

- Each turn's memory tokens change meaning: earlier stats used the estimate, and a bucket that mixes both is not comparable.
- Every memory search and open writes an event.
- A quiet week costs the review nothing. A busy one costs one model call over a short candidate list.
- The judge's quality is visible only through the user's ratings until `evals/learning` has cases.
