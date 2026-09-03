# Memory feed gate

Status: Failed. The graph arm passed every must-pass correctness check but exceeded the
memory-token threshold.

Run both arms in one Inspect log from the merged default branch:

```bash
uv run --env-file .env --group evals inspect eval evals/memory --model openai/gpt-5
```

Each outcome lists the model-graded fact, memory behavior, and estimated memory tokens. A
stuffing-arm behavior score is incorrect when the case requires a graph node because the
stuffing arm has no graph.

| Case | Gate role | Graph fact / behavior / tokens | Stuffing fact / behavior / tokens |
|---|---|---:|---:|
| `temporal-day` | Must pass | C / C / 148.75 | C / I / 1,031.00 |
| `temporal-last-touch` | Must pass | C / C / 191.25 | C / I / 751.00 |
| `temporal-current-decision` | Must pass | C / C / 162.50 | C / I / 905.00 |
| `remember-recall` | Must pass | C / C / 157.50 | C / C / 496.25 |
| `forget-stays-forgotten` | Must pass | C / C / 80.25 | C / I / 666.25 |
| `changed-between-dates` | Report only | C / C / 265.00 | C / I / 630.00 |
| `evolved-project` | Report only | C / C / 474.00 | C / I / 733.75 |

The light graph passes only when both conditions hold:

- Must-pass correctness on the graph arm is 100%.
- Mean memory tokens on the graph arm are at most 25% of the stuffing arm.

Run date: 2026-09-03

Active model: `openai/gpt-5`

Judge model: `openai/gpt-5-mini`

Must-pass graph correctness: 100% (5/5 model-graded facts and 5/5 behavior checks)

Mean memory-token ratio: 28.37% (211.32 graph / 744.75 stuffing)

Verdict: Fail. The memory-token ratio exceeds the 25% threshold.

Inspect log: `logs/2026-09-03T22-39-01-00-00_memory_ktyeVcZreaeDZ4HpQQGobW.eval`
