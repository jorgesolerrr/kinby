# Consolidation writes unasked, from two episodes, and a fact is found by its sources

Amends [ADR 0074](0074-recall-returns-facts-and-episodes-are-raw-material.md) and [ADR 0099](0099-dreaming-runs-in-stages-as-routines-whose-body-is-the-lens.md).

On 2026-10-18 the dreaming stages ran on copies of the five live instances. Three things failed:

- Every instance runs `mode = "ask"`, and the shipped consolidation routine sets no mode, so its one `consolidate` call waited for approval every night. Pruning runs only when the stage's writes need no approval, so it never ran either.
- On the two coders with one episode each, consolidation wrote a fact that restated that episode. The fact then took the episode's place in search.
- On home, 17 episodes became 6 facts. Asked which instances are e2e test instances and what state they were left in, the agent searched "e2e". Two other facts matched it, so the search returned facts only. The fact that held the answer said "test instances", not "e2e", and its source episode, which said "e2e", was hidden behind the facts. The answer was wrong. The same question on the raw graph was answered right.

## Decision

- **Consolidation's own write needs no approval.** The gate allows the `consolidate` tool in every mode but read-only, ahead of any other rule, as it asks for a hub write ahead of any rule ([ADR 0096](0096-the-home-instance-asks-the-hub-and-the-hub-checks-the-approval.md)). The tool writes only facts that cite the frame's episodes and refuses one that writes back a forgotten fact. The stage's other writes keep the routine's mode. Pruning follows, because it reads the same decision. Approving what dreaming changes stays with reflection's proposals.
- **A fact cites at least two episodes.** `consolidate` refuses a fact with fewer, and the frame's rules say so. An area fact that takes an older one's place cites the old sources and the new episode, so it passes.
- **The frame shows earlier episodes.** Next to the batch, the frame lists the covered episodes on the batch's subjects that no live fact cites and that are younger than the prune age, newest first, at most 50. A fact may cite them. A lesson or an area that shows up on two nights can then become a fact, which [ADR 0098](0098-dreaming-also-consolidates-episodes-about-one-area-into-an-area-fact.md) needs on an instance that records one episode a night.
- **A fact matches a search on its source episodes.** A search term matches a fact when it is in the fact's description or subjects, or in those of an episode it cites. A fact takes its sources' place in recall, so it keeps their words. Facts still come first, and episodes are still returned only when the search is bounded by date or no fact matches.

## Considered options

- **Ship `mode: full-access` on the consolidation routine.** Rejected. The whole stage turn would run unasked, bash included, while the model reads episodes that can hold content from outside.
- **Ship `consolidate = "allow"` in the starter `permissions.toml`.** Rejected. Every existing instance would need its file edited. A tool override cannot target the write alone either: the code step and the model's write are both named `consolidate`, so `consolidate = "ask"` would stop the code step and fail the stage.
- **Enforce two episodes only in the frame's wording.** Rejected. The real run ignored the rule it already had.
- **Skip a one-episode batch in the code step.** Not taken. The tool's refusal is enough, and earlier episodes give a lone episode a later chance.
- **Return episodes no matched fact cites.** Rejected. It brings back the episode flood ADR 0074 removed.
- **Better subjects from the frame's rules.** Not taken. It depends on the model, and the source match keeps the episodes' words without it.

## Consequences

- No mode makes consolidation wait for an approval. To stop it, disable the routine or set it to `read-only`.
- A refused call costs the stage another model call to fix its facts.
- The frame grows by the earlier episodes on the batch's subjects.
