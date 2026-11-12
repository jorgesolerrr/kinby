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

Status: Not run yet.

A lesson case gives the agent ordinary work while a fact in its memory warns against a mistake that
work invites. The `lessons` task runs every case under one arm, each a way memory reaches the
agent:

- `pull`, the default: the instance prompt gets a line telling the agent to search memory for the
  task before it starts.
- `index`: the system prompt lists the newest facts' ids and descriptions, up to about 1k tokens,
  and tells the agent to open a fact before acting on it.
- `pre-search`: the runtime searches memory with the work's text and puts the top 3 fact bodies in
  the message, recording their recall.

Each case runs three times, with its memory filled to 10, 100 and 500 facts. The extra facts are
the same unrelated errands on every run.

Run every case under one arm on both models in one command from the merged default branch, once
per arm:

```bash
uv run --env-file .env --group evals inspect eval evals/memory/task.py@lessons \
  --model anthropic/claude-opus-5-5,anthropic/claude-sonnet-5-5 -T arm=pull
```

Each sample scores two things. `model_graded_qa` is whether the work followed the lesson: the
judge model compares the answer with the case's `followed` and `mistake`, and an answer that makes
the mistake is graded I. `memory_tokens` is the turn's memory tokens, with the index or the
pre-searched facts counted at four characters a token. Both report their mean per fact count, so
the `model_graded_qa` mean is the lesson-following rate.

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
holds the lesson. The task adds the unrelated facts and the pull arm's line itself.

When a turn or factory run misses a lesson it had in memory, rewrite the lesson as the fixture's
fact and the work as the case's `work`.

## First run

Record the run date, the active and judge models, the following rate and mean memory tokens per
arm, model and fact count, and the Inspect logs here.
