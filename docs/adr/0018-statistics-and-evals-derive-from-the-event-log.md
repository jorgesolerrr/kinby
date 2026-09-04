# Statistics and evals derive from the event log

Ticket #10 chose to measure an instance from its thread event logs alone: per-turn metrics (tokens, cost, tool calls, memory calls, approvals, duration) are computed on demand by one core function, and the two consumers, the offline eval scorer and the `kinby stats` report, both call it rather than keeping their own telemetry. The user's verdict on a turn is recorded as a `turn.rated` event on the same log, so the live signal lives next to the turn it judges and the derived report stays recomputable from `.state/`. A separate metrics store, or hosted tracing as the source of truth, was rejected because the transcript store is already canonical and a second record of the same turns would drift from it.

## Consequences

- Offline evals run under Inspect AI in-process: the solver calls the dispatcher with a model factory that routes to Inspect's active model. The agent bridge and Docker sandboxes wait for coding-task evals.
- An eval case is a fixture instance directory plus a fresh turn. Replaying an event log to a sequence is not part of the eval contract.
- Memory-set correctness is model-graded against a reference answer and also requires that the expected node was opened; memory tokens are compared with a transcript-stuffing arm run as a task parameter. Both arms use the same token estimator, so the ratio is what the gate reads.
- Feed gate: must-pass correctness 100%, memory tokens at most 25% of the stuffing arm on average. Stretch questions report only.
- Evals never run in pytest or CI. Logs are gitignored; a passing gate commits one summary next to the task.
- Any closed turn can be rated through `thread.turn.rate`; the REPL asks after completed turns only, and the latest rating wins.
- Cost comes from a shipped price map overridable in `kinby.toml`; an unknown model reports cost as unknown, never zero.
- Gate decisions and turn duration are not in the log yet. Ticket #12 owns adding what the statistics need.

## Considered options

- The live rating as the feed gate was rejected: it is noisy, user-dependent, and has no reference answer, so it is a trend, not a threshold.
- A web dashboard was left out because the map rules out any UI beyond the REPL; the JSON report in `.state/` is the seam it would read.
