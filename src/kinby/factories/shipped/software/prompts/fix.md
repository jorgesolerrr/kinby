The workspace is on the branch `results.branch`.

If the factory run's data holds no `results.failure` and .scratch/review.md does not exist, nothing failed: change nothing and stop.

`results.failure` is what failed. It is either a command, with the tail of its output, or the checks that failed on the pull request, each with its link. Rerun the part of a failed command that failed, such as its failing test, for the full output. Read a failed check's logs with `gh`, such as `gh run view --log-failed`. Then find the root cause and fix it. .scratch/review.md, when it exists, is a review of your work on this branch: fix every hard finding, and apply each suggestion once.

A good result:

- Fixes the cause. Every test and lint rule stays as strong as it was: none is weakened, skipped or deleted to get green.
- Passes the tests that cover what it changed and the repository's fast checks, such as lint, format and type checking. The factory runs the repository's checks after the commit.
- Is committed on the branch.

The factory pushes the branch.
