# Consolidation supersedes facts, frames by workbench, and defaults to the recap model

Amends [ADR 0098](0098-dreaming-also-consolidates-episodes-about-one-area-into-an-area-fact.md), [ADR 0099](0099-dreaming-runs-in-stages-as-routines-whose-body-is-the-lens.md), and [ADR 0100](0100-consolidation-writes-unasked-from-two-episodes-and-a-fact-is-found-by-its-sources.md).

Ticket [#1528](https://github.com/jorgesolerrr/kinby/issues/1528): inspecting the playground hub on 2026-11-26 showed why dreaming accounted for 40% of hub API spend ($22.66) and 97% of `CEC management`'s spend ($10.59 of $10.92):

- Updating an area fact or lesson left the replaced fact live forever. Of `CEC management`'s 124 live facts, 24 were strictly superseded by a newer live fact whose `sources` were a superset, and 43 more shared two or more sources.
- Consolidation's frame loaded up to 50 full-body batch episodes, up to 50 full-body earlier episodes, and every live fact sharing any subject across all workbenches. Recent `CEC management` runs sent 312 to 493 KB frames (150k to 434k input tokens) to `claude-opus-5-5`, costing $0.80 to $1.57 per night.
- Fact bodies grew into 1.3 to 3.7 KB historical narratives (up to 10.2 KB on `home`) instead of short actionable lessons.

## Decision

- **Consolidation retires replaced facts via append-only `supersedes`.** `consolidate` accepts an optional `supersedes` list of live fact node ids per written fact, validated against the frame's live facts. The ids are stored in the new fact's frontmatter; old fact files are never edited in place or marked with a user `tombstone` (ADR 0074). `GraphStore` treats any fact named in `supersedes` of any fact (live or tombstoned) as superseded: hidden from `recall()`, `live_facts()`, and future consolidation frames, and no longer protecting its source episodes from 90-day pruning. Forgetting the replacement leaves the superseded fact hidden instead of resurrecting stale knowledge. Existing duplicate facts are superseded explicitly by consolidation rather than by an implicit read-time heuristic.
- **Consolidation frames within each workbench and trims payload size.** `BATCH_LIMIT` drops from 50 to 20. Each batch episode matches earlier episodes and live facts only within its own `workbench` (unmarked with unmarked, `wb` with `wb`), keeping workbench marks intact and preventing one repository from pulling another repository's or the owner's personal facts into its frame. Superseded facts are excluded. Framed live facts are capped (top 25 by subject overlap, newest first), and framed earlier episodes are capped (top 20 by subject overlap) and sent as metadata (node, date, description, subjects) without full bodies.
- **Facts are written to be scanned and acted on.** Consolidation's frame rules and shipped default lens instruct the model to write short, actionable facts (where an area lives, current decisions, and gotchas to follow rather than run-by-run history) and to supersede or merge overlapping facts rather than add parallel copies.
- **Routines can name a model, and consolidation defaults to `models.recap`.** `ROUTINE.md` gains an optional `model` field validated as a **model choice** against the instance's price map. When omitted, ordinary routines keep `models.main`, while consolidation defaults to `models.recap`. Consolidation keeps its nightly schedule.

## Considered options

- **Prune (delete) superseded fact files immediately.** Rejected. Keeping superseded files immutable on disk preserves the append-only graph invariant (`CONTEXT.md`) and leaves audit history intact if a bad consolidation needs inspection.
- **Hide superseded facts at read time by `sources` subset matching.** Rejected. Fails to clean up partially overlapping duplicate facts (43 of 124 on `CEC management`) and hides nodes implicitly instead of recording the replacement in frontmatter.
- **Keep cross-workbench framing during consolidation.** Rejected. A single shared subject like a repo or workflow term pulled unrelated workbenches and personal ops facts into 400 KB frames and stripped `workbench` marks when sources mixed. Cross-repo patterns belong to reflection or the owner's own turns.
- **Cut consolidation cadence to weekly or gate it on a minimum episode count.** Rejected for now. Fixing supersession, frame size, and model selection addresses the actual cost drivers while keeping nightly memory fresh.
- **Separate factory-lesson fact kind.** Rejected. Concise, deduplicated facts help chat turns as much as factory runs without splitting the `Fact` concept.

## Consequences

- Facts gain an optional `supersedes` list in their frontmatter.
- Forgetting a consolidated fact never brings back the older facts it replaced; a later episode after the forget is still required to relearn the lesson.
- Consolidation will not synthesize a single unmarked fact directly from episodes of two different workbenches.
- Setting a cheaper `models.recap` in `kinby.toml` moves recaps, thread titles, and consolidation off `models.main` together; any routine can still pin its own `model`.
- Existing instances with customized `consolidation/ROUTINE.md` (such as `home` on the playground hub) still inherit the frame rules once they use `run: consolidate`.
