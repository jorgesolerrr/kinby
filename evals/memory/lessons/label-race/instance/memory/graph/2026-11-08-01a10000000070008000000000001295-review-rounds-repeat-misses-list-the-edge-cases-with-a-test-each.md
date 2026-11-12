---
date: 2026-11-08
description: "Review rounds repeat misses: list the edge cases with a test each before publishing"
subjects: ["review", "pull request", "tests", "GitHub label", "race"]
---
Review sent pull requests back for cases the first pass skipped, and each one cost a full fix, checks and publish round. In #822 the assess step checked for the merge-ready label, then created it, and raced another run creating it at the same moment. The fix reads the labels again when the create fails and re-raises only if the label is still missing. Never use `gh label create --force`: it overwrites the color and description the maintainer set.

First-pass habits: any check-then-act on GitHub or disk handles the act having already happened; any step the hub retries asks whose resource it acts on; list the edge cases with one test each before publishing.
