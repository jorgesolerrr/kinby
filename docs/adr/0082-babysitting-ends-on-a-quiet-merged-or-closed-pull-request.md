# Babysitting ends on a quiet, merged or closed pull request

Amends [ADR 0078](0078-the-software-factory-babysits-its-pull-requests-until-merge-ready.md) and [ADR 0079](0079-babysitting-trusts-reviewers-by-author-association.md).

Babysitting called a pull request merge-ready only once a trusted author left a review on its head, and it woke only on a review, a review comment or a finished check suite. On the first runs of the software factory (#621) that left two ways to wait until the 7-day deadline. A review app that finds nothing on a re-review leaves no review. Greptile reports a clean pass as a successful check run. And a merge sends a `pull_request` event, which woke nothing.

Babysitting now ends four more ways:

- A trusted review app's successful check run on the head counts as a review of that head. The apps are the ones ADR 0079 names.
- A wait step can declare a `quiet` time. With no signal for that long it moves on by itself and records `quiet` as true. `babysit` declares 20 minutes in place of its deadline. After a quiet wait, `assess` labels a pull request with every check finished and no actionable thread merge-ready without a review of its head, and comments on it to say so.
- `babysit` also wakes when its pull request closes. A merged one ends the run as done, with `merge_ready` recorded, since whoever merged it accepted it. One closed without a merge stops the run as cancelled, and the issue is left as it is.
- `factory.run.cancel` accepts a parked run, and `hub.sh cancel <run-id>` calls it on the playground.

## Consequences

- The quiet time counts from the last wake, not from the last push. A signal that changes nothing, like another app's check suite, starts it again.
- A pull request can carry `merge-ready` with no review of its head. Its comment says so, and the maintainer's review request stands.
- The repository's webhook has to send `pull_request` events too. Without them a closed pull request still ends its run at the next quiet wake.
- The web app has no cancel button yet.
