# A factory run is recapped once, from a digest of its coding sessions

Amends [ADR 0017](0017-recap-runs-after-the-turn-as-an-event-marked-reactor.md).

Ticket [#591](https://github.com/jorgesolerrr/kinby/issues/591): a `client` **step** records only `run.delegated`. It belongs to no thread or turn, so the **recap** never sees it, and nothing the coder learns implementing an issue or answering a review reaches its memory. Memory in kinby is the claim that an agent improves with use: better results, and fewer tokens, because what a session found on the way need not be found again. For a coder, what it finds is about the code: which files make up a feature, what was decided and why, what review pushed back on, where it lost time.

## Decision

- **One episode per factory run.** When a **factory run** ends (merged, closed, needs human, or failed for good), the recap writes one **episode** into the memory of the **instance** that owns the run. A run's steps write none of their own.
- **The recap reads the run's coding sessions, through a session digest.** Code first reduces each Claude session the run used to a **session digest**: the files read and the files changed, from the tool calls; the commands that failed, with a short tail of their output; and the client's own prose, where its reasoning and decisions are. Tool outputs and file contents are dropped. The model distills the episode from the digests plus the run record: the work item, step attempts and their endings, every send-back with its cause, the review comments and the replies given.
- **The episode carries code knowledge.** "What happened" is the issue and its outcome; "Path taken" lists the files read and changed; "What was decided" lists each decision with its reason; "What should have gone differently" holds review pushback, check failures and dead ends. Its subjects name the feature or area and its main paths, so a search on either finds it.
- **The factory file never asks for it.** A run carries its own recap marker, as a turn carries `memory.recapped`, and the startup catch-up recaps ended runs without one.

## Considered options

- **The coding client writes its own episode.** Rejected. It has no kinby tools, and its own account of itself is what the memory sample showed to be unreliable ("checks passed" taken on trust in 34 of 60 recaps).
- **A hook writes it.** Rejected. A hook is deterministic and records values from the world (ADR 0076). It can template an episode, not distill one.
- **An `agent` step at the end of each factory.** Rejected. Every factory would have to include it, a run that fails never reaches it, and the episode would describe the recap turn, not the work.
- **One episode per step attempt.** Rejected. One issue would write three to eight episodes that mostly say the hook passed, and the story of the issue would be split across them.
- **The run record alone.** Rejected. It has endings and send-backs, but nothing about the code.
- **The raw transcript.** Rejected. A long session would cost more to recap than its own work saved.

## Consequences

- Codex sessions get no digest yet. A run that used only Codex is recapped from its run record.
- Whether the memory helps is measured on real runs: tokens per run and send-backs, on areas with memory against areas without.
- The coding client gets `kinby memory search` through its shell, read-only, with the same results as `memory_search`. Facts are not put in its prompt yet.
