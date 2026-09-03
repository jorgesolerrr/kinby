# Memory feed gate

Status: Pending a local run after this pull request lands.

Run both arms in one Inspect log from the merged default branch:

```bash
uv run --group evals inspect eval evals/memory --model provider/model
```

Record the run date and the active and judge models here. Then replace each pending outcome with the scores from the Inspect log.

| Case | Gate role | Graph | Stuffing |
|---|---|---:|---:|
| `temporal-day` | Must pass | Pending | Pending |
| `temporal-last-touch` | Must pass | Pending | Pending |
| `temporal-current-decision` | Must pass | Pending | Pending |
| `remember-recall` | Must pass | Pending | Pending |
| `forget-stays-forgotten` | Must pass | Pending | Pending |
| `changed-between-dates` | Report only | Pending | Pending |
| `evolved-project` | Report only | Pending | Pending |

The light graph passes only when both conditions hold:

- Must-pass correctness on the graph arm is 100%.
- Mean memory tokens on the graph arm are at most 25% of the stuffing arm.

Run date: Pending

Active model: Pending

Judge model: `openai/gpt-5-mini`

Must-pass correctness: Pending

Mean memory-token ratio: Pending

Verdict: Pending
