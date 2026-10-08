Review the branch `results.branch` against the GitHub issue numbered `work_item.issue` in the factory run's data. The change is `git diff origin/<base>...HEAD`, where `<base>` is `results.base`.

Follow the adversarial-review skill in /instance/skills/adversarial-review/SKILL.md, with `origin/<base>` as its fixed point. Do not change the branch.

Write the verdict to .scratch/review.md. Its first line is `clean` when the review has no hard findings, and `changes` when it has any. The findings follow, one per line, each starting with `[hard]` or `[suggestion]` and its path:line.
