If the factory run's data holds no `results.feedback`, the pull request has no review threads to answer yet: change nothing and stop.

Otherwise `results.feedback` is a JSON list of the review threads to answer on pull request `results.pr`, of the branch `results.branch`, each with its id, path, line and comments. If the workspace is on another branch, switch to `results.branch` before you start.

A good result:

- Answers every thread. Before you claim a fix, check whether a commit already on the branch fixed the thread, and say so in the reply when one did.
- Fixes what you agree with, held to the rules you implemented the issue by, and explains anything you do not fix.
- Is committed on the branch, and `bun run check` passes.
- Has .scratch/review-replies.json as one JSON object mapping every thread id to `{"fixed": true|false, "reply": "your reply"}`.
- Rewrites the pull request's title or body only where this round changed what it says: the whole new title in .scratch/pr-title.txt, the whole new body in .scratch/pr-body.md, with the pr skill and then unslop. Leave a file unwritten to keep that part as it is.

The factory pushes the branch, applies the title and body, and posts the replies.

Read a skill from /instance/skills/<name>/SKILL.md. Paths in a skill are relative to its own directory.
