Review the branch `results.branch` against the GitHub issue numbered `work_item.issue` in the factory run's data. The change is `git diff origin/<base>...HEAD`, where `<base>` is `results.base`.

Review it adversarially, with `origin/<base>` as the fixed point. Do not change the branch.

Search memory with `kinby memory search` for findings earlier reviews on this repository kept raising, and check the change for them too.

Write the verdict to .scratch/review.md. Its first line is `clean` when the review has no hard findings, and `changes` when it has any. The findings follow, one per line, each starting with `[hard]` or `[suggestion]` and its path:line.
