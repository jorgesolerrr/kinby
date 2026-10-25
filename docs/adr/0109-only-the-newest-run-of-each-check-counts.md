# Only the newest run of each check counts

Amends [ADR 0086](0086-a-failed-check-on-the-head-blocks-merge-ready.md).

`assess` judged every check run on the pull request's head. When a newer workflow run replaces an earlier one on the same head, through a concurrency group for example, GitHub keeps the earlier run's checks as `cancelled`. Its `filter=latest` keeps the latest run of each name only within one check suite, so those runs still come back. `cancelled` blocks merge-ready, so `assess` waited on every wake until babysitting's round cap, though the pull request was green on GitHub.

## Decision

**`assess` judges only the newest run of each check on the head.** A check is its app and its name. The newest run is the one that started last, then the one with the highest id; a run not started yet is the newest. Every rule of ADR 0086 applies to those runs: the wait on a running check, the split between repairable and other failures, the trusted review app's exemption, and its successful run counting as a review.

A check whose newest run is `cancelled`, `action_required` or `startup_failure` still waits, as ADR 0086 says. Only an older run of a check stops counting.
