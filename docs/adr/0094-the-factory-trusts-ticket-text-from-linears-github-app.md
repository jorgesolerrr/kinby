# The factory trusts ticket text from Linear's GitHub app

Amends [ADR 0073](0073-the-factory-implements-tickets-only-trusted-authors-wrote.md).

ADR 0073 trusts ticket text only from the repository's owner, a member or a collaborator, by GitHub's `author_association`. Linear's GitHub integration comments a linkback on every new issue as `linear-code[bot]`, whose association is `NONE`. In a repository with that integration, every run stopped at `prepare` and the issue went to a human ([#712](https://github.com/jorgesolerrr/kinby/issues/712)).

## Decision

The factory also trusts ticket text from the ticket apps, whatever their association. Linear's GitHub app (`linear-code`) is the only one. An author counts as an app only when GitHub reports the user as a `Bot`, so a user account named `linear-code` gains nothing.

The ticket apps are a set of their own, apart from the review apps of [ADR 0079](0079-babysitting-trusts-reviewers-by-author-association.md). Linear writes tickets, not reviews: its review comments and check runs keep the standing they had.

## Considered options

- **Adding Linear to the review apps.** Rejected. A Linear check run would count as a review of a pull request's head, and its review threads would start fix rounds.
- **Letting a repository or an instance name the apps it trusts.** Not now. The set stays in the template's code until a second app needs it.

## Consequences

- Linear's linkback reaches the coding client's prompt, along with anything else a Linear user syncs into a comment through the app. The factory container stays the execution boundary from ADR 0029.
