# Workbench agent steps pull owner memory through the hub

Amends [ADR 0097](0097-a-factory-run-is-recapped-once-from-a-digest-of-its-coding-sessions.md), [ADR 0101](0101-the-hub-counts-memorys-cost-exactly-and-each-instance-reviews-its-own-learning.md), and [ADR 0125](0125-a-factory-runs-on-a-workbench-and-an-instance-owns-its-lines.md).

Ticket [#1528](https://github.com/jorgesolerrr/kinby/issues/1528): a memory review on the playground hub (2026-11-22) found that 0 of 90 factory runs had `recalled_fact` set, despite 892M tokens spent and 265 send-backs. Before the workbench conversion in ADR 0125 (~2026-11-15), coding sessions did call `kinby memory search` (50 sessions on `software-coder`, 33 on `cec-backend`, 6 on `landing-coder`). After ADR 0125, memory moved to the line owner marked with its workbench, workbenches kept zero graph files, coding-client steps switched to `remembers=False`, and kinby-engine workbench turns received no memory tools. Factory runs kept writing recaps into owner memory that no run could read back.

## Decision

- **Both agent-step engines pull owner memory through the hub.** A workbench keeps no memory of its own. Every workbench receives a read-only memory scope on its hub route using its existing **control token**. Every memory request carries the active `run_id` (plus `step` and `attempt`). The hub verifies that `run_id` belongs to a **line** on the calling workbench, resolves that line's owner and workbench name, and relays the read-only lookup to the running owner instance filtered to nodes marked with that workbench plus unmarked nodes.
- **Coding clients use the CLI; kinby turns use read-only memory tools.** `kinby memory search` and `kinby memory open` call the workbench's hub route when running inside a workbench run (`KINBY_HUB_URL` and `KINBY_RUN` present), and coding-client steps receive `_CLIENT_MEMORY` again. Kinby-engine workbench turns receive read-only `memory_search` and `memory_open` tools backed by the same hub methods, plus prompt guidance to search before acting.
- **Pull only, guided by factory prompts and skills.** No pre-search or memory index is injected into step messages yet. For the software factory, the hub holds only `{issue: int}` and branch names when `implement` starts, whereas the agent reads the full issue and code before searching; the lesson eval (`evals/memory/RESULTS.md`) already showed pull ties pre-search and beats an index. `implement-ticket` asks the agent to search memory after reading the issue and before coding, and `answer`, `fix`, and `review` add short nudges for recurring review or check lessons. No hook blocks a step that skips searching.
- **Relay through the running owner and fail soft when unavailable.** The hub never opens an owner's graph directory or sqlite database directly, preserving the single-process **runtime lock**. If the owner instance is stopped or unreachable, the search or open call returns a clear non-fatal message so the factory step can continue without memory.
- **Recalls are recorded on the owner for both search and open.** When the owner handles a relayed search or open, it records the recalled node ids in its `recalls` table under `run_id`, `step`, and `attempt` (and `kinby memory open` on an instance records recalls too). `run_tokens` in instance stats creates a run entry on recall so active or unrecapped runs do not drop `recalled_fact`. Pre-conversion `recalls` rows left in converted workbenches' old databases are not backfilled.

## Considered options

- **Internal MCP server for coding clients.** Rejected. The CLI already exists, works identically for Claude Code and Codex, and adds no MCP server lifecycle or per-client config to workbenches.
- **Push memory into step messages (pre-search or lessons skill).** Rejected for now. For software `implement`, the hub lacks the issue body and codebase context needed for good queries, and the lesson eval showed pull matches pre-search once the agent searches. Push stays an option later if measured recall rates are still too low.
- **Restrict workbench recall to workbench-marked nodes only.** Rejected. Keeping `workbench + unmarked` matches `CONTEXT.md` and lets a line like `reading-brief` read its owner `Reading`'s unmarked preferences without adding a per-line toggle.
- **Hub reads the owner's graph files on disk when the owner is stopped.** Rejected. Bypasses the owner's process boundary and complicates recall recording against sqlite locks.
- **Block `implement` with a hook when no memory search ran.** Rejected. Forces empty or noisy queries on greenfield or trivial tickets.

## Consequences

- Every workbench container now receives `KINBY_HUB_URL` and `KINBY_HUB_SCOPES` with read-only memory scopes (existing workbenches pick them up on recreation or update).
- Because a coding client inherits the workbench's environment, its shell holds the workbench's control token; the hub's scope check restricts that token to read-only memory lookups for runs owned by that workbench (plus browser methods if the workbench has a browser).
- A run whose owner is stopped runs without memory and logs no recalls until the owner starts again.
- `stats.runs` and `learning_review` on the line owner now count factory run recalls directly without querying workbenches.
