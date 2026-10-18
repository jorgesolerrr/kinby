# Dreaming runs in stages as routines whose body is the lens

Ticket [#108](https://github.com/jorgesolerrr/kinby/issues/108) designs **dreaming**. [ADR 0074](0074-recall-returns-facts-and-episodes-are-raw-material.md) and [ADR 0098](0098-dreaming-also-consolidates-episodes-about-one-area-into-an-area-fact.md) said what it writes: a **fact** for a repeated lesson, an **area fact** for a shared area, each citing its **source episodes**. One night of episodes is too little to show how the user works, and each instance looks for different patterns, so a single nightly pass with a fixed prompt would not do.

## Decision

- **Dreaming runs in stages.** Each **dreaming stage** is an ordinary **routine** with its own cadence. kinby ships two. **Consolidation** runs nightly and writes facts and area facts. **Reflection** runs weekly and makes **proposals**. An instance adds a stage, such as a monthly one, by copying a routine and changing its `schedule`.
- **The code step renders the frame and the routine body is the lens.** The code step supplies the batch, the rules and the write contract. The `ROUTINE.md` body says what this instance looks for and what it optimizes. Editing the lens cannot break the frame, the same split the recap has with `RECAP.md`.
- **`kinby init` writes both stages.** It writes `routines/consolidation/` and `routines/reflection/` with kinby's default lens. Their code steps are shared core code-step tools, `consolidate` and `reflect`. A factory's instance template may ship its own lens. Instances created before this get the files by hand or through the `write-routine` skill.
- **Consolidation.** Its code step first prunes every episode that is older than three months, **covered**, and cited by no live fact. It then takes up to 50 uncovered episodes, oldest first, and returns `None` when there are none. It groups the batch by subject and puts the live and tombstoned facts on those subjects in the frame. An area is a subject; an area fact needs no field of its own. The model returns the whole consolidation in one call to a core `consolidate` tool, offered only on stage turns. The tool refuses a source outside the batch, and it refuses a fact that cites the sources of a tombstoned fact unless it also cites an episode recorded after the forget.
- **Coverage is a set of episode ids on the event log.** When the `consolidate` call succeeds, the turn appends a `memory.consolidated` event listing the episodes it covered. An episode is uncovered while no such event lists it. A failed run appends nothing, so its batch stays eligible. A set, not a date cursor, because the startup recap sweep can write an episode with an older date after a consolidation ran.
- **Reflection.** It reads the facts written since the last successful reflection, the episodes behind them, and its earlier dream reports, so it does not repeat a proposal the user declined. Its code step returns `None` when consolidation wrote no fact since then. A proposal is a fact, a skill, or a routine without a code step. Reflection applies proposals itself through `remember`, `skill_write` and `routine_write`, and its routine runs at `mode: ask`, so each write waits on the user's approval.
- **The stage's thread is the dream report.** The turn's final message lists the facts written, with their sources, and the proposals. The user acts on it by replying in that thread.
- **A stage turn writes no episode.** Its recap writes the `memory.recapped` marker with no node, so dreaming never consolidates its own runs.
- **Dreaming never edits the profile and never writes code.** The profile stays the user's. Hooks, tools and code steps stay out of proposals, because a tool declares its own write flag ([ADR 0028](0028-instance-tools-are-gated-core-write-tools-over-whole-files.md)).
- **Budgets.** The batch holds at most 50 episodes. The shipped `tokens` and `steps` limits come from a measured first run.
- **Evaluation.** A new `evals/dreaming` runs fixture graphs that span several days. Consolidation cases must pass: a repeated lesson becomes a fact a later question recalls, a later episode supersedes an area fact, a forgotten fact stays forgotten, and nothing is written outside the batch. Reflection cases are report-only until they are stable. The memory eval reruns on a consolidated graph and must still pass the feed gate.

## Considered options

- **One nightly routine.** Rejected. A workflow pattern shows across a week, not a day.
- **Stages configured in `kinby.toml`, with no routine files.** Rejected. The routine file is already the source of truth for cadence, mode and budgets.
- **The whole prompt in `ROUTINE.md`.** Rejected. A lens edit could drop the citation rules or the write contract.
- **A runtime-owned consolidator like `RecapWriter`.** Rejected. A turn that wrote facts would record as no work, and dreaming would lose the gate, budgets and catch-up a routine has.
- **Citations through `remember`.** Rejected for consolidation. Separate calls can stop halfway, and coverage would advance past writes that never happened. One `consolidate` call is all or nothing.
- **A `covered` key in episode frontmatter.** Rejected. It rewrites episodes, which ADR 0074 forbids, and makes the graph the authority over what the event log has been distilled into, which [ADR 0017](0017-recap-runs-after-the-turn-as-an-event-marked-reactor.md) rejected for the recap.
- **Deleting duplicate episodes nightly.** Rejected. ADR 0074 keeps the evidence. A fact that cites the duplicates takes their place in recall.
- **A dream report as a file in the instance.** Rejected. The thread already holds it, every client shows it, and a reply there is how the user acts on a proposal.
- **Profile edits by a gated verb, or as proposals.** Rejected. The profile is the one memory always in context, and the user alone edits it.

## Consequences

- Facts gain an optional `sources` list in their frontmatter.
- With consolidation disabled, nothing is covered and nothing is pruned.
- A reflection waiting on an approval holds only its own thread. Consolidation keeps running.
- Notifying the user of a dream report, a manual recap sweep, and agent-written code are outside this decision.
