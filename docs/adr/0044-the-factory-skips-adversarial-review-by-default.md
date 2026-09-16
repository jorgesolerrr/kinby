# The factory skips adversarial review by default

The review-fix loop repeatedly hit its timeout on #236 and #237, preventing completed
implementations from reaching pull requests. The maintainer chose third-party pull
request reviewers instead of automatic adversarial review before publication.

The implementation pipeline and the coder routine now default `review_round_limit`
to `0`. That setting skips the initial review and review after a check fix. Codex
implementation, repository checks, one check-repair attempt, and pull request
publication retain their existing behavior. The babysitting routine still handles
feedback from reviewers on published pull requests.

A skipped review is `null` in the pipeline report. The pull request states that
adversarial review was not run. Passing repository checks does not imply that a
reviewer approved the change. A positive round limit remains an explicit opt-in to
the existing review loop, so existing instance configurations can choose it.
