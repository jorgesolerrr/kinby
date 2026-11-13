# Memory feed gate

Status: Passed.

Run both arms in one Inspect log from the merged default branch:

```bash
uv run --env-file .env --group evals inspect eval evals/memory/task.py@memory --model openai/gpt-5
```

Each outcome lists the model-graded fact, memory behavior, and estimated memory tokens. A
stuffing-arm behavior score is incorrect when the case requires a graph node because the
stuffing arm has no graph.

| Case | Gate role | Graph fact / behavior / tokens | Stuffing fact / behavior / tokens |
|---|---|---:|---:|
| `temporal-day` | Must pass | C / C / 97.25 | C / I / 1,030.50 |
| `temporal-last-touch` | Must pass | C / C / 141.50 | C / I / 751.00 |
| `temporal-current-decision` | Must pass | C / C / 110.00 | C / I / 875.00 |
| `remember-recall` | Must pass | C / C / 108.50 | C / C / 591.25 |
| `forget-stays-forgotten` | Must pass | C / C / 80.25 | C / I / 666.25 |
| `changed-between-dates` | Report only | C / C / 169.75 | C / I / 630.00 |
| `evolved-project` | Report only | C / C / 304.50 | C / I / 733.75 |

The light graph passes only when both conditions hold:

- Must-pass correctness on the graph arm is 100%.
- Mean memory tokens on the graph arm are at most 25% of the stuffing arm.

Run date: 2026-09-03

Active model: `openai/gpt-5`

Judge model: `openai/gpt-5-mini`

Must-pass graph correctness: 100% (5/5 model-graded facts and 5/5 behavior checks)

Mean memory-token ratio: 19.17% (144.54 graph / 753.96 stuffing)

Verdict: Pass.

Inspect log: `logs/2026-09-03T22-47-32-00-00_memory_T9HLjtuttuTEnmEKt6DP27.eval`

# Lesson cases

Status: Run on 2026-11-12, with the control on 2026-11-13. Pull ships alone: no arm beats it by
10 points at any fact count on either model. Without the lesson, both models follow it about half
the time.

A lesson case gives the agent ordinary work while a fact in its memory warns against a mistake that
work invites. The `lessons` task runs every case under one arm, each a way memory reaches the
agent:

- `pull`, the default: the instance prompt gets a line telling the agent to search memory for the
  task before it starts.
- `index`: the system prompt lists the newest facts' ids and descriptions, up to about 1k tokens,
  and tells the agent to open a fact before acting on it.
- `pre-search`: the runtime searches memory with the work's text and puts the top 3 fact bodies in
  the message, recording their recall.
- `embedded-pre-search`: a pre-search that ranks facts by how close their embeddings lie to the
  work's, with the model the fixture's `models.embed` names. The eval embeds the facts, never the
  episodes, holds the vectors in memory for the run, and writes none to the instance. Without
  `models.embed`, the task refuses to start this arm and says so; the other arms do not read it.
- `control`: the lesson is removed from memory and nothing is delivered. The unrelated facts stay,
  so memory holds one fact fewer than the other arms. Its lesson-following rate is the base rate:
  how often the model avoids the mistake with no lesson to recall.

Each case runs three times, with its memory filled to 10, 100 and 500 facts. The extra facts are
the same unrelated errands on every run. Every turn runs in read-only mode. Nobody answers an
approval in the eval, so the gate denies a write such as `remember` instead of leaving the turn
waiting.

Run every case under one arm on both models in one command from the merged default branch, once
per arm:

```bash
uv run --env-file .env --group evals inspect eval evals/memory/task.py@lessons \
  --model anthropic/claude-opus-5-5,anthropic/claude-sonnet-5-5 -T arm=pull
```

Each sample scores three things. `model_graded_qa` is whether the work followed the lesson: the
judge model compares the answer with the case's `followed` and `mistake`, and an answer that makes
the mistake is graded I. `lesson_recalled` is whether the turn recalled the lesson: the agent
opened it with `memory_open`, or a pre-search put its body in the message. `memory_tokens` is the
turn's memory tokens, with the index or the pre-searched facts counted at four characters a token.
All three report their mean per fact count, so the `model_graded_qa` mean is the lesson-following
rate and the `lesson_recalled` mean is the recall rate.

Together the two grades split the samples four ways: recalled and followed, recalled and missed,
not recalled and followed, not recalled and missed. A sample that follows the lesson without
recalling it got there by common sense, and the control arm measures how often that happens.

`lesson_recalled` counts the lesson as recalled only when the agent read its body. A search hit
shows only its description, and the index only lists it, so neither counts. This is narrower than
the runtime's `memory.recalled` event. `memory_search` writes one for every hit, and #1260 counts a
search as a recall. In the first run the difference never showed, as every turn whose search found
the lesson also opened it.

| Case | The work | The lesson |
|---|---|---|
| `label-race` | Create a label before the assess step adds it | Review rounds repeat misses, such as a check-then-create race and `gh label create --force` |
| `known-ci-flake` | Get a pull request's failed checks green | A known flaky test is rerun, never fixed on the branch |
| `mapped-column-own-pr` | Plan a reminder job that needs a new column | A mapped column ships in its own PR |
| `release-branch-from-main` | Cut the 2.5 release branch | Release branches are cut from main, never from develop |
| `staging-key-from-vault` | Deploy a site to staging | Load STAGING_KEY from the vault first |
| `invoicing-maintenance-window` | Schedule the invoice export for the second Tuesday | The invoicing API is down for maintenance then |

## Adding a lesson case

A lesson case is a directory under `lessons/` with a `case.toml` and an `instance/` fixture.
`case.toml` holds the case's name, the `work` the agent is asked for, what the work does when it
`followed` the lesson, and the `mistake` the lesson warns against. The fixture's `memory/graph/`
holds the lesson, and its `kinby.toml` names the embedding model in `models.embed`. The task adds
the unrelated facts, the read-only mode and the pull arm's line itself.

When a turn or factory run misses a lesson it had in memory, rewrite the lesson as the fixture's
fact and the work as the case's `work`. Run the control on the new case too: a case the control
follows most of the time tests common sense, not memory.

## First run

Run date: 2026-11-12. The control ran on 2026-11-13.

Active models: `anthropic/claude-opus-5-5`, `anthropic/claude-sonnet-5-5`

Judge model: `openai/gpt-5-mini`. Embedding model: `openai:text-embedding-3-small`.

Every arm ran twice on both models, so each cell holds 12 samples: 6 cases, two runs. The rate is
the lesson-following rate over both runs. One sample moves a cell by about 8 points. The memory
tokens come from the second run only: the first run counted none of an Anthropic turn's tool results,
because the eval passed Anthropic's uncached input as the whole input.

| Arm | Model | 10 facts | 100 facts | 500 facts |
|---|---|---:|---:|---:|
| `pull` | Opus 5.5 | 100% / 377 | 100% / 603 | 100% / 901 |
| `pull` | Sonnet 5.5 | 83% / 499 | 92% / 538 | 100% / 678 |
| `index` | Opus 5.5 | 100% / 598 | 83% / 1,261 | 100% / 1,484 |
| `index` | Sonnet 5.5 | 92% / 574 | 83% / 1,245 | 75% / 1,259 |
| `pre-search` | Opus 5.5 | 100% / 296 | 100% / 331 | 100% / 326 |
| `pre-search` | Sonnet 5.5 | 83% / 289 | 92% / 297 | 92% / 292 |
| `embedded-pre-search` | Opus 5.5 | 92% / 278 | 100% / 296 | 100% / 279 |
| `embedded-pre-search` | Sonnet 5.5 | 92% / 260 | 92% / 262 | 92% / 262 |
| `control` | Opus 5.5 | 50% / 70 | 50% / 138 | 42% / 288 |
| `control` | Sonnet 5.5 | 42% / 53 | 50% / 58 | 50% / 578 |

Each cell is the lesson-following rate / mean memory tokens.

The control is the base rate. With no lesson in memory, both models follow it 17 times in 36. That
average hides two kinds of case.

| Case | Opus 5.5 control | Sonnet 5.5 control |
|---|---:|---:|
| `label-race` | 6 / 6 | 5 / 6 |
| `mapped-column-own-pr` | 6 / 6 | 6 / 6 |
| `known-ci-flake` | 4 / 6 | 6 / 6 |
| `staging-key-from-vault` | 1 / 6 | 0 / 6 |
| `release-branch-from-main` | 0 / 6 | 0 / 6 |
| `invoicing-maintenance-window` | 0 / 6 | 0 / 6 |

Both models avoid the first three cases' mistakes without memory: a check-then-create race, a
column shipped with the code that uses it, and a fix for a test the work says is flaky. Those cases
test common sense, so they cannot tell the arms apart. The last three hinge on a fact only the
lesson holds, and without it the models miss them 35 times in 36. On those three cases, over both
runs and every fact count:

| Arm | Opus 5.5 | Sonnet 5.5 |
|---|---:|---:|
| `pull` | 18 / 18 | 16 / 18 |
| `index` | 16 / 18 | 12 / 18 |
| `pre-search` | 18 / 18 | 16 / 18 |
| `embedded-pre-search` | 17 / 18 | 17 / 18 |
| `control` | 1 / 18 | 0 / 18 |

The recall split comes from the same logs. A script read it from each sample's messages, since the
first run predates the `lesson_recalled` scorer. Each cell is recalled and followed / recalled and
missed / not recalled and followed / not recalled and missed:

| Arm | Model | 10 facts | 100 facts | 500 facts | Followed when recalled |
|---|---|---|---|---|---:|
| `pull` | Opus 5.5 | 12 / 0 / 0 / 0 | 12 / 0 / 0 / 0 | 12 / 0 / 0 / 0 | 36 / 36 |
| `pull` | Sonnet 5.5 | 10 / 2 / 0 / 0 | 11 / 1 / 0 / 0 | 12 / 0 / 0 / 0 | 33 / 36 |
| `index` | Opus 5.5 | 12 / 0 / 0 / 0 | 10 / 2 / 0 / 0 | 12 / 0 / 0 / 0 | 34 / 36 |
| `index` | Sonnet 5.5 | 11 / 1 / 0 / 0 | 10 / 2 / 0 / 0 | 9 / 1 / 0 / 2 | 30 / 34 |
| `pre-search` | Opus 5.5 | 12 / 0 / 0 / 0 | 12 / 0 / 0 / 0 | 12 / 0 / 0 / 0 | 36 / 36 |
| `pre-search` | Sonnet 5.5 | 10 / 2 / 0 / 0 | 11 / 1 / 0 / 0 | 10 / 1 / 1 / 0 | 31 / 35 |
| `embedded-pre-search` | Opus 5.5 | 11 / 1 / 0 / 0 | 12 / 0 / 0 / 0 | 12 / 0 / 0 / 0 | 35 / 36 |
| `embedded-pre-search` | Sonnet 5.5 | 11 / 1 / 0 / 0 | 11 / 1 / 0 / 0 | 11 / 1 / 0 / 0 | 33 / 36 |
| `control` | Opus 5.5 | 0 / 0 / 6 / 6 | 0 / 0 / 6 / 6 | 0 / 0 / 5 / 7 | - |
| `control` | Sonnet 5.5 | 0 / 0 / 5 / 7 | 0 / 0 / 6 / 6 | 0 / 0 / 6 / 6 | - |

Every arm recalls the lesson in almost every sample. Pull's agent opened it in all 72 of its
samples. Recall failed three times, all on Sonnet 5.5 at 500 facts. Twice under `index`, on
`invoicing-maintenance-window`: the lesson had fallen out of the 1k-token index and the agent did
not search, and both turns made the mistake. Once under `pre-search`, on `mapped-column-own-pr`:
the keyword search ranked three errands above the lesson, and the agent followed it anyway, as the
control does. Every other miss is a recalled lesson the agent read and then broke, which the
learning review calls an ignored lesson. Better delivery cannot fix those misses.

Verdict: pull ships alone, unchanged by the control. On Opus 5.5, pull follows every lesson at
every fact count, and no arm beats it. On Sonnet 5.5, the largest lead over pull is 9 points, by
`index` and `embedded-pre-search` at 10 facts, which is under the 10-point margin. On the three
cases memory decides, the largest lead is 1 sample in 18, by `embedded-pre-search` on Sonnet 5.5.
No result splits by model or fact count. Each run alone shows a split in one Sonnet cell, a
different cell each time. Pooled, the split goes away.

The control shows that memory itself matters. Pooled over fact counts, every arm follows the
lessons 36 to 53 points more often than the control. On the three cases memory decides, the
models go from almost never following the lesson to almost always.

Pull costs the most tokens of the three cheap arms as memory grows: its searches return more hits
at 500 facts, about 900 tokens on Opus against about 300 for either pre-search. The win rule gives
tokens weight only on a tie, so this does not change the verdict. `index` costs the most and
follows the fewest lessons on Sonnet 5.5.

Misses by case, over both runs: `release-branch-from-main` misses under every arm, and is the only
case Opus 5.5 ever misses. On Sonnet 5.5, `invoicing-maintenance-window` misses under every arm but
pull, `known-ci-flake` under pull and both pre-searches, and `staging-key-from-vault` once each under
pull and `index`.

Inspect logs:

| Arm | First run | Second run |
|---|---|---|
| `pull` | `logs/2026-11-12T23-16-26-00-00_lessons_EdyueUNdxmGWCPK8LxEPff.eval`, `logs/2026-11-12T23-16-26-00-00_lessons_Yg5AsjmRVQ8nGJpabLAF4m.eval` | `logs/2026-11-12T23-32-34-00-00_lessons_B5ztVb5McyhhvGT753J79A.eval`, `logs/2026-11-12T23-32-34-00-00_lessons_WudJTkEpAp6uSChMLGRWDv.eval` |
| `index` | `logs/2026-11-12T23-16-27-00-00_lessons_3479LUTWsSkGyJXyn7pSaK.eval`, `logs/2026-11-12T23-16-27-00-00_lessons_ixBZs9VFmZkCp35ScH6psH.eval` | `logs/2026-11-12T23-32-35-00-00_lessons_FFFP6nuv5aprJHp26QQteN.eval`, `logs/2026-11-12T23-32-35-00-00_lessons_bYAZy5Bi2DxF6qPnzs4ikx.eval` |
| `pre-search` | `logs/2026-11-12T23-16-27-00-00_lessons_AwRE6zMzc2it6LcjVVxzDQ.eval`, `logs/2026-11-12T23-16-27-00-00_lessons_35P2CD2zUk6QXGpJ3aDuYJ.eval` | `logs/2026-11-12T23-32-34-00-00_lessons_AjfHamhXAYvKxkYb5Y4yZ7.eval`, `logs/2026-11-12T23-32-34-00-00_lessons_7yG7fPdYHTQciU66c55biL.eval` |
| `embedded-pre-search` | `logs/2026-11-12T23-16-25-00-00_lessons_MmjzrHsAsYmbsVzMjJtZnL.eval`, `logs/2026-11-12T23-16-25-00-00_lessons_GqZXch7zGm7XvRJzFVVbgz.eval` | `logs/2026-11-12T23-32-34-00-00_lessons_R6vC2kZ5PVfma6Pwwp9fgx.eval`, `logs/2026-11-12T23-32-34-00-00_lessons_R69w6sZcVQzwvd8EReZhhS.eval` |
| `control` | `logs/2026-11-13T00-34-07-00-00_lessons_Zde446Zrmm2ELgPRJNvPLv.eval`, `logs/2026-11-13T00-34-08-00-00_lessons_AwNjhHe7xGqRhZw4MUrnsj.eval` | `logs/2026-11-13T00-34-10-00-00_lessons_JLT7RjV7CzputNx3yJxAkm.eval`, `logs/2026-11-13T00-34-10-00-00_lessons_bDaVYwPUXxz7RqG9rEoNjn.eval` |

The runs used this branch's adapter, which replays each assistant turn with its thinking and counts
cached input, on a Linux container: the eval imports `fcntl`, so it does not run on Windows.

Only the control ran in read-only mode. Its first attempt hung on an Opus 5.5 turn that called
`remember` and waited for an approval, so the eval now denies writes. The other arms ran before
that change and never called a write tool, or they would have hung the same way, so read-only mode
would not have changed them. In the control's two runs, one `remember` call was denied.
