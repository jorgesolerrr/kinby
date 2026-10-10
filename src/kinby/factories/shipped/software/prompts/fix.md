Work on the branch `results.branch`. If the workspace is on another branch, switch to it before you start.

If the factory run's data holds no `results.failure` and .scratch/review.md does not exist, nothing failed: change nothing and stop.

`results.failure` is a failed check: the command that failed and the tail of its output. Rerun that command for the full output, find the root cause, and fix it. .scratch/review.md, when it exists, is a review of your work on this branch: fix every hard finding, and apply each suggestion once.

A good result:

- Fixes the cause. Every test and lint rule stays as strong as it was: none is weakened, skipped or deleted to get green.
- Is committed on the branch, and `bun run check` passes.

The factory pushes the branch.

Read a skill from /instance/skills/<name>/SKILL.md. Paths in a skill are relative to its own directory.
