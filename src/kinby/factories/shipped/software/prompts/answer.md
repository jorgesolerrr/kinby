If the factory run's data holds no `results.feedback`, the pull request has no review threads to answer yet: change nothing and stop.

Otherwise `results.feedback` is a JSON list of the review threads to answer on the pull request of the branch `results.branch`, each with its id, path, line and comments. If the workspace is on another branch, switch to `results.branch` before you start.

Address every thread. Fix what you agree with, run the relevant tests, and commit the fixes on the branch. For anything you do not fix, explain why. Then write .scratch/review-replies.json as one JSON object mapping every thread id to `{"fixed": true|false, "reply": "your reply"}`.

Do not push, and do not reply on GitHub. The factory owns those operations.
