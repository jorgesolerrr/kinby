# Real memory sample: three coder instances on the playground box

Ticket #521, part of map #516. Sampled on 2026-10-03 from `memory/graph/` of three instances on the playground hub. Nothing on the box changed: the graph directories were copied with `tar` and analyzed locally, and `.state/events.jsonl` was read in place to attribute each episode's turn to its origin (`turn.started.origin`) and outcome (`turn.completed.outcome`).

| Instance | Role | Graph files | Turns logged |
|---|---|---|---|
| `cec-agent-coder` (009d97d0) | daily driver, live webhooks from `pb-cec/CEC-Agent` | 690 | 691 |
| Forge (1c50cdc0) | coder, schedule only | 318 | 320 |
| Anvil (6e46798a) | coder, schedule only, stopped 2026-09-30 | 140 | 140 |

All three run the same package: two routines, `implement-ready-issue` (hourly schedule plus a GitHub webhook signal) and `babysit-pull-request` (hourly schedule).

## Summary

- Memory is one node per turn. 1,148 nodes across the three instances, 1,087 of them (95%) are trace episodes from no-work turns.
- No facts exist anywhere. `memory/profile.md` is the untouched 165-byte template on every instance. No node has `source: user` or `tombstone: true`.
- Every node comes from a routine. The one chat turn across these instances (on Forge) wrote no node.
- Scheduled no-work traces are byte-identical apart from date, thread and turn: the description is the routine prompt and the body is `{"signal": {}}`.
- Signal no-work traces use the entire GitHub webhook JSON as the description: median 10 KB, max 45 KB. These descriptions match almost any search term.
- In every instance, an empty search and every common term return 20 trace episodes from the last one or two days. On the daily instance, `issue` matches all 690 nodes.

## Counts

### By kind

| Instance | Trace episodes | Recap episodes | Facts | Tombstones | Graph bytes | Bytes in traces |
|---|---|---|---|---|---|---|
| cec-agent-coder | 630 | 60 | 0 | 0 | 8.27 MB | 8.09 MB (98%) |
| Forge | 317 | 1 | 0 | 0 | 182 KB | 180 KB |
| Anvil | 140 | 0 | 0 | 0 | 79 KB | 79 KB |

### By origin (cec-agent-coder)

| Origin | Outcome | Turns | Nodes written |
|---|---|---|---|
| `implement-ready-issue` / signal | no-work | 404 | 404 traces |
| `implement-ready-issue` / signal | work | 57 | 58 recap episodes (one from the failed turn) |
| `implement-ready-issue` / signal | failed | 1 | counted above |
| `implement-ready-issue` / scheduled | no-work | 113 | 112 traces (one turn wrote nothing) |
| `implement-ready-issue` / scheduled | work | 2 | 2 recap episodes |
| `babysit-pull-request` / scheduled | no-work | 114 | 114 traces |
| chat | | 0 | 0 |

Forge: 159 babysit and 158 implement scheduled no-work traces, one recap episode from a manual `implement-ready-issue` run, and one chat (`user`) turn with outcome `work` that wrote no node. Anvil: 70 and 70 scheduled no-work traces, nothing else.

On cec-agent-coder every work turn produced a recap episode. The only work turn without a node is the Forge chat turn.

### By day (cec-agent-coder)

| Day | Traces | Recap episodes | Signal | Scheduled implement | Scheduled babysit |
|---|---|---|---|---|---|
| 2026-09-28 | 2 | 0 | 2 | 0 | 0 |
| 2026-09-29 | 218 | 30 | 202 | 23 | 23 |
| 2026-09-30 | 150 | 8 | 110 | 24 | 24 |
| 2026-10-01 | 89 | 10 | 51 | 24 | 24 |
| 2026-10-02 | 118 | 3 | 73 | 24 | 24 |
| 2026-10-03 (partial) | 53 | 9 | 24 | 19 | 19 |

The schedule adds a fixed 48 traces a day. Forge and Anvil show exactly that: 48 a day, every day.

## Clusters of near-identical episodes

### 1. Scheduled no-work traces: identical text

Across the three instances, 683 scheduled traces fall into two texts. They differ only in `date`, `thread`, `turn` and the id.

- `description` is the routine's prompt, collapsed to one line: 248 characters for implement, 427 for babysit. The trace path takes the description from `turn.started.message`, and on a scheduled turn that message is the routine prompt.
- `subjects` is `[]`.
- `tools` is `["implement_ready_issue"]` or `["babysit_pull_request"]`.
- The body is one line, `1. implement_ready_issue: {"signal": {}}`.

On cec-agent-coder these are 226 of the 690 nodes. On Forge they are 317 of 318, and on Anvil 140 of 140.

### 2. Signal no-work traces: same shape, huge descriptions

The 404 signal traces are webhook deliveries that the routine's code step turned away: an issue or PR event that was not a `ready-for-agent` label. Each description is the raw webhook body, so no two are byte-identical, but they share one shape:

| Event | Traces |
|---|---|
| issue closed | 62 |
| issue opened | 53 |
| pull_request closed / opened | 50 / 50 |
| issue labeled `ready-for-agent` | 39 |
| pull_request stacked / synchronize | 36 / 34 |
| issue edited | 26 |
| issue labeled `ready-for-human` | 14 |
| other (unlabeled, assigned, deleted, spec, needs-triage, ping, ...) | 40 |

They cover 130 distinct issues and PRs, up to 8 traces for one issue. After digits are normalized, the bodies fall into a handful of prefixes. `implement_ready_issue: {"signal": {"body": {"action": "labeled", ...` alone covers 64. Descriptions run from 0.25 KB to 45 KB, median 10 KB, and all have empty subjects.

Recall matches query terms against the description and subjects, so a 10 KB webhook body matches nearly any GitHub word: `issue`, `pull request`, `merge`, `comment`, `label`, `review`, `ci`.

### 3. Recap episodes: distinct facts, repeated templates

The 60 recap episodes on cec-agent-coder are the useful memory, and their descriptions are all distinct. They do repeat in two ways.

- **Template.** Most read "Implemented issue #N (title) via implement_ready_issue, opened PR #M, posted summary comment". Their subjects repeat the same anchors: `implement_ready_issue` in 46 of 60, `pb-cec/CEC-Agent` in 31, `CEC-Agent` in 19, `CEC-Agent repo` in 9. These are near-synonyms for one repo.
- **The same lesson, relearned.** 10 descriptions start with or contain "Mismatched": the webhook named one issue and the pipeline implemented another. 34 of the 60 retrospectives say the turn trusted the tool's self-reported `checks passed: true` without checking it independently. Nothing ever folded this lesson into a fact, so each turn writes it again.

Some issues get more than one episode: #59 appears in 4 descriptions, and #14, #17, #34, #36, #54, #55, #57, #58 and #60 in 2 each, usually a failed run followed by a retry, or a mismatch naming both issues.

## Facts and who wrote them

There are none. No instance has a fact node, `profile.md` is the stock template everywhere, and no node carries `source: user`. Nothing on these instances writes facts: the only writer is the turn recap, and it writes episodes. No `forget` has ever run either, since no file has `tombstone: true`.

## Does search return the newest-20 crowd?

Recall returns the newest 20 matches, sorted by `(date, id)`. Here is what common queries returned on cec-agent-coder:

| Query | Matches | Traces in top 20 | Distinct descriptions in top 20 | Date span of top 20 |
|---|---|---|---|---|
| (empty) | 690 | 20 | 8 | 10-03 |
| `issue` | 690 | 20 | 8 | 10-03 |
| `comment` | 667 | 20 | 8 | 10-03 |
| `pull request` | 632 | 20 | 8 | 10-03 |
| `merge` | 522 | 20 | 9 | 10-03 |
| `ready` | 458 | 20 | 8 | 10-03 |
| `routine` | 230 | 20 | 2 | 10-03 |
| `failed` | 216 | 20 | 5 | 10-03 |
| `babysit` | 114 | 20 | 1 | 10-02 to 10-03 |
| `label` | 411 | 20 | 20 | 10-02 to 10-03 |
| `review` | 194 | 20 | 20 | 10-02 to 10-03 |
| `implement` | 122 | 11 | 20 | 10-02 to 10-03 |

For 14 of the 15 terms tried, traces took 16 to 20 of the 20 slots. The exception was `implement`, at 11, because recap subjects carry `implement_ready_issue`. The top 20 never reached back more than two days. On Forge and Anvil every matching query returned 20 traces with one or two distinct descriptions, and `implement` on Forge was the only query that found the single recap episode.

So yes: on the daily instance, any common term returns the newest-20 crowd. The crowd is today's hourly schedule traces plus today's webhook bodies. A recap from 2026-09-29, such as the first "Mismatched" lesson, is unreachable without a date bound or a rare term.

Recall also parses every file on every query, so each search on the daily instance reads 8.3 MB, 98% of it trace text.

## Five examples of repetition

1. Scheduled implement trace, written 158 times on Forge and 112 times on cec-agent-coder:
   > description: "The routine data is a pipeline report. Comment a short summary on its issue, including the pull request URL on success or the failure reason on failure, then stop. ..."
   > `1. implement_ready_issue: {"signal": {}}`
2. Scheduled babysit trace, written 159 times on Forge, 114 on cec-agent-coder and 70 on Anvil:
   > description: "The routine data is a JSON list of babysit reports. Comment a one-line summary for each report on its pull request, starting with `Babysit report: `. ..."
3. Signal trace description, the first 120 characters of a 10 KB webhook body, in one of 62 "issue closed" traces:
   > `{"action":"closed","issue":{"url":"https://api.github.com/repos/pb-cec/CEC-Agent/issues/...","repository_url":...`
4. The same lesson in four recap episodes:
   > "Mismatched webhook handling: issue #59 labeled event triggered implementation and PR/comment for unrelated issue #65"
   > "Mismatched issue trigger: webhook for issue #93 (parse trial) instead implemented issue #96 ..."
5. The same retrospective in 34 of 60 recap episodes:
   > "The turn relied entirely on the implement_ready_issue tool's self-reported 'checks passed: true' with no independent verification ..."
   > "The turn only reports the implement_ready_issue tool's self-reported checks ("checks passed": true) and never independently re-ran ..."

## What this means for #516

- The duplication is almost all no-work traces. Removing or collapsing them on these instances would cut node count by 91% on cec-agent-coder and by over 99% on Forge and Anvil. They would also stop crowding search out.
- Using `turn.started.message` as the trace description causes both problems: identical prompts on scheduled turns, and multi-kilobyte webhook bodies on signal turns.
- Recap episodes are not duplicates of each other. They repeat a lesson that no fact ever captures, which is a separate problem from the trace flood.
- No facts and no tombstones exist, so this data cannot inform fact dedup or forgetting.
