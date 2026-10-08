# The software factory babysits its pull requests until merge-ready

Supersedes [ADR 0046](0046-the-coder-leaves-review-feedback-to-humans.md).

ADR 0046 turned babysitting off. The coder opened a pull request and left review feedback to humans. Now that factories are hub-run steps, the shipped software factory babysits again, and it is on by default. After `publish` opens the pull request, the run parks at a `babysit` wait. A review of the pull request, a comment on one of its review threads, or a check suite that completed on its branch wakes it, and `assess` reads the pull request:

- Threads that only trusted authors wrote start a fix round: `answer`, `checks`, then `publish`, which pushes, replies on each thread and resolves the ones it fixed.
- A thread another author wrote starts no fix round. When such threads are all that is left, the run stops as needs human.
- A pull request a trusted reviewer reviewed on its head, with no thread left and no check running, gets the `merge-ready` label and a review request for the maintainer. The run is done.
- Anything else sends the run back to wait.

The factory never merges. After three fix rounds the run needs a human, and the issue gets `ready-for-human`.

## Consequences

- The repository's webhook has to send `pull_request_review`, `pull_request_review_comment` and `check_suite` events to the coder's `scan` routine, or a run waits until its 7-day deadline and needs a human.
- A maintainer who wants the 0046 behaviour removes `babysit`, `assess` and `done_requires` from the factory, and the run is done once the pull request opens.
