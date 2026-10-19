# Learning review eval

Status: Not run yet.

Run every fixture week from the merged default branch:

```bash
uv run --env-file .env --group evals inspect eval evals/learning --model openai/gpt-5
```

The task runs the learning review stage with the shipped lens over each case's fixture instance,
as of the case's `today`. The judge model compares the dream report with the case's reference
verdicts. A case passes when the report names every reference verdict, citing the same turn or
run and the same fact, and names no other verdict.

| Case | The week | Reference verdicts |
|---|---|---|
| `repeated-mistake` | A staging deploy fails for a missing key that a fact describes. The turn never recalled the fact. | One repeated mistake |
| `ignored-lesson` | A factory run recalls that release branches are cut from main, cuts one from develop, and review sends it back. | One ignored lesson |
| `clean-week` | Nothing goes badly. A failure the week before and a dreaming stage's failure are not work. | None, and no work |
| `nothing-to-learn` | An invoice export fails. The only fact that describes the failure was learned two days later. | None |

Every week also holds clean turns and a clean factory run.

## Adding a case

A case is a directory under `cases/` with a `case.toml` and an `instance/` fixture. `case.toml`
holds the case's name, the `today` the review runs on, and the reference verdicts. The fixture's
`.state/events.jsonl` and `memory/graph/` hold the week. The task writes the stage's routine
itself, so a fixture holds none.

When you rate a review's turn bad for a wrong verdict, copy the turns, runs and facts that verdict
read into a new case, and write the verdicts you expected as its reference.

## First run

Record the run date, the active and judge models, each case's grade, and the Inspect log here.
