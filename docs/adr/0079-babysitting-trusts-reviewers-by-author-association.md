# Babysitting trusts reviewers by author association

Supersedes [ADR 0031](0031-babysitting-accepts-feedback-from-trusted-authors.md).

ADR 0031 let babysitting answer review feedback only from the repository maintainer and the production Greptile app. ADR 0073 then trusted ticket text by GitHub's `author_association`, so the check keeps working in an organization's repository, where the factory has no single maintainer.

Review feedback now follows the same rule. A review thread starts a fix round only when every comment in it comes from the repository's owner, a member, a collaborator, or the Greptile app. A thread with any other author never reaches the coding client's prompt. When such threads are all that is left, the run stops as needs human. A pull request counts as reviewed only once one of those trusted authors reviewed its head.

## Consequences

- One rule decides who the factory trusts, for both tickets and reviews.
- Every collaborator can now put review text in the coding client's prompt, and the client runs commands in the factory container. The factory container stays the execution boundary from ADR 0029.
