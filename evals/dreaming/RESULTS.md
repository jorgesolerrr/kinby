# Consolidation eval

Status: Not run yet.

Run every fixture graph from the merged default branch:

```bash
uv run --env-file .env --group evals inspect eval evals/dreaming --model anthropic/claude-sonnet-5
```

The task runs the consolidation stage with the shipped lens over each case's fixture instance, as
of the case's `today`, and grades the live facts it leaves. No judge model is involved. A case
passes when, for each replacement it expects, one written fact supersedes every fact the
replacement names, those facts are no longer live, and the written fact cites the replacement's
sources, mentions each of its terms, and keeps its body under 800 characters.

| Case | The graph | Expected replacement |
|---|---|---|
| `area-fact-update` | An area fact maps the booking site's Stripe webhook. Two new runs on the same workbench add the idempotency decision and the `stripe listen` secret gotcha. | One area fact that supersedes the old one and cites its sources and both new episodes |
| `duplicate-merge` | Three facts earlier consolidations wrote tell one lesson, loading STAGING_KEY before a staging deploy, each with a run-by-run history. A new turn repeats it. | One fact that supersedes all three and cites every episode they cite |

## Adding a case

A case is a directory under `cases/` with a `case.toml` and an `instance/` fixture. `case.toml`
holds the case's name, the `today` consolidation runs on, and its `replacements`: for each, the
facts it supersedes, the episodes it cites, and the terms it mentions. The fixture's
`memory/graph/` holds the episodes and facts, and `.state/events.jsonl` covers the episodes earlier
consolidations reviewed. The task writes the stage's routine itself, so a fixture holds none.

`tests/test_dreaming_eval.py` runs each case with a scripted model that writes the expected
replacements, so a fixture the stage would refuse fails there, without a live model.

When you rate a consolidation's turn bad, copy the episodes and facts its frame held into a new
case, and write the replacements you expected.

## First run

Record the run date, the model, each case's grade, and the Inspect log here.
